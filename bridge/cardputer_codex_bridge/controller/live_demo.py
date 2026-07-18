from __future__ import annotations

import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import TextIO

from cardputer_codex_bridge.app_server import (
    AppServerClient,
    AppServerOperations,
    ClientInfo,
    ThreadStartOptions,
    codex_app_server_command,
)
from cardputer_codex_bridge.models import TurnCompletedEvent, TurnStartedEvent, TurnStartResult


def run_codex_demo(output: TextIO) -> None:
    """認証済みCodex app-serverを安全な一時threadで実測する。"""
    temporary = Path(tempfile.mkdtemp(prefix="cardputer-codex-demo-"))
    client = AppServerClient(
        command=codex_app_server_command(),
        request_timeout=30.0,
        shutdown_timeout=10.0,
    )
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
        if not operations.list_models(limit=10).models:
            raise RuntimeError("model list is empty")
        _pass(output, "model_list")

        thread = operations.start_thread(
            ThreadStartOptions(
                cwd=temporary,
                approval_policy="never",
                sandbox="read-only",
                ephemeral=True,
            )
        )
        _pass(output, "thread_start")
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

        starter = threading.Thread(target=start_turn, name="codex-demo-turn")
        starter.start()
        started = _wait_for_started(client, operations)
        _pass(output, "turn_started")
        operations.steer_turn(
            thread.thread_id,
            started.turn_id,
            "Reply with the single word READY.",
        )
        _pass(output, "turn_steer")
        operations.interrupt_turn(thread.thread_id, started.turn_id)
        _pass(output, "turn_interrupt")

        starter.join(timeout=30.0)
        if starter.is_alive() or start_errors or len(turns) != 1:
            raise RuntimeError("turn/start did not finish cleanly")
        completion = _wait_for_completed(client, operations)
        if completion.thread_id != thread.thread_id:
            raise RuntimeError("completion thread did not match")
        if completion.turn_id != turns[0].turn_id:
            raise RuntimeError("completion turn did not match")
        _pass(output, "turn_completed")
    finally:
        shutdown = client.close()
        shutil.rmtree(temporary, ignore_errors=True)
    if shutdown.forced or shutdown.exit_code != 0:
        raise RuntimeError("app-server did not close cleanly")
    _pass(output, "app_server_close")
    _pass(output, "codex_demo")


def _wait_for_started(
    client: AppServerClient,
    operations: AppServerOperations,
) -> TurnStartedEvent:
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        event = operations.handle_notification(client.next_message(timeout=5.0))
        if isinstance(event, TurnStartedEvent):
            return event
    raise TimeoutError("turn/started was not received")


def _wait_for_completed(
    client: AppServerClient,
    operations: AppServerOperations,
) -> TurnCompletedEvent:
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        event = operations.handle_notification(client.next_message(timeout=5.0))
        if isinstance(event, TurnCompletedEvent):
            return event
    raise TimeoutError("turn/completed was not received")


def _pass(output: TextIO, step: str) -> None:
    output.write(f"{step} PASS\n")
    output.flush()
