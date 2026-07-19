from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from cardputer_codex_bridge.models import (
    ModelInfo,
    ModelPage,
    ThreadId,
    ThreadStartResult,
    TurnCompletedEvent,
    TurnId,
    TurnStartedEvent,
    TurnStartResult,
    TurnStatus,
)

from .client import AppServerClient
from .errors import ActiveTurnRequiredError, AppServerProtocolError, AppServerStateError
from .types import JsonObject, JsonValue

type ApprovalPolicy = Literal["untrusted", "on-request", "never"]
type SandboxMode = Literal["read-only", "workspace-write", "danger-full-access"]

_COMPLETED_TOMBSTONE_LIMIT = 256


@dataclass(frozen=True, slots=True)
class ThreadStartOptions:
    cwd: Path | None = None
    model: str | None = None
    approval_policy: ApprovalPolicy | None = None
    sandbox: SandboxMode | None = None
    ephemeral: bool | None = None

    def as_params(self) -> JsonObject:
        params: JsonObject = {}
        if self.cwd is not None:
            if not self.cwd.is_absolute():
                raise ValueError("thread cwd must be absolute")
            params["cwd"] = str(self.cwd)
        if self.model is not None:
            params["model"] = _require_nonempty_string(self.model, "thread model")
        if self.approval_policy is not None:
            params["approvalPolicy"] = self.approval_policy
        if self.sandbox is not None:
            params["sandbox"] = self.sandbox
        if self.ephemeral is not None:
            params["ephemeral"] = self.ephemeral
        return params


