from __future__ import annotations

import io
import threading

import cardputer_codex_bridge.cli as cli_module
import pytest
from cardputer_codex_bridge.cli import main
from cardputer_codex_bridge.controller.approval_fixture import run_approval_fixture
from cardputer_codex_bridge.device_link import SyntheticSerialProvider


class GuidedFixtureInput(io.StringIO):
    def __init__(self, provider: SyntheticSerialProvider) -> None:
        super().__init__()
        self._provider = provider
        self._step = 0

    def readline(self, size: int = -1) -> str:
        del size
        responses = {
            3: ("fixture-long", "accept", 2),
            6: ("fixture-high-risk", "decline", 3),
            10: ("fixture-incomplete", "decline", 4),
        }
        response = responses.get(self._step)
        self._step += 1
        if response is not None:
            approval_id, decision, sequence = response
            self._provider.inject(
                {
                    "t": "decision",
                    "seq": sequence,
                    "deviceApprovalId": approval_id,
                    "decision": decision,
                }
            )
        return "ok\n"


def test_synthetic_approval_fixture_covers_safe_device_contract() -> None:
    provider = SyntheticSerialProvider()
    output = io.StringIO()

    run_approval_fixture(
        output,
        io.StringIO(),
        port="synthetic",
        provider=provider,
        synthetic_device=provider,
        step_timeout=2.0,
        settle_timeout=0.01,
    )

    result = output.getvalue()
    assert "guard_before_300ms PASS\n" in result
    assert "body_end_gate PASS\n" in result
    assert "long_body_accept PASS\n" in result
    assert "high_risk_accept_hidden PASS\n" in result
    assert "high_risk_decline PASS\n" in result
    assert "incomplete_accept_hidden PASS\n" in result
    assert "incomplete_hold PASS\n" in result
    assert "incomplete_decline PASS\n" in result
    assert result.endswith("approval_fixture PASS\n")

    approvals = [
        message
        for message in provider.decoded_host_messages()
        if message["t"] == "approval"
    ]
    assert len(approvals) == 3
    assert len(approvals[0]["lines"]) >= 5
    assert approvals[0]["contentComplete"] is True
    assert approvals[0]["riskClass"] == "normal"
    assert approvals[0]["decisions"] == ["accept", "decline"]
    assert approvals[1]["riskClass"] == "high"
    assert approvals[1]["decisions"] == ["decline"]
    assert approvals[2]["contentComplete"] is False
    assert approvals[2]["decisions"] == ["decline"]

    resolutions = [
        message
        for message in provider.decoded_host_messages()
        if message["t"] == "approval_resolved"
    ]
    assert [message["decision"] for message in resolutions] == [
        "accept",
        "decline",
        "decline",
    ]


def test_approval_fixture_output_is_sanitized() -> None:
    provider = SyntheticSerialProvider()
    output = io.StringIO()

    run_approval_fixture(
        output,
        io.StringIO(),
        port="private-port-value",
        provider=provider,
        synthetic_device=provider,
        step_timeout=2.0,
        settle_timeout=0.01,
    )

    result = output.getvalue()
    assert "private-port-value" not in result
    assert "fixture-long" not in result
    assert "deviceApprovalId" not in result
    assert "表示確認" not in result
    assert "{" not in result


def test_guided_operator_path_requires_confirmation_and_observes_wire_decisions() -> None:
    provider = SyntheticSerialProvider()
    output = io.StringIO()

    def device_hello() -> None:
        provider.wait_for_port(timeout=2.0)
        provider.inject({"t": "hello", "seq": 1, "proto": 1})

    hello_thread = threading.Thread(target=device_hello)
    hello_thread.start()
    run_approval_fixture(
        output,
        GuidedFixtureInput(provider),
        port="private-port-value",
        provider=provider,
        step_timeout=2.0,
        settle_timeout=0.01,
    )
    hello_thread.join(timeout=2.0)

    result = output.getvalue()
    assert hello_thread.is_alive() is False
    assert result.count(" ACTION ") == 11
    assert result.endswith("approval_fixture PASS\n")
    assert "private-port-value" not in result
    assert "fixture-long" not in result


def test_approval_fixture_dry_run_does_not_start_runner(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli_module,
        "run_approval_fixture",
        lambda *args, **kwargs: pytest.fail("dry-run started approval fixture"),
    )
    monkeypatch.setattr(
        cli_module,
        "PySerialProvider",
        lambda: pytest.fail("dry-run created a serial provider"),
    )

    assert (
        main(
            [
                "approval-fixture",
                "--port",
                "private-port-value",
                "--dry-run",
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "serial_io_opened false\n" in captured.out
    assert "codex_connection N/A\n" in captured.out
    assert "approval_fixture_dry_run PASS\n" in captured.out
    assert "private-port-value" not in captured.out


def test_approval_fixture_cli_routes_synthetic_provider(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    called: list[dict[str, object]] = []

    def synthetic_pass(output: object, input_stream: object, **kwargs: object) -> None:
        del input_stream
        called.append(kwargs)
        output.write("approval_fixture PASS\n")

    monkeypatch.setattr(cli_module, "run_approval_fixture", synthetic_pass)

    assert main(["approval-fixture", "--synthetic"]) == 0
    assert capsys.readouterr().out == "approval_fixture PASS\n"
    assert len(called) == 1
    assert called[0]["port"] == "synthetic"
    assert isinstance(called[0]["provider"], SyntheticSerialProvider)
    assert called[0]["synthetic_device"] is called[0]["provider"]


def test_approval_fixture_failure_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("private port and fixture body")

    monkeypatch.setattr(cli_module, "run_approval_fixture", fail)

    assert main(["approval-fixture", "--synthetic"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "approval_fixture FAIL RuntimeError\n"
