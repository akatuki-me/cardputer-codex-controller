from __future__ import annotations

import json

import pytest
from cardputer_codex_bridge.device_link import (
    BringupDecoder,
    BringupProtocolError,
    encode_bringup_message,
    fnv1a,
    maximum_echo_payload,
)


def test_fnv1a_matches_the_firmware_fixture() -> None:
    assert fnv1a("hello") == 0x4F9F2CAB


def test_maximum_echo_fills_exactly_one_4096_byte_line() -> None:
    payload = maximum_echo_payload(sequence=2, request_id=1)
    message = {"t": "echo", "seq": 2, "id": 1, "payload": payload}

    assert len(encode_bringup_message(message)) - 1 == 4096
    with pytest.raises(BringupProtocolError, match="4096"):
        encode_bringup_message({**message, "payload": payload + "x"})


def test_host_encoder_rejects_non_diagnostic_commands() -> None:
    with pytest.raises(BringupProtocolError, match="not supported"):
        encode_bringup_message({"t": "interrupt", "seq": 1})


def test_decoder_buffers_partial_lines_and_returns_multiple_messages() -> None:
    decoder = BringupDecoder()
    assert decoder.feed(b'{"t":"hello","seq":1,"proto":1,') == []

    messages = decoder.feed(
        b'"mode":"m1-bringup","firmware":"test","board":24,"adv":true,"heap":1}\n'
        b'{"t":"pong","seq":2,"id":7}\n'
    )

    assert [message["t"] for message in messages] == ["hello", "pong"]


def test_new_decoder_accepts_device_sequence_reset_after_reconnect() -> None:
    first = BringupDecoder()
    second = BringupDecoder()
    message = b'{"t":"pong","seq":1,"id":1}\n'

    assert first.feed(message) == [{"t": "pong", "seq": 1, "id": 1}]
    assert first.feed(message) == []
    assert second.feed(message) == [{"t": "pong", "seq": 1, "id": 1}]


def test_decoder_accepts_device_measured_stale_silence() -> None:
    message = b'{"t":"stale","seq":1,"silenceMs":5500}\n'

    assert BringupDecoder().feed(message) == [
        {"t": "stale", "seq": 1, "silenceMs": 5500}
    ]


def test_decoder_rejects_stale_without_device_elapsed_time() -> None:
    with pytest.raises(BringupProtocolError, match="silenceMs"):
        BringupDecoder().feed(b'{"t":"stale","seq":1}\n')


def test_invalid_ready_identity_is_rejected() -> None:
    payload = json.dumps(
        {
            "t": "ready",
            "seq": 1,
            "mode": "m1-bringup",
            "session": "session",
            "board": 24,
            "adv": "true",
        },
        separators=(",", ":"),
    ).encode()

    with pytest.raises(BringupProtocolError, match="adv"):
        BringupDecoder().feed(payload + b"\n")


def test_device_line_limit_and_invalid_utf8_are_rejected() -> None:
    with pytest.raises(BringupProtocolError, match="byte limit"):
        BringupDecoder(max_line_bytes=16).feed(b"x" * 17)
    with pytest.raises(BringupProtocolError, match="UTF-8"):
        BringupDecoder().feed(b"\xff\n")
