from __future__ import annotations

import pytest
from cardputer_codex_bridge.app_server import JsonObject, RequestId
from cardputer_codex_bridge.approval import ApprovalCoordinator, HostApprovalConsole


def _command_message(
    request_id: RequestId,
    command: str,
    *,
    available_decisions: list[object] | None = None,
    proposed_amendment: object | None = None,
) -> JsonObject:
    params: JsonObject = {
        "threadId": "thread-1",
        "turnId": "turn-1",
        "itemId": "item-1",
        "startedAtMs": 1000,
        "command": command,
        "cwd": "workspace/fixture",
    }
    if available_decisions is not None:
        params["availableDecisions"] = available_decisions
    if proposed_amendment is not None:
        params["proposedExecpolicyAmendment"] = proposed_amendment
    return {
        "id": request_id,
        "method": "item/commandExecution/requestApproval",
        "params": params,
    }


def test_console_renders_full_host_details_without_rpc_id_and_responds() -> None:
    responses: list[tuple[RequestId, JsonObject]] = []
    coordinator = ApprovalCoordinator(
        send_response=lambda request_id, result: responses.append((request_id, result)),
        send_device_approval=lambda _approval, _count: True,
        send_device_resolved=lambda _approval_id, _decision: True,
        slots_by_thread={"thread-1": 1},
        codex_version="0.144.6",
    )
    console = HostApprovalConsole(coordinator)
    full_command = "tool --check fixture.txt --explain-everything"
    coordinator.handle_message(_command_message("private-rpc-id", full_command))

    rendered = console.render()

    assert "approval-000001" in rendered
    assert "slot=1" in rendered
    assert "risk=normal" in rendered
    assert "complete=true" in rendered
    assert full_command in rendered
    assert "workspace/fixture" in rendered
    assert "private-rpc-id" not in rendered
    assert console.execute("approve approval-000001") is True
    assert responses == [("private-rpc-id", {"decision": "accept"})]
    assert console.execute("decline approval-000001") is False


def test_console_exposes_only_exact_safe_host_actions() -> None:
    responses: list[tuple[RequestId, JsonObject]] = []
    coordinator = ApprovalCoordinator(
        send_response=lambda request_id, result: responses.append((request_id, result)),
        send_device_approval=lambda _approval, _count: True,
        send_device_resolved=lambda _approval_id, _decision: True,
        slots_by_thread={"thread-1": 1},
        codex_version="0.144.6",
    )
    console = HostApprovalConsole(coordinator)
    coordinator.handle_message(
        _command_message(
            "rpc-contextual",
            "tool --check fixture.txt",
            available_decisions=["accept", {"amendment": {}}, "cancel"],
            proposed_amendment={"command": ["fixture"]},
        )
    )

    rendered = console.render()
    assert "actions: cancel" in rendered
    assert "actions: approve" not in rendered
    assert console.execute("approve approval-000001") is False
    assert console.execute("decline approval-000001") is False
    assert console.execute("cancel approval-000001") is True
    assert responses == [("rpc-contextual", {"decision": "cancel"})]


def test_console_allows_explicit_host_approval_for_high_risk_request() -> None:
    responses: list[tuple[RequestId, JsonObject]] = []
    coordinator = ApprovalCoordinator(
        send_response=lambda request_id, result: responses.append((request_id, result)),
        send_device_approval=lambda _approval, _count: True,
        send_device_resolved=lambda _approval_id, _decision: True,
        slots_by_thread={"thread-1": 1},
        codex_version="0.144.6",
    )
    console = HostApprovalConsole(coordinator)
    coordinator.handle_message(_command_message("rpc-high", "sudo tool --check"))

    assert "risk=high" in console.render()
    assert console.execute("approve approval-000001") is True
    assert responses == [("rpc-high", {"decision": "accept"})]


@pytest.mark.parametrize(
    "command",
    ["", "approve", "cancel", "accept approval-000001", "approve one extra"],
)
def test_console_rejects_unknown_or_ambiguous_commands(command: str) -> None:
    coordinator = ApprovalCoordinator(
        send_response=lambda _request_id, _result: None,
        send_device_approval=lambda _approval, _count: True,
        send_device_resolved=lambda _approval_id, _decision: True,
        slots_by_thread={"thread-1": 1},
        codex_version="0.144.6",
    )
    console = HostApprovalConsole(coordinator)

    with pytest.raises(ValueError):
        console.execute(command)
