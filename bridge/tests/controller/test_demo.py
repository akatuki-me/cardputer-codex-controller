from __future__ import annotations

import io

import cardputer_codex_bridge.cli as cli_module
import pytest
from cardputer_codex_bridge.cli import main
from cardputer_codex_bridge.controller.demo import run_demo


def test_demo_covers_controller_vertical_slice_deterministically() -> None:
    first = io.StringIO()
    second = io.StringIO()
    run_demo(first)
    run_demo(second)

    assert first.getvalue() == second.getvalue()
    output = first.getvalue()
    assert 'device_hello {"device": "synthetic", "proto": 1' in output
    assert "home_full " in output
    assert output.count('"slot":') >= 6
    assert "turn_started " in output
    assert "turn_running " in output
    assert 'host_command {"command": "turn/interrupt"' in output
    assert "turn_completed " in output
    assert 'device_hold_local {"action": "hold", "retained": true' in output
    assert 'host_resolved {"decision": "decline"' in output
    assert output.count('"acceptPresented": false') == 2
    assert '"codexConnection": "N/A"' in output
    assert '"hardwarePortOpened": false' in output


def test_console_main_runs_demo(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["demo"]) == 0
    assert "device_hello " in capsys.readouterr().out


def test_console_main_routes_codex_demo_without_exposing_values(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli_module,
        "run_codex_demo",
        lambda output: output.write("codex_demo PASS\n"),
    )

    assert main(["codex-demo"]) == 0
    assert capsys.readouterr().out == "codex_demo PASS\n"


def test_codex_demo_failure_does_not_print_exception_message(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(_output: object) -> None:
        raise RuntimeError("sensitive value")

    monkeypatch.setattr(cli_module, "run_codex_demo", fail)

    assert main(["codex-demo"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "codex_demo FAIL RuntimeError\n"
