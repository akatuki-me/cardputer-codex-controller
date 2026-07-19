from __future__ import annotations

import io
import sys
import threading
from pathlib import Path

import cardputer_codex_bridge.cli as cli_module
import cardputer_codex_bridge.controller.runtime as runtime_module
import pytest
from cardputer_codex_bridge.cli import main
from cardputer_codex_bridge.controller.runtime import run_controller
from cardputer_codex_bridge.device_link import SyntheticSerialProvider

FAKE_SERVER = Path(__file__).with_name("fake_controller_app_server.py")


class CoordinatedOutput(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.condition = threading.Condition()

    def write(self, value: str) -> int:
        with self.condition:
            written = super().write(value)
            self.condition.notify_all()
            return written

    def wait_for(self, value: str, timeout: float = 2.0) -> bool:
        with self.condition:
            return self.condition.wait_for(lambda: value in self.getvalue(), timeout=timeout)


class ApprovalInput(io.StringIO):
    def __init__(self, output: CoordinatedOutput) -> None:
        super().__init__()
        self._output = output
        self._step = 0

    def readline(self, size: int = -1) -> str:
        del size
        if self._step == 0:
            assert self._output.wait_for("pending approvals: 1")
            self._step += 1
            return "decline approval-000001\n"
        if self._step == 1:
            assert self._output.wait_for("pending approvals: 0")
            self._step += 1
            return "quit\n"
        return ""


class DeviceApprovalInput(io.StringIO):
    def __init__(
        self,
        output: CoordinatedOutput,
        provider: SyntheticSerialProvider,
    ) -> None:
        super().__init__()
        self._output = output
        self._provider = provider
        self._sent = False

    def readline(self, size: int = -1) -> str:
        del size
        if not self._sent:
            assert self._output.wait_for("pending approvals: 1")
            approval = next(
                message
                for message in self._provider.decoded_host_messages()
                if message["t"] == "approval"
            )
            self._provider.inject(
                {
                    "t": "decision",
                    "seq": 2,
                    "deviceApprovalId": approval["deviceApprovalId"],
                    "decision": "decline",
                }
            )
            self._sent = True
        assert self._output.wait_for("pending approvals: 0")
        return "quit\n"


def test_controller_runtime_reaches_device_ready_and_closes_cleanly(
    tmp_path: Path,
) -> None:
    provider = SyntheticSerialProvider()
    output = io.StringIO()

    run_controller(
        output,
        io.StringIO("quit\n"),
        cwd=tmp_path,
        label="fixture",
        port="synthetic",
        provider=provider,
        synthetic_device=provider,
        command=(sys.executable, "-u", str(FAKE_SERVER)),
        step_timeout=2.0,
    )

    result = output.getvalue()
    assert "app_server PASS\n" in result
    assert "controller_thread PASS\n" in result
    assert "device_link PASS\n" in result
    assert "controller_ready PASS\n" in result
    assert "cancel <id>" in result
    assert result.endswith("controller_stopped PASS\n")
    assert str(tmp_path) not in result
    messages = provider.decoded_host_messages()
    assert [message["t"] for message in messages[:2]] == ["hello", "state"]
    assert messages[1]["slots"][0]["label"] == "fixture"


def test_controller_runtime_allows_bounded_session_end_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: list[float] = []
    real_client = runtime_module.AppServerClient

    def client_with_observed_timeout(**kwargs: object) -> object:
        captured.append(float(kwargs["shutdown_timeout"]))
        return real_client(**kwargs)

    monkeypatch.setattr(runtime_module, "AppServerClient", client_with_observed_timeout)
    provider = SyntheticSerialProvider()

    run_controller(
        io.StringIO(),
        io.StringIO("quit\n"),
        cwd=tmp_path,
        label="fixture",
        port="synthetic",
        provider=provider,
        synthetic_device=provider,
        command=(sys.executable, "-u", str(FAKE_SERVER)),
        step_timeout=2.0,
    )

    assert captured == [60.0]


def test_controller_runtime_host_response_reaches_device_resolved(
    tmp_path: Path,
) -> None:
    provider = SyntheticSerialProvider()
    output = CoordinatedOutput()

    run_controller(
        output,
        ApprovalInput(output),
        cwd=tmp_path,
        label="fixture",
        port="synthetic",
        provider=provider,
        synthetic_device=provider,
        command=(sys.executable, "-u", str(FAKE_SERVER), "approval"),
        step_timeout=2.0,
    )

    result = output.getvalue()
    assert "pending approvals: 1\n" in result
    assert "approval_response PASS\n" in result
    assert "pending approvals: 0\n" in result
    messages = provider.decoded_host_messages()
    approval = next(message for message in messages if message["t"] == "approval")
    assert approval["decisions"] == ["accept", "decline"]
    resolved = next(
        message for message in messages if message["t"] == "approval_resolved"
    )
    assert resolved["deviceApprovalId"] == approval["deviceApprovalId"]
    assert resolved["decision"] == "decline"


def test_controller_runtime_device_decision_reaches_app_server(
    tmp_path: Path,
) -> None:
    provider = SyntheticSerialProvider()
    output = CoordinatedOutput()

    run_controller(
        output,
        DeviceApprovalInput(output, provider),
        cwd=tmp_path,
        label="fixture",
        port="synthetic",
        provider=provider,
        synthetic_device=provider,
        command=(sys.executable, "-u", str(FAKE_SERVER), "approval"),
        step_timeout=2.0,
    )

    messages = provider.decoded_host_messages()
    sending = [
        message
        for message in messages
        if message["t"] == "approval" and message["sending"] is True
    ]
    assert len(sending) == 1
    assert sending[0]["decisions"] == []
    assert any(message["t"] == "approval_resolved" for message in messages)


def test_controller_runtime_runs_and_waits_for_a_turn(tmp_path: Path) -> None:
    provider = SyntheticSerialProvider()
    output = io.StringIO()

    run_controller(
        output,
        io.StringIO("run Reply with READY\nwait\nquit\n"),
        cwd=tmp_path,
        label="fixture",
        port="synthetic",
        provider=provider,
        synthetic_device=provider,
        command=(sys.executable, "-u", str(FAKE_SERVER), "turn"),
        step_timeout=2.0,
    )

    result = output.getvalue()
    assert "turn_request PASS\n" in result
    assert "turn_started PASS\n" in result
    assert "turn_completed PASS\n" in result
    assert "turn_wait PASS\n" in result
    assert "command REJECTED\n" not in result
    states = [
        message for message in provider.decoded_host_messages() if message["t"] == "state"
    ]
    assert any(state["slots"][0]["turnActive"] is True for state in states)
    assert any(state["slots"][0]["attentionKind"] == "done" for state in states)


def test_control_dry_run_does_not_start_controller(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        cli_module,
        "run_controller",
        lambda *args, **kwargs: pytest.fail("dry-run started controller"),
    )

    assert (
        main(
            [
                "control",
                "--cwd",
                str(tmp_path),
                "--label",
                "fixture",
                "--port",
                "synthetic-device",
                "--dry-run",
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "controller_dry_run PASS\n" in captured.out
    assert "synthetic-device" not in captured.out


def test_control_cli_routes_synthetic_provider(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    called: list[dict[str, object]] = []

    def synthetic_pass(output: object, input_stream: object, **kwargs: object) -> None:
        del input_stream
        called.append(kwargs)
        output.write("controller_stopped PASS\n")

    monkeypatch.setattr(cli_module, "run_controller", synthetic_pass)

    assert (
        main(
            [
                "control",
                "--cwd",
                str(tmp_path),
                "--label",
                "fixture",
                "--synthetic",
            ]
        )
        == 0
    )
    assert capsys.readouterr().out == "controller_stopped PASS\n"
    assert len(called) == 1
    assert called[0]["port"] == "synthetic"
    assert isinstance(called[0]["provider"], SyntheticSerialProvider)
