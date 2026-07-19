from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass

from cardputer_codex_bridge.app_server.types import JsonObject, RequestId

from .contract import MAX_ANSWER_BYTES, UserInputContract
from .errors import UserInputError
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
    question: UserInputQuestion
    input_id: str


class HostUserInputCoordinator:
    """host-onlyの質問queueと短縮IDを管理する。"""

    def __init__(self, *, send_response: ResponseSender) -> None:
        self._contract = UserInputContract(send_response)
        self._bindings: list[_Binding] = []
        self._by_request_id: dict[RequestId, tuple[_Binding, ...]] = {}
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
                if pending.status is UserInputStatus.RESPONSE_SENT:
                    status = pending.status.value
                elif binding.question.question_id in pending.answered_question_ids:
                    status = "answer_staged"
                else:
                    status = pending.status.value
                result.append(
                    HostUserInputView(
                        input_id=binding.input_id,
                        thread_id=binding.request.thread_id,
                        status=status,
                        supported=True,
                        unsupported_reason=None,
                        question_count=len(binding.request.questions),
                        header=binding.question.header,
                        question=binding.question.question,
                        options=binding.question.options,
                        is_other=binding.question.is_other,
                        is_secret=binding.question.is_secret,
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

    def respond_host(
        self,
        input_id: str,
        answer: str,
        *,
        secret_surface: bool = False,
    ) -> bool:
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
            if binding.question.question_id in pending.answered_question_ids:
                return False
            try:
                self._contract.respond(
                    binding.request.request_id,
                    binding.question.question_id,
                    answer,
                    secret_surface=secret_surface,
                )
            except UserInputError:
                return False
            return True

    def accepts_secret(self, input_id: str) -> bool:
        with self._lock:
            binding = self._by_input_id.get(input_id)
            if binding is None or not binding.question.is_secret:
                return False
            pending = self._contract.get(binding.request.request_id)
            return (
                pending is not None
                and pending.status is UserInputStatus.AWAITING_ANSWER
                and binding.question.question_id
                not in pending.answered_question_ids
            )

    def discard_turn(self, thread_id: str, turn_id: str) -> bool:
        discarded = self._contract.discard_turn(thread_id, turn_id)
        if not discarded:
            return False
        with self._lock:
            for request in discarded:
                self._remove_request(request.request_id)
        return True

    def _register(self, request: UserInputRequest) -> None:
        with self._lock:
            if request.request_id in self._by_request_id:
                return
            bindings: list[_Binding] = []
            for question in request.questions:
                self._next_input_id += 1
                binding = _Binding(
                    request=request,
                    question=question,
                    input_id=f"question-{self._next_input_id:06d}",
                )
                bindings.append(binding)
                self._bindings.append(binding)
                self._by_input_id[binding.input_id] = binding
            self._by_request_id[request.request_id] = tuple(bindings)

    def _resolve(self, event: UserInputResolved) -> None:
        with self._lock:
            self._remove_request(event.request.request_id)

    def _remove_request(self, request_id: RequestId) -> None:
        bindings = self._by_request_id.pop(request_id, ())
        for binding in bindings:
            if binding in self._bindings:
                self._bindings.remove(binding)
            self._by_input_id.pop(binding.input_id, None)


__all__ = ["HostUserInputCoordinator", "HostUserInputView"]
