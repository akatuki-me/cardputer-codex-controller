from __future__ import annotations

import os
import queue
import re
import shutil
import subprocess
import threading
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import TextIO, cast

from .errors import (
    AppServerClosedError,
    AppServerError,
    AppServerProtocolError,
    AppServerResponseError,
    AppServerShutdownError,
    AppServerStartError,
    AppServerStateError,
    AppServerTimeoutError,
    AppServerVersionMismatch,
)
from .protocol import decode_message, encode_message
from .types import (
    AppServerState,
    ClientInfo,
    InitializeResult,
    JsonObject,
    JsonValue,
    RequestId,
    ShutdownResult,
    StderrSummary,
)

_CODEX_VERSION_PATTERN = re.compile(r"/(?P<version>\d+\.\d+\.\d+)(?:[ )]|$)")
SUPPORTED_CODEX_VERSIONS = ("0.144.5", "0.144.6")
_INHERITED_ENV_NAMES = {
    "APPDATA",
    "CODEX_HOME",
    "COMSPEC",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LOCALAPPDATA",
    "PATH",
    "PATHEXT",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "WINDIR",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_RUNTIME_DIR",
}
_SENSITIVE_ENV_SUFFIXES = ("_API_KEY", "_PASSWORD", "_SECRET", "_TOKEN")
_SENSITIVE_ENV_NAMES = {
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "CODEX_ACCESS_TOKEN",
    "CODEX_API_KEY",
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "OPENAI_API_KEY",
}
_MAX_STDOUT_LINE_CHARS = 16 * 1024 * 1024
_MAX_STDERR_LINE_CHARS = 64 * 1024


def codex_app_server_command(executable: str | None = None) -> tuple[str, ...]:
    resolved = executable
    if resolved is None:
        executable_name = "codex.exe" if os.name == "nt" else "codex"
        resolved = shutil.which(executable_name)
    if resolved is None:
        raise AppServerStartError("Codex executable was not found")
    resolved_path = Path(resolved)
    if not resolved_path.is_absolute():
        raise AppServerStartError("Codex executable path must be absolute")
    if os.name == "nt" and resolved_path.suffix.casefold() != ".exe":
        raise AppServerStartError("Codex executable must be a native .exe on Windows")
    return (str(resolved_path), "app-server", "--stdio")


def _is_sensitive_environment_name(name: str) -> bool:
    normalized = name.upper()
    return normalized in _SENSITIVE_ENV_NAMES or normalized.endswith(_SENSITIVE_ENV_SUFFIXES)


def _build_child_environment(
    overrides: Mapping[str, str] | None,
    *,
    allow_sensitive_overrides: bool,
) -> dict[str, str]:
    child_environment = {
        name: value
        for name, value in os.environ.items()
        if name.upper() in _INHERITED_ENV_NAMES
    }
    if overrides is None:
        return child_environment
    for name, value in overrides.items():
        if _is_sensitive_environment_name(name) and not allow_sensitive_overrides:
            raise ValueError(f"sensitive child environment variable requires opt-in: {name}")
        child_environment[name] = value
    return child_environment


