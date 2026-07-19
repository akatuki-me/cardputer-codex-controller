from __future__ import annotations

import copy
import threading
from collections.abc import Callable
from dataclasses import replace
from typing import cast

from cardputer_codex_bridge.app_server.types import JsonObject, JsonValue, RequestId

from .errors import (
    ApprovalDecisionError,
    ApprovalProtocolError,
    ApprovalRequestIdError,
    ApprovalStateError,
)
from .types import (
    AcceptWithExecpolicyAmendment,
    ApplyNetworkPolicyAmendment,
    ApprovalRequest,
    ApprovalResolved,
    ApprovalStatus,
    CommandApprovalDecision,
    CommandApprovalRequest,
    FileChangeApprovalDecision,
    FileChangeApprovalRequest,
    PendingApproval,
)

COMMAND_APPROVAL_METHOD = "item/commandExecution/requestApproval"
FILE_CHANGE_APPROVAL_METHOD = "item/fileChange/requestApproval"
SERVER_REQUEST_RESOLVED_METHOD = "serverRequest/resolved"

type ApprovalEvent = ApprovalRequest | ApprovalResolved
type ResponseSender = Callable[[RequestId, JsonObject], None]

_SIMPLE_DECISIONS = frozenset(
    {"accept", "acceptForSession", "decline", "cancel"}
)


class ApprovalContract:
    """Track app-server approval requests until explicit resolution."""

    def __init__(self, send_response: ResponseSender) -> None:
        self._send_response = send_response
        self._pending: dict[RequestId, PendingApproval] = {}
        self._lock = threading.Lock()

    @property
    def pending(self) -> tuple[PendingApproval, ...]:
        with self._lock:
            return tuple(self._pending.values())

    def get(self, request_id: RequestId) -> PendingApproval | None:
        with self._lock:
            return self._pending.get(request_id)

    def handle_message(self, message: JsonObject) -> ApprovalEvent | None:
        method = message.get("method")
        if method == COMMAND_APPROVAL_METHOD:
            command_request = _parse_command_request(message)
            self._register(command_request)
            return command_request
        if method == FILE_CHANGE_APPROVAL_METHOD:
            file_request = _parse_file_change_request(message)
            self._register(file_request)
            return file_request
        if method == SERVER_REQUEST_RESOLVED_METHOD:
            return self._resolve(message)
        return None

    def respond_command(
        self,
        request_id: RequestId,
        decision: CommandApprovalDecision,
    ) -> None:
        result: JsonObject = {"decision": _command_decision_json(decision)}
        self._respond(request_id, CommandApprovalRequest, result)

    def respond_file_change(
        self,
        request_id: RequestId,
        decision: FileChangeApprovalDecision,
    ) -> None:
        result: JsonObject = {"decision": _simple_decision(decision)}
        self._respond(request_id, FileChangeApprovalRequest, result)

    def _register(self, request: ApprovalRequest) -> None:
        with self._lock:
            if request.request_id in self._pending:
                raise ApprovalRequestIdError("approval request ID is already pending")
            self._pending[request.request_id] = PendingApproval(
                request=request,
                status=ApprovalStatus.AWAITING_DECISION,
            )

    def _respond(
        self,
        request_id: RequestId,
        expected_type: type[CommandApprovalRequest] | type[FileChangeApprovalRequest],
        result: JsonObject,
    ) -> None:
        _require_request_id(request_id)
        with self._lock:
            pending = self._pending.get(request_id)
            if pending is None:
                raise ApprovalRequestIdError("approval response ID is not pending")
            if not isinstance(pending.request, expected_type):
                raise ApprovalStateError("approval response kind does not match request kind")
            if pending.status is not ApprovalStatus.AWAITING_DECISION:
                raise ApprovalStateError("approval request already has a response")
            self._send_response(request_id, result)
            self._pending[request_id] = replace(
                pending,
                status=ApprovalStatus.RESPONSE_SENT,
                response_result=copy.deepcopy(result),
            )

    def _resolve(self, message: JsonObject) -> ApprovalResolved:
        params = _require_params(message)
        request_id = params.get("requestId")
        _require_request_id(request_id)
        checked_request_id = cast(RequestId, request_id)
        thread_id = _required_string(params, "threadId")
        with self._lock:
            pending = self._pending.get(checked_request_id)
            if pending is None:
                raise ApprovalRequestIdError("resolved request ID is not pending")
            if pending.request.thread_id != thread_id:
                raise ApprovalRequestIdError("resolved thread ID does not match request")
            del self._pending[checked_request_id]
            return ApprovalResolved(
                request=pending.request,
                response_result=copy.deepcopy(pending.response_result),
            )


def _parse_command_request(message: JsonObject) -> CommandApprovalRequest:
    request_id, params = _request_parts(message)
    return CommandApprovalRequest(
        request_id=request_id,
        thread_id=_required_string(params, "threadId"),
        turn_id=_required_string(params, "turnId"),
        item_id=_required_string(params, "itemId"),
        started_at_ms=_required_integer(params, "startedAtMs"),
        command=_optional_string(params, "command"),
        reason=_optional_string(params, "reason"),
        params=copy.deepcopy(params),
    )


def _parse_file_change_request(message: JsonObject) -> FileChangeApprovalRequest:
    request_id, params = _request_parts(message)
    return FileChangeApprovalRequest(
        request_id=request_id,
        thread_id=_required_string(params, "threadId"),
        turn_id=_required_string(params, "turnId"),
        item_id=_required_string(params, "itemId"),
        started_at_ms=_required_integer(params, "startedAtMs"),
        reason=_optional_string(params, "reason"),
        grant_root=_optional_string(params, "grantRoot"),
        params=copy.deepcopy(params),
    )


def _request_parts(message: JsonObject) -> tuple[RequestId, JsonObject]:
    request_id = message.get("id")
    _require_request_id(request_id)
    return cast(RequestId, request_id), _require_params(message)


def _require_params(message: JsonObject) -> JsonObject:
    params = message.get("params")
    if not isinstance(params, dict):
        raise ApprovalProtocolError("approval params must be an object")
    return params


def _require_request_id(value: JsonValue | RequestId) -> None:
    if not isinstance(value, (int, str)) or isinstance(value, bool):
        raise ApprovalProtocolError("approval request ID must be an integer or string")


def _required_string(params: JsonObject, name: str) -> str:
    value = params.get(name)
    if not isinstance(value, str):
        raise ApprovalProtocolError(f"approval {name} must be a string")
    return value


def _optional_string(params: JsonObject, name: str) -> str | None:
    value = params.get(name)
    if value is not None and not isinstance(value, str):
        raise ApprovalProtocolError(f"approval {name} must be a string or null")
    return value


def _required_integer(params: JsonObject, name: str) -> int:
    value = params.get(name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ApprovalProtocolError(f"approval {name} must be an integer")
    return value


def _simple_decision(decision: object) -> str:
    if not isinstance(decision, str) or decision not in _SIMPLE_DECISIONS:
        raise ApprovalDecisionError("unknown approval decision")
    return decision


def _command_decision_json(decision: object) -> JsonValue:
    if isinstance(decision, AcceptWithExecpolicyAmendment):
        if any(not isinstance(value, str) for value in decision.execpolicy_amendment):
            raise ApprovalDecisionError("execpolicy amendment must contain strings")
        return decision.as_json()
    if isinstance(decision, ApplyNetworkPolicyAmendment):
        if decision.action not in ("allow", "deny") or not isinstance(decision.host, str):
            raise ApprovalDecisionError("network policy amendment is invalid")
        return decision.as_json()
    return _simple_decision(decision)
