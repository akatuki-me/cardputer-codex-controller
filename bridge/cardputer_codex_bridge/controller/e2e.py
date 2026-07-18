from __future__ import annotations

import shutil
import tempfile
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from cardputer_codex_bridge.app_server import (
    AppServerClient,
    AppServerOperations,
    ClientInfo,
    ThreadStartOptions,
    codex_app_server_command,
)
from cardputer_codex_bridge.device_link import SerialProvider, SyntheticSerialProvider
from cardputer_codex_bridge.models import (
    ThreadId,
    TurnCompletedEvent,
    TurnId,
    TurnStartedEvent,
    TurnStartResult,
)

from .device_session import DeviceControllerSession
from .state import ControllerState


class PortSelectionError(ValueError):
    """Machine-localなport選択が不正。"""


class CodexOperationsInterruptAdapter:
    def __init__(self, operations: AppServerOperations) -> None:
        self._operations = operations
        self._lock = threading.Lock()
        self._forward_count = 0

    @property
    def forward_count(self) -> int:
        with self._lock:
            return self._forward_count

    def interrupt(self, thread_id: str, turn_id: str) -> None:
        self._operations.interrupt_turn(ThreadId(thread_id), TurnId(turn_id))
        with self._lock:
            self._forward_count += 1


def resolve_port(*, explicit_port: str | None, handle_file: Path | None) -> str:
    """明示値またはGit管理外のlocal handleからportを解決し、値は表示しない。"""
    if (explicit_port is None) == (handle_file is None):
        raise PortSelectionError("exactly one port source is required")
    if handle_file is not None:
        try:
            raw = handle_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise PortSelectionError("local port handle could not be read") from exc
        lines = raw.splitlines()
        if len(lines) != 1:
            raise PortSelectionError("local port handle must contain one line")
        selected = lines[0].strip()
    else:
        assert explicit_port is not None
        selected = explicit_port.strip()
    if not selected or len(selected) > 512 or "\x00" in selected:
        raise PortSelectionError("selected port is invalid")
    return selected


def run_e2e_dry_run(output: TextIO) -> None:
    """選択後の配線だけを確認し、serialもCodexも起動しない。"""
    _pass(output, "transport_selection")
    output.write("serial_io_opened false\n")
    output.write("codex_connection N/A\n")
    _pass(output, "e2e_dry_run")


