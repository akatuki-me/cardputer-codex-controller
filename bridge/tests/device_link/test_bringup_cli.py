from __future__ import annotations

from pathlib import Path

import cardputer_codex_bridge.cli as cli_module
import pytest
from cardputer_codex_bridge.cli import main
from cardputer_codex_bridge.device_link import PortSelectionError, resolve_port


def test_port_handle_is_local_only_and_resolved_without_echo(tmp_path: Path) -> None:
    handle = tmp_path / "device-port.txt"
    handle.write_text("synthetic-device\n", encoding="utf-8")

    assert resolve_port(explicit_port=None, handle_file=handle) == "synthetic-device"


def test_port_selection_requires_exactly_one_source() -> None:
    with pytest.raises(PortSelectionError):
        resolve_port(explicit_port=None, handle_file=None)


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


def test_bringup_dry_run_accepts_local_handle_without_echoing_it(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    handle = tmp_path / "device-port.txt"
    handle.write_text("synthetic-device\n", encoding="utf-8")

    assert main(["bringup", "--port-handle", str(handle), "--dry-run"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "bringup_dry_run PASS\n" in captured.out
    assert "synthetic-device" not in captured.out


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
