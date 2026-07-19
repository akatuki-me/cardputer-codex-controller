from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import replace
from typing import cast

from cardputer_codex_bridge.app_server.types import JsonObject, JsonValue, RequestId

from .errors import (
    UserInputProtocolError,
    UserInputRequestIdError,
    UserInputStateError,
)
from .types import (
    PendingUserInput,
    UserInputEvent,
    UserInputOption,
    UserInputQuestion,
    UserInputRequest,
    UserInputResolved,
    UserInputStatus,
)

USER_INPUT_REQUEST_METHOD = "item/tool/requestUserInput"
SERVER_REQUEST_RESOLVED_METHOD = "serverRequest/resolved"
MAX_ANSWER_BYTES = 4096
MAX_REQUEST_ANSWER_BYTES = 16384
_MAX_UINT64 = (1 << 64) - 1

type ResponseSender = Callable[[RequestId, JsonObject], None]


class UserInputContract:
    """request_user_inputを明示応答またはserver解決まで保持する。"""

    def __init__(self, send_response: ResponseSender) -> None:
        self._send_response = send_response
        self._pending: dict[RequestId, PendingUserInput] = {}
        self._answers: dict[RequestId, dict[str, str]] = {}
        self._lock = threading.Lock()

    @property
    def pending(self) -> tuple[PendingUserInput, ...]:
        with self._lock:
            return tuple(self._pending.values())

    def get(self, request_id: RequestId) -> PendingUserInput | None:
        with self._lock:
            return self._pending.get(request_id)

    def owns(self, request_id: object) -> bool:
        if not _is_request_id(request_id):
            return False
        with self._lock:
            return cast(RequestId, request_id) in self._pending

    def handle_message(self, message: JsonObject) -> UserInputEvent | None:
        method = message.get("method")
        if method == USER_INPUT_REQUEST_METHOD:
            request = _parse_request(message)
            self._register(request)
            return request
        if method == SERVER_REQUEST_RESOLVED_METHOD:
            return self._resolve(message)
        return None

    def respond(
        self,
        request_id: RequestId,
        question_id: str,
        answer: str,
        *,
        secret_surface: bool = False,
    ) -> bool:
        _require_request_id(request_id)
        if not isinstance(question_id, str):
            raise UserInputProtocolError("question ID must be a string")
        if not isinstance(answer, str):
            raise UserInputProtocolError("answer must be a string")
        if not answer.strip():
            raise UserInputStateError("answer must not be empty")
        if len(answer.encode("utf-8")) > MAX_ANSWER_BYTES:
            raise UserInputStateError("answer exceeds the host limit")
        with self._lock:
            pending = self._pending.get(request_id)
            if pending is None:
                raise UserInputRequestIdError("user input response ID is not pending")
            if pending.status is not UserInputStatus.AWAITING_ANSWER:
                raise UserInputStateError("user input request already has a response")
            question = next(
                (
                    item
                    for item in pending.request.questions
                    if item.question_id == question_id
                ),
                None,
            )
            if question is None:
                raise UserInputStateError("question ID does not match request")
            if question.is_secret and not secret_surface:
                raise UserInputStateError("secret input requires a no-echo surface")
            if not question.is_secret and secret_surface:
                raise UserInputStateError("no-echo surface is only for secret input")
            _validate_choice(question, answer)
            staged = self._answers[request_id]
            if question_id in staged:
                raise UserInputStateError("question already has an answer")
            total_bytes = sum(
                len(value.encode("utf-8")) for value in staged.values()
            ) + len(answer.encode("utf-8"))
            if total_bytes > MAX_REQUEST_ANSWER_BYTES:
                raise UserInputStateError("answers exceed the request limit")
            candidate = {**staged, question_id: answer}
            answered_question_ids = frozenset(candidate)
            if len(candidate) < len(pending.request.questions):
                self._answers[request_id] = candidate
                self._pending[request_id] = replace(
                    pending,
                    answered_question_ids=answered_question_ids,
                )
                return False
            result: JsonObject = {
                "answers": {
                    item.question_id: {"answers": [candidate[item.question_id]]}
                    for item in pending.request.questions
                }
            }
            self._send_response(request_id, result)
            self._answers.pop(request_id, None)
            self._pending[request_id] = replace(
                pending,
                status=UserInputStatus.RESPONSE_SENT,
                answered_question_ids=answered_question_ids,
            )
            return True

    def discard_turn(
        self,
        thread_id: str,
        turn_id: str,
    ) -> tuple[UserInputRequest, ...]:
        with self._lock:
            discarded = tuple(
                pending.request
                for pending in self._pending.values()
                if pending.status is UserInputStatus.AWAITING_ANSWER
                and pending.request.thread_id == thread_id
                and pending.request.turn_id == turn_id
            )
            for request in discarded:
                pending = self._pending[request.request_id]
                self._pending[request.request_id] = replace(
                    pending,
                    status=UserInputStatus.DISCARDED,
                    answered_question_ids=frozenset(),
                )
                self._answers.pop(request.request_id, None)
            return discarded

    def _register(self, request: UserInputRequest) -> None:
        with self._lock:
            if request.request_id in self._pending:
                raise UserInputRequestIdError("user input request ID is already pending")
            self._pending[request.request_id] = PendingUserInput(
                request=request,
                status=UserInputStatus.AWAITING_ANSWER,
            )
            self._answers[request.request_id] = {}

    def _resolve(self, message: JsonObject) -> UserInputResolved:
        params = _require_params(message)
        request_id = params.get("requestId")
        _require_request_id(request_id)
        checked_request_id = cast(RequestId, request_id)
        thread_id = _required_string(params, "threadId")
        with self._lock:
            pending = self._pending.get(checked_request_id)
            if pending is None:
                raise UserInputRequestIdError("resolved user input ID is not pending")
            if pending.request.thread_id != thread_id:
                raise UserInputRequestIdError(
                    "resolved user input thread does not match request"
                )
            del self._pending[checked_request_id]
            self._answers.pop(checked_request_id, None)
            return UserInputResolved(
                request=pending.request,
                response_sent=pending.status is UserInputStatus.RESPONSE_SENT,
            )


