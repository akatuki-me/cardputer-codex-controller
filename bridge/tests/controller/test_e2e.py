from __future__ import annotations

import io
import sys
from pathlib import Path

import cardputer_codex_bridge.cli as cli_module
import pytest
from cardputer_codex_bridge.cli import main
from cardputer_codex_bridge.controller.e2e import resolve_port, run_serial_e2e
from cardputer_codex_bridge.device_link import SyntheticSerialProvider

FIXTURE = Path(__file__).parents[1] / "app_server" / "fake_app_server.py"


def test_codex_operations_and_synthetic_serial_interrupt_active_turn_once() -> None:
    provider = SyntheticSerialProvider()
    output = io.StringIO()

    run_serial_e2e(
        output,
        port="synthetic",
        provider=provider,
        synthetic_device=provider,
        command=(sys.executable, str(FIXTURE), "operations"),
        step_timeout=2.0,
    )

    result = output.getvalue()
    assert "device_hello PASS\n" in result
    assert "turn_started PASS\n" in result
    assert "device_interrupt PASS\n" in result
    assert "interrupt_forwarded_once PASS\n" in result
    assert result.endswith("e2e PASS\n")
    states = [
        message
        for message in provider.decoded_host_messages()
        if message["t"] == "state"
    ]
    assert any(
        isinstance(state["slots"], list) and state["slots"][0]["turnActive"] is True
        for state in states
    )
    assert any(
        isinstance(state["slots"], list) and state["slots"][0]["attentionKind"] == "done"
        for state in states
    )


def test_port_handle_is_local_only_and_resolved_without_echo(tmp_path: Path) -> None:
    handle = tmp_path / "device-port.txt"
    handle.write_text("synthetic-device\n", encoding="utf-8")

    assert resolve_port(explicit_port=None, handle_file=handle) == "synthetic-device"


def test_e2e_dry_run_does_not_call_serial_or_codex_runner(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    called = False

    def unexpected(*args: object, **kwargs: object) -> None:
        del args, kwargs
        nonlocal called
        called = True

    monkeypatch.setattr(cli_module, "run_serial_e2e", unexpected)

    assert main(["e2e", "--port", "synthetic-device", "--dry-run"]) == 0
    captured = capsys.readouterr()
    assert called is False
    assert captured.err == ""
    assert "serial_io_opened false\n" in captured.out
    assert "codex_connection N/A\n" in captured.out
    assert "synthetic-device" not in captured.out


def test_e2e_dry_run_accepts_a_local_handle_without_echoing_it(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    handle = tmp_path / "device-port.txt"
    handle.write_text("synthetic-device\n", encoding="utf-8")
    monkeypatch.setattr(
        cli_module,
        "run_serial_e2e",
        lambda *args, **kwargs: pytest.fail("dry-run opened the E2E runner"),
    )

    assert main(["e2e", "--port-handle", str(handle), "--dry-run"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "e2e_dry_run PASS\n" in captured.out
    assert "synthetic-device" not in captured.out


def test_e2e_cli_routes_synthetic_without_printing_exception_values(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def synthetic_pass(output: object, **kwargs: object) -> None:
        del kwargs
        output.write("e2e PASS\n")

    monkeypatch.setattr(cli_module, "run_serial_e2e", synthetic_pass)

    assert main(["e2e", "--synthetic"]) == 0
    assert capsys.readouterr().out == "e2e PASS\n"


def test_e2e_failure_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("sensitive port value")

    monkeypatch.setattr(cli_module, "run_serial_e2e", fail)

    assert main(["e2e", "--synthetic"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "e2e FAIL RuntimeError\n"


def test_bringup_dry_run_does_not_open_serial_or_call_runner(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli_module,
        "PySerialProvider",
        lambda: pytest.fail("dry-run created a serial provider"),
    )
    monkeypatch.setattr(
        cli_module,
        "run_bringup",
        lambda *args, **kwargs: pytest.fail("dry-run called the bring-up runner"),
    )

    assert main(["bringup", "--port", "sensitive-port", "--dry-run"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "serial_io_opened false\n" in captured.out
    assert "codex_connection N/A\n" in captured.out
    assert "sensitive-port" not in captured.out


def test_bringup_cli_routes_synthetic_provider(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: dict[str, object] = {}

    def synthetic_pass(output: object, **kwargs: object) -> None:
        observed.update(kwargs)
        output.write("bringup PASS\n")

    monkeypatch.setattr(cli_module, "run_bringup", synthetic_pass)

    assert main(["bringup", "--synthetic"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == "bringup PASS\n"
    assert observed["port"] == "synthetic"
    assert observed["provider"] is observed["synthetic_device"]


def test_bringup_failure_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("sensitive diagnostic value")

    monkeypatch.setattr(cli_module, "run_bringup", fail)

    assert main(["bringup", "--synthetic"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "bringup FAIL RuntimeError\n"
