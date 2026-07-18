from __future__ import annotations

import importlib
import time
from dataclasses import dataclass, field
from typing import Any

import pytest
from cardputer_codex_bridge.controller import (
    ControllerState,
    DeviceControllerSession,
    LinkState,
)
from cardputer_codex_bridge.device_link import (
    PySerialProvider,
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
        ping_interval=1.0,
        stale_after=3.0,
        reconnect_delay=0.01,
    )

    session.start()
    try:
        provider.wait_for_port()
        provider.inject({"t": "hello", "seq": 1, "proto": 1})
        provider.inject({"t": "interrupt", "seq": 2})
        provider.inject({"t": "interrupt", "seq": 3})

        assert session.wait_for_device_hello(1.0)
        assert session.wait_for_interrupt_messages(2, 1.0)
        assert adapter.calls == [("thread-synthetic", "turn-synthetic")]
        host_messages = provider.decoded_host_messages()
        assert host_messages[0]["t"] == "hello"
        assert host_messages[0]["proto"] == 1
        assert host_messages[1]["t"] == "state"
        assert host_messages[1]["full"] is True
        assert len(host_messages[1]["slots"]) == 6
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
        first.disconnect()

        provider.wait_for_port(2)
        provider.inject({"t": "hello", "seq": 1, "proto": 1}, port_number=2)
        provider.inject({"t": "interrupt", "seq": 2}, port_number=2)
        assert session.wait_for_interrupt(1.0)
        assert adapter.calls == [("thread-reconnect", "turn-reconnect")]
        reconnect_messages = provider.decoded_host_messages(port_number=2)
        assert [message["t"] for message in reconnect_messages[:2]] == ["hello", "state"]
        assert reconnect_messages[1]["full"] is True
        assert reconnect_messages[1]["linkState"] == "active"
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


def _wait_until(predicate: Any, *, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return bool(predicate())
