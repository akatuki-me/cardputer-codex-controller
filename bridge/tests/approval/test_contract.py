from __future__ import annotations

from typing import Any, cast

import pytest
from cardputer_codex_bridge.app_server import JsonObject, RequestId
from cardputer_codex_bridge.approval import (
    AcceptWithExecpolicyAmendment,
    ApplyNetworkPolicyAmendment,
    ApprovalContract,
    ApprovalDecisionError,
    ApprovalProtocolError,
    ApprovalRequestIdError,
    ApprovalResolved,
    ApprovalStateError,
    ApprovalStatus,
    CommandApprovalRequest,
    FileChangeApprovalRequest,
)


def _command_message(request_id: RequestId = "command-1") -> JsonObject:
    return {
        "id": request_id,
        "method": "item/commandExecution/requestApproval",
        "params": {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "itemId": "item-1",
            "startedAtMs": 1000,
            "command": "tool --check fixture.txt",
            "reason": "合成fixture",
            "commandActions": [{"type": "unknown-fixture", "value": "kept"}],
        },
    }


def _file_message(request_id: RequestId = "file-1") -> JsonObject:
    return {
        "id": request_id,
        "method": "item/fileChange/requestApproval",
        "params": {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "itemId": "item-2",
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


def test_command_response_stays_pending_until_matching_resolved() -> None:
    sent: list[tuple[RequestId, JsonObject]] = []
    contract = ApprovalContract(lambda request_id, result: sent.append((request_id, result)))

    request = contract.handle_message(_command_message())

    assert isinstance(request, CommandApprovalRequest)
    assert request.params["commandActions"] == [
        {"type": "unknown-fixture", "value": "kept"}
    ]
    assert sent == []
    assert contract.get("command-1") is not None
    assert contract.get("command-1").status is ApprovalStatus.AWAITING_DECISION  # type: ignore[union-attr]

    contract.respond_command("command-1", "accept")

    assert sent == [("command-1", {"decision": "accept"})]
    pending = contract.get("command-1")
    assert pending is not None
    assert pending.status is ApprovalStatus.RESPONSE_SENT

    resolved = contract.handle_message(_resolved("command-1"))

    assert isinstance(resolved, ApprovalResolved)
    assert resolved.response_result == {"decision": "accept"}
    assert contract.get("command-1") is None


def test_file_change_request_has_distinct_type_and_response_method() -> None:
    sent: list[tuple[RequestId, JsonObject]] = []
    contract = ApprovalContract(lambda request_id, result: sent.append((request_id, result)))
    request = contract.handle_message(_file_message())

    assert isinstance(request, FileChangeApprovalRequest)
    with pytest.raises(ApprovalStateError):
        contract.respond_command("file-1", "decline")

    contract.respond_file_change("file-1", "decline")

    assert sent == [("file-1", {"decision": "decline"})]


@pytest.mark.parametrize(
    ("decision", "expected"),
    [
        (
            AcceptWithExecpolicyAmendment(("tool", "--check")),
            {
                "acceptWithExecpolicyAmendment": {
                    "execpolicy_amendment": ["tool", "--check"]
                }
            },
        ),
        (
            ApplyNetworkPolicyAmendment(action="allow", host="example.invalid"),
            {
                "applyNetworkPolicyAmendment": {
                    "network_policy_amendment": {
                        "action": "allow",
                        "host": "example.invalid",
                    }
                }
            },
        ),
    ],
)
def test_command_object_decision_payload_is_preserved(
    decision: object,
    expected: JsonObject,
) -> None:
    sent: list[tuple[RequestId, JsonObject]] = []
    contract = ApprovalContract(lambda request_id, result: sent.append((request_id, result)))
    contract.handle_message(_command_message())

    contract.respond_command("command-1", decision)  # type: ignore[arg-type]

    assert sent == [("command-1", {"decision": expected})]


def test_unknown_decision_request_id_and_double_response_are_rejected() -> None:
    sent: list[tuple[RequestId, JsonObject]] = []
    contract = ApprovalContract(lambda request_id, result: sent.append((request_id, result)))
    contract.handle_message(_command_message())

    with pytest.raises(ApprovalRequestIdError):
        contract.respond_command("other", "accept")
    with pytest.raises(ApprovalDecisionError):
        contract.respond_command("command-1", cast(Any, "approve"))

    contract.respond_command("command-1", "cancel")
    with pytest.raises(ApprovalStateError):
        contract.respond_command("command-1", "decline")

    assert sent == [("command-1", {"decision": "cancel"})]


def test_resolution_requires_matching_request_and_thread_ids() -> None:
    contract = ApprovalContract(lambda _request_id, _result: None)
    contract.handle_message(_command_message())

    with pytest.raises(ApprovalRequestIdError):
        contract.handle_message(_resolved("other"))
    with pytest.raises(ApprovalRequestIdError):
        contract.handle_message(_resolved("command-1", "thread-other"))

    assert contract.get("command-1") is not None


def test_malformed_or_duplicate_request_is_rejected_without_auto_response() -> None:
    sent: list[tuple[RequestId, JsonObject]] = []
    contract = ApprovalContract(lambda request_id, result: sent.append((request_id, result)))
    malformed = _command_message()
    malformed["params"] = {"threadId": "thread-1"}

    with pytest.raises(ApprovalProtocolError):
        contract.handle_message(malformed)

    contract.handle_message(_command_message())
    with pytest.raises(ApprovalRequestIdError):
        contract.handle_message(_file_message("command-1"))

    assert sent == []


def test_unknown_message_is_left_for_another_consumer() -> None:
    sent: list[tuple[RequestId, JsonObject]] = []
    contract = ApprovalContract(lambda request_id, result: sent.append((request_id, result)))

    assert contract.handle_message({"method": "turn/completed", "params": {}}) is None
    assert contract.pending == ()
    assert sent == []
