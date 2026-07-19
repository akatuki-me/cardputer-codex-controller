from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal

from cardputer_codex_bridge.app_server.types import JsonObject, RequestId

type SimpleApprovalDecision = Literal[
    "accept",
    "acceptForSession",
    "decline",
    "cancel",
]
type NetworkPolicyAction = Literal["allow", "deny"]


@dataclass(frozen=True, slots=True)
class AcceptWithExecpolicyAmendment:
    execpolicy_amendment: tuple[str, ...]

    def as_json(self) -> JsonObject:
        return {
            "acceptWithExecpolicyAmendment": {
                "execpolicy_amendment": list(self.execpolicy_amendment)
            }
        }


@dataclass(frozen=True, slots=True)
class ApplyNetworkPolicyAmendment:
    action: NetworkPolicyAction
    host: str

    def as_json(self) -> JsonObject:
        return {
            "applyNetworkPolicyAmendment": {
                "network_policy_amendment": {
                    "action": self.action,
                    "host": self.host,
                }
            }
        }


type CommandApprovalDecision = (
    SimpleApprovalDecision
    | AcceptWithExecpolicyAmendment
    | ApplyNetworkPolicyAmendment
)
type FileChangeApprovalDecision = SimpleApprovalDecision


@dataclass(frozen=True, slots=True)
class CommandApprovalRequest:
    request_id: RequestId
    thread_id: str
    turn_id: str
    item_id: str
    started_at_ms: int
    command: str | None
    reason: str | None
    params: JsonObject


@dataclass(frozen=True, slots=True)
class FileChangeApprovalRequest:
    request_id: RequestId
    thread_id: str
    turn_id: str
    item_id: str
    started_at_ms: int
    reason: str | None
    grant_root: str | None
    params: JsonObject


type ApprovalRequest = CommandApprovalRequest | FileChangeApprovalRequest


class ApprovalStatus(Enum):
    AWAITING_DECISION = "awaiting_decision"
    RESPONSE_SENT = "response_sent"


@dataclass(frozen=True, slots=True)
class PendingApproval:
    request: ApprovalRequest
    status: ApprovalStatus
    response_result: JsonObject | None = None


@dataclass(frozen=True, slots=True)
class ApprovalResolved:
    request: ApprovalRequest
    response_result: JsonObject | None