def _parse_request(message: JsonObject) -> UserInputRequest:
    request_id = message.get("id")
    _require_request_id(request_id)
    params = _require_params(message)
    questions_value = params.get("questions")
    if not isinstance(questions_value, list):
        raise UserInputProtocolError("user input questions must be an array")
    questions = tuple(_parse_question(value) for value in questions_value)
    if not questions:
        raise UserInputProtocolError("user input questions must not be empty")
    question_ids = {question.question_id for question in questions}
    if len(question_ids) != len(questions):
        raise UserInputProtocolError("user input question IDs must be unique")
    auto_resolution_ms = params.get("autoResolutionMs")
    if auto_resolution_ms is not None and (
        not isinstance(auto_resolution_ms, int)
        or isinstance(auto_resolution_ms, bool)
        or auto_resolution_ms < 0
        or auto_resolution_ms > _MAX_UINT64
    ):
        raise UserInputProtocolError(
            "user input autoResolutionMs must be a non-negative integer or null"
        )
    return UserInputRequest(
        request_id=cast(RequestId, request_id),
        thread_id=_required_string(params, "threadId"),
        turn_id=_required_string(params, "turnId"),
        item_id=_required_string(params, "itemId"),
        questions=questions,
        auto_resolution_ms=auto_resolution_ms,
    )


def _parse_question(value: JsonValue) -> UserInputQuestion:
    if not isinstance(value, dict):
        raise UserInputProtocolError("user input question must be an object")
    options_value = value.get("options")
    if options_value is None:
        options: tuple[UserInputOption, ...] = ()
    elif isinstance(options_value, list):
        options = tuple(_parse_option(option) for option in options_value)
    else:
        raise UserInputProtocolError("user input options must be an array or null")
    return UserInputQuestion(
        question_id=_required_string(value, "id"),
        header=_required_string(value, "header"),
        question=_required_string(value, "question"),
        options=options,
        is_other=_optional_boolean(value, "isOther"),
        is_secret=_optional_boolean(value, "isSecret"),
    )


def _parse_option(value: JsonValue) -> UserInputOption:
    if not isinstance(value, dict):
        raise UserInputProtocolError("user input option must be an object")
    return UserInputOption(
        label=_required_string(value, "label"),
        description=_required_string(value, "description"),
    )


def _validate_choice(question: UserInputQuestion, answer: str) -> None:
    if not question.options or question.is_other:
        return
    if answer not in {option.label for option in question.options}:
        raise UserInputStateError("answer must match an available option")


def _require_params(message: JsonObject) -> JsonObject:
    params = message.get("params")
    if not isinstance(params, dict):
        raise UserInputProtocolError("user input params must be an object")
    return params


def _required_string(values: JsonObject, name: str) -> str:
    value = values.get(name)
    if not isinstance(value, str):
        raise UserInputProtocolError(f"user input {name} must be a string")
    return value


def _optional_boolean(values: JsonObject, name: str) -> bool:
    value = values.get(name, False)
    if not isinstance(value, bool):
        raise UserInputProtocolError(f"user input {name} must be a boolean")
    return value


def _is_request_id(value: object) -> bool:
    return isinstance(value, (int, str)) and not isinstance(value, bool)


def _require_request_id(value: JsonValue | RequestId) -> None:
    if not _is_request_id(value):
        raise UserInputProtocolError(
            "user input request ID must be an integer or string"
        )


__all__ = [
    "MAX_ANSWER_BYTES",
    "MAX_REQUEST_ANSWER_BYTES",
    "SERVER_REQUEST_RESOLVED_METHOD",
    "USER_INPUT_REQUEST_METHOD",
    "UserInputContract",
]
