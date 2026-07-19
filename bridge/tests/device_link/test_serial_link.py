from __future__ import annotations

import importlib
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import pytest
from cardputer_codex_bridge.app_server.types import JsonObject
from cardputer_codex_bridge.approval import DeviceApproval
from cardputer_codex_bridge.controller import (
    ControllerState,
    DeviceControllerSession,
    LinkState,
)
from cardputer_codex_bridge.device_link import (
    PySerialProvider,
    SerialLink,
    SerialPort,
    SyntheticSerialPort,
    SyntheticSerialProvider,
)


@dataclass
class RecordingAdapter:
    calls: list[tuple[str, str]] = field(default_factory=list)

    def interrupt(self, thread_id: str, turn_id: str) -> None:
        self.calls.append((thread_id, turn_id))


def test_session_sends_hello_full_snapshot_and_forwards_interrupt_once() -> None:
    provider = SyntheticSerialProvider()
    state = ControllerState()
    adapter = RecordingAdapter()
    state.turn_started(0, thread_id="thread-synthetic", turn_id="turn-synthetic")
    session = DeviceControllerSession(
        state=state,
        adapter=adapter,
        provider=provider,
        port="synthetic",
        read_timeout=0.01,
        ping_interval=0.03,
        stale_after=0.15,
        reconnect_delay=0.01,
    )

    session.start()
    try:
        provider.wait_for_port()
        time.sleep(0.08)
        assert state.link_state is LinkState.STALE
        assert provider.decoded_host_messages() == []
        provider.inject({"t": "hello", "seq": 1, "proto": 1})
        provider.inject(
            {"t": "interrupt", "seq": 2, "slot": 1, "turnId": "turn-synthetic"}
        )
        provider.inject(
            {"t": "interrupt", "seq": 3, "slot": 1, "turnId": "turn-synthetic"}
        )

        assert session.wait_for_device_hello(1.0)
        assert session.wait_for_interrupt_messages(2, 1.0)
        assert adapter.calls == [("thread-synthetic", "turn-synthetic")]
        provider.inject({"t": "hello", "seq": 4, "proto": 1})
        time.sleep(0.05)
        host_messages = provider.decoded_host_messages()
        assert host_messages[0]["t"] == "hello"
        assert host_messages[0]["proto"] == 1
        assert isinstance(host_messages[0]["session"], str)
        assert host_messages[0]["session"]
        assert host_messages[0]["seq"] == 1
        assert host_messages[1]["t"] == "state"
        assert host_messages[1]["seq"] == 2
        assert host_messages[1]["full"] is True
        assert len(host_messages[1]["slots"]) == 6
        assert state.link_state is LinkState.ACTIVE
        assert sum(message["t"] == "hello" for message in host_messages) == 1
        assert sum(message["t"] == "state" for message in host_messages) == 1
    finally:
        session.close()


def test_session_routes_approval_messages_only_after_handshake() -> None:
    provider = SyntheticSerialProvider()
    decisions: list[tuple[str, str]] = []
    ready_calls: list[bool] = []
    session = DeviceControllerSession(
        state=ControllerState(),
        adapter=RecordingAdapter(),
        provider=provider,
        port="synthetic",
        read_timeout=0.005,
        ping_interval=1.0,
        stale_after=3.0,
        reconnect_delay=0.01,
    )
    session.configure_approval(
        decision_handler=lambda approval_id, decision: (
            decisions.append((approval_id, decision)) or True
        ),
        ready_handler=lambda: ready_calls.append(True),
    )
    approval = DeviceApproval(
        approval_id="approval-000001",
        summary="fixture",
        slot=2,
        kind="command",
        lines=("tool --check fixture.txt",),
        cwd="workspace/fixture",
        decisions=("accept", "decline"),
    )

    assert session.send_approval(approval, pending_count=1) is False
    session.start()
    try:
        provider.wait_for_port()
        provider.inject(
            {
                "t": "decision",
                "seq": 1,
                "deviceApprovalId": approval.approval_id,
                "decision": "accept",
            }
        )
        time.sleep(0.03)
        assert decisions == []

        provider.inject({"t": "hello", "seq": 2, "proto": 1})
        assert session.wait_for_device_hello(1.0)
        assert ready_calls == [True]
        assert session.send_approval(approval, pending_count=1) is True

        provider.inject(
            {
                "t": "decision",
                "seq": 3,
                "deviceApprovalId": approval.approval_id,
                "decision": "accept",
            }
        )
        provider.inject(
            {
                "t": "decision",
                "seq": 4,
                "deviceApprovalId": approval.approval_id,
                "decision": "unknown",
            }
        )
        assert _wait_until(lambda: len(decisions) == 1, timeout=1.0)
        assert decisions == [(approval.approval_id, "accept")]
        assert session.send_approval_resolved(approval.approval_id, "accept") is True

        messages = provider.decoded_host_messages()
        approval_message = next(message for message in messages if message["t"] == "approval")
        assert approval_message == {
            "t": "approval",
            "seq": 3,
            "deviceApprovalId": approval.approval_id,
            "slot": 2,
            "kind": "command",
            "lines": ["tool --check fixture.txt"],
            "cwd": "workspace/fixture",
            "decisions": ["accept", "decline"],
            "contentComplete": True,
            "riskClass": "normal",
            "pendingCount": 1,
            "sending": False,
        }
        resolved_message = next(
            message for message in messages if message["t"] == "approval_resolved"
        )
        assert resolved_message["deviceApprovalId"] == approval.approval_id
        assert resolved_message["decision"] == "accept"
    finally:
        session.close()


