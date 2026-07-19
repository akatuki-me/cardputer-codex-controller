from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TextIO

from cardputer_codex_bridge.app_server import (
    AppServerClient,
    AppServerClosedError,
    AppServerOperations,
    AppServerTimeoutError,
    ClientInfo,
    JsonObject,
    ThreadStartOptions,
    codex_app_server_command,
)
from cardputer_codex_bridge.approval import (
    ApprovalCoordinator,
    ApprovalResolved,
    CommandApprovalRequest,
    FileChangeApprovalRequest,
    HostApprovalConsole,
)
from cardputer_codex_bridge.device_link import SerialProvider, SyntheticSerialProvider
from cardputer_codex_bridge.models import ThreadId, TurnCompletedEvent, TurnId, TurnStartedEvent

from .device_session import DeviceControllerSession
from .state import ControllerState

_APP_SERVER_SHUTDOWN_TIMEOUT_SECONDS = 60.0


class _OperationsInterruptAdapter:
    def __init__(
        self,
        operations: AppServerOperations,
        emit: Callable[[str], None],
    ) -> None:
        self._operations = operations
        self._emit = emit

    def interrupt(self, thread_id: str, turn_id: str) -> None:
        self._operations.interrupt_turn(ThreadId(thread_id), TurnId(turn_id))
        self._emit("device_interrupt PASS\n")


