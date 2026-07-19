from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import cardputer_codex_bridge.app_server.client as client_module
import pytest
from cardputer_codex_bridge.app_server import (
    AppServerClient,
    AppServerClosedError,
    AppServerProtocolError,
    AppServerShutdownError,
    AppServerStartError,
    AppServerState,
    AppServerStateError,
    AppServerTimeoutError,
    AppServerVersionMismatch,
    ClientInfo,
    codex_app_server_command,
)

FAKE_SERVER = Path(__file__).with_name("fake_app_server.py")
CLIENT_INFO = ClientInfo(
    name="cardputer-test",
    title="Cardputer test client",
    version="0.0.0",
)


def _client(
    mode: str,
    *,
    request_timeout: float = 1.0,
    shutdown_timeout: float = 0.5,
) -> AppServerClient:
    return AppServerClient(
        command=(sys.executable, "-u", str(FAKE_SERVER), mode),
        request_timeout=request_timeout,
        shutdown_timeout=shutdown_timeout,
    )


def test_initialize_sends_initialized_then_shutdowns_normally() -> None:
    client = _client("normal")
    client.start()

    result = client.initialize(CLIENT_INFO)

    assert result.codex_version == "0.144.5"
    assert result.platform_family == "synthetic"
    assert result.platform_os == "synthetic"
    assert client.state is AppServerState.READY

    shutdown = client.close()

    assert shutdown.forced is False
    assert shutdown.exit_code == 0
    assert client.is_running is False
    assert client.return_code == 0


def test_initialize_accepts_schema_verified_patch_version() -> None:
    client = _client("version-0.144.6")
    client.start()
    try:
        result = client.initialize(CLIENT_INFO)
        assert result.codex_version == "0.144.6"
    finally:
        shutdown = client.close()

    assert shutdown.forced is False
    assert shutdown.exit_code == 0


def test_initialize_can_opt_into_experimental_server_requests() -> None:
    client = _client("experimental-capability")
    client.start()
    try:
        result = client.initialize(CLIENT_INFO, experimental_api=True)
        assert result.codex_version == "0.144.5"
    finally:
        shutdown = client.close()

    assert shutdown.forced is False
    assert shutdown.exit_code == 0


def test_close_closes_child_stdio_streams() -> None:
    client = _client("normal")
    client.start()
    client.initialize(CLIENT_INFO)
    process = client._process
    assert process is not None
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None

    client.close()

    assert process.stdin.closed
    assert process.stdout.closed
    assert process.stderr.closed


def test_concurrent_start_calls_spawn_only_one_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client("normal")
    original_popen = subprocess.Popen
    first_popen_entered = threading.Event()
    release_first_popen = threading.Event()
    second_popen_entered = threading.Event()
    call_lock = threading.Lock()
    spawned: list[subprocess.Popen[str]] = []
    errors: list[Exception] = []
    call_count = 0

    def blocking_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[str]:
        nonlocal call_count
        with call_lock:
            call_count += 1
            current_call = call_count
        if current_call == 1:
            first_popen_entered.set()
            if not release_first_popen.wait(2.0):
                raise TimeoutError("test did not release the first Popen call")
        else:
            second_popen_entered.set()
        process = original_popen(*args, **kwargs)
        spawned.append(process)
        return process

    def start_client() -> None:
        try:
            client.start()
        except Exception as error:
            errors.append(error)

    monkeypatch.setattr(subprocess, "Popen", blocking_popen)
    first = threading.Thread(target=start_client)
    second = threading.Thread(target=start_client)
    first.start()
    assert first_popen_entered.wait(1.0)
    second.start()
    try:
        assert not second_popen_entered.wait(0.2)
        release_first_popen.set()
        first.join(timeout=2.0)
        second.join(timeout=2.0)
        assert not first.is_alive()
        assert not second.is_alive()
        assert call_count == 1
        assert len(errors) == 1
        assert isinstance(errors[0], AppServerStateError)
    finally:
        release_first_popen.set()
        first.join(timeout=2.0)
        second.join(timeout=2.0)
        client.close()
        for process in spawned:
            if process.poll() is None:
                process.kill()
                process.wait()
    assert client.is_running is False