def test_disconnect_reconnects_and_resends_a_full_snapshot() -> None:
    provider = SyntheticSerialProvider()
    state = ControllerState()
    adapter = RecordingAdapter()
    state.turn_started(0, thread_id="thread-reconnect", turn_id="turn-reconnect")
    session = DeviceControllerSession(
        state=state,
        adapter=adapter,
        provider=provider,
        port="synthetic",
        read_timeout=0.01,
        ping_interval=1.0,
        stale_after=3.0,
        reconnect_delay=0.01,
    )

    session.start()
    try:
        first = provider.wait_for_port(1)
        provider.inject({"t": "hello", "seq": 20, "proto": 1}, port_number=1)
        assert session.wait_for_device_hello(1.0)
        first_messages = provider.decoded_host_messages(port_number=1)
        first.disconnect()

        provider.wait_for_port(2)
        provider.inject({"t": "hello", "seq": 1, "proto": 1}, port_number=2)
        provider.inject(
            {"t": "interrupt", "seq": 2, "slot": 1, "turnId": "turn-reconnect"},
            port_number=2,
        )
        assert session.wait_for_interrupt(1.0)
        assert adapter.calls == [("thread-reconnect", "turn-reconnect")]
        reconnect_messages = provider.decoded_host_messages(port_number=2)
        assert [message["t"] for message in reconnect_messages[:2]] == ["hello", "state"]
        assert first_messages[0]["session"] != reconnect_messages[0]["session"]
        assert [message["seq"] for message in reconnect_messages[:2]] == [1, 2]
        assert reconnect_messages[1]["full"] is True
        assert reconnect_messages[1]["linkState"] == "active"
    finally:
        session.close()


def test_snapshot_sequence_and_write_order_are_serialized() -> None:
    class BlockingSnapshotState(ControllerState):
        def __init__(self) -> None:
            super().__init__()
            self.block_next = False
            self.entered = threading.Event()
            self.release = threading.Event()

        def snapshot(self, *, seq: int) -> JsonObject:
            if self.block_next:
                self.block_next = False
                self.entered.set()
                self.release.wait(timeout=1.0)
            return super().snapshot(seq=seq)

    provider = SyntheticSerialProvider()
    state = BlockingSnapshotState()
    session = DeviceControllerSession(
        state=state,
        adapter=RecordingAdapter(),
        provider=provider,
        port="synthetic",
        read_timeout=0.005,
        ping_interval=1.0,
        stale_after=3.0,
        reconnect_delay=0.01,
    )

    session.start()
    try:
        provider.wait_for_port()
        provider.inject({"t": "hello", "seq": 1, "proto": 1})
        assert session.wait_for_device_hello(1.0)
        state.block_next = True
        first = threading.Thread(target=session.send_snapshot)
        second = threading.Thread(target=session.send_snapshot)
        first.start()
        assert state.entered.wait(1.0)
        second.start()
        time.sleep(0.03)
        state.release.set()
        first.join(timeout=1.0)
        second.join(timeout=1.0)

        sequences = [
            message["seq"]
            for message in provider.decoded_host_messages()
            if message["t"] == "state"
        ]
        assert sequences == sorted(sequences)
    finally:
        session.close()


def test_ping_is_sent_and_pong_keeps_the_link_active() -> None:
    provider = SyntheticSerialProvider()
    session = DeviceControllerSession(
        state=ControllerState(),
        adapter=RecordingAdapter(),
        provider=provider,
        port="synthetic",
        read_timeout=0.005,
        ping_interval=0.03,
        stale_after=0.15,
        reconnect_delay=0.01,
    )

    session.start()
    try:
        provider.wait_for_port()
        provider.inject({"t": "hello", "seq": 1, "proto": 1})
        assert session.wait_for_device_hello(1.0)
        assert _wait_until(
            lambda: any(
                message["t"] == "ping" for message in provider.decoded_host_messages()
            ),
            timeout=1.0,
        )
        provider.inject({"t": "pong", "seq": 2})
        time.sleep(0.05)
        assert session.active is True
        assert provider.open_count == 1
    finally:
        session.close()


