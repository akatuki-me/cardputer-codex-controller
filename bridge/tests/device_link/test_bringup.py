from __future__ import annotations

import io
import time
from collections.abc import Callable

from cardputer_codex_bridge.device_link import (
    BringupSession,
    EchoMeasurement,
    SyntheticSerialProvider,
    ThroughputMeasurement,
    run_bringup,
)
from cardputer_codex_bridge.device_link.bringup import _write_hardware_measurements


def test_synthetic_bringup_exercises_the_m1_acceptance_path() -> None:
    provider = SyntheticSerialProvider()
    output = io.StringIO()

    run_bringup(
        output,
        port="synthetic-secret-port",
        provider=provider,
        synthetic_device=provider,
        step_timeout=2.0,
    )

    result = output.getvalue()
    for step in (
        "serial_link",
        "device_ready",
        "firmware_version",
        "board_cardputer_adv",
        "echo_small",
        "echo_4096",
        "rtt",
        "throughput",
        "heartbeat",
        "keyboard_digit",
        "g0_short",
        "g0_long",
        "bringup",
    ):
        assert f"{step} PASS\n" in result
    assert "codex_connection N/A\n" in result
    assert "synthetic-secret-port" not in result
    assert "session" not in result
    assert "payload" not in result
    assert '"code"' not in result
    assert "echo_4096_elapsed_ms" not in result
    assert "throughput_bytes_per_second" not in result


def test_hardware_measurements_are_unit_labeled_and_do_not_include_transport_identity() -> None:
    output = io.StringIO()

    _write_hardware_measurements(
        output,
        maximum_echo=EchoMeasurement(payload_bytes=4_040, elapsed_ms=12.3456),
        rtt_ms=4.5678,
        throughput=ThroughputMeasurement(
            payload_bytes=4_096,
            elapsed_ms=250.0,
            bytes_per_second=16_384.4,
        ),
        heap_bytes=250_000,
    )

    assert output.getvalue().splitlines() == [
        "echo_4096_elapsed_ms 12.346",
        "rtt_ms 4.568",
        "throughput_bytes_per_second 16384",
        "heap_bytes 250000",
    ]


def test_reconnect_rotates_session_and_resets_host_sequence() -> None:
    provider = SyntheticSerialProvider()
    session = BringupSession(
        port="synthetic",
        provider=provider,
        read_timeout=0.005,
        ping_interval=1.0,
        stale_after=3.0,
        reconnect_delay=0.01,
    )

    session.start()
    try:
        first_port = provider.wait_for_port(1)
        assert provider.decoded_host_messages(port_number=1) == []
        provider.inject(_device_hello(sequence=20), port_number=1)
        assert _wait_until(
            lambda: len(provider.decoded_host_messages(port_number=1)) == 1,
            timeout=1.0,
        )
        first_hello = provider.decoded_host_messages(port_number=1)[0]
        assert first_hello["seq"] == 1
        first_session = first_hello["session"]
        assert isinstance(first_session, str)
        provider.inject(_device_ready(21, first_session), port_number=1)
        assert session.wait_ready(1.0)

        first_port.disconnect()
        assert session.wait_generation(2, 1.0)
        provider.wait_for_port(2)
        provider.inject(_device_hello(sequence=22), port_number=2)
        assert _wait_until(
            lambda: len(provider.decoded_host_messages(port_number=2)) == 1,
            timeout=1.0,
        )
        second_hello = provider.decoded_host_messages(port_number=2)[0]
        assert second_hello["seq"] == 1
        second_session = second_hello["session"]
        assert isinstance(second_session, str)
        assert second_session != first_session
        provider.inject(_device_ready(23, second_session), port_number=2)
        assert session.wait_ready(1.0)
    finally:
        session.close()


def _device_hello(sequence: int) -> dict[str, object]:
    return {
        "t": "hello",
        "seq": sequence,
        "proto": 1,
        "mode": "m1-bringup",
        "firmware": "synthetic",
        "board": 24,
        "adv": True,
        "heap": 250_000,
    }


def _device_ready(sequence: int, session: str) -> dict[str, object]:
    return {
        "t": "ready",
        "seq": sequence,
        "mode": "m1-bringup",
        "session": session,
        "board": 24,
        "adv": True,
    }


def _wait_until(predicate: Callable[[], bool], *, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()
