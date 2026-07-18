from __future__ import annotations

import sys
from pathlib import Path

import pytest
from cardputer_codex_bridge.app_server import (
    ActiveTurnRequiredError,
    AppServerClient,
    AppServerOperations,
    AppServerProtocolError,
    AppServerResponseError,
    ClientInfo,
    ThreadStartOptions,
)
from cardputer_codex_bridge.models import ThreadId, TurnId, TurnStartedEvent, TurnStatus

FIXTURE = Path(__file__).with_name("fake_app_server.py")
CLIENT_INFO = ClientInfo(name="cardputer-test", version="0.0.0")


def _client(mode: str) -> AppServerClient:
    return AppServerClient(
        command=(sys.executable, str(FIXTURE), mode),
        request_timeout=1.0,
        shutdown_timeout=0.1,
    )


def test_required_operations_keep_request_thread_and_turn_ids_separate(tmp_path: Path) -> None:
    client = _client("operations")
    client.start()
    try:
        client.initialize(CLIENT_INFO)
        operations = AppServerOperations(client)

        thread = operations.start_thread(
            ThreadStartOptions(
                cwd=tmp_path,
                approval_policy="never",
                sandbox="read-only",
                ephemeral=True,
            )
        )
        models = operations.list_models()
        turn = operations.start_turn(thread.thread_id, "Synthetic initial input")

        assert thread.thread_id == ThreadId("thread-synthetic-1")
        assert turn.turn_id == TurnId("turn-synthetic-1")
        assert turn.status is TurnStatus.IN_PROGRESS
        assert operations.active_turn(thread.thread_id) == turn.turn_id
        assert models.find("future-model") is not None
        assert models.models[0].supported_reasoning_efforts == ("future-effort",)
        assert models.find("not-advertised") is None

        started = operations.handle_notification(client.next_message(timeout=1.0))
        assert isinstance(started, TurnStartedEvent)
        assert started.turn_id == turn.turn_id

        assert operations.steer_turn(
            thread.thread_id,
            turn.turn_id,
            "Synthetic steering input",
        ) == TurnId("turn-synthetic-1")
        operations.interrupt_turn(thread.thread_id, turn.turn_id)

        event = operations.handle_notification(client.next_message(timeout=1.0))
        assert event is not None
        assert event.thread_id == thread.thread_id
        assert event.turn_id == turn.turn_id
        assert event.status is TurnStatus.INTERRUPTED
        assert event.matched_active_turn is True
        assert event.has_error is False
        assert operations.active_turn(thread.thread_id) is None
    finally:
        shutdown = client.close()

    assert shutdown.forced is False
    assert shutdown.exit_code == 0


def test_interrupt_rejects_a_non_active_turn_without_sending_a_request() -> None:
    client = _client("model-list-empty")
    client.start()
    try:
        client.initialize(CLIENT_INFO)
        operations = AppServerOperations(client)

        with pytest.raises(ActiveTurnRequiredError):
            operations.interrupt_turn(
                ThreadId("thread-synthetic-1"),
                TurnId("turn-not-active"),
            )

        assert operations.list_models().models == ()
    finally:
        client.close()


def test_model_list_error_is_propagated_without_a_synthetic_fallback() -> None:
    client = _client("operations-error")
    client.start()
    try:
        client.initialize(CLIENT_INFO)
        operations = AppServerOperations(client)

        with pytest.raises(AppServerResponseError) as captured:
            operations.list_models()

        assert captured.value.code == -32602
    finally:
        client.close()


def test_stale_completion_does_not_clear_a_newer_active_turn() -> None:
    operations = AppServerOperations(_client("normal"))
    thread_id = ThreadId("thread-synthetic-1")
    active_turn = TurnId("turn-synthetic-2")
    operations._active_turns[thread_id] = active_turn

    event = operations.handle_notification(
        {
            "method": "turn/completed",
            "params": {
                "threadId": thread_id,
                "turn": {
                    "id": "turn-synthetic-1",
                    "items": [],
                    "status": "completed",
                },
            },
        }
    )

    assert event is not None
    assert event.matched_active_turn is False
    assert operations.active_turn(thread_id) == active_turn


def test_stale_started_notification_does_not_replace_a_newer_active_turn() -> None:
    operations = AppServerOperations(_client("normal"))
    thread_id = ThreadId("thread-synthetic-1")
    active_turn = TurnId("turn-synthetic-2")
    operations._active_turns[thread_id] = active_turn

    operations.handle_notification(
        {
            "method": "turn/started",
            "params": {
                "threadId": thread_id,
                "turn": {
                    "id": "turn-synthetic-1",
                    "items": [],
                    "status": "inProgress",
                },
            },
        }
    )

    assert operations.active_turn(thread_id) == active_turn


def test_turn_completed_rejects_an_in_progress_status() -> None:
    operations = AppServerOperations(_client("normal"))

    with pytest.raises(AppServerProtocolError, match="active turn"):
        operations.handle_notification(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread-synthetic-1",
                    "turn": {
                        "id": "turn-synthetic-1",
                        "items": [],
                        "status": "inProgress",
                    },
                },
            }
        )


def test_non_completion_notification_is_left_unhandled() -> None:
    operations = AppServerOperations(_client("normal"))

    assert operations.handle_notification({"method": "item/started", "params": {}}) is None


def test_thread_start_rejects_a_relative_cwd_before_transport() -> None:
    with pytest.raises(ValueError, match="absolute"):
        ThreadStartOptions(cwd=Path("synthetic-workspace")).as_params()