class AppServerClient:
    def __init__(
        self,
        *,
        command: Sequence[str],
        expected_codex_versions: Sequence[str] = SUPPORTED_CODEX_VERSIONS,
        request_timeout: float = 10.0,
        shutdown_timeout: float = 2.0,
        cwd: str | Path | None = None,
        env: Mapping[str, str] | None = None,
        allow_sensitive_env: bool = False,
    ) -> None:
        if not command or any(not part for part in command):
            raise ValueError("command must contain non-empty arguments")
        if request_timeout <= 0 or shutdown_timeout <= 0:
            raise ValueError("timeouts must be positive")
        checked_versions = tuple(expected_codex_versions)
        if not checked_versions or any(not version for version in checked_versions):
            raise ValueError("expected_codex_versions must contain non-empty versions")

        self._command = tuple(command)
        self._expected_codex_versions = checked_versions
        self._request_timeout = request_timeout
        self._shutdown_timeout = shutdown_timeout
        self._cwd = cwd
        self._env = _build_child_environment(
            env,
            allow_sensitive_overrides=allow_sensitive_env,
        )

        self._state = AppServerState.NEW
        self._state_lock = threading.RLock()
        self._lifecycle_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._stderr_lock = threading.Lock()
        self._process_shutdown_lock = threading.Lock()

        self._process: subprocess.Popen[str] | None = None
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._next_request_id = 1
        self._pending: dict[RequestId, queue.Queue[JsonObject | AppServerError]] = {}
        self._inbound: queue.Queue[JsonObject | AppServerError] = queue.Queue()
        self._fatal_error: AppServerError | None = None
        self._shutdown_failure: AppServerShutdownError | None = None
        self._process_shutdown_result: ShutdownResult | None = None
        self._shutdown_result: ShutdownResult | None = None

        self._stderr_total_lines = 0
        self._stderr_nonempty_lines = 0
        self._stderr_warning_lines = 0
        self._stderr_error_lines = 0

    @property
    def state(self) -> AppServerState:
        with self._state_lock:
            return self._state

    @property
    def is_running(self) -> bool:
        process = self._process
        return process is not None and process.poll() is None

    @property
    def return_code(self) -> int | None:
        process = self._process
        return None if process is None else process.poll()

    @property
    def stderr_summary(self) -> StderrSummary:
        with self._stderr_lock:
            return StderrSummary(
                total_lines=self._stderr_total_lines,
                nonempty_lines=self._stderr_nonempty_lines,
                warning_lines=self._stderr_warning_lines,
                error_lines=self._stderr_error_lines,
            )

    def start(self) -> None:
        with self._lifecycle_lock:
            self._start_locked()

    def _start_locked(self) -> None:
        with self._state_lock:
            if self._state is not AppServerState.NEW:
                raise AppServerStateError(f"cannot start from state {self._state.value}")
        creation_flags = 0
        if os.name == "nt":
            creation_flags = cast(int, getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            process = subprocess.Popen(
                self._command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                cwd=self._cwd,
                env=self._env,
                shell=False,
                creationflags=creation_flags,
            )
        except OSError as exc:
            with self._state_lock:
                self._state = AppServerState.FAILED
            raise AppServerStartError("failed to start app-server process") from exc

        if process.stdout is None or process.stderr is None or process.stdin is None:
            process.kill()
            process.wait()
            with self._state_lock:
                self._state = AppServerState.FAILED
            raise AppServerStartError("app-server stdio pipes were not created")

        self._process = process
        with self._state_lock:
            self._state = AppServerState.RUNNING
        self._stdout_thread = threading.Thread(
            target=self._read_stdout,
            args=(process.stdout,),
            name="app-server-stdout",
            daemon=True,
        )
        self._stderr_thread = threading.Thread(
            target=self._read_stderr,
            args=(process.stderr,),
            name="app-server-stderr",
            daemon=True,
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

    def initialize(self, client_info: ClientInfo) -> InitializeResult:
        with self._lifecycle_lock:
            return self._initialize_locked(client_info)

    def _initialize_locked(self, client_info: ClientInfo) -> InitializeResult:
        with self._state_lock:
            self._raise_fatal_locked()
            if self._state is not AppServerState.RUNNING:
                raise AppServerStateError(f"cannot initialize from state {self._state.value}")
            self._state = AppServerState.INITIALIZING

        params: JsonObject = {
            "clientInfo": client_info.as_json(),
            "capabilities": {"experimentalApi": False},
        }
        try:
            value = self._send_request(
                "initialize",
                params,
                allowed_states=(AppServerState.INITIALIZING,),
            )
            result = self._parse_initialize_result(value)
            self._write_message({"method": "initialized"})
            with self._state_lock:
                self._raise_fatal_locked()
                self._state = AppServerState.READY
            return result
        except AppServerError:
            with self._state_lock:
                if self._state not in (AppServerState.CLOSING, AppServerState.CLOSED):
                    self._state = AppServerState.FAILED
            raise

    def request(
        self,
        method: str,
        params: JsonObject | None = None,
        *,
        timeout: float | None = None,
    ) -> JsonValue:
        return self._send_request(
            method,
            params,
            timeout=timeout,
            allowed_states=(AppServerState.READY,),
        )

    def notify(self, method: str, params: JsonObject | None = None) -> None:
        self._require_state((AppServerState.READY,))
        message: JsonObject = {"method": method}
        if params is not None:
            message["params"] = params
        self._write_message(message)

    def respond(self, request_id: RequestId, result: JsonObject) -> None:
        """Send a response to a request initiated by app-server."""
        if not isinstance(request_id, (int, str)) or isinstance(request_id, bool):
            raise ValueError("request_id must be an integer or string")
        self._require_state((AppServerState.READY,))
        self._write_message({"id": request_id, "result": result})

    def next_message(self, *, timeout: float | None = None) -> JsonObject:
        with self._state_lock:
            self._raise_fatal_locked()
            if self._state is AppServerState.NEW:
                raise AppServerStateError("cannot receive messages before app-server start")
            if self._state in (AppServerState.CLOSING, AppServerState.CLOSED):
                raise AppServerClosedError("app-server client is closed")
        wait_timeout = self._request_timeout if timeout is None else timeout
        try:
            message = self._inbound.get(timeout=wait_timeout)
        except queue.Empty as exc:
            with self._state_lock:
                self._raise_fatal_locked()
                if self._state in (AppServerState.CLOSING, AppServerState.CLOSED):
                    raise AppServerClosedError("app-server client is closed") from exc
            raise AppServerTimeoutError("timed out waiting for an app-server message") from exc
        if isinstance(message, AppServerError):
            self._inbound.put(message)
            raise message
        return message

    def close(self) -> ShutdownResult:
        with self._lifecycle_lock:
            return self._close_locked()

    def _close_locked(self) -> ShutdownResult:
        with self._state_lock:
            if self._shutdown_result is not None:
                return self._shutdown_result
            if self._state is AppServerState.NEW:
                self._state = AppServerState.CLOSED
                self._shutdown_result = ShutdownResult(exit_code=None, forced=False)
                return self._shutdown_result
            self._state = AppServerState.CLOSING

        close_error = AppServerClosedError("app-server client is closing")
        self._fail_pending(close_error)
        self._inbound.put(close_error)
        result = self._shutdown_process()

        self._join_reader_threads()
        with self._state_lock:
            self._state = AppServerState.CLOSED
            self._shutdown_result = result
        return result

    def __enter__(self) -> AppServerClient:
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def _send_request(
        self,
        method: str,
        params: JsonObject | None,
        *,
        timeout: float | None = None,
        allowed_states: tuple[AppServerState, ...],
    ) -> JsonValue:
        self._require_state(allowed_states)
        with self._pending_lock:
            request_id = self._next_request_id
            self._next_request_id += 1
            response_queue: queue.Queue[JsonObject | AppServerError] = queue.Queue(maxsize=1)
            self._pending[request_id] = response_queue

        message: JsonObject = {"id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        try:
            self._write_message(message)
        except AppServerError:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise

        wait_timeout = self._request_timeout if timeout is None else timeout
        try:
            response = response_queue.get(timeout=wait_timeout)
        except queue.Empty as exc:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            timeout_error = AppServerTimeoutError(
                f"app-server request {request_id!r} timed out"
            )
            self._set_fatal(timeout_error)
            raise timeout_error from exc
        if isinstance(response, AppServerError):
            raise response
        if "error" in response:
            error = response["error"]
            if not isinstance(error, dict):
                protocol_error = AppServerProtocolError("response error must be an object")
                self._set_fatal(protocol_error)
                raise protocol_error
            code = error.get("code")
            if not isinstance(code, int) or isinstance(code, bool):
                protocol_error = AppServerProtocolError("response error code must be an integer")
                self._set_fatal(protocol_error)
                raise protocol_error
            raise AppServerResponseError(request_id=request_id, code=code)
        return response["result"]

    def _write_message(self, message: JsonObject) -> None:
        process = self._process
        if process is None or process.stdin is None or process.poll() is not None:
            raise AppServerClosedError("app-server stdin is not available")
        encoded = encode_message(message)
        with self._write_lock:
            try:
                process.stdin.write(encoded)
                process.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as exc:
                error = AppServerClosedError("app-server stdin closed while writing")
                self._set_fatal(error)
                raise error from exc

    def _read_stdout(self, stream: TextIO) -> None:
        while True:
            try:
                line = _read_bounded_line(
                    stream,
                    limit=_MAX_STDOUT_LINE_CHARS,
                    stream_name="stdout",
                )
            except AppServerProtocolError as error:
                self._set_fatal(error)
                return
            if line == "":
                with self._state_lock:
                    closing = self._state in (AppServerState.CLOSING, AppServerState.CLOSED)
                if not closing:
                    self._set_fatal(
                        AppServerClosedError("app-server stdout closed before client shutdown")
                    )
                return
            try:
                message = decode_message(line)
                self._dispatch_message(message)
            except AppServerError as error:
                self._set_fatal(error)
                return

    def _read_stderr(self, stream: TextIO) -> None:
        while True:
            try:
                line = _read_bounded_line(
                    stream,
                    limit=_MAX_STDERR_LINE_CHARS,
                    stream_name="stderr",
                )
            except AppServerProtocolError as error:
                self._set_fatal(error)
                return
            if line == "":
                return
            normalized = line.strip().casefold()
            with self._stderr_lock:
                self._stderr_total_lines += 1
                if normalized:
                    self._stderr_nonempty_lines += 1
                if "warn" in normalized:
                    self._stderr_warning_lines += 1
                if any(token in normalized for token in ("error", "fatal", "panic")):
                    self._stderr_error_lines += 1

    def _dispatch_message(self, message: JsonObject) -> None:
        if "method" in message:
            if not isinstance(message["method"], str):
                raise AppServerProtocolError("message method must be a string")
            self._inbound.put(message)
            return
        if "id" not in message:
            raise AppServerProtocolError("response is missing an id")
        response_id = message["id"]
        if not isinstance(response_id, (int, str)) or isinstance(response_id, bool):
            raise AppServerProtocolError("response id must be an integer or string")
        has_result = "result" in message
        has_error = "error" in message
        if has_result == has_error:
            raise AppServerProtocolError("response must contain exactly one of result or error")
        with self._pending_lock:
            response_queue = self._pending.pop(response_id, None)
        if response_queue is None:
            raise AppServerProtocolError("response id does not match a pending request")
        response_queue.put(message)

    def _parse_initialize_result(self, value: JsonValue) -> InitializeResult:
        if not isinstance(value, dict):
            raise AppServerProtocolError("initialize result must be an object")
        codex_home = value.get("codexHome")
        platform_family = value.get("platformFamily")
        platform_os = value.get("platformOs")
        user_agent = value.get("userAgent")
        if not all(
            isinstance(item, str)
            for item in (codex_home, platform_family, platform_os, user_agent)
        ):
            raise AppServerProtocolError("initialize result is missing required string fields")
        assert isinstance(platform_family, str)
        assert isinstance(platform_os, str)
        assert isinstance(user_agent, str)
        match = _CODEX_VERSION_PATTERN.search(user_agent)
        if match is None:
            raise AppServerProtocolError("initialize userAgent has no Codex version")
        codex_version = match.group("version")
        if codex_version not in self._expected_codex_versions:
            raise AppServerVersionMismatch(
                expected=", ".join(self._expected_codex_versions),
                actual=codex_version,
            )
        return InitializeResult(
            codex_version=codex_version,
            platform_family=platform_family,
            platform_os=platform_os,
            user_agent=user_agent,
        )

    def _require_state(self, allowed_states: tuple[AppServerState, ...]) -> None:
        with self._state_lock:
            self._raise_fatal_locked()
            if self._state not in allowed_states:
                allowed = ", ".join(state.value for state in allowed_states)
                raise AppServerStateError(
                    f"operation requires state {allowed}; current state is {self._state.value}"
                )

    def _raise_fatal_locked(self) -> None:
        if self._shutdown_failure is not None:
            raise self._shutdown_failure
        if self._fatal_error is not None:
            raise self._fatal_error

    def _set_fatal(self, error: AppServerError) -> None:
        with self._state_lock:
            if self._state in (AppServerState.CLOSING, AppServerState.CLOSED):
                return
            if self._fatal_error is not None:
                return
            self._fatal_error = error
            self._state = AppServerState.FAILED
        self._fail_pending(error)
        self._inbound.put(error)
        try:
            self._shutdown_process()
        except AppServerShutdownError as shutdown_error:
            with self._state_lock:
                self._shutdown_failure = shutdown_error

    def _fail_pending(self, error: AppServerError) -> None:
        with self._pending_lock:
            pending = tuple(self._pending.values())
            self._pending.clear()
        for response_queue in pending:
            response_queue.put(error)

    def _shutdown_process(self) -> ShutdownResult:
        with self._process_shutdown_lock:
            if self._process_shutdown_result is not None:
                return self._process_shutdown_result
            process = self._process
            if process is None:
                result = ShutdownResult(exit_code=None, forced=False)
            else:
                if process.stdin is not None and not process.stdin.closed:
                    with suppress(OSError):
                        process.stdin.close()
                forced = False
                try:
                    process.wait(timeout=self._shutdown_timeout)
                except subprocess.TimeoutExpired:
                    forced = True
                    try:
                        process.kill()
                    except OSError as exc:
                        if process.poll() is None:
                            raise AppServerShutdownError(
                                "failed to kill app-server after shutdown timeout"
                            ) from exc
                    try:
                        process.wait(timeout=self._shutdown_timeout)
                    except subprocess.TimeoutExpired as exc:
                        raise AppServerShutdownError(
                            "app-server did not exit after kill"
                        ) from exc
                    except OSError as exc:
                        raise AppServerShutdownError(
                            "failed while waiting for app-server exit after kill"
                        ) from exc
                except OSError as exc:
                    raise AppServerShutdownError(
                        "failed while waiting for app-server exit"
                    ) from exc
                result = ShutdownResult(exit_code=process.returncode, forced=forced)
            self._process_shutdown_result = result
            with self._state_lock:
                self._shutdown_failure = None
            return result

    def _join_reader_threads(self) -> None:
        for thread in (self._stdout_thread, self._stderr_thread):
            if thread is not None:
                thread.join(timeout=1.0)
        process = self._process
        if process is not None:
            for stream in (process.stdout, process.stderr):
                if stream is not None and not stream.closed:
                    with suppress(OSError):
                        stream.close()


def _read_bounded_line(stream: TextIO, *, limit: int, stream_name: str) -> str:
    line = stream.readline(limit + 1)
    if len(line) > limit:
        raise AppServerProtocolError(
            f"app-server {stream_name} line exceeds {limit} character limit"
        )
    return line
