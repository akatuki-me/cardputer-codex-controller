from __future__ import annotations

import io
import sys
import threading
from collections.abc import Callable
from pathlib import Path

import cardputer_codex_bridge.cli as cli_module
import cardputer_codex_bridge.controller.runtime as runtime_module
import pytest
from cardputer_codex_bridge.cli import main
from cardputer_codex_bridge.controller.instance_guard import (
    ControllerAlreadyRunningError,
)
from cardputer_codex_bridge.controller.runtime import run_controller
from cardputer_codex_bridge.device_link import SerialPort, SyntheticSerialProvider

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


class FailingSerialProvider:
    def open(
        self,
        port: str,
        *,
        baudrate: int,
        read_timeout: float,
    ) -> SerialPort:
        del port, baudrate, read_timeout
        raise OSError("synthetic open failure")


class _ExclusiveSyntheticPort:
    def __init__(self, inner: SerialPort, release: Callable[[], None]) -> None:
        self._inner = inner
        self._release = release
        self._closed = False

    def read(self, size: int = 1) -> bytes:
        return self._inner.read(size)

    def write(self, data: bytes) -> int | None:
        return self._inner.write(data)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._inner.close()
        finally:
            self._release()


class ExclusiveSyntheticProvider:
    def __init__(self) -> None:
        self._delegate = SyntheticSerialProvider()
        self._lock = threading.Lock()
        self._active = False
        self.released = threading.Event()

    def open(
        self,
        port: str,
        *,
        baudrate: int,
        read_timeout: float,
    ) -> SerialPort:
        with self._lock:
            if self._active:
                raise OSError("synthetic port is already open")
            self._active = True
            self.released.clear()
        try:
            inner = self._delegate.open(
                port,
                baudrate=baudrate,
                read_timeout=read_timeout,
            )
        except BaseException:
            self._mark_released()
            raise
        return _ExclusiveSyntheticPort(inner, self._mark_released)

    def inject(self, message: dict[str, object]) -> None:
        self._delegate.inject(message)

    def _mark_released(self) -> None:
        with self._lock:
            self._active = False
            self.released.set()


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


class DeviceInterruptInput(io.StringIO):
    def __init__(
        self,
        output: CoordinatedOutput,
        provider: SyntheticSerialProvider,
    ) -> None:
        super().__init__()
        self._output = output
        self._provider = provider
        self._step = 0

    def readline(self, size: int = -1) -> str:
        del size
        if self._step == 0:
            self._step += 1
            return "run Keep the turn active\n"
        if self._step == 1:
            assert self._output.wait_for("turn_started PASS")
            state = next(
                message
                for message in reversed(self._provider.decoded_host_messages())
                if message["t"] == "state" and message["slots"][0]["turnActive"] is True
            )
            self._provider.inject(
                {
                    "t": "interrupt",
                    "seq": 2,
                    "slot": 1,
                    "turnId": state["slots"][0]["turnId"],
                }
            )
            assert self._output.wait_for("device_interrupt PASS")
            self._step += 1
            return "wait\n"
        assert self._output.wait_for("turn_wait PASS"), self._output.getvalue()
        return "quit\n"


class UserInput(io.StringIO):
    def __init__(self, output: CoordinatedOutput) -> None:
        super().__init__()
        self._output = output
        self._step = 0

    def readline(self, size: int = -1) -> str:
        del size
        if self._step == 0:
            self._step += 1
            return "run Ask one synthetic question\n"
        if self._step == 1:
            assert self._output.wait_for("pending questions: 1")
            self._step += 1
            return "answer question-000001 A で進める\n"
        if self._step == 2:
            assert self._output.wait_for(
                "pending questions: 0"
            ), self._output.getvalue()
            self._step += 1
            return "wait\n"
        assert self._output.wait_for("turn_wait PASS"), self._output.getvalue()
        return "quit\n"


class StructuredUserInput(io.StringIO):
    def __init__(self, output: CoordinatedOutput) -> None:
        super().__init__()
        self._output = output
        self._step = 0

    def isatty(self) -> bool:
        return True

    def readline(self, size: int = -1) -> str:
        del size
        if self._step == 0:
            self._step += 1
            return "run Ask structured questions\n"
        if self._step == 1:
            assert self._output.wait_for("pending questions: 2")
            self._step += 1
            return "answer question-000001 A\n"
        if self._step == 2:
            assert self._output.wait_for("question_response PASS")
            self._step += 1
            return "pending\n"
        if self._step == 3:
            assert self._output.wait_for("status=answer_staged")
            self._step += 1
            return "secret question-000002\n"
        if self._step == 4:
            assert self._output.wait_for("pending questions: 0")
            self._step += 1
            return "wait\n"
        assert self._output.wait_for("turn_wait PASS"), self._output.getvalue()
        return "quit\n"


