from __future__ import annotations

import pytest
from cardputer_codex_bridge.device_link import (
    DeviceLinkDecoder,
    DeviceLinkProtocolError,
    encode_message,
)


def test_partial_line_is_buffered_until_lf() -> None:
    decoder = DeviceLinkDecoder()
    assert decoder.feed(b'{"t":"hello","seq":') == []
    assert decoder.feed(b'1,"proto":1}\n') == [{"t": "hello", "seq": 1, "proto": 1}]


def test_multiple_lines_are_returned_in_order() -> None:
    decoder = DeviceLinkDecoder()
    messages = decoder.feed(b'{"t":"hello","seq":1,"proto":1}\n{"t":"pong","seq":2}\n')
    assert messages == [
        {"t": "hello", "seq": 1, "proto": 1},
        {"t": "pong", "seq": 2},
    ]


def test_invalid_utf8_is_rejected() -> None:
    with pytest.raises(DeviceLinkProtocolError, match="UTF-8"):
        DeviceLinkDecoder().feed(b'\xff\n')


def test_invalid_json_is_rejected() -> None:
    with pytest.raises(DeviceLinkProtocolError, match="valid JSON"):
        DeviceLinkDecoder().feed(b'{nope}\n')


def test_device_to_host_oversize_is_rejected_before_lf() -> None:
    decoder = DeviceLinkDecoder(max_line_bytes=32)
    with pytest.raises(DeviceLinkProtocolError, match="byte limit"):
        decoder.feed(b"x" * 33)


def test_unknown_type_is_ignored_and_advances_sequence() -> None:
    decoder = DeviceLinkDecoder()
    assert decoder.feed(b'{"t":"future-message","seq":2}\n') == []
    assert decoder.feed(b'{"t":"pong","seq":1}\n') == []
    assert decoder.feed(b'{"t":"pong","seq":3}\n') == [{"t": "pong", "seq": 3}]
    assert decoder.last_seq == 3


def test_old_and_duplicate_sequence_are_ignored() -> None:
    decoder = DeviceLinkDecoder()
    assert decoder.feed(b'{"t":"pong","seq":4}\n') == [{"t": "pong", "seq": 4}]
    assert decoder.feed(b'{"t":"pong","seq":4}\n{"t":"pong","seq":3}\n') == []


def test_host_encoder_is_deterministic_and_lf_terminated() -> None:
    assert encode_message({"seq": 1, "t": "ping"}) == b'{"seq":1,"t":"ping"}\n'


def test_host_encoder_applies_4096_utf8_byte_limit() -> None:
    with pytest.raises(DeviceLinkProtocolError, match="4096"):
        encode_message({"t": "approval", "seq": 1, "text": "あ" * 1400})


def test_hello_requires_protocol_version_one() -> None:
    with pytest.raises(DeviceLinkProtocolError, match="proto must be 1"):
        DeviceLinkDecoder().feed(b'{"t":"hello","seq":1,"proto":2}\n')


def test_host_encoder_rejects_device_to_host_type() -> None:
    with pytest.raises(DeviceLinkProtocolError, match="not supported"):
        encode_message({"t": "interrupt", "seq": 1})
