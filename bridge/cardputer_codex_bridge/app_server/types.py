from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]
type RequestId = int | str


class AppServerState(Enum):
    NEW = "new"
    RUNNING = "running"
    INITIALIZING = "initializing"
    READY = "ready"
    FAILED = "failed"
    CLOSING = "closing"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class ClientInfo:
    name: str
    version: str
    title: str | None = None

    def as_json(self) -> JsonObject:
        value: JsonObject = {"name": self.name, "version": self.version}
        if self.title is not None:
            value["title"] = self.title
        return value


@dataclass(frozen=True, slots=True)
class InitializeResult:
    codex_version: str
    platform_family: str
    platform_os: str
    user_agent: str


@dataclass(frozen=True, slots=True)
class ShutdownResult:
    exit_code: int | None
    forced: bool


@dataclass(frozen=True, slots=True)
class StderrSummary:
    total_lines: int
    nonempty_lines: int
    warning_lines: int
    error_lines: int