def run_serial_e2e(
    output: TextIO,
    *,
    port: str,
    provider: SerialProvider,
    synthetic_device: SyntheticSerialProvider | None = None,
    command: Sequence[str] | None = None,
    step_timeout: float = 30.0,
) -> None:
    """実Codexとserial consumerをつなぎ、device interruptを一度だけ転送する。"""
    temporary = Path(tempfile.mkdtemp(prefix="cardputer-e2e-"))
    client = AppServerClient(
        command=tuple(command) if command is not None else codex_app_server_command(),
        request_timeout=step_timeout,
        shutdown_timeout=10.0,
    )
    session: DeviceControllerSession | None = None
    shutdown = None
    client.start()
    _pass(output, "app_server_start")
    try:
        client.initialize(
            ClientInfo(
                name="cardputer-codex-controller",
                title="Cardputer Codex Controller",
                version="0.0.0",
            )
        )
        _pass(output, "initialize")
        operations = AppServerOperations(client)
        thread = operations.start_thread(
            ThreadStartOptions(
                cwd=temporary,
                approval_policy="never",
                sandbox="read-only",
                ephemeral=True,
            )
        )
        _pass(output, "thread_start")
        if not operations.list_models(limit=10).models:
            raise RuntimeError("model list is empty")
        _pass(output, "model_list")

        state = ControllerState()
        adapter = CodexOperationsInterruptAdapter(operations)
        session = DeviceControllerSession(
            state=state,
            adapter=adapter,
            provider=provider,
            port=port,
        )
        session.start()
        if not session.wait_connected(step_timeout):
            raise TimeoutError("serial link did not connect")
        _pass(output, "serial_link")
        if synthetic_device is not None:
            synthetic_device.inject({"t": "hello", "seq": 1, "proto": 1})
        if not session.wait_for_device_hello(step_timeout):
            raise TimeoutError("device hello was not received")
        _pass(output, "device_hello")

        turns: list[TurnStartResult] = []
        start_errors: list[BaseException] = []

        def start_turn() -> None:
            try:
                turns.append(
                    operations.start_turn(
                        thread.thread_id,
                        "Run a shell wait for 60 seconds, then reply with the single word READY.",
                    )
                )
            except BaseException as error:
                start_errors.append(error)

        starter = threading.Thread(target=start_turn, name="cardputer-e2e-turn")
        starter.start()
        started = _wait_for_started(client, operations, timeout=step_timeout)
        state.turn_started(
            0,
            thread_id=str(started.thread_id),
            turn_id=str(started.turn_id),
        )
        _pass(output, "turn_started")
        if not session.send_snapshot():
            raise RuntimeError("active turn snapshot was not sent")
        _pass(output, "state_snapshot")

        operations.steer_turn(
            thread.thread_id,
            started.turn_id,
            "Reply with the single word READY.",
        )
        _pass(output, "turn_steer")
        if synthetic_device is not None:
            synthetic_device.inject({"t": "interrupt", "seq": 2})
            synthetic_device.inject({"t": "interrupt", "seq": 3})
        if not session.wait_for_interrupt(step_timeout):
            raise TimeoutError("device interrupt was not forwarded")
        _pass(output, "device_interrupt")
        if synthetic_device is not None and not session.wait_for_interrupt_messages(
            2,
            step_timeout,
        ):
            raise TimeoutError("duplicate interrupt fixture was not consumed")
        if adapter.forward_count != 1:
            raise RuntimeError("device interrupt was not forwarded exactly once")
        _pass(output, "interrupt_forwarded_once")

        starter.join(timeout=step_timeout)
        if starter.is_alive() or start_errors or len(turns) != 1:
            raise RuntimeError("turn/start did not finish cleanly")
        completed = _wait_for_completed(client, operations, timeout=step_timeout)
        if completed.thread_id != thread.thread_id or completed.turn_id != turns[0].turn_id:
            raise RuntimeError("completion did not match the active turn")
        state.turn_completed(0)
        if not session.send_snapshot():
            raise RuntimeError("completed turn snapshot was not sent")
        _pass(output, "turn_completed")
    finally:
        if session is not None:
            session.close()
        shutdown = client.close()
        shutil.rmtree(temporary, ignore_errors=True)
    if shutdown.forced or shutdown.exit_code != 0:
        raise RuntimeError("app-server did not close cleanly")
    _pass(output, "serial_link_close")
    _pass(output, "app_server_close")
    _pass(output, "e2e")


def _wait_for_started(
    client: AppServerClient,
    operations: AppServerOperations,
    *,
    timeout: float,
) -> TurnStartedEvent:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        event = operations.handle_notification(client.next_message(timeout=timeout))
        if isinstance(event, TurnStartedEvent):
            return event
    raise TimeoutError("turn/started was not received")


def _wait_for_completed(
    client: AppServerClient,
    operations: AppServerOperations,
    *,
    timeout: float,
) -> TurnCompletedEvent:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        event = operations.handle_notification(client.next_message(timeout=timeout))
        if isinstance(event, TurnCompletedEvent):
            return event
    raise TimeoutError("turn/completed was not received")


def _pass(output: TextIO, step: str) -> None:
    output.write(f"{step} PASS\n")
    output.flush()


__all__ = [
    "CodexOperationsInterruptAdapter",
    "PortSelectionError",
    "resolve_port",
    "run_e2e_dry_run",
    "run_serial_e2e",
]