class PartialInterruptInput(io.StringIO):
    def __init__(self, output: CoordinatedOutput) -> None:
        super().__init__()
        self._output = output
        self._step = 0

    def readline(self, size: int = -1) -> str:
        del size
        if self._step == 0:
            self._step += 1
            return "run Ask then interrupt\n"
        if self._step == 1:
            assert self._output.wait_for("pending questions: 2")
            self._step += 1
            return "answer question-000001 first-answer\n"
        if self._step == 2:
            assert self._output.wait_for("question_response PASS")
            self._step += 1
            return "interrupt\n"
        if self._step == 3:
            assert self._output.wait_for("pending questions: 0")
            self._step += 1
            return "wait\n"
        assert self._output.wait_for("turn_wait PASS"), self._output.getvalue()
        return "quit\n"


class PartialDeviceInterruptInput(io.StringIO):
    def __init__(
        self,
        output: CoordinatedOutput,
        provider: SyntheticSerialProvider,
    ) -> None:
        super().__init__()
        self._output = output
        self._provider = provider
        self._step = 0

    def readline(self, size: int = -1) -> str:
        del size
        if self._step == 0:
            self._step += 1
            return "run Ask then device interrupt\n"
        if self._step == 1:
            assert self._output.wait_for("pending questions: 2")
            self._step += 1
            return "answer question-000001 first-answer\n"
        if self._step == 2:
            assert self._output.wait_for("question_response PASS")
            state = next(
                message
                for message in reversed(self._provider.decoded_host_messages())
                if message["t"] == "state" and message["slots"][0]["turnActive"]
            )
            self._provider.inject(
                {
                    "t": "interrupt",
                    "seq": 2,
                    "slot": 1,
                    "turnId": state["slots"][0]["turnId"],
                }
            )
            assert self._output.wait_for("device_interrupt PASS")
            self._step += 1
            return "pending\n"
        if self._step == 3:
            assert self._output.wait_for("pending questions: 0")
            self._step += 1
            return "wait\n"
        assert self._output.wait_for("turn_wait PASS"), self._output.getvalue()
        return "quit\n"


class MixedPendingInput(io.StringIO):
    def __init__(
        self,
        output: CoordinatedOutput,
        provider: SyntheticSerialProvider,
    ) -> None:
        super().__init__()
        self._output = output
        self._provider = provider
        self._step = 0

    def readline(self, size: int = -1) -> str:
        del size
        if self._step == 0:
            self._step += 1
            return "run Ask and request approval\n"
        if self._step == 1:
            assert self._output.wait_for("pending approvals: 1")
            assert self._output.wait_for("pending questions: 1")
            self._step += 1
            return "decline approval-000001\n"
        if self._step == 2:
            assert self._output.wait_for("pending approvals: 0")
            attentions = [
                message["slots"][0]["attentionKind"]
                for message in self._provider.decoded_host_messages()
                if message["t"] == "state"
            ]
            approval_index = attentions.index("approval")
            assert "question" in attentions[:approval_index]
            assert "question" in attentions[approval_index + 1 :]
            self._step += 1
            return "answer question-000001 A\n"
        if self._step == 3:
            assert self._output.wait_for("pending questions: 0")
            self._step += 1
            return "wait\n"
        assert self._output.wait_for("turn_wait PASS"), self._output.getvalue()
        return "quit\n"


class UnsupportedRequestInput(io.StringIO):
    def __init__(self, output: CoordinatedOutput) -> None:
        super().__init__()
        self._output = output
        self._step = 0

    def readline(self, size: int = -1) -> str:
        del size
        if self._step == 0:
            self._step += 1
            return "run Trigger unsupported request\n"
        assert self._output.wait_for("turn_completed PASS"), self._output.getvalue()
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
    assert "answer <id> <text>" in result
    assert "secret <id>" in result
    assert result.endswith("controller_stopped PASS\n")
    assert str(tmp_path) not in result
    messages = provider.decoded_host_messages()
    assert [message["t"] for message in messages[:2]] == ["hello", "state"]
    assert messages[1]["slots"][0]["label"] == "fixture"