def test_close_waits_for_start_in_progress_and_reaps_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client("normal")
    original_popen = subprocess.Popen
    popen_entered = threading.Event()
    release_popen = threading.Event()
    close_completed = threading.Event()
    errors: list[Exception] = []

    def blocking_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[str]:
        popen_entered.set()
        if not release_popen.wait(2.0):
            raise TimeoutError("test did not release Popen")
        return original_popen(*args, **kwargs)

    def start_client() -> None:
        try:
            client.start()
        except Exception as error:
            errors.append(error)

    def close_client() -> None:
        try:
            client.close()
        except Exception as error:
            errors.append(error)
        finally:
            close_completed.set()

    monkeypatch.setattr(subprocess, "Popen", blocking_popen)
    start_thread = threading.Thread(target=start_client)
    close_thread = threading.Thread(target=close_client)
    start_thread.start()
    assert popen_entered.wait(1.0)
    close_thread.start()
    try:
        assert not close_completed.wait(0.2)
        release_popen.set()
        start_thread.join(timeout=2.0)
        close_thread.join(timeout=2.0)
        assert not start_thread.is_alive()
        assert not close_thread.is_alive()
        assert errors == []
        assert client.state is AppServerState.CLOSED
        assert client.is_running is False
        assert client.return_code is not None
    finally:
        release_popen.set()
        start_thread.join(timeout=2.0)
        close_thread.join(timeout=2.0)
        client.close()


def test_close_waits_for_initialize_without_restoring_ready_state() -> None:
    client = _client("timeout", request_timeout=0.2, shutdown_timeout=0.05)
    initialize_completed = threading.Event()
    close_completed = threading.Event()
    errors: list[Exception] = []
    client.start()

    def initialize_client() -> None:
        try:
            client.initialize(CLIENT_INFO)
        except Exception as error:
            errors.append(error)
        finally:
            initialize_completed.set()

    def close_client() -> None:
        client.close()
        close_completed.set()

    initialize_thread = threading.Thread(target=initialize_client)
    close_thread = threading.Thread(target=close_client)
    initialize_thread.start()
    deadline = time.monotonic() + 1.0
    while client.state is not AppServerState.INITIALIZING and time.monotonic() < deadline:
        time.sleep(0.005)
    assert client.state is AppServerState.INITIALIZING
    close_thread.start()

    assert not close_completed.wait(0.05)
    initialize_thread.join(timeout=2.0)
    close_thread.join(timeout=2.0)

    assert initialize_completed.is_set()
    assert close_completed.is_set()
    assert len(errors) == 1
    assert isinstance(errors[0], AppServerTimeoutError)
    assert client.state is AppServerState.CLOSED
    assert client.is_running is False
    assert client.return_code is not None


def test_sensitive_host_environment_is_not_inherited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODEX_ACCESS_TOKEN", "fixture-access-token")
    monkeypatch.setenv("GH_TOKEN", "fixture-gh-token")
    monkeypatch.setenv("GITHUB_TOKEN", "fixture-github-token")
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-openai-key")
    monkeypatch.setenv("DATABASE_URL", "fixture-database-url")
    monkeypatch.setenv("DOCKER_AUTH_CONFIG", "fixture-docker-auth")
    monkeypatch.setenv("SESSION_COOKIE", "fixture-session-cookie")
    client = _client("environment")
    client.start()
    try:
        result = client.initialize(CLIENT_INFO)
        assert result.codex_version == "0.144.5"
    finally:
        client.close()


def test_non_allowlisted_environment_can_be_explicitly_overridden() -> None:
    client = AppServerClient(
        command=(sys.executable, "-u", str(FAKE_SERVER), "explicit-environment"),
        env={"DATABASE_URL": "fixture-database-url"},
    )
    client.start()
    try:
        result = client.initialize(CLIENT_INFO)
        assert result.codex_version == "0.144.5"
    finally:
        client.close()


def test_sensitive_environment_override_requires_explicit_opt_in() -> None:
    with pytest.raises(ValueError, match="requires opt-in"):
        AppServerClient(
            command=(sys.executable, "-c", "pass"),
            env={"GH_TOKEN": "fixture-token"},
        )


def test_sensitive_environment_override_can_be_explicitly_opted_in() -> None:
    client = AppServerClient(
        command=(sys.executable, "-u", str(FAKE_SERVER), "sensitive-environment"),
        env={"CODEX_ACCESS_TOKEN": "fixture-token"},
        allow_sensitive_env=True,
    )
    client.start()
    try:
        result = client.initialize(CLIENT_INFO)
        assert result.codex_version == "0.144.5"
    finally:
        client.close()


def test_command_resolution_uses_platform_native_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_names: list[str] = []
    native_path = (
        r"C:\fixture\codex.exe"
        if client_module.os.name == "nt"
        else "/fixture/codex"
    )

    def fake_which(name: str) -> str:
        requested_names.append(name)
        return native_path

    monkeypatch.setattr(client_module.shutil, "which", fake_which)

    command = codex_app_server_command()

    expected_name = "codex.exe" if client_module.os.name == "nt" else "codex"
    assert requested_names == [expected_name]
    assert command == (native_path, "app-server", "--stdio")