def test_interrupt_requires_selected_slot_and_current_turn_id() -> None:
    provider = SyntheticSerialProvider()
    state = ControllerState()
    adapter = RecordingAdapter()
    state.turn_started(0, thread_id="thread-current", turn_id="turn-current")
    session = DeviceControllerSession(
        state=state,
        adapter=adapter,
        provider=provider,
        port="synthetic",
        read_timeout=0.005,
        ping_interval=1.0,
        stale_after=3.0,
        reconnect_delay=0.01,
    )

    session.start()
    try:
        provider.wait_for_port()
        provider.inject({"t": "hello", "seq": 1, "proto": 1})
        assert session.wait_for_device_hello(1.0)
        provider.inject({"t": "interrupt", "seq": 2})
        provider.inject(
            {"t": "interrupt", "seq": 3, "slot": 2, "turnId": "turn-current"}
        )
        provider.inject(
            {"t": "interrupt", "seq": 4, "slot": 1, "turnId": "turn-old"}
        )
        time.sleep(0.05)
        assert adapter.calls == []

        provider.inject(
            {"t": "interrupt", "seq": 5, "slot": 1, "turnId": "turn-current"}
        )
        assert session.wait_for_interrupt(1.0)
        assert adapter.calls == [("thread-current", "turn-current")]
    finally:
        session.close()


def test_disconnect_marks_stale_and_locks_sends_while_reopen_fails() -> None:
    class SingleOpenProvider:
        def __init__(self) -> None:
            self.port = SyntheticSerialPort(read_timeout=0.01)
            self.calls = 0

        def open(
            self,
            port: str,
            *,
            baudrate: int,
            read_timeout: float,
        ) -> SerialPort:
            del port, baudrate, read_timeout
            self.calls += 1
            if self.calls > 1:
                raise OSError("synthetic unavailable")
            return self.port

    provider = SingleOpenProvider()
    state = ControllerState()
    session = DeviceControllerSession(
        state=state,
        adapter=RecordingAdapter(),
        provider=provider,
        port="synthetic",
        read_timeout=0.01,
        ping_interval=1.0,
        stale_after=3.0,
        reconnect_delay=0.01,
    )

    session.start()
    try:
        assert session.wait_connected(1.0)
        provider.port.inject({"t": "hello", "seq": 1, "proto": 1})
        assert session.wait_for_device_hello(1.0)
        provider.port.disconnect()
        assert _wait_until(
            lambda: state.link_state is LinkState.STALE and not session.active,
            timeout=1.0,
        )
        assert session.send_snapshot() is False
    finally:
        session.close()


def test_pyserial_provider_disables_flow_control_without_line_toggles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    class FakePort:
        def read(self, size: int = 1) -> bytes:
            del size
            return b""

        def write(self, data: bytes) -> int:
            return len(data)

        def close(self) -> None:
            pass

    class FakeSerialModule:
        @staticmethod
        def Serial(**kwargs: Any) -> FakePort:
            calls.append(kwargs)
            return FakePort()

    monkeypatch.setattr(importlib, "import_module", lambda name: FakeSerialModule())

    PySerialProvider().open("synthetic-port", baudrate=115_200, read_timeout=0.1)

    assert len(calls) == 1
    assert calls[0]["rtscts"] is False
    assert calls[0]["dsrdtr"] is False
    assert "dtr" not in calls[0]
    assert "rts" not in calls[0]


def test_close_contains_attribute_error_from_windows_double_close_race() -> None:
    class CloseRacePort:
        def __init__(self) -> None:
            self.unblock = threading.Event()
            self.close_calls = 0

        def read(self, size: int = 1) -> bytes:
            del size
            self.unblock.wait(timeout=5.0)
            raise OSError("synthetic close")

        def write(self, data: bytes) -> int:
            return len(data)

        def close(self) -> None:
            self.close_calls += 1
            self.unblock.set()
            if self.close_calls > 1:
                raise AttributeError("synthetic Windows serial close race")

    class SinglePortProvider:
        def __init__(self) -> None:
            self.port = CloseRacePort()

        def open(
            self,
            port: str,
            *,
            baudrate: int,
            read_timeout: float,
        ) -> SerialPort:
            del port, baudrate, read_timeout
            return self.port

    provider = SinglePortProvider()
    link = SerialLink(
        port="synthetic",
        provider=provider,
        on_message=lambda message: None,
        on_connected=lambda: None,
        on_stale=lambda: None,
        ping_factory=lambda: {"t": "ping", "seq": 1},
        read_timeout=0.01,
        ping_interval=1.0,
        stale_after=3.0,
        reconnect_delay=0.01,
    )

    link.start()
    assert link.wait_connected(1.0)
    link.close()

    assert provider.port.close_calls == 2
    assert link.last_unexpected_error_type is None


def _wait_until(predicate: Any, *, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return bool(predicate())