def test_controller_startup_failure_keeps_the_connection_timeout(
    tmp_path: Path,
) -> None:
    output = io.StringIO()

    with pytest.raises(TimeoutError, match="serial link did not connect"):
        run_controller(
            output,
            io.StringIO("quit\n"),
            cwd=tmp_path,
            label="fixture",
            port="synthetic",
            provider=FailingSerialProvider(),
            command=(sys.executable, "-u", str(FAKE_SERVER), "idle"),
            step_timeout=0.5,
        )

    assert "controller event pump did not stop" not in output.getvalue()


def test_controller_closes_client_when_app_server_start_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class StartFailingClient:
        def __init__(self) -> None:
            self.close_calls = 0

        def start(self) -> None:
            raise RuntimeError("synthetic start failure")

        def close(self) -> object:
            self.close_calls += 1
            return object()

    client = StartFailingClient()
    monkeypatch.setattr(
        runtime_module,
        "AppServerClient",
        lambda **kwargs: client,
    )

    with pytest.raises(RuntimeError, match="synthetic start failure"):
        run_controller(
            io.StringIO(),
            io.StringIO("quit\n"),
            cwd=tmp_path,
            label="fixture",
            port="synthetic",
            provider=FailingSerialProvider(),
            step_timeout=0.2,
        )

    assert client.close_calls == 1


