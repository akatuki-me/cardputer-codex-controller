from __future__ import annotations

import json
from typing import Any, cast

from cardputer_codex_bridge.app_server.types import JsonObject

from .protocol import DeviceLinkProtocolError

BRINGUP_MODE = "m1-bringup"
MAX_BRINGUP_HOST_BYTES = 4096
MAX_BRINGUP_DEVICE_BYTES = 1024

_DEVICE_TYPES = {"hello", "ready", "echo", "pong", "heartbeat", "key", "g0", "error"}
_HOST_TYPES = {"hello", "echo", "ping"}


class BringupProtocolError(DeviceLinkProtocolError):
    """M1診断protocolの境界違反。"""


class BringupDecoder:
    def __init__(self, *, max_line_bytes: int = MAX_BRINGUP_DEVICE_BYTES) -> None:
        if max_line_bytes <= 0:
            raise ValueError("max_line_bytes must be positive")
        self._buffer = bytearray()
        self._last_sequence = -1
        self._max_line_bytes = max_line_bytes

    def feed(self, chunk: bytes) -> list[JsonObject]:
        self._buffer.extend(chunk)
        messages: list[JsonObject] = []
        while True:
            newline = self._buffer.find(b"\n")
            if newline < 0:
                if len(self._buffer) > self._max_line_bytes:
                    self._buffer.clear()
                    raise BringupProtocolError("bringup line exceeds byte limit")
                return messages
            raw = bytes(self._buffer[:newline])
            del self._buffer[: newline + 1]
            if raw.endswith(b"\r"):
                raw = raw[:-1]
            if not raw:
                continue
            if len(raw) > self._max_line_bytes:
                raise BringupProtocolError("bringup line exceeds byte limit")
            message = _decode_line(raw)
            sequence = _integer(message, "seq")
            if sequence <= self._last_sequence:
                continue
            self._last_sequence = sequence
            message_type = message["t"]
            assert isinstance(message_type, str)
            if message_type not in _DEVICE_TYPES:
                continue
            _validate_device_message(message)
            messages.append(message)


def encode_bringup_message(message: JsonObject) -> bytes:
    _validate_envelope(message)
    message_type = message["t"]
    assert isinstance(message_type, str)
    if message_type not in _HOST_TYPES:
        raise BringupProtocolError("host bringup message t is not supported")
    _validate_host_message(message)
    encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    payload = encoded.encode("utf-8")
    if len(payload) > MAX_BRINGUP_HOST_BYTES:
        raise BringupProtocolError("bringup line exceeds 4096 bytes")
    return payload + b"\n"


def maximum_echo_payload(*, sequence: int, request_id: int) -> str:
    empty: JsonObject = {"t": "echo", "seq": sequence, "id": request_id, "payload": ""}
    overhead = len(encode_bringup_message(empty)) - 1
    payload = "x" * (MAX_BRINGUP_HOST_BYTES - overhead)
    candidate: JsonObject = {
        "t": "echo",
        "seq": sequence,
        "id": request_id,
        "payload": payload,
    }
    if len(encode_bringup_message(candidate)) - 1 != MAX_BRINGUP_HOST_BYTES:
        raise AssertionError("maximum bringup echo did not reach the byte limit")
    return payload


def fnv1a(value: str) -> int:
    checksum = 2_166_136_261
    for byte in value.encode("utf-8"):
        checksum ^= byte
        checksum = (checksum * 16_777_619) & 0xFFFFFFFF
    return checksum


def _decode_line(raw: bytes) -> JsonObject:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise BringupProtocolError("bringup line is not valid UTF-8") from exc
    try:
        value: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BringupProtocolError("bringup line is not valid JSON") from exc
    if not isinstance(value, dict):
        raise BringupProtocolError("bringup message must be an object")
    message = cast(JsonObject, value)
    _validate_envelope(message)
    return message


def _validate_envelope(message: JsonObject) -> None:
    message_type = message.get("t")
    if not isinstance(message_type, str) or not message_type:
        raise BringupProtocolError("bringup t must be a string")
    _integer(message, "seq")


def _validate_host_message(message: JsonObject) -> None:
    message_type = message["t"]
    if message_type == "hello":
        session = message.get("session")
        if (
            message.get("proto") != 1
            or message.get("mode") != BRINGUP_MODE
            or not isinstance(session, str)
            or not 1 <= len(session) <= 32
        ):
            raise BringupProtocolError("bringup hello is invalid")
    elif message_type == "echo":
        _integer(message, "id")
        if not isinstance(message.get("payload"), str):
            raise BringupProtocolError("bringup echo payload must be a string")
    elif message_type == "ping":
        _integer(message, "id")


def _validate_device_message(message: JsonObject) -> None:
    message_type = message["t"]
    if message_type == "hello":
        if message.get("proto") != 1 or message.get("mode") != BRINGUP_MODE:
            raise BringupProtocolError("device bringup hello is invalid")
        firmware = message.get("firmware")
        if not isinstance(firmware, str) or not 1 <= len(firmware) <= 32:
            raise BringupProtocolError("device bringup firmware is invalid")
        _integer(message, "board")
        _integer(message, "heap")
        if not isinstance(message.get("adv"), bool):
            raise BringupProtocolError("device bringup adv must be boolean")
    elif message_type == "ready":
        if message.get("mode") != BRINGUP_MODE or not isinstance(message.get("session"), str):
            raise BringupProtocolError("device bringup ready is invalid")
        _integer(message, "board")
        if not isinstance(message.get("adv"), bool):
            raise BringupProtocolError("device bringup adv must be boolean")
    elif message_type == "echo":
        _integer(message, "id")
        _integer(message, "payloadBytes")
        _integer(message, "checksum")
    elif message_type == "pong":
        _integer(message, "id")
    elif message_type == "heartbeat":
        for field in ("uptimeMs", "heap", "rx", "tx", "errors"):
            _integer(message, field)
    elif message_type == "key":
        code = _integer(message, "code")
        if code > 255:
            raise BringupProtocolError("bringup key code is out of range")
    elif message_type == "g0":
        if message.get("action") not in {"press", "short", "long", "release"}:
            raise BringupProtocolError("bringup g0 action is invalid")
        _integer(message, "heldMs")
    elif message_type == "error" and not isinstance(message.get("kind"), str):
        raise BringupProtocolError("bringup error kind must be a string")


def _integer(message: JsonObject, field: str) -> int:
    value = message.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise BringupProtocolError(f"bringup {field} must be a non-negative integer")
    return value


__all__ = [
    "BRINGUP_MODE",
    "BringupDecoder",
    "BringupProtocolError",
    "encode_bringup_message",
    "fnv1a",
    "maximum_echo_payload",
]
