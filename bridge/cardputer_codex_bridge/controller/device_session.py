from __future__ import annotations

import threading
import time

from cardputer_codex_bridge.app_server.types import JsonObject
from cardputer_codex_bridge.device_link import SerialLink, SerialProvider

from .state import ControllerState, HostCommandAdapter, LinkState


class DeviceControllerSession:
    """ControllerStateとUSB CDC device-linkを接続するconsumer。"""

    def __init__(
        self,
        *,
        state: ControllerState,
        adapter: HostCommandAdapter,
        provider: SerialProvider,
        port: str,
        read_timeout: float = 0.1,
        ping_interval: float = 2.0,
        stale_after: float = 6.0,
        reconnect_delay: float = 0.25,
    ) -> None:
        self._state = state
        self._adapter = adapter
        self._sequence = 0
        self._sequence_lock = threading.Lock()
        self._message_lock = threading.Lock()
        self._device_hello = threading.Event()
        self._interrupt_forwarded = threading.Event()
        self._interrupt_messages = 0
        self._link = SerialLink(
            port=port,
            provider=provider,
            on_message=self._on_message,
            on_connected=self._on_connected,
            on_stale=self._on_stale,
            ping_factory=self._ping,
            read_timeout=read_timeout,
            ping_interval=ping_interval,
            stale_after=stale_after,
            reconnect_delay=reconnect_delay,
        )

    @property
    def active(self) -> bool:
        return self._link.active

    @property
    def interrupt_messages(self) -> int:
        with self._message_lock:
            return self._interrupt_messages

    def start(self) -> None:
        self._link.start()

    def close(self) -> None:
        self._link.close()

    def wait_connected(self, timeout: float) -> bool:
        return self._link.wait_connected(timeout)

    def wait_for_device_hello(self, timeout: float) -> bool:
        return self._device_hello.wait(timeout)

    def wait_for_interrupt(self, timeout: float) -> bool:
        return self._interrupt_forwarded.wait(timeout)

    def wait_for_interrupt_messages(self, count: int, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.interrupt_messages >= count:
                return True
            time.sleep(0.005)
        return self.interrupt_messages >= count

    def send_snapshot(self) -> bool:
        return self._link.send(self._state.snapshot(seq=self._next_sequence()))

    def _on_connected(self) -> None:
        self._state.link_state = LinkState.ACTIVE
        hello: JsonObject = {
            "t": "hello",
            "seq": self._next_sequence(),
            "proto": 1,
            "host": "bridge",
        }
        if not self._link.send(hello) or not self.send_snapshot():
            raise OSError("initial device-link snapshot could not be sent")

    def _on_stale(self) -> None:
        self._state.link_state = LinkState.STALE

    def _on_message(self, message: JsonObject) -> None:
        message_type = message["t"]
        if message_type == "hello":
            self._device_hello.set()
            return
        if message_type == "select":
            slot = message.get("slot")
            if isinstance(slot, int) and not isinstance(slot, bool) and 1 <= slot <= 6:
                self._state.select(slot - 1)
                self.send_snapshot()
            return
        if message_type != "interrupt":
            return
        with self._message_lock:
            self._interrupt_messages += 1
        if self._state.interrupt_active(self._adapter):
            self._interrupt_forwarded.set()

    def _ping(self) -> JsonObject:
        return {"t": "ping", "seq": self._next_sequence()}

    def _next_sequence(self) -> int:
        with self._sequence_lock:
            self._sequence += 1
            return self._sequence


__all__ = ["DeviceControllerSession"]
