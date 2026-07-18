from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest
from cardputer_codex_bridge.app_server import (
    AppServerClient,
    AppServerOperations,
    ClientInfo,
    ThreadStartOptions,
    codex_app_server_command,
)
from cardputer_codex_bridge.models import TurnStartedEvent, TurnStartResult, TurnStatus


@pytest.mark.skipif(
    os.environ.get("CARDPUTER_CODEX_LIVE_OPERATIONS") != "1",
    reason="認証済みCodex CLIを使うthread/turn local acceptance test",
)
def test_live_app_server_01445_operations(tmp_path: Path) -> None:
    client = AppServerClient(
        command=codex_app_server_command(),
        request_timeout=30.0,
        shutdown_timeout=10.0,
    )
    client.start()
    try:
        client.initialize(
            ClientInfo(
                name="cardputer-codex-controller",
                title="Cardputer Codex Controller",
                version="0.0.0",
            )
        )
        operations = AppServerOperations(client)
        models = operations.list_models(limit=10)
        thread = operations.start_thread(
            ThreadStartOptions(
                cwd=tmp_path,
                approval_policy="never",
                sandbox="read-only",
                ephemeral=True,
            )
        )
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

        starter = threading.Thread(target=start_turn)
        starter.start()
        started = None
        deadline = time.monotonic() + 30.0
        while started is None and time.monotonic() < deadline:
            event = operations.handle_notification(client.next_message(timeout=5.0))
            if isinstance(event, TurnStartedEvent):
                started = event

        assert started is not None
        operations.steer_turn(
            thread.thread_id,
            started.turn_id,
            "Reply with the single word READY.",
        )
        operations.interrupt_turn(thread.thread_id, started.turn_id)
        starter.join(timeout=30.0)
        assert not starter.is_alive()
        assert not start_errors
        assert len(turns) == 1
        turn = turns[0]

        deadline = time.monotonic() + 30.0
        completion = None
        while completion is None and time.monotonic() < deadline:
            completion = operations.handle_notification(client.next_message(timeout=5.0))

        assert models.models
        assert completion is not None
        assert completion.thread_id == thread.thread_id
        assert completion.turn_id == turn.turn_id
        assert completion.status in {
            TurnStatus.COMPLETED,
            TurnStatus.INTERRUPTED,
            TurnStatus.FAILED,
        }
        assert completion.matched_active_turn is True
        assert operations.active_turn(thread.thread_id) is None
    finally:
        shutdown = client.close()

    assert shutdown.forced is False
    assert shutdown.exit_code == 0
