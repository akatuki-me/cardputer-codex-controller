from __future__ import annotations

import json
import queue
import threading
from typing import cast

from cardputer_codex_bridge.app_server.types import JsonObject

from .serial_link import SerialPort


class SyntheticSerialPort:
    """実serial I/Oを使わない、device側から注入可能な合成port。"""

    def __init__(self, *, read_timeout: float) -> None:
        self._read_timeout = read_timeout
        self._inbound: queue.Queue[bytes | OSError] = queue.Queue()
        self._writes: list[bytes] = []
        self._write_lock = threading.Lock()
        self._closed = threading.Event()

    @property
    def writes(self) -> tuple[bytes, ...]:
        with self._write_lock:
            return tuple(self._writes)

    def read(self, size: int = 1) -> bytes:
        del size
        if self._closed.is_set():
            raise OSError("synthetic serial port is closed")
        try:
            item = self._inbound.get(timeout=self._read_timeout)
        except queue.Empty:
            return b""
        if isinstance(item, OSError):
            raise item
        return item

    def write(self, data: bytes) -> int:
        if self._closed.is_set():
            raise OSError("synthetic serial port is closed")
        with self._write_lock:
            self._writes.append(data)
        return len(data)

    def inject(self, message: JsonObject) -> None:
        payload = json.dumps(
            message,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        self._inbound.put(payload + b"\n")

    def disconnect(self) -> None:
        self._inbound.put(OSError("synthetic disconnect"))

    def close(self) -> None:
        self._closed.set()


class SyntheticSerialProvider:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._ports: list[SyntheticSerialPort] = []
        self.open_calls: list[tuple[str, int, float]] = []

    @property
    def open_count(self) -> int:
        with self._condition:
            return len(self._ports)

    def open(
        self,
        port: str,
        *,
        baudrate: int,
        read_timeout: float,
    ) -> SerialPort:
        serial_port = SyntheticSerialPort(read_timeout=read_timeout)
        with self._condition:
            self.open_calls.append((port, baudrate, read_timeout))
            self._ports.append(serial_port)
            self._condition.notify_all()
        return serial_port

    def wait_for_port(self, number: int = 1, *, timeout: float = 2.0) -> SyntheticSerialPort:
        if number <= 0:
            raise ValueError("number must be positive")
        with self._condition:
            ready = self._condition.wait_for(lambda: len(self._ports) >= number, timeout=timeout)
            if not ready:
                raise TimeoutError("synthetic serial port did not open")
            return self._ports[number - 1]

    def inject(self, message: JsonObject, *, port_number: int | None = None) -> None:
        with self._condition:
            if not self._ports:
                raise RuntimeError("synthetic serial port is not open")
            index = len(self._ports) - 1 if port_number is None else port_number - 1
            serial_port = self._ports[index]
        serial_port.inject(message)

    def decoded_host_messages(self, *, port_number: int | None = None) -> list[JsonObject]:
        with self._condition:
            if not self._ports:
                return []
            index = len(self._ports) - 1 if port_number is None else port_number - 1
            writes = self._ports[index].writes
        result: list[JsonObject] = []
        for payload in writes:
            value = json.loads(payload)
            if not isinstance(value, dict):
                raise TypeError("synthetic host message must be an object")
            result.append(cast(JsonObject, value))
        return result


__all__ = ["SyntheticSerialPort", "SyntheticSerialProvider"]
