from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path

from cardputer_codex_bridge.app_server import AppServerClient, ClientInfo
from cardputer_codex_bridge.approval import (
    ApprovalContract,
    ApprovalCoordinator,
    ApprovalResolved,
    ApprovalStatus,
    CommandApprovalRequest,
    FileChangeApprovalRequest,
    HostApprovalConsole,
)
from cardputer_codex_bridge.controller import ControllerState, DeviceControllerSession
from cardputer_codex_bridge.device_link import SyntheticSerialProvider

FAKE_SERVER = Path(__file__).with_name("fake_approval_app_server.py")
CLIENT_INFO = ClientInfo(
    name="cardputer-codex-controller",
    title="Cardputer Codex Controller",
    version="0.0.0",
)


@dataclass
class NoopAdapter:
    def interrupt(self, thread_id: str, turn_id: str) -> None:
        del thread_id, turn_id


def test_transport_approval_response_and_resolved_round_trip() -> None:
    client = AppServerClient(command=(sys.executable, "-u", str(FAKE_SERVER)))
    client.start()
    try:
        client.initialize(CLIENT_INFO)
        contract = ApprovalContract(client.respond)

        command = contract.handle_message(client.next_message())
        assert isinstance(command, CommandApprovalRequest)
        assert contract.get(command.request_id).status is ApprovalStatus.AWAITING_DECISION  # type: ignore[union-attr]

        contract.respond_command(command.request_id, "accept")
        assert contract.get(command.request_id).status is ApprovalStatus.RESPONSE_SENT  # type: ignore[union-attr]
        command_resolved = contract.handle_message(client.next_message())
        assert isinstance(command_resolved, ApprovalResolved)
        assert contract.get(command.request_id) is None

        file_change = contract.handle_message(client.next_message())
        assert isinstance(file_change, FileChangeApprovalRequest)
        contract.respond_file_change(file_change.request_id, "decline")
        file_resolved = contract.handle_message(client.next_message())
        assert isinstance(file_resolved, ApprovalResolved)
        assert contract.pending == ()
    finally:
        shutdown = client.close()

    assert shutdown.exit_code == 0
    assert shutdown.forced is False


def test_app_server_device_and_host_surfaces_share_the_approval_lifecycle() -> None:
    client = AppServerClient(command=(sys.executable, "-u", str(FAKE_SERVER)))
    provider = SyntheticSerialProvider()
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
    client.start()
    try:
        initialized = client.initialize(CLIENT_INFO)
        coordinator = ApprovalCoordinator(
            send_response=client.respond,
            send_device_approval=session.send_approval,
            send_device_resolved=session.send_approval_resolved,
            slots_by_thread={"thread-synthetic": 1},
            codex_version=initialized.codex_version,
        )
        session.configure_approval(
            decision_handler=coordinator.handle_device_decision,
            ready_handler=coordinator.republish,
        )
        console = HostApprovalConsole(coordinator)
        session.start()
        provider.wait_for_port()
        provider.inject({"t": "hello", "seq": 1, "proto": 1})
        assert session.wait_for_device_hello(1.0)

        coordinator.handle_message(client.next_message())
        assert _wait_until(
            lambda: any(
                message["t"] == "approval"
                for message in provider.decoded_host_messages()
            ),
            timeout=1.0,
        )
        command_approval = provider.decoded_host_messages()[-1]
        assert command_approval["decisions"] == ["accept", "decline"]
        provider.inject(
            {
                "t": "decision",
                "seq": 2,
                "deviceApprovalId": command_approval["deviceApprovalId"],
                "decision": "accept",
            }
        )
        coordinator.handle_message(client.next_message())

        coordinator.handle_message(client.next_message())
        assert "kind=file" in console.render()
        file_id = coordinator.pending[0].device_approval_id
        assert coordinator.pending[0].content_complete is False
        assert console.execute(f"decline {file_id}") is True
        coordinator.handle_message(client.next_message())

        assert coordinator.pending == ()
        device_messages = provider.decoded_host_messages()
        assert sum(message["t"] == "approval_resolved" for message in device_messages) == 2
        file_messages = [
            message
            for message in device_messages
            if message["t"] == "approval" and message["kind"] == "file"
        ]
        assert file_messages[0]["decisions"] == ["decline"]
        assert file_messages[-1]["sending"] is True
        assert file_messages[-1]["decisions"] == []
    finally:
        session.close()
        shutdown = client.close()

    assert shutdown.exit_code == 0
    assert shutdown.forced is False


def _wait_until(predicate: object, *, timeout: float) -> bool:
    assert callable(predicate)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return bool(predicate())