class _EventPump:
    def __init__(
        self,
        *,
        client: AppServerClient,
        operations: AppServerOperations,
        coordinator: ApprovalCoordinator,
        console: HostApprovalConsole,
        state: ControllerState,
        session: DeviceControllerSession,
        thread_id: ThreadId,
        emit: Callable[[str], None],
    ) -> None:
        self._client = client
        self._operations = operations
        self._coordinator = coordinator
        self._console = console
        self._state = state
        self._session = session
        self._thread_id = thread_id
        self._emit = emit
        self._stop = threading.Event()
        self._errors: queue.Queue[BaseException] = queue.Queue(maxsize=1)
        self._thread = threading.Thread(
            target=self._run,
            name="cardputer-controller-events",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._stop.set()

    def join(self) -> None:
        self._thread.join(timeout=2.0)
        if self._thread.is_alive():
            raise RuntimeError("controller event pump did not stop")

    def raise_if_failed(self) -> None:
        try:
            error = self._errors.get_nowait()
        except queue.Empty:
            return
        raise error

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    message = self._client.next_message(timeout=0.2)
                except AppServerTimeoutError:
                    continue
                self._handle(message)
        except AppServerClosedError as error:
            if not self._stop.is_set():
                self._record_error(error)
        except BaseException as error:
            self._record_error(error)

    def _handle(self, message: JsonObject) -> None:
        approval_event = self._coordinator.handle_message(message)
        if isinstance(approval_event, (CommandApprovalRequest, FileChangeApprovalRequest)):
            if approval_event.thread_id == self._thread_id:
                self._state.set_attention(0, "approval")
                self._session.send_snapshot()
            self._emit(self._console.render())
            return
        if isinstance(approval_event, ApprovalResolved):
            if approval_event.request.thread_id == self._thread_id:
                has_thread_approval = any(item.slot == 1 for item in self._coordinator.pending)
                if not has_thread_approval:
                    self._state.set_attention(0, None, require_active=True)
                self._session.send_snapshot()
            self._emit(self._console.render())
            return

        operation_event = self._operations.handle_notification(message)
        if isinstance(operation_event, TurnStartedEvent):
            if operation_event.thread_id == self._thread_id:
                self._state.turn_started(
                    0,
                    thread_id=str(operation_event.thread_id),
                    turn_id=str(operation_event.turn_id),
                )
                self._session.send_snapshot()
                self._emit("turn_started PASS\n")
            return
        if (
            isinstance(operation_event, TurnCompletedEvent)
            and operation_event.thread_id == self._thread_id
        ):
            self._state.turn_completed(0)
            self._session.send_snapshot()
            self._emit("turn_completed PASS\n")

    def _record_error(self, error: BaseException) -> None:
        if self._errors.empty():
            self._errors.put(error)


def run_controller(
    output: TextIO,
    input_stream: TextIO,
    *,
    cwd: Path,
    label: str,
    port: str,
    provider: SerialProvider,
    synthetic_device: SyntheticSerialProvider | None = None,
    command: Sequence[str] | None = None,
    step_timeout: float = 30.0,
) -> None:
    """単一controller-owned threadをCardputerとhost consoleへ接続する。"""
    checked_cwd = cwd.resolve()
    if not checked_cwd.is_dir():
        raise ValueError("controller cwd must be an existing directory")
    checked_label = label.strip()
    if not checked_label or len(checked_label.encode("utf-8")) > 24:
        raise ValueError("controller label must be 1 to 24 UTF-8 bytes")

    output_lock = threading.Lock()

    def emit(value: str) -> None:
        with output_lock:
            output.write(value)
            output.flush()

    client = AppServerClient(
        command=tuple(command) if command is not None else codex_app_server_command(),
        request_timeout=step_timeout,
        shutdown_timeout=_APP_SERVER_SHUTDOWN_TIMEOUT_SECONDS,
    )
    session: DeviceControllerSession | None = None
    pump: _EventPump | None = None
    shutdown = None
    client.start()
    emit("app_server PASS\n")
    try:
        initialized = client.initialize(
            ClientInfo(
                name="cardputer-codex-controller",
                title="Cardputer Codex Controller",
                version="0.0.0",
            )
        )
        operations = AppServerOperations(client)
        controller_thread = operations.start_thread(
            ThreadStartOptions(
                cwd=checked_cwd,
                approval_policy="on-request",
                sandbox="read-only",
                ephemeral=True,
            )
        )
        emit("controller_thread PASS\n")

        state = ControllerState()
        state.configure_slot(
            0,
            label=checked_label,
            thread_id=str(controller_thread.thread_id),
        )
        session = DeviceControllerSession(
            state=state,
            adapter=_OperationsInterruptAdapter(operations, emit),
            provider=provider,
            port=port,
        )
        coordinator = ApprovalCoordinator(
            send_response=client.respond,
            send_device_approval=session.send_approval,
            send_device_resolved=session.send_approval_resolved,
            slots_by_thread={str(controller_thread.thread_id): 1},
            codex_version=initialized.codex_version,
        )
        console = HostApprovalConsole(coordinator)

        def handle_device_decision(approval_id: str, decision: str) -> bool:
            accepted = coordinator.handle_device_decision(approval_id, decision)
            emit(f"device_{decision} {'PASS' if accepted else 'REJECTED'}\n")
            return accepted

        session.configure_approval(
            decision_handler=handle_device_decision,
            ready_handler=coordinator.republish,
        )
        session.start()
        if not session.wait_connected(step_timeout):
            raise TimeoutError("controller serial link did not connect")
        if synthetic_device is not None:
            synthetic_device.inject({"t": "hello", "seq": 1, "proto": 1})
        if not session.wait_for_device_hello(step_timeout):
            raise TimeoutError("controller device hello was not received")
        emit("device_link PASS\n")

        pump = _EventPump(
            client=client,
            operations=operations,
            coordinator=coordinator,
            console=console,
            state=state,
            session=session,
            thread_id=controller_thread.thread_id,
            emit=emit,
        )
        pump.start()
        emit("controller_ready PASS\n")
        emit(
            "commands: run <text> | wait | pending | approve <id> | "
            "decline <id> | cancel <id> | interrupt | quit\n"
        )

        while True:
            pump.raise_if_failed()
            line = input_stream.readline()
            if line == "":
                break
            command_line = line.strip()
            if not command_line:
                continue
            if command_line == "quit":
                break
            if command_line == "pending":
                emit(console.render())
                continue
            if command_line == "wait":
                deadline = time.monotonic() + step_timeout
                wait_incomplete = False
                while operations.active_turn(controller_thread.thread_id) is not None:
                    pump.raise_if_failed()
                    if any(
                        item.status == "awaiting_decision"
                        for item in coordinator.pending
                    ):
                        emit("turn_wait BLOCKED pending_approval\n")
                        wait_incomplete = True
                        break
                    if time.monotonic() >= deadline:
                        emit("turn_wait TIMEOUT\n")
                        wait_incomplete = True
                        break
                    time.sleep(0.01)
                if not wait_incomplete:
                    emit("turn_wait PASS\n")
                continue
            if command_line.startswith(
                ("approve ", "decline ", "cancel ")
            ):
                accepted = console.execute(command_line)
                emit(f"approval_response {'PASS' if accepted else 'REJECTED'}\n")
                continue
            if command_line == "interrupt":
                active_turn = operations.active_turn(controller_thread.thread_id)
                if active_turn is None:
                    emit("interrupt REJECTED\n")
                else:
                    operations.interrupt_turn(controller_thread.thread_id, active_turn)
                    emit("interrupt PASS\n")
                continue
            if command_line.startswith("run ") and command_line[4:].strip():
                if operations.active_turn(controller_thread.thread_id) is not None:
                    emit("turn_request REJECTED\n")
                    continue
                operations.start_turn(
                    controller_thread.thread_id,
                    command_line[4:].strip(),
                    timeout=step_timeout,
                )
                emit("turn_request PASS\n")
                continue
            emit("command REJECTED\n")
        pump.raise_if_failed()
    finally:
        if pump is not None:
            pump.close()
        if session is not None:
            session.close()
        shutdown = client.close()
        if pump is not None:
            pump.join()
    if shutdown.forced or shutdown.exit_code != 0:
        stderr = client.stderr_summary
        emit(
            "app_server_shutdown FAIL "
            f"forced={str(shutdown.forced).lower()} "
            f"exit={shutdown.exit_code} "
            f"stderr_errors={stderr.error_lines}\n"
        )
        raise RuntimeError("controller app-server did not close cleanly")
    emit("controller_stopped PASS\n")


def run_controller_dry_run(output: TextIO, *, cwd: Path, label: str) -> None:
    if not cwd.resolve().is_dir():
        raise ValueError("controller cwd must be an existing directory")
    if not label.strip() or len(label.strip().encode("utf-8")) > 24:
        raise ValueError("controller label must be 1 to 24 UTF-8 bytes")
    output.write("controller_config PASS\n")
    output.write("serial_io_opened false\n")
    output.write("codex_connection N/A\n")
    output.write("controller_dry_run PASS\n")


__all__ = ["run_controller", "run_controller_dry_run"]