def test_explicit_executable_path_must_be_absolute() -> None:
    with pytest.raises(AppServerStartError, match="must be absolute"):
        codex_app_server_command("codex")


def test_windows_explicit_executable_rejects_command_wrapper() -> None:
    if client_module.os.name != "nt":
        pytest.skip("Windows固有のcommand validation")

    with pytest.raises(AppServerStartError, match="native .exe"):
        codex_app_server_command(r"C:\fixture\codex.cmd")


def test_windows_command_resolution_does_not_fallback_to_wrapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if client_module.os.name != "nt":
        pytest.skip("Windows固有のcommand resolution")
    requested_names: list[str] = []

    def fake_which(name: str) -> str | None:
        requested_names.append(name)
        return None if name == "codex.exe" else "codex.cmd"

    monkeypatch.setattr(client_module.shutil, "which", fake_which)

    with pytest.raises(AppServerStartError):
        codex_app_server_command()

    assert requested_names == ["codex.exe"]


def test_request_before_initialize_is_rejected() -> None:
    client = _client("normal")
    client.start()
    try:
        with pytest.raises(AppServerStateError):
            client.request("model/list", {})
    finally:
        client.close()


def test_initialize_only_runs_once() -> None:
    client = _client("normal")
    client.start()
    client.initialize(CLIENT_INFO)
    try:
        with pytest.raises(AppServerStateError):
            client.initialize(CLIENT_INFO)
    finally:
        client.close()


@pytest.mark.parametrize("mode", ["invalid-json", "malformed-response", "wrong-id"])
def test_protocol_errors_fail_the_pending_initialize(mode: str) -> None:
    client = _client(mode)
    client.start()
    try:
        with pytest.raises(AppServerProtocolError):
            client.initialize(CLIENT_INFO)
        assert client.state is AppServerState.FAILED
    finally:
        client.close()


def test_early_eof_fails_the_pending_initialize() -> None:
    client = _client("early-eof")
    client.start()
    try:
        with pytest.raises(AppServerClosedError):
            client.initialize(CLIENT_INFO)
    finally:
        client.close()


def test_next_message_raises_closed_error_immediately_on_stdout_eof() -> None:
    client = _client("eof-after-initialized", request_timeout=2.0)
    client.start()
    try:
        client.initialize(CLIENT_INFO)
        started = time.monotonic()

        with pytest.raises(AppServerClosedError):
            client.next_message(timeout=2.0)

        assert time.monotonic() - started < 1.0
    finally:
        client.close()


def test_next_message_reraises_protocol_error_without_waiting_for_timeout() -> None:
    client = _client(
        "invalid-after-initialized",
        request_timeout=2.0,
        shutdown_timeout=0.05,
    )
    client.start()
    try:
        client.initialize(CLIENT_INFO)
        started = time.monotonic()

        with pytest.raises(AppServerProtocolError, match="non-JSON"):
            client.next_message(timeout=2.0)

        assert time.monotonic() - started < 1.0
    finally:
        client.close()


def test_close_wakes_a_blocked_next_message_consumer() -> None:
    client = _client("normal", request_timeout=2.0)
    waiting = threading.Event()
    errors: list[Exception] = []
    client.start()
    client.initialize(CLIENT_INFO)

    def wait_for_message() -> None:
        waiting.set()
        try:
            client.next_message(timeout=2.0)
        except Exception as error:
            errors.append(error)

    consumer = threading.Thread(target=wait_for_message)
    consumer.start()
    assert waiting.wait(1.0)
    time.sleep(0.05)

    started = time.monotonic()
    client.close()
    consumer.join(timeout=1.0)

    assert not consumer.is_alive()
    assert time.monotonic() - started < 1.0
    assert len(errors) == 1
    assert isinstance(errors[0], AppServerClosedError)


def test_close_wakes_all_blocked_next_message_consumers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client("normal", request_timeout=0.5)
    entered_get = threading.Barrier(3)
    errors: list[Exception] = []
    client.start()
    client.initialize(CLIENT_INFO)
    original_get = client._inbound.get

    def gated_get(block: bool = True, timeout: float | None = None) -> object:
        entered_get.wait(timeout=1.0)
        return original_get(block=block, timeout=timeout)

    monkeypatch.setattr(client._inbound, "get", gated_get)

    def wait_for_message() -> None:
        try:
            client.next_message(timeout=0.5)
        except Exception as error:
            errors.append(error)

    consumers = [threading.Thread(target=wait_for_message) for _ in range(2)]
    for consumer in consumers:
        consumer.start()
    entered_get.wait(timeout=1.0)

    started = time.monotonic()
    client.close()
    for consumer in consumers:
        consumer.join(timeout=1.0)

    assert all(not consumer.is_alive() for consumer in consumers)
    assert time.monotonic() - started < 0.25
    assert len(errors) == 2
    assert all(isinstance(error, AppServerClosedError) for error in errors)


