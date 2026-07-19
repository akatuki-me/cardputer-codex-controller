from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from cardputer_codex_bridge.app_server.types import JsonObject
from cardputer_codex_bridge.device_link import DeviceLinkDecoder, encode_message

_FIXTURE_DIR = Path(__file__).parents[3] / "firmware" / "test" / "fixtures"


def _fixture_lines(name: str) -> list[bytes]:
    return [line for line in (_FIXTURE_DIR / name).read_bytes().splitlines() if line]


def test_host_to_device_fixture_matches_host_encoder_and_4096_byte_limit() -> None:
    for line in _fixture_lines("host_to_device.ndjson"):
        message = cast(JsonObject, json.loads(line))
        assert encode_message(message) == line + b"\n"
        assert len(line) <= 4096


def test_device_to_host_fixture_matches_host_decoder_and_1024_byte_limit() -> None:
    lines = _fixture_lines("device_to_host.ndjson")
    payload = b"\n".join(lines) + b"\n"
    decoder = DeviceLinkDecoder()

    messages = decoder.feed(payload[:17]) + decoder.feed(payload[17:])

    assert [message["t"] for message in messages] == [
        "hello",
        "pong",
        "interrupt",
        "decision",
    ]
    assert all(len(line) <= 1024 for line in lines)
