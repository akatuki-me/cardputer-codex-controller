from __future__ import annotations

import sys
from pathlib import Path

from cardputer_codex_bridge.app_server import AppServerClient, ClientInfo
from cardputer_codex_bridge.approval import (
    ApprovalContract,
    ApprovalResolved,
    ApprovalStatus,
    CommandApprovalRequest,
    FileChangeApprovalRequest,
)

FAKE_SERVER = Path(__file__).with_name("fake_approval_app_server.py")
CLIENT_INFO = ClientInfo(
    name="cardputer-codex-controller",
    title="Cardputer Codex Controller",
    version="0.0.0",
)


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
