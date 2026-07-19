from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable

from cardputer_codex_bridge.app_server.types import JsonObject, JsonValue
from cardputer_codex_bridge.approval import DeviceApproval, device_decisions
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
        self._handshake_lock = threading.Lock()
        self._handshake_complete = False
        self._host_session = ""
        self._message_lock = threading.Lock()
        self._device_hello = threading.Event()
        self._interrupt_forwarded = threading.Event()
        self._interrupt_messages = 0
        self._started = False
        self._decision_handler: Callable[[str, str], bool] | None = None
        self._ready_handler: Callable[[], object] | None = None
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
        self._started = True
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

    def configure_approval(
        self,
        *,
        decision_handler: Callable[[str, str], bool],
        ready_handler: Callable[[], object],
    ) -> None:
        """Approval callbackはserial thread開始前に一度だけ配線する。"""
        if self._started:
            raise RuntimeError("approval handlers must be configured before start")
        if self._decision_handler is not None or self._ready_handler is not None:
            raise RuntimeError("approval handlers are already configured")
        self._decision_handler = decision_handler
        self._ready_handler = ready_handler

    def send_approval(self, approval: DeviceApproval, pending_count: int) -> bool:
        if not 0 <= pending_count <= 65_535:
            raise ValueError("pending_count must fit uint16")
        with self._handshake_lock:
            if not self._handshake_complete:
                return False
        decisions: list[JsonValue] = list(device_decisions(approval))
        lines: list[JsonValue] = list(approval.lines)
        return self._send_generated(
            lambda seq: {
                "t": "approval",
                "seq": seq,
                "deviceApprovalId": approval.approval_id,
                "slot": approval.slot,
                "kind": approval.kind,
                "lines": lines,
                "cwd": approval.cwd,
                "decisions": decisions,
                "contentComplete": approval.content_complete,
                "riskClass": approval.risk_class,
                "pendingCount": pending_count,
                "sending": approval.sending,
            }
        )

    def send_approval_resolved(
        self,
        approval_id: str,
        decision: str | None,
    ) -> bool:
        with self._handshake_lock:
            if not self._handshake_complete:
                return False

        def message(seq: int) -> JsonObject:
            result: JsonObject = {
                "t": "approval_resolved",
                "seq": seq,
                "deviceApprovalId": approval_id,
            }
            if decision is not None:
                result["decision"] = decision
            return result

        return self._send_generated(message)

    def send_snapshot(self) -> bool:
        with self._handshake_lock:
            if not self._handshake_complete:
                return False
        return self._send_generated(lambda seq: self._state.snapshot(seq=seq))

    def _on_connected(self) -> None:
        self._state.set_link_state(LinkState.STALE)
        self._device_hello.clear()
        with self._handshake_lock:
            self._handshake_complete = False
            self._host_session = uuid.uuid4().hex
        with self._sequence_lock:
            self._sequence = 0

    def _on_stale(self) -> None:
        self._state.set_link_state(LinkState.STALE)
        with self._handshake_lock:
            self._handshake_complete = False

    def _on_message(self, message: JsonObject) -> None:
        message_type = message["t"]
        if message_type == "hello":
            with self._handshake_lock:
                if self._handshake_complete:
                    return
                session = self._host_session
                if not session:
                    raise OSError("host session was not initialized")
                if not self._send_generated(
                    lambda seq: {
                        "t": "hello",
                        "seq": seq,
                        "proto": 1,
                        "host": "bridge",
                        "session": session,
                    }
                ):
                    raise OSError("initial device-link snapshot could not be sent")
                self._state.set_link_state(LinkState.ACTIVE)
                if not self._send_generated(lambda seq: self._state.snapshot(seq=seq)):
                    self._state.set_link_state(LinkState.STALE)
                    raise OSError("initial device-link snapshot could not be sent")
                self._handshake_complete = True
            ready_handler = self._ready_handler
            if ready_handler is not None:
                ready_handler()
            self._device_hello.set()
            return
        with self._handshake_lock:
            if not self._handshake_complete:
                return
        if message_type == "select":
            slot = message.get("slot")
            if isinstance(slot, int) and not isinstance(slot, bool) and 1 <= slot <= 6:
                self._state.select(slot - 1)
                self.send_snapshot()
            return
        if message_type == "decision":
            approval_id = message.get("deviceApprovalId")
            decision = message.get("decision")
            handler = self._decision_handler
            if (
                isinstance(approval_id, str)
                and approval_id
                and decision in ("accept", "decline")
                and handler is not None
            ):
                assert isinstance(decision, str)
                handler(approval_id, decision)
            return
        if message_type != "interrupt":
            return
        slot = message.get("slot")
        turn_id = message.get("turnId")
        if (
            not isinstance(slot, int)
            or isinstance(slot, bool)
            or not isinstance(turn_id, str)
            or not turn_id
        ):
            return
        with self._message_lock:
            self._interrupt_messages += 1
        if self._state.interrupt_claimed(slot, turn_id, self._adapter):
            self._interrupt_forwarded.set()

    def _ping(self) -> JsonObject | None:
        with self._handshake_lock:
            if not self._handshake_complete:
                return None
        return {"t": "ping", "seq": self._next_sequence()}

    def _send_generated(self, factory: Callable[[int], JsonObject]) -> bool:
        return self._link.send_generated(lambda: factory(self._next_sequence()))

    def _next_sequence(self) -> int:
        with self._sequence_lock:
            self._sequence += 1
            return self._sequence


__all__ = ["DeviceControllerSession"]
