from __future__ import annotations

from typing import Literal

from cardputer_codex_bridge.models import ThreadId, TurnId

from .types import RequestId

type AppServerResponseErrorKind = Literal[
    "invalid_state",
    "not_found",
    "permission_denied",
    "unclassified",
]


class AppServerError(RuntimeError):
    """Base error for the app-server transport."""


class AppServerStartError(AppServerError):
    """The app-server process could not be started."""


class AppServerShutdownError(AppServerError):
    """The app-server process could not be stopped within the shutdown contract."""


class AppServerStateError(AppServerError):
    """An operation was attempted in an invalid lifecycle state."""


class ActiveTurnRequiredError(AppServerStateError):
    """The requested turn is not the active turn for its thread."""

    def __init__(self, *, thread_id: ThreadId, turn_id: TurnId) -> None:
        self.thread_id = thread_id
        self.turn_id = turn_id
        super().__init__("operation requires the matching active turn")


class AppServerProtocolError(AppServerError):
    """The peer emitted a message that violates the JSONL contract."""


class AppServerClosedError(AppServerError):
    """The app-server process or stream closed before the operation completed."""


class AppServerTimeoutError(AppServerError):
    """The app-server did not complete an operation before its deadline."""


class AppServerVersionMismatch(AppServerProtocolError):
    def __init__(self, *, expected: str, actual: str) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"expected Codex {expected}, received {actual}")


class AppServerResponseError(AppServerError):
    def __init__(
        self,
        *,
        request_id: RequestId,
        code: int,
        kind: AppServerResponseErrorKind = "unclassified",
    ) -> None:
        self.request_id = request_id
        self.code = code
        self.kind = kind
        super().__init__(
            f"app-server request {request_id!r} failed with code {code} ({kind})"
        )
