from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from cardputer_codex_bridge.app_server.types import RequestId


@dataclass(frozen=True, slots=True)
class UserInputOption:
    label: str
    description: str


@dataclass(frozen=True, slots=True)
class UserInputQuestion:
    question_id: str
    header: str
    question: str
    options: tuple[UserInputOption, ...]
    is_other: bool
    is_secret: bool


@dataclass(frozen=True, slots=True)
class UserInputRequest:
    request_id: RequestId
    thread_id: str
    turn_id: str
    item_id: str
    questions: tuple[UserInputQuestion, ...]
    auto_resolution_ms: int | None


class UserInputStatus(Enum):
    AWAITING_ANSWER = "awaiting_answer"
    RESPONSE_SENT = "response_sent"


@dataclass(frozen=True, slots=True)
class PendingUserInput:
    request: UserInputRequest
    status: UserInputStatus


@dataclass(frozen=True, slots=True)
class UserInputResolved:
    request: UserInputRequest
    response_sent: bool


type UserInputEvent = UserInputRequest | UserInputResolved


__all__ = [
    "PendingUserInput",
    "UserInputEvent",
    "UserInputOption",
    "UserInputQuestion",
    "UserInputRequest",
    "UserInputResolved",
    "UserInputStatus",
]