class AppServerOperations:
    """Typed M0 operations layered on a ready :class:`AppServerClient`."""

    def __init__(self, client: AppServerClient) -> None:
        self._client = client
        self._active_turns: dict[ThreadId, TurnId] = {}
        self._starting_threads: set[ThreadId] = set()
        self._completed_turns: set[tuple[ThreadId, TurnId]] = set()
        self._completed_turn_order: deque[tuple[ThreadId, TurnId]] = deque()
        self._turn_lock = threading.RLock()

    def start_thread(
        self,
        options: ThreadStartOptions | None = None,
        *,
        timeout: float | None = None,
    ) -> ThreadStartResult:
        params = (options or ThreadStartOptions()).as_params()
        result = self._client.request("thread/start", params, timeout=timeout)
        value = _require_object(result, "thread/start result")
        thread = _require_object(value.get("thread"), "thread/start thread")
        return ThreadStartResult(
            thread_id=ThreadId(_required_id(thread.get("id"), "thread/start thread id")),
            model=_required_id(value.get("model"), "thread/start model"),
            model_provider=_required_id(
                value.get("modelProvider"),
                "thread/start model provider",
            ),
        )

    def start_turn(
        self,
        thread_id: ThreadId,
        text: str,
        *,
        model: str | None = None,
        effort: str | None = None,
        timeout: float | None = None,
    ) -> TurnStartResult:
        params: JsonObject = {
            "threadId": _required_id(thread_id, "thread id"),
            "input": [_text_input(text)],
        }
        if model is not None:
            params["model"] = _require_nonempty_string(model, "turn model")
        if effort is not None:
            params["effort"] = _require_nonempty_string(effort, "reasoning effort")

        with self._turn_lock:
            if thread_id in self._starting_threads:
                raise AppServerStateError("turn/start is already pending for this thread")
            if thread_id in self._active_turns:
                raise AppServerStateError(
                    "turn/start requires no active turn for this thread"
                )
            self._starting_threads.add(thread_id)
        try:
            result = self._client.request("turn/start", params, timeout=timeout)
            turn = _parse_turn_result(result, "turn/start")
        except Exception:
            with self._turn_lock:
                self._starting_threads.discard(thread_id)
            raise
        with self._turn_lock:
            self._starting_threads.discard(thread_id)
            completed_early = (thread_id, turn.turn_id) in self._completed_turns
            if turn.status is TurnStatus.IN_PROGRESS and not completed_early:
                self._active_turns[thread_id] = turn.turn_id
            else:
                self._active_turns.pop(thread_id, None)
            return TurnStartResult(
                thread_id=thread_id,
                turn_id=turn.turn_id,
                status=turn.status,
            )

    def steer_turn(
        self,
        thread_id: ThreadId,
        expected_turn_id: TurnId,
        text: str,
        *,
        timeout: float | None = None,
    ) -> TurnId:
        with self._turn_lock:
            self._require_active_turn(thread_id, expected_turn_id)
            result = self._client.request(
                "turn/steer",
                {
                    "threadId": _required_id(thread_id, "thread id"),
                    "expectedTurnId": _required_id(expected_turn_id, "expected turn id"),
                    "input": [_text_input(text)],
                },
                timeout=timeout,
            )
            value = _require_object(result, "turn/steer result")
            actual_turn_id = TurnId(
                _required_id(value.get("turnId"), "turn/steer turn id")
            )
            if actual_turn_id != expected_turn_id:
                raise AppServerProtocolError(
                    "turn/steer response does not match the expected active turn"
                )
            return actual_turn_id

    def interrupt_turn(
        self,
        thread_id: ThreadId,
        turn_id: TurnId,
        *,
        timeout: float | None = None,
    ) -> None:
        with self._turn_lock:
            self._require_active_turn(thread_id, turn_id)
            result = self._client.request(
                "turn/interrupt",
                {
                    "threadId": _required_id(thread_id, "thread id"),
                    "turnId": _required_id(turn_id, "turn id"),
                },
                timeout=timeout,
            )
            _require_object(result, "turn/interrupt result")

    def list_models(
        self,
        *,
        cursor: str | None = None,
        limit: int | None = None,
        include_hidden: bool | None = None,
        timeout: float | None = None,
    ) -> ModelPage:
        params: JsonObject = {}
        if cursor is not None:
            params["cursor"] = _require_nonempty_string(cursor, "model cursor")
        if limit is not None:
            if isinstance(limit, bool) or limit <= 0:
                raise ValueError("model limit must be a positive integer")
            params["limit"] = limit
        if include_hidden is not None:
            params["includeHidden"] = include_hidden
        result = self._client.request("model/list", params, timeout=timeout)
        return _parse_model_page(result)

    def active_turn(self, thread_id: ThreadId) -> TurnId | None:
        with self._turn_lock:
            return self._active_turns.get(thread_id)

    def handle_notification(
        self,
        message: JsonObject,
    ) -> TurnStartedEvent | TurnCompletedEvent | None:
        method = message.get("method")
        if method == "turn/started":
            params = _require_object(message.get("params"), "turn/started params")
            thread_id = ThreadId(
                _required_id(params.get("threadId"), "turn/started thread id")
            )
            turn = _parse_turn_object(params.get("turn"), "turn/started turn")
            if turn.status is not TurnStatus.IN_PROGRESS:
                raise AppServerProtocolError("turn/started does not contain an active turn")
            with self._turn_lock:
                key = (thread_id, turn.turn_id)
                current = self._active_turns.get(thread_id)
                can_activate = (
                    thread_id in self._starting_threads
                    or current is None
                    or current == turn.turn_id
                )
                if not can_activate or key in self._completed_turns:
                    return None
                self._active_turns[thread_id] = turn.turn_id
            return TurnStartedEvent(
                thread_id=thread_id,
                turn_id=turn.turn_id,
                status=turn.status,
            )
        if method != "turn/completed":
            return None
        params = _require_object(message.get("params"), "turn/completed params")
        thread_id = ThreadId(
            _required_id(params.get("threadId"), "turn/completed thread id")
        )
        turn = _parse_turn_object(params.get("turn"), "turn/completed turn")
        if turn.status is TurnStatus.IN_PROGRESS:
            raise AppServerProtocolError("turn/completed contains an active turn")
        turn_value = _require_object(params.get("turn"), "turn/completed turn")
        with self._turn_lock:
            self._remember_completed((thread_id, turn.turn_id))
            matched = self._active_turns.get(thread_id) == turn.turn_id
            if matched:
                self._active_turns.pop(thread_id, None)
        return TurnCompletedEvent(
            thread_id=thread_id,
            turn_id=turn.turn_id,
            status=turn.status,
            matched_active_turn=matched,
            has_error=turn_value.get("error") is not None,
        )

    def _require_active_turn(self, thread_id: ThreadId, turn_id: TurnId) -> None:
        if self._active_turns.get(thread_id) != turn_id:
            raise ActiveTurnRequiredError(thread_id=thread_id, turn_id=turn_id)

    def _remember_completed(self, key: tuple[ThreadId, TurnId]) -> None:
        if key in self._completed_turns:
            return
        if len(self._completed_turn_order) >= _COMPLETED_TOMBSTONE_LIMIT:
            oldest = self._completed_turn_order.popleft()
            self._completed_turns.remove(oldest)
        self._completed_turn_order.append(key)
        self._completed_turns.add(key)