def test_controller_releases_serial_before_app_server_shutdown_wait(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = ExclusiveSyntheticProvider()
    shutdown_started = threading.Event()
    app_server_close_started = threading.Event()
    allow_app_server_close = threading.Event()
    errors: list[BaseException] = []
    real_client = runtime_module.AppServerClient

    class DelayedCloseClient(real_client):
        def close(self) -> object:
            app_server_close_started.set()
            if not allow_app_server_close.wait(timeout=5.0):
                raise TimeoutError("synthetic close release was not received")
            return super().close()

    class ShutdownInput(io.StringIO):
        def readline(self, size: int = -1) -> str:
            shutdown_started.set()
            return super().readline(size)

    monkeypatch.setattr(runtime_module, "AppServerClient", DelayedCloseClient)

    def run() -> None:
        try:
            run_controller(
                io.StringIO(),
                ShutdownInput("quit\n"),
                cwd=tmp_path,
                label="fixture",
                port="synthetic",
                provider=provider,
                synthetic_device=provider,
                command=(sys.executable, "-u", str(FAKE_SERVER)),
                step_timeout=2.0,
            )
        except BaseException as error:
            errors.append(error)

    controller_thread = threading.Thread(target=run)
    controller_thread.start()
    try:
        assert shutdown_started.wait(timeout=2.0)
        assert provider.released.wait(timeout=3.0)
        assert app_server_close_started.wait(timeout=3.0)
        assert controller_thread.is_alive()
        reopened = provider.open(
            "synthetic",
            baudrate=115_200,
            read_timeout=0.1,
        )
        reopened.close()
    finally:
        allow_app_server_close.set()
        controller_thread.join(timeout=5.0)

    assert not controller_thread.is_alive()
    assert errors == []


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
    assert "device_decline PASS\n" in output.getvalue()


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


def test_controller_wait_returns_to_console_when_approval_arrives(
    tmp_path: Path,
) -> None:
    provider = SyntheticSerialProvider()
    output = io.StringIO()

    run_controller(
        output,
        io.StringIO(
            "run Inspect the repository\n"
            "wait\n"
            "decline approval-000001\n"
            "wait\n"
            "quit\n"
        ),
        cwd=tmp_path,
        label="fixture",
        port="synthetic",
        provider=provider,
        synthetic_device=provider,
        command=(sys.executable, "-u", str(FAKE_SERVER), "turn_approval"),
        step_timeout=2.0,
    )

    result = output.getvalue()
    assert "turn_wait BLOCKED pending_approval\n" in result
    assert "approval_response PASS\n" in result
    assert "turn_completed PASS\n" in result
    assert "turn_wait PASS\n" in result
    assert result.endswith("controller_stopped PASS\n")


def test_controller_answers_single_question_without_exposing_answer(
    tmp_path: Path,
) -> None:
    provider = SyntheticSerialProvider()
    output = CoordinatedOutput()

    run_controller(
        output,
        UserInput(output),
        cwd=tmp_path,
        label="fixture",
        port="synthetic",
        provider=provider,
        synthetic_device=provider,
        command=(sys.executable, "-u", str(FAKE_SERVER), "turn_user_input"),
        step_timeout=2.0,
    )

    result = output.getvalue()
    assert "turn_wait BLOCKED pending_question\n" not in result
    assert "pending questions: 1\n" in result
    assert "question_response PASS\n" in result
    assert "pending questions: 0\n" in result
    assert "A で進める" not in result
    assert "rpc-question-private" not in result
    assert "schema-question-private" not in result
    states = [
        message for message in provider.decoded_host_messages() if message["t"] == "state"
    ]
    assert any(state["slots"][0]["attentionKind"] == "question" for state in states)
    assert result.endswith("controller_stopped PASS\n")


def test_controller_collects_multiple_questions_with_no_echo_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = SyntheticSerialProvider()
    output = CoordinatedOutput()
    prompts: list[str] = []

    def read_secret(prompt: str) -> str:
        prompts.append(prompt)
        return "hidden-value"

    monkeypatch.setattr(runtime_module, "read_tty_secret", read_secret)

    run_controller(
        output,
        StructuredUserInput(output),
        cwd=tmp_path,
        label="fixture",
        port="synthetic",
        provider=provider,
        synthetic_device=provider,
        command=(sys.executable, "-u", str(FAKE_SERVER), "turn_structured_input"),
        step_timeout=2.0,
    )

    result = output.getvalue()
    assert "question-000001" in result
    assert "question-000002" in result
    assert "status=answer_staged" in result
    assert result.count("question_response PASS\n") == 2
    assert prompts == ["secret answer: "]
    assert "hidden-value" not in result
    assert "structured-first-private" not in result
    assert "structured-second-private" not in result
    assert "rpc-structured-private" not in result
    assert result.endswith("controller_stopped PASS\n")


def test_interrupt_discards_partial_answers_without_sending_response(
    tmp_path: Path,
) -> None:
    provider = SyntheticSerialProvider()
    output = CoordinatedOutput()

    run_controller(
        output,
        PartialInterruptInput(output),
        cwd=tmp_path,
        label="fixture",
        port="synthetic",
        provider=provider,
        synthetic_device=provider,
        command=(
            sys.executable,
            "-u",
            str(FAKE_SERVER),
            "turn_user_input_interrupt",
        ),
        step_timeout=2.0,
    )

    result = output.getvalue()
    assert "question_response PASS\n" in result
    assert "interrupt PASS\n" in result
    assert "pending questions: 0\n" in result
    assert "first-answer" not in result
    assert "partial-first-private" not in result
    attentions = [
        message["slots"][0]["attentionKind"]
        for message in provider.decoded_host_messages()
        if message["t"] == "state"
    ]
    question_index = attentions.index("question")
    assert None in attentions[question_index + 1 :]
    assert result.endswith("controller_stopped PASS\n")


def test_device_interrupt_discards_partial_answers_without_response(
    tmp_path: Path,
) -> None:
    provider = SyntheticSerialProvider()
    output = CoordinatedOutput()

    run_controller(
        output,
        PartialDeviceInterruptInput(output, provider),
        cwd=tmp_path,
        label="fixture",
        port="synthetic",
        provider=provider,
        synthetic_device=provider,
        command=(
            sys.executable,
            "-u",
            str(FAKE_SERVER),
            "turn_user_input_interrupt",
        ),
        step_timeout=2.0,
    )

    result = output.getvalue()
    assert result.count("device_interrupt PASS\n") == 1
    assert "pending questions: 0\n" in result
    assert "first-answer" not in result
    assert "partial-first-private" not in result
    attentions = [
        message["slots"][0]["attentionKind"]
        for message in provider.decoded_host_messages()
        if message["t"] == "state"
    ]
    question_index = attentions.index("question")
    assert None in attentions[question_index + 1 :]
    assert result.endswith("controller_stopped PASS\n")


def test_controller_wait_returns_to_console_when_question_arrives(
    tmp_path: Path,
) -> None:
    provider = SyntheticSerialProvider()
    output = io.StringIO()

    run_controller(
        output,
        io.StringIO(
            "run Ask one synthetic question\n"
            "wait\n"
            "answer question-000001 A で進める\n"
            "wait\n"
            "quit\n"
        ),
        cwd=tmp_path,
        label="fixture",
        port="synthetic",
        provider=provider,
        synthetic_device=provider,
        command=(sys.executable, "-u", str(FAKE_SERVER), "turn_user_input"),
        step_timeout=2.0,
    )

    result = output.getvalue()
    assert "turn_wait BLOCKED pending_question\n" in result
    assert "question_response PASS\n" in result
    assert "turn_completed PASS\n" in result
    assert "turn_wait PASS\n" in result
    assert "A で進める" not in result
    assert result.endswith("controller_stopped PASS\n")


def test_controller_routes_mixed_resolutions_and_restores_question_attention(
    tmp_path: Path,
) -> None:
    provider = SyntheticSerialProvider()
    output = CoordinatedOutput()

    run_controller(
        output,
        MixedPendingInput(output, provider),
        cwd=tmp_path,
        label="fixture",
        port="synthetic",
        provider=provider,
        synthetic_device=provider,
        command=(sys.executable, "-u", str(FAKE_SERVER), "turn_mixed_pending"),
        step_timeout=2.0,
    )

    result = output.getvalue()
    assert "approval_response PASS\n" in result
    assert "question_response PASS\n" in result
    assert "turn_completed PASS\n" in result
    assert result.endswith("controller_stopped PASS\n")


def test_controller_fails_closed_for_unsupported_server_request(
    tmp_path: Path,
) -> None:
    provider = SyntheticSerialProvider()
    output = CoordinatedOutput()

    run_controller(
        output,
        UnsupportedRequestInput(output),
        cwd=tmp_path,
        label="fixture",
        port="synthetic",
        provider=provider,
        synthetic_device=provider,
        command=(sys.executable, "-u", str(FAKE_SERVER), "unsupported_request"),
        step_timeout=2.0,
    )

    result = output.getvalue()
    assert "unsupported_server_request REJECTED\n" in result
    assert "unsupported_server_request RESOLVED\n" in result
    assert "rpc-unsupported-private" not in result
    assert "turn_completed PASS\n" in result
    assert result.endswith("controller_stopped PASS\n")


def test_controller_reports_device_interrupt(tmp_path: Path) -> None:
    provider = SyntheticSerialProvider()
    output = CoordinatedOutput()

    run_controller(
        output,
        DeviceInterruptInput(output, provider),
        cwd=tmp_path,
        label="fixture",
        port="synthetic",
        provider=provider,
        synthetic_device=provider,
        command=(sys.executable, "-u", str(FAKE_SERVER), "turn_interrupt"),
        step_timeout=2.0,
    )

    result = output.getvalue()
    assert result.count("device_interrupt PASS\n") == 1
    assert "turn_completed PASS\n" in result
    assert result.endswith("controller_stopped PASS\n")


def test_controller_wait_timeout_returns_to_console(tmp_path: Path) -> None:
    provider = SyntheticSerialProvider()
    output = io.StringIO()

    run_controller(
        output,
        io.StringIO("run Keep the turn active\nwait\ninterrupt\nwait\nquit\n"),
        cwd=tmp_path,
        label="fixture",
        port="synthetic",
        provider=provider,
        synthetic_device=provider,
        command=(sys.executable, "-u", str(FAKE_SERVER), "turn_interrupt"),
        step_timeout=0.2,
    )

    result = output.getvalue()
    assert "turn_wait TIMEOUT\n" in result
    assert "interrupt PASS\n" in result
    assert "turn_completed PASS\n" in result
    assert "turn_wait PASS\n" in result
    assert result.endswith("controller_stopped PASS\n")


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
    monkeypatch.setattr(
        cli_module,
        "ControllerInstanceGuard",
        lambda: pytest.fail("dry-run acquired controller instance guard"),
        raising=False,
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


def test_control_cli_rejects_duplicate_before_provider_creation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    class RejectingGuard:
        def __enter__(self) -> None:
            raise ControllerAlreadyRunningError("controller is already running")

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(cli_module, "ControllerInstanceGuard", RejectingGuard, raising=False)
    monkeypatch.setattr(
        cli_module,
        "SyntheticSerialProvider",
        lambda: pytest.fail("duplicate controller created a provider"),
    )
    monkeypatch.setattr(
        cli_module,
        "run_controller",
        lambda *args, **kwargs: pytest.fail("duplicate controller started runtime"),
    )

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
        == 1
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "control FAIL ControllerAlreadyRunningError\n"
