from __future__ import annotations

from typing import cast

import pytest
from cardputer_codex_bridge.app_server import JsonObject, JsonValue, RequestId
from cardputer_codex_bridge.approval.coordinator import ApprovalCoordinator
from cardputer_codex_bridge.approval.queue import DeviceApproval, device_decisions


def _command_message(
    request_id: RequestId,
    *,
    thread_id: str = "thread-1",
    command: str = "tool --check fixture.txt",
    available_decisions: object | None = None,
) -> JsonObject:
    params: JsonObject = {
        "threadId": thread_id,
        "turnId": "turn-1",
        "itemId": f"item-{request_id}",
        "startedAtMs": 1000,
        "command": command,
        "cwd": "workspace/fixture",
        "reason": "合成fixture",
    }
    if available_decisions is not None:
        params["availableDecisions"] = cast(JsonValue, available_decisions)
    return {
        "id": request_id,
        "method": "item/commandExecution/requestApproval",
        "params": params,
    }


def _file_message(request_id: RequestId) -> JsonObject:
    return {
        "id": request_id,
        "method": "item/fileChange/requestApproval",
        "params": {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "itemId": f"item-{request_id}",
            "startedAtMs": 1001,
            "reason": "相対pathの合成変更",
            "grantRoot": "workspace/fixture",
        },
    }


def _resolved(request_id: RequestId, thread_id: str = "thread-1") -> JsonObject:
    return {
        "method": "serverRequest/resolved",
        "params": {"requestId": request_id, "threadId": thread_id},
    }


class Recorder:
    def __init__(self) -> None:
        self.responses: list[tuple[RequestId, JsonObject]] = []
        self.approvals: list[tuple[DeviceApproval, int]] = []
        self.resolved: list[tuple[str, str | None]] = []

    def send_response(self, request_id: RequestId, result: JsonObject) -> None:
        self.responses.append((request_id, result))

    def send_approval(self, approval: DeviceApproval, pending_count: int) -> bool:
        self.approvals.append((approval, pending_count))
        return True

    def send_resolved(self, approval_id: str, decision: str | None) -> bool:
        self.resolved.append((approval_id, decision))
        return True


def _coordinator(
    recorder: Recorder,
    *,
    slots_by_thread: dict[str, int] | None = None,
    codex_version: str = "0.144.6",
) -> ApprovalCoordinator:
    return ApprovalCoordinator(
        send_response=recorder.send_response,
        send_device_approval=recorder.send_approval,
        send_device_resolved=recorder.send_resolved,
        slots_by_thread=slots_by_thread or {"thread-1": 1},
        codex_version=codex_version,
    )


def test_safe_current_decision_stays_pending_until_matching_resolution() -> None:
    recorder = Recorder()
    coordinator = _coordinator(recorder)

    coordinator.handle_message(_command_message("rpc-command-1"))

    approval, pending_count = recorder.approvals[-1]
    assert approval.approval_id == "approval-000001"
    assert approval.slot == 1
    assert approval.kind == "command"
    assert approval.content_complete is True
    assert approval.risk_class == "normal"
    assert device_decisions(approval) == ("accept", "decline")
    assert pending_count == 0
    assert "rpc-command-1" not in repr(approval)

    assert coordinator.handle_device_decision("approval-other", "accept") is False
    assert coordinator.handle_device_decision(approval.approval_id, "accept") is True
    assert recorder.responses == [("rpc-command-1", {"decision": "accept"})]
    assert coordinator.pending[0].status == "response_sent"
    assert recorder.approvals[-1][0].sending is True
    assert coordinator.handle_device_decision(approval.approval_id, "decline") is False

    coordinator.handle_message(_resolved("rpc-command-1"))

    assert recorder.resolved == [(approval.approval_id, "accept")]
    assert coordinator.pending == ()


def test_multiple_pending_update_count_without_replacing_current_and_auto_advance() -> None:
    recorder = Recorder()
    coordinator = _coordinator(recorder)

    coordinator.handle_message(_command_message("rpc-command-1"))
    first = recorder.approvals[-1][0]
    coordinator.handle_message(_command_message("rpc-command-2", command="tool --check two"))

    current, pending_count = recorder.approvals[-1]
    assert current.approval_id == first.approval_id
    assert pending_count == 1

    assert coordinator.handle_device_decision(first.approval_id, "decline") is True
    coordinator.handle_message(_resolved("rpc-command-1"))

    next_approval, next_count = recorder.approvals[-1]
    assert recorder.resolved == [(first.approval_id, "decline")]
    assert next_approval.approval_id == "approval-000002"
    assert next_count == 0


