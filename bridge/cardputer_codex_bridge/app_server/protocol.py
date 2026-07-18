from __future__ import annotations

import json
from typing import cast

from .errors import AppServerProtocolError
from .types import JsonObject


def encode_message(message: JsonObject) -> str:
    try:
        return (
            json.dumps(
                message,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        )
    except (TypeError, ValueError) as exc:
        raise AppServerProtocolError("outbound message is not valid JSON") from exc


def decode_message(line: str) -> JsonObject:
    if not line.endswith("\n"):
        raise AppServerProtocolError("app-server stdout ended in a partial JSONL frame")
    try:
        value = json.loads(line, parse_constant=_reject_nonstandard_number)
    except (json.JSONDecodeError, ValueError) as exc:
        raise AppServerProtocolError("app-server emitted a non-JSON line") from exc
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise AppServerProtocolError("app-server message must be a JSON object")
    return cast(JsonObject, value)


def _reject_nonstandard_number(value: str) -> None:
    raise ValueError(f"non-standard JSON number: {value}")
