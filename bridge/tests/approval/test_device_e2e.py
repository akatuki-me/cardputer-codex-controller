from __future__ import annotations

import time
from dataclasses import dataclass

from cardputer_codex_bridge.app_server import JsonObject, RequestId
from cardputer_codex_bridge.approval import ApprovalCoordinator, HostApprovalConsole
from cardputer_codex_bridge.controller import ControllerState, DeviceControllerSession
from cardputer_codex_bridge.device_link import SyntheticSerialProvider


@dataclass
class NoopAdapter:
    def interrupt(self, thread_id: str, turn_id: str) -> None:
        del thread_id, turn_id


def _command_message(request_id: RequestId, command: str) -> JsonObject:
    return {
        "id": request_id,
        "method": "item/commandExecution/requestApproval",
        "params": {
            "threadId": "thread-controller",
            "turnId": "turn-1",
            "itemId": "item-1",
            "startedAtMs": 1000,
            "command": command,
            "cwd": "workspace/fixture",
        },
    }


def _resolved(request_id: RequestId) -> JsonObject:
    return {
        "method": "serverRequest/resolved",
        "params": {"requestId": request_id, "threadId": "thread-controller"},
    }


def test_hold_host_response_reconnect_and_resolved_share_one_pending_source() -> None:
    provider = SyntheticSerialProvider()
    responses: list[tuple[RequestId, JsonObject]] = []
    session = DeviceControllerSession(
        state=ControllerState(),
        adapter=NoopAdapter(),
        provider=provider,
        port="synthetic",
        read_timeout=0.005,
        ping_interval=1.0,
        stale_after=3.0,
        reconnect_delay=0.01,
    )
    coordinator = ApprovalCoordinator(
        send_response=lambda request_id, result: responses.append((request_id, result)),
        send_device_approval=session.send_approval,
        send_device_resolved=session.send_approval_resolved,
        slots_by_thread={"thread-controller": 1},
        codex_version="0.144.6",
    )
    session.configure_approval(
        decision_handler=coordinator.handle_device_decision,
        ready_handler=coordinator.republish,
    )
    console = HostApprovalConsole(coordinator)

    session.start()
    try:
        first_port = provider.wait_for_port(1)
        provider.inject({"t": "hello", "seq": 1, "proto": 1}, port_number=1)
        assert session.wait_for_device_hello(1.0)
        coordinator.handle_message(_command_message("rpc-private", "tool --check"))
        assert _wait_until(
            lambda: any(
                message["t"] == "approval"
                for message in provider.decoded_host_messages(port_number=1)
            ),
            timeout=1.0,
        )
        initial = provider.decoded_host_messages(port_number=1)[-1]
        assert initial["deviceApprovalId"] == "approval-000001"
        assert initial["decisions"] == ["accept", "decline"]
        assert initial["sending"] is False
        assert "rpc-private" not in repr(initial)

        # Device上のholdはpending正本を変更せず、host queueから同じIDを解決する。
        assert "approval-000001" in console.render()
        assert console.execute("decline approval-000001") is True
        assert responses == [("rpc-private", {"decision": "decline"})]
        assert provider.decoded_host_messages(port_number=1)[-1]["sending"] is True

        first_port.disconnect()
        provider.wait_for_port(2)
        provider.inject({"t": "hello", "seq": 1, "proto": 1}, port_number=2)
        assert _wait_until(
            lambda: len(provider.decoded_host_messages(port_number=2)) >= 3,
            timeout=1.0,
        )
        restored = provider.decoded_host_messages(port_number=2)[2]
        assert restored["t"] == "approval"
        assert restored["deviceApprovalId"] == "approval-000001"
        assert restored["sending"] is True
        assert restored["decisions"] == []

        coordinator.handle_message(_resolved("rpc-private"))
        assert _wait_until(
            lambda: provider.decoded_host_messages(port_number=2)[-1]["t"]
            == "approval_resolved",
            timeout=1.0,
        )
        resolved = provider.decoded_host_messages(port_number=2)[-1]
        assert resolved["deviceApprovalId"] == "approval-000001"
        assert resolved["decision"] == "decline"
        assert coordinator.pending == ()
    finally:
        session.close()


def _wait_until(predicate: object, *, timeout: float) -> bool:
    assert callable(predicate)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return bool(predicate())
