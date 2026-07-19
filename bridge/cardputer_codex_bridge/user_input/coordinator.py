from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass

from cardputer_codex_bridge.app_server.types import JsonObject, RequestId

from .contract import MAX_ANSWER_BYTES, UserInputContract
from .types import (
    UserInputEvent,
    UserInputOption,
    UserInputQuestion,
    UserInputRequest,
    UserInputResolved,
    UserInputStatus,
)

type ResponseSender = Callable[[RequestId, JsonObject], None]


@dataclass(frozen=True, slots=True)
class HostUserInputView:
    input_id: str
    thread_id: str
    status: str
    supported: bool
    unsupported_reason: str | None
    question_count: int
    header: str
    question: str
    options: tuple[UserInputOption, ...]
    is_other: bool
    is_secret: bool


@dataclass(frozen=True, slots=True)
class _Binding:
    request: UserInputRequest
    input_id: str


class HostUserInputCoordinator:
    """host-onlyの質問queueと短縮IDを管理する。"""

    def __init__(self, *, send_response: ResponseSender) -> None:
        self._contract = UserInputContract(send_response)
        self._bindings: list[_Binding] = []
        self._by_request_id: dict[RequestId, _Binding] = {}
        self._by_input_id: dict[str, _Binding] = {}
        self._next_input_id = 0
        self._lock = threading.RLock()

    @property
    def pending(self) -> tuple[HostUserInputView, ...]:
        with self._lock:
            result: list[HostUserInputView] = []
            for binding in self._bindings:
                pending = self._contract.get(binding.request.request_id)
                if pending is None:
                    continue
                questions = binding.request.questions
                supported, reason = _support_state(questions)
                if len(questions) == 1:
                    question = questions[0]
                    header = question.header
                    prompt = question.question
                    options = question.options
                    is_other = question.is_other
                    is_secret = question.is_secret
                else:
                    header = ""
                    prompt = ""
                    options = ()
                    is_other = False
                    is_secret = any(item.is_secret for item in questions)
                result.append(
                    HostUserInputView(
                        input_id=binding.input_id,
                        thread_id=binding.request.thread_id,
                        status=pending.status.value,
                        supported=supported,
                        unsupported_reason=reason,
                        question_count=len(questions),
                        header=header,
                        question=prompt,
                        options=options,
                        is_other=is_other,
                        is_secret=is_secret,
                    )
                )
            return tuple(result)

    def owns(self, request_id: object) -> bool:
        return self._contract.owns(request_id)

    def handle_message(self, message: JsonObject) -> UserInputEvent | None:
        event = self._contract.handle_message(message)
        if isinstance(event, UserInputRequest):
            self._register(event)
        elif isinstance(event, UserInputResolved):
            self._resolve(event)
        return event

    def respond_host(self, input_id: str, answer: str) -> bool:
        if not isinstance(answer, str) or not answer.strip():
            return False
        if len(answer.encode("utf-8")) > MAX_ANSWER_BYTES:
            return False
        with self._lock:
            binding = self._by_input_id.get(input_id)
            if binding is None:
                return False
            pending = self._contract.get(binding.request.request_id)
            if pending is None or pending.status is not UserInputStatus.AWAITING_ANSWER:
                return False
            supported, _reason = _support_state(binding.request.questions)
            if not supported:
                return False
            question = binding.request.questions[0]
            self._contract.respond(
                binding.request.request_id,
                question.question_id,
                answer,
            )
            return True

    def _register(self, request: UserInputRequest) -> None:
        with self._lock:
            if request.request_id in self._by_request_id:
                return
            self._next_input_id += 1
            binding = _Binding(
                request=request,
                input_id=f"question-{self._next_input_id:06d}",
            )
            self._bindings.append(binding)
            self._by_request_id[request.request_id] = binding
            self._by_input_id[binding.input_id] = binding

    def _resolve(self, event: UserInputResolved) -> None:
        with self._lock:
            binding = self._by_request_id.pop(event.request.request_id, None)
            if binding is None:
                return
            self._bindings.remove(binding)
            self._by_input_id.pop(binding.input_id, None)


def _support_state(
    questions: tuple[UserInputQuestion, ...],
) -> tuple[bool, str | None]:
    if len(questions) != 1:
        return False, "multiple_questions"
    question = questions[0]
    if question.is_secret:
        return False, "secret_input"
    return True, None


__all__ = ["HostUserInputCoordinator", "HostUserInputView"]