@pytest.mark.parametrize(
    ("mode", "constant_name", "stream_name"),
    [
        ("oversized-stdout", "_MAX_STDOUT_LINE_CHARS", "stdout"),
        ("oversized-stderr", "_MAX_STDERR_LINE_CHARS", "stderr"),
    ],
)
def test_oversized_child_line_is_fatal_and_process_is_reaped(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    constant_name: str,
    stream_name: str,
) -> None:
    monkeypatch.setattr(client_module, constant_name, 128)
    client = _client(mode, request_timeout=1.0, shutdown_timeout=0.05)
    client.start()

    with pytest.raises(AppServerProtocolError, match=f"{stream_name} line exceeds"):
        client.initialize(CLIENT_INFO)

    shutdown = client.close()

    assert shutdown.forced is True
    assert client.is_running is False
    assert client.return_code is not None


def test_request_timeout_then_forced_shutdown_reaps_process() -> None:
    client = _client("timeout", request_timeout=0.05, shutdown_timeout=0.05)
    client.start()
    with pytest.raises(AppServerTimeoutError):
        client.initialize(CLIENT_INFO)

    shutdown = client.close()

    assert shutdown.forced is True
    assert client.is_running is False
    assert client.return_code is not None


def test_ready_request_timeout_is_terminal_and_reaps_delayed_response_process() -> None:
    client = _client(
        "delayed-request-response",
        request_timeout=1.0,
        shutdown_timeout=0.5,
    )
    client.start()
    try:
        client.initialize(CLIENT_INFO)

        with pytest.raises(AppServerTimeoutError):
            client.request("model/list", timeout=0.05)

        assert client.state is AppServerState.FAILED
        assert client.is_running is False
    finally:
        client.close()


def test_shutdown_surfaces_kill_failure_without_unbounded_wait() -> None:
    class FakeStdin:
        closed = False

        def close(self) -> None:
            self.closed = True

    class KillFailureProcess:
        stdin = FakeStdin()
        returncode: int | None = None

        def wait(self, timeout: float) -> None:
            raise subprocess.TimeoutExpired(cmd="fixture", timeout=timeout)

        def kill(self) -> None:
            raise OSError("synthetic kill failure")

        def poll(self) -> None:
            return None

    client = AppServerClient(command=("fixture",), shutdown_timeout=0.01)
    client._process = KillFailureProcess()  # type: ignore[assignment]
    client._state = AppServerState.RUNNING
    started = time.monotonic()

    with pytest.raises(AppServerShutdownError, match="failed to kill"):
        client.close()

    assert time.monotonic() - started < 0.5


def test_shutdown_surfaces_post_kill_timeout_without_unbounded_wait() -> None:
    class FakeStdin:
        closed = False

        def close(self) -> None:
            self.closed = True

    class PostKillTimeoutProcess:
        stdin = FakeStdin()
        returncode: int | None = None
        killed = False

        def wait(self, timeout: float) -> None:
            raise subprocess.TimeoutExpired(cmd="fixture", timeout=timeout)

        def kill(self) -> None:
            self.killed = True

        def poll(self) -> None:
            return None

    process = PostKillTimeoutProcess()
    client = AppServerClient(command=("fixture",), shutdown_timeout=0.01)
    client._process = process  # type: ignore[assignment]
    client._state = AppServerState.RUNNING
    started = time.monotonic()

    with pytest.raises(AppServerShutdownError, match="did not exit after kill"):
        client.close()

    assert process.killed is True
    assert time.monotonic() - started < 0.5


def test_shutdown_kills_a_process_that_ignores_stdin_eof() -> None:
    client = _client("ignore-eof", shutdown_timeout=0.05)
    client.start()
    client.initialize(CLIENT_INFO)

    shutdown = client.close()

    assert shutdown.forced is True
    assert client.is_running is False
    assert client.return_code is not None


def test_stderr_is_reduced_to_counts_without_retaining_content() -> None:
    client = _client("stderr")
    client.start()
    client.initialize(CLIENT_INFO)
    client.close()

    summary = client.stderr_summary

    assert summary.total_lines == 2
    assert summary.nonempty_lines == 2
    assert summary.warning_lines == 1
    assert summary.error_lines == 1
    assert not hasattr(summary, "lines")
    assert "secret" not in repr(summary).lower()


def test_version_mismatch_does_not_send_initialized() -> None:
    client = _client("version-mismatch")
    client.start()
    try:
        with pytest.raises(AppServerVersionMismatch):
            client.initialize(CLIENT_INFO)
        assert client.state is AppServerState.FAILED
    finally:
        client.close()
