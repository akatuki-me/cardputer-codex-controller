from __future__ import annotations

import json
from typing import Any, cast

from cardputer_codex_bridge.app_server.types import JsonObject

MAX_HOST_TO_DEVICE_BYTES = 4096
MAX_DEVICE_TO_HOST_BYTES = 1024

_DEVICE_TO_HOST_TYPES = {
    "hello",
    "select",
    "decision",
    "interrupt",
    "quickReply",
    "effort",
    "pong",
    "log",
}
_HOST_TO_DEVICE_TYPES = {
    "hello",
    "state",
    "detail",
    "approval",
    "approval_resolved",
    "toast",
    "ping",
}


class DeviceLinkProtocolError(ValueError):
    """device-link v1の境界違反。"""


class DeviceLinkDecoder:
    """USB CDCから届くbytesを、単調増加seqのNDJSONへ復元する。"""

    def __init__(self, *, max_line_bytes: int = MAX_DEVICE_TO_HOST_BYTES) -> None:
        if max_line_bytes <= 0:
            raise ValueError("max_line_bytes must be positive")
        self._buffer = bytearray()
        self._last_seq = -1
        self._max_line_bytes = max_line_bytes

    @property
    def last_seq(self) -> int:
        return self._last_seq

    def feed(self, chunk: bytes) -> list[JsonObject]:
        self._buffer.extend(chunk)
        messages: list[JsonObject] = []
        while True:
            newline = self._buffer.find(b"\n")
            if newline < 0:
                if len(self._buffer) > self._max_line_bytes:
                    self._buffer.clear()
                    raise DeviceLinkProtocolError("device-link line exceeds byte limit")
                return messages
            raw = bytes(self._buffer[:newline])
            del self._buffer[: newline + 1]
            if raw.endswith(b"\r"):
                raw = raw[:-1]
            if not raw:
                continue
            if len(raw) > self._max_line_bytes:
                raise DeviceLinkProtocolError("device-link line exceeds byte limit")
            message = _decode_line(raw)
            seq = message["seq"]
            assert isinstance(seq, int) and not isinstance(seq, bool)
            if seq <= self._last_seq:
                continue
            self._last_seq = seq
            message_type = message["t"]
            assert isinstance(message_type, str)
            if message_type not in _DEVICE_TO_HOST_TYPES:
                continue
            _validate_known_message(message)
            messages.append(message)


def encode_message(message: JsonObject) -> bytes:
    _validate_message(message)
    message_type = message["t"]
    assert isinstance(message_type, str)
    if message_type not in _HOST_TO_DEVICE_TYPES:
        raise DeviceLinkProtocolError("host-to-device message t is not supported")
    _validate_known_message(message)
    if message_type == "hello":
        session = message.get("session")
        if not isinstance(session, str) or not session or len(session) > 64:
            raise DeviceLinkProtocolError("device-link host hello session is invalid")
    encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    payload = encoded.encode("utf-8")
    if len(payload) > MAX_HOST_TO_DEVICE_BYTES:
        raise DeviceLinkProtocolError("device-link line exceeds 4096 bytes")
    return payload + b"\n"


def _decode_line(raw: bytes) -> JsonObject:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise DeviceLinkProtocolError("device-link line is not valid UTF-8") from exc
    try:
        value: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DeviceLinkProtocolError("device-link line is not valid JSON") from exc
    if not isinstance(value, dict):
        raise DeviceLinkProtocolError("device-link message must be an object")
    message = cast(JsonObject, value)
    _validate_message(message)
    return message


def _validate_message(message: JsonObject) -> None:
    message_type = message.get("t")
    seq = message.get("seq")
    if not isinstance(message_type, str) or not message_type:
        raise DeviceLinkProtocolError("device-link message t must be a string")
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
        raise DeviceLinkProtocolError("device-link message seq must be a non-negative integer")


def _validate_known_message(message: JsonObject) -> None:
    if message["t"] == "hello" and message.get("proto") != 1:
        raise DeviceLinkProtocolError("device-link hello proto must be 1")