@dataclass(frozen=True, slots=True)
class _ParsedTurn:
    turn_id: TurnId
    status: TurnStatus


def _parse_turn_result(value: JsonValue, method: str) -> _ParsedTurn:
    result = _require_object(value, f"{method} result")
    return _parse_turn_object(result.get("turn"), f"{method} turn")


def _parse_turn_object(value: JsonValue, label: str) -> _ParsedTurn:
    turn = _require_object(value, label)
    turn_id = TurnId(_required_id(turn.get("id"), f"{label} id"))
    raw_status = _required_id(turn.get("status"), f"{label} status")
    try:
        status = TurnStatus(raw_status)
    except ValueError as exc:
        raise AppServerProtocolError(f"{label} has an unknown status") from exc
    return _ParsedTurn(turn_id=turn_id, status=status)


def _parse_model_page(value: JsonValue) -> ModelPage:
    result = _require_object(value, "model/list result")
    data = result.get("data")
    if not isinstance(data, list):
        raise AppServerProtocolError("model/list data must be an array")
    models = tuple(_parse_model(item) for item in data)
    next_cursor = result.get("nextCursor")
    if next_cursor is not None and not isinstance(next_cursor, str):
        raise AppServerProtocolError("model/list nextCursor must be a string or null")
    return ModelPage(models=models, next_cursor=next_cursor)


def _parse_model(value: JsonValue) -> ModelInfo:
    model = _require_object(value, "model/list model")
    efforts = model.get("supportedReasoningEfforts")
    if not isinstance(efforts, list):
        raise AppServerProtocolError("model reasoning efforts must be an array")
    parsed_efforts: list[str] = []
    for value in efforts:
        effort = _require_object(value, "model reasoning effort")
        parsed_efforts.append(
            _required_id(effort.get("reasoningEffort"), "model reasoning effort value")
        )
    is_default = model.get("isDefault")
    hidden = model.get("hidden")
    if not isinstance(is_default, bool) or not isinstance(hidden, bool):
        raise AppServerProtocolError("model visibility flags must be booleans")
    return ModelInfo(
        id=_required_id(model.get("id"), "model id"),
        model=_required_id(model.get("model"), "model name"),
        display_name=_required_id(model.get("displayName"), "model display name"),
        description=_required_id(model.get("description"), "model description"),
        is_default=is_default,
        hidden=hidden,
        default_reasoning_effort=_required_id(
            model.get("defaultReasoningEffort"),
            "model default reasoning effort",
        ),
        supported_reasoning_efforts=tuple(parsed_efforts),
    )


def _text_input(text: str) -> JsonObject:
    return {
        "type": "text",
        "text": _require_nonempty_string(text, "turn text"),
        "text_elements": [],
    }


def _require_object(value: JsonValue, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise AppServerProtocolError(f"{label} must be an object")
    return value


def _required_id(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise AppServerProtocolError(f"{label} must be a non-empty string")
    return value


def _require_nonempty_string(value: str, label: str) -> str:
    if not value:
        raise ValueError(f"{label} must be non-empty")
    return value


__all__ = [
    "AppServerOperations",
    "ApprovalPolicy",
    "SandboxMode",
    "ThreadStartOptions",
]