def test_high_risk_incomplete_and_server_decision_guards_are_conservative() -> None:
    recorder = Recorder()
    coordinator = _coordinator(recorder)

    coordinator.handle_message(_command_message("rpc-high", command="sudo tool --check"))
    high = recorder.approvals[-1][0]
    assert high.risk_class == "high"
    assert device_decisions(high) == ("decline",)
    assert coordinator.handle_device_decision(high.approval_id, "accept") is False
    assert coordinator.respond_host(high.approval_id, "accept") is True
    assert recorder.responses[-1] == ("rpc-high", {"decision": "accept"})

    coordinator.handle_message(_file_message("rpc-file"))
    file_approval = coordinator.pending[-1]
    assert file_approval.content_complete is False
    assert "accept" not in file_approval.host_decisions

    coordinator.handle_message(
        _command_message(
            "rpc-decline-only",
            command="tool --check three",
            available_decisions=["decline", "cancel"],
        )
    )
    decline_only = coordinator.pending[-1]
    assert decline_only.device_decisions == ("decline",)

    contextual = _command_message("rpc-context", command="tool --check context")
    contextual_params = contextual["params"]
    assert isinstance(contextual_params, dict)
    contextual_params["networkApprovalContext"] = {}
    coordinator.handle_message(contextual)
    assert coordinator.pending[-1].content_complete is False
    assert coordinator.pending[-1].device_decisions == ("decline",)


def test_semantic_context_requires_host_cancel_instead_of_hidden_approval() -> None:
    recorder = Recorder()
    coordinator = _coordinator(recorder)
    message = _command_message(
        "rpc-contextual",
        available_decisions=[
            "accept",
            {"acceptWithExecpolicyAmendment": {"execpolicy_amendment": ["fixture"]}},
            "cancel",
        ],
    )
    params = message["params"]
    assert isinstance(params, dict)
    params["proposedExecpolicyAmendment"] = {"command": ["fixture"]}

    coordinator.handle_message(message)

    assert recorder.approvals == []
    view = coordinator.pending[0]
    assert view.device_decisions == ()
    assert view.host_decisions == ("cancel",)
    assert coordinator.respond_host(view.device_approval_id, "accept") is False
    assert coordinator.respond_host(view.device_approval_id, "decline") is False
    assert coordinator.respond_host(view.device_approval_id, "cancel") is True
    assert recorder.responses == [("rpc-contextual", {"decision": "cancel"})]


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf fixture",
        "Remove-Item -LiteralPath fixture -Recurse",
        "git push --force-with-lease origin main",
        "sudo tool --check",
        "rmdir /s fixture",
        "del /s fixture",
    ],
)
def test_recursive_delete_force_push_and_privilege_patterns_are_high_risk(
    command: str,
) -> None:
    recorder = Recorder()
    coordinator = _coordinator(recorder)

    coordinator.handle_message(_command_message("rpc-high", command=command))

    assert coordinator.pending[0].risk_class == "high"
    assert coordinator.pending[0].device_decisions == ("decline",)


def test_unknown_thread_malformed_decisions_and_unknown_version_stay_host_only() -> None:
    recorder = Recorder()
    coordinator = _coordinator(recorder)

    coordinator.handle_message(
        _command_message("rpc-unknown-thread", thread_id="thread-unowned")
    )
    coordinator.handle_message(
        _command_message(
            "rpc-malformed",
            command="tool --check malformed",
            available_decisions={"unexpected": True},
        )
    )

    assert recorder.approvals == []
    assert [view.slot for view in coordinator.pending] == [None, None]
    assert all(view.device_decisions == () for view in coordinator.pending)

    version_recorder = Recorder()
    unknown_version = _coordinator(version_recorder, codex_version="0.145.0")
    unknown_version.handle_message(_command_message("rpc-unknown-version"))
    assert version_recorder.approvals == []
    assert unknown_version.pending[0].device_decisions == ()


def test_long_utf8_payload_is_bounded_and_republish_restores_current_state() -> None:
    recorder = Recorder()
    coordinator = _coordinator(recorder)
    long_command = "実行" * 300

    coordinator.handle_message(_command_message("rpc-long", command=long_command))

    approval = recorder.approvals[-1][0]
    assert approval.content_complete is False
    assert device_decisions(approval) == ("decline",)
    assert len(approval.lines) == 8
    assert all(len(line.encode("utf-8")) <= 60 for line in approval.lines)
    assert coordinator.republish() is True
    assert recorder.approvals[-1][0] == approval
