from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import NewType

ThreadId = NewType("ThreadId", str)
TurnId = NewType("TurnId", str)


class TurnStatus(StrEnum):
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    FAILED = "failed"
    IN_PROGRESS = "inProgress"


@dataclass(frozen=True, slots=True)
class ThreadStartResult:
    thread_id: ThreadId
    model: str
    model_provider: str


@dataclass(frozen=True, slots=True)
class TurnStartResult:
    thread_id: ThreadId
    turn_id: TurnId
    status: TurnStatus


@dataclass(frozen=True, slots=True)
class TurnStartedEvent:
    thread_id: ThreadId
    turn_id: TurnId
    status: TurnStatus


@dataclass(frozen=True, slots=True)
class TurnCompletedEvent:
    thread_id: ThreadId
    turn_id: TurnId
    status: TurnStatus
    matched_active_turn: bool
    has_error: bool


@dataclass(frozen=True, slots=True)
class ModelInfo:
    id: str
    model: str
    display_name: str
    description: str
    is_default: bool
    hidden: bool
    default_reasoning_effort: str
    supported_reasoning_efforts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ModelPage:
    models: tuple[ModelInfo, ...]
    next_cursor: str | None

    def find(self, model: str) -> ModelInfo | None:
        return next((item for item in self.models if item.model == model), None)


__all__ = [
    "ModelInfo",
    "ModelPage",
    "ThreadId",
    "ThreadStartResult",
    "TurnCompletedEvent",
    "TurnId",
    "TurnStartResult",
    "TurnStartedEvent",
    "TurnStatus",
]
