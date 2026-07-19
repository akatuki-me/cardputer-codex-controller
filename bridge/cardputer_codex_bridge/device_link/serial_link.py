from __future__ import annotations

import importlib
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from typing import Protocol, cast

from cardputer_codex_bridge.app_server.types import JsonObject

from .protocol import DeviceLinkDecoder, DeviceLinkProtocolError, encode_message


class SerialPort(Protocol):
    def read(self, size: int = 1) -> bytes: ...

    def write(self, data: bytes) -> int | None: ...

    def close(self) -> None: ...


class SerialProvider(Protocol):
    """Serial portの生成を実機と合成fixtureの間で差し替える境界。"""

    def open(
        self,
        port: str,
        *,
        baudrate: int,
        read_timeout: float,
    ) -> SerialPort: ...


class PySerialProvider:
    """DTR/RTSを個別操作せず、hardware flow controlを無効化して開く。"""

    def open(
        self,
        port: str,
        *,
        baudrate: int,
        read_timeout: float,
    ) -> SerialPort:
        serial_module = importlib.import_module("serial")
        serial_port = serial_module.Serial(
            port=port,
            baudrate=baudrate,
            timeout=read_timeout,
            write_timeout=read_timeout,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )
        return cast(SerialPort, serial_port)


class SerialLink:
    """device-link v1のread thread、heartbeat、stale、再接続を管理する。"""

    def __init__(
        self,
        *,
        port: str,
        provider: SerialProvider,
        on_message: Callable[[JsonObject], None],
        on_connected: Callable[[], None],
        on_stale: Callable[[], None],
        ping_factory: Callable[[], JsonObject],
        baudrate: int = 115_200,
        read_timeout: float = 0.1,
        ping_interval: float = 2.0,
        stale_after: float = 6.0,
        reconnect_delay: float = 0.25,
    ) -> None:
        if not port:
            raise ValueError("port must not be empty")
        if min(baudrate, read_timeout, ping_interval, stale_after, reconnect_delay) <= 0:
            raise ValueError("serial link timing and baudrate values must be positive")
        if stale_after <= ping_interval:
            raise ValueError("stale_after must be greater than ping_interval")
        self._port_name = port
        self._provider = provider
        self._on_message = on_message
        self._on_connected = on_connected
        self._on_stale = on_stale
        self._ping_factory = ping_factory
        self._baudrate = baudrate
        self._read_timeout = read_timeout
        self._ping_interval = ping_interval
        self._stale_after = stale_after
        self._reconnect_delay = reconnect_delay
        self._stop = threading.Event()
        self._active = threading.Event()
        self._connection_lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._connection: SerialPort | None = None
        self._thread: threading.Thread | None = None
        self._last_unexpected_error_type: str | None = None

    @property
    def active(self) -> bool:
        return self._active.is_set()

    @property
    def last_unexpected_error_type(self) -> str | None:
        return self._last_unexpected_error_type

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("serial link is already started")
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="cardputer-device-link",
            daemon=True,
        )
        self._thread.start()

    def wait_connected(self, timeout: float) -> bool:
        return self._active.wait(timeout)

    def send(self, message: JsonObject) -> bool:
        payload = encode_message(message)
        with self._write_lock, self._connection_lock:
            connection = self._connection
            if connection is None or not self._active.is_set():
                return False
            written = connection.write(payload)
            if written is not None and written != len(payload):
                raise OSError("serial write was incomplete")
            return True

    def close(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=max(1.0, self._read_timeout * 4))
            if thread.is_alive():
                with self._connection_lock:
                    connection = self._connection
                if connection is not None:
                    with suppress(OSError, AttributeError):
                        connection.close()
                thread.join(timeout=max(1.0, self._read_timeout * 4))
        self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            connection: SerialPort | None = None
            became_active = False
            try:
                connection = self._provider.open(
                    self._port_name,
                    baudrate=self._baudrate,
                    read_timeout=self._read_timeout,
                )
                decoder = DeviceLinkDecoder()
                with self._connection_lock:
                    self._connection = connection
                    self._active.set()
                became_active = True
                last_received = time.monotonic()
                next_ping = last_received + self._ping_interval
                self._on_connected()

                while not self._stop.is_set():
                    chunk = connection.read(512)
                    now = time.monotonic()
                    if chunk:
                        last_received = now
                        for message in decoder.feed(chunk):
                            self._on_message(message)
                    if now >= next_ping:
                        if not self.send(self._ping_factory()):
                            raise OSError("serial link became unavailable")
                        next_ping = now + self._ping_interval
                    if now - last_received >= self._stale_after:
                        raise TimeoutError("serial link heartbeat became stale")
            except (DeviceLinkProtocolError, OSError, TimeoutError):
                pass
            except Exception as error:
                # Background threadから値やportを含むtracebackを通常stderrへ出さない。
                self._last_unexpected_error_type = type(error).__name__
            finally:
                with self._connection_lock:
                    self._active.clear()
                    if self._connection is connection:
                        self._connection = None
                if connection is not None:
                    with suppress(OSError, AttributeError):
                        connection.close()
                if became_active:
                    self._on_stale()
            if not self._stop.wait(self._reconnect_delay):
                continue


__all__ = ["PySerialProvider", "SerialLink", "SerialPort", "SerialProvider"]
