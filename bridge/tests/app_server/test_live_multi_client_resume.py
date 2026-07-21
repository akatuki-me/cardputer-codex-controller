from __future__ import annotations

import json
import os
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

import pytest
from cardputer_codex_bridge.app_server import (
    AppServerClient,
    AppServerError,
    AppServerResponseError,
    AppServerTimeoutError,
    ClientInfo,
    JsonObject,
    JsonValue,
    codex_app_server_command,
)

LIVE_ENV = "CARDPUTER_CODEX_LIVE_MULTI_CLIENT"
LIVE_CODEX_VERSION = "0.144.6"
CLIENT_INFO = ClientInfo(
    name="cardputer-multi-client-live-probe",
    title="Cardputer Multi-client Live Probe",
    version="0.0.0",
)
WAIT_PROMPT = "Run a shell wait for 60 seconds, then reply with the single word READY."
SEED_PROMPT = "Reply with the single word READY."


class LiveProbeContractError(RuntimeError):
    """The live probe could not reduce an observation to its public contract."""


@dataclass(frozen=True, slots=True)
class ThreadSnapshot:
    thread_id: str
    status: str
    turn_statuses: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ResumeObservation:
    outcome: str
    error_code: int | None
    status: str | None
    turn_statuses: tuple[str, ...]

    def as_public_json(self) -> JsonObject:
        return {
            "errorCode": self.error_code,
            "outcome": self.outcome,
            "status": self.status,
            "turnStatuses": list(self.turn_statuses),
        }


def _client() -> AppServerClient:
    return AppServerClient(
        command=codex_app_server_command(),
        expected_codex_versions=(LIVE_CODEX_VERSION,),
        request_timeout=30.0,
        shutdown_timeout=65.0,
    )


def _open_client() -> AppServerClient:
    client = _client()
    client.start()
    try:
        initialized = client.initialize(CLIENT_INFO, experimental_api=True)
        if initialized.codex_version != LIVE_CODEX_VERSION:
            raise LiveProbeContractError("live probe connected to an unexpected Codex version")
    except Exception:
        client.close()
        raise
    return client


def _require_object(value: JsonValue, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise LiveProbeContractError(f"{label} is not an object")
    return value


def _require_nonempty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise LiveProbeContractError(f"{label} is not a non-empty string")
    return value


def _status_name(value: object, label: str) -> str:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, dict):
        return _require_nonempty_string(value.get("type"), label)
    raise LiveProbeContractError(f"{label} is not a supported status")


def _thread_snapshot(value: JsonValue, label: str) -> ThreadSnapshot:
    result = _require_object(value, f"{label} result")
    thread = _require_object(result.get("thread"), f"{label} thread")
    turns = thread.get("turns")
    if not isinstance(turns, list):
        raise LiveProbeContractError(f"{label} turns is not an array")
    turn_statuses: list[str] = []
    for turn_value in turns:
        turn = _require_object(turn_value, f"{label} turn")
        turn_statuses.append(_status_name(turn.get("status"), f"{label} turn status"))
    return ThreadSnapshot(
        thread_id=_require_nonempty_string(thread.get("id"), f"{label} thread id"),
        status=_status_name(thread.get("status"), f"{label} thread status"),
        turn_statuses=tuple(turn_statuses),
    )


def _resume(client: AppServerClient, thread_id: str, **extra: JsonValue) -> ResumeObservation:
    params: JsonObject = {"threadId": thread_id, **extra}
    try:
        result = client.request("thread/resume", params)
    except AppServerResponseError as error:
        return ResumeObservation(
            outcome="error",
            error_code=error.code,
            status=None,
            turn_statuses=(),
        )
    snapshot = _thread_snapshot(result, "thread/resume")
    if snapshot.thread_id != thread_id:
        raise LiveProbeContractError("thread/resume returned another thread")
    return ResumeObservation(
        outcome="resumed",
        error_code=None,
        status=snapshot.status,
        turn_statuses=snapshot.turn_statuses,
    )


def _start_persistent_thread(client: AppServerClient, cwd: Path) -> ThreadSnapshot:
    result = client.request(
        "thread/start",
        {
            "approvalPolicy": "never",
            "cwd": str(cwd),
            "ephemeral": False,
            "sandbox": "read-only",
        },
    )
    return _thread_snapshot(result, "thread/start")


def _start_turn(client: AppServerClient, thread_id: str, text: str) -> str:
    result = _require_object(
        client.request(
            "turn/start",
            {
                "threadId": thread_id,
                "input": [{"type": "text", "text": text, "text_elements": []}],
            },
        ),
        "turn/start result",
    )
    turn = _require_object(result.get("turn"), "turn/start turn")
    status = _status_name(turn.get("status"), "turn/start status")
    if status != "inProgress":
        raise LiveProbeContractError("turn/start did not create an active turn")
    return _require_nonempty_string(turn.get("id"), "turn/start turn id")


def _notification_kind(message: JsonObject) -> str:
    method = message.get("method")
    if not isinstance(method, str):
        return "invalid"
    known = {
        "account/rateLimits/updated": "rate_limits_updated",
        "item/agentMessage/delta": "agent_message_delta",
        "item/completed": "item_completed",
        "item/started": "item_started",
        "thread/started": "thread_started",
        "thread/tokenUsage/updated": "token_usage_updated",
        "turn/completed": "turn_completed",
        "turn/started": "turn_started",
    }
    if "id" in message:
        return "server_request"
    return known.get(method, "other")


def _observe_until(
    client: AppServerClient,
    target_method: str,
    *,
    timeout: float,
    required: bool,
) -> tuple[tuple[str, ...], bool]:
    deadline = time.monotonic() + timeout
    kinds: list[str] = []
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            message = client.next_message(timeout=min(1.0, remaining))
        except AppServerTimeoutError:
            continue
        kind = _notification_kind(message)
        if kind not in kinds:
            kinds.append(kind)
        request_id = message.get("id")
        if request_id is not None:
            client.reject_request(request_id)
        if message.get("method") == target_method:
            return tuple(kinds), True
    if required:
        raise LiveProbeContractError("required lifecycle notification was not observed")
    return tuple(kinds), False


def _interrupt(client: AppServerClient, thread_id: str, turn_id: str) -> None:
    _require_object(
        client.request(
            "turn/interrupt",
            {"threadId": thread_id, "turnId": turn_id},
        ),
        "turn/interrupt result",
    )


def _unsubscribe(client: AppServerClient, thread_id: str) -> str:
    result = _require_object(
        client.request("thread/unsubscribe", {"threadId": thread_id}),
        "thread/unsubscribe result",
    )
    status = _require_nonempty_string(
        result.get("status"),
        "thread/unsubscribe status",
    )
    if status not in {"notLoaded", "notSubscribed", "unsubscribed"}:
        raise LiveProbeContractError("thread/unsubscribe returned an unknown status")
    return status


def _close(client: AppServerClient) -> int:
    started_at = time.monotonic()
    shutdown = client.close()
    elapsed_ms = round((time.monotonic() - started_at) * 1000)
    if shutdown.forced or shutdown.exit_code != 0:
        raise LiveProbeContractError("app-server did not close cleanly")
    return elapsed_ms


def _archive_best_effort(thread_id: str) -> None:
    cleanup: AppServerClient | None = None
    try:
        cleanup = _open_client()
        cleanup.request("thread/archive", {"threadId": thread_id}, timeout=30.0)
    except (AppServerError, LiveProbeContractError):
        pass
    finally:
        if cleanup is not None:
            with suppress(AppServerError):
                cleanup.close()


def test_archive_best_effort_suppresses_open_contract_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_open() -> AppServerClient:
        raise LiveProbeContractError("cleanup contract failure")

    monkeypatch.setitem(globals(), "_open_client", fail_open)

    _archive_best_effort("cleanup-thread")


def test_archive_best_effort_suppresses_request_contract_error_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ContractFailingCleanup:
        closed = False

        def request(
            self,
            method: str,
            params: JsonObject,
            *,
            timeout: float,
        ) -> JsonValue:
            raise LiveProbeContractError("cleanup request contract failure")

        def close(self) -> None:
            self.closed = True

    cleanup = ContractFailingCleanup()
    monkeypatch.setitem(globals(), "_open_client", lambda: cleanup)

    _archive_best_effort("cleanup-thread")

    assert cleanup.closed is True


def _archive(thread_id: str) -> None:
    cleanup = _open_client()
    try:
        _require_object(
            cleanup.request("thread/archive", {"threadId": thread_id}, timeout=30.0),
            "thread/archive result",
        )
    finally:
        _close(cleanup)


def _require_equal(actual: object, expected: object, label: str) -> None:
    if actual != expected:
        raise LiveProbeContractError(f"{label} violated the live contract")


@pytest.mark.skipif(
    os.environ.get(LIVE_ENV) != "1",
    reason="認証済みCodex CLIを使うmulti-client/thread resume local acceptance test",
)
def test_live_multi_client_resume_contract(tmp_path: Path) -> None:
    primary = _open_client()
    open_clients = [primary]
    thread_id: str | None = None
    archived = False
    public_record: JsonObject | None = None
    try:
        started = _start_persistent_thread(primary, tmp_path)
        thread_id = started.thread_id

        _start_turn(primary, thread_id, SEED_PROMPT)
        seed_notifications, _ = _observe_until(
            primary,
            "turn/completed",
            timeout=60.0,
            required=True,
        )

        idle_client = _open_client()
        open_clients.append(idle_client)
        idle_resume = _resume(idle_client, thread_id)
        idle_notifications, idle_saw_thread_started = _observe_until(
            idle_client,
            "thread/started",
            timeout=1.0,
            required=False,
        )
        idle_unsubscribe = _unsubscribe(idle_client, thread_id)
        _require_equal(
            idle_resume,
            ResumeObservation(
                outcome="resumed",
                error_code=None,
                status="idle",
                turn_statuses=("completed",),
            ),
            "idle resume",
        )
        _require_equal(idle_saw_thread_started, False, "idle resume notification")
        _require_equal(idle_unsubscribe, "unsubscribed", "idle unsubscribe")
        idle_shutdown_ms = _close(idle_client)
        open_clients.remove(idle_client)

        active_turn_id = _start_turn(primary, thread_id, WAIT_PROMPT)
        primary_started_notifications, _ = _observe_until(
            primary,
            "turn/started",
            timeout=30.0,
            required=True,
        )

        running_client = _open_client()
        open_clients.append(running_client)
        running_resume = _resume(running_client, thread_id)
        running_notifications, running_saw_thread_started = _observe_until(
            running_client,
            "thread/started",
            timeout=1.0,
            required=False,
        )

        state_error = _resume(primary, thread_id, history=[])
        _require_equal(
            state_error,
            ResumeObservation(
                outcome="error",
                error_code=-32600,
                status=None,
                turn_statuses=(),
            ),
            "loaded-state resume",
        )
        _interrupt(primary, thread_id, active_turn_id)
        primary_completed_notifications, _ = _observe_until(
            primary,
            "turn/completed",
            timeout=30.0,
            required=True,
        )
        secondary_completion_notifications, secondary_saw_completion = _observe_until(
            running_client,
            "turn/completed",
            timeout=3.0,
            required=False,
        )

        running_unsubscribe = _unsubscribe(running_client, thread_id)
        _require_equal(
            running_resume,
            ResumeObservation(
                outcome="resumed",
                error_code=None,
                status="idle",
                turn_statuses=("completed", "interrupted"),
            ),
            "running resume",
        )
        _require_equal(running_saw_thread_started, False, "running resume notification")
        _require_equal(secondary_saw_completion, False, "secondary completion notification")
        _require_equal(running_unsubscribe, "unsubscribed", "running unsubscribe")
        running_shutdown_ms = _close(running_client)
        open_clients.remove(running_client)
        primary_shutdown_ms = _close(primary)
        open_clients.remove(primary)

        resumed_client = _open_client()
        open_clients.append(resumed_client)
        disconnected_resume = _resume(resumed_client, thread_id)
        disconnected_notifications, disconnected_saw_thread_started = _observe_until(
            resumed_client,
            "thread/started",
            timeout=1.0,
            required=False,
        )
        missing_resume = _resume(
            resumed_client,
            "ffffffff-ffff-7fff-bfff-ffffffffffff",
        )
        disconnected_unsubscribe = _unsubscribe(resumed_client, thread_id)
        _require_equal(
            disconnected_resume,
            ResumeObservation(
                outcome="resumed",
                error_code=None,
                status="idle",
                turn_statuses=("completed", "interrupted"),
            ),
            "disconnected resume",
        )
        _require_equal(
            missing_resume,
            ResumeObservation(
                outcome="error",
                error_code=-32600,
                status=None,
                turn_statuses=(),
            ),
            "missing resume",
        )
        _require_equal(
            disconnected_saw_thread_started,
            False,
            "disconnected resume notification",
        )
        _require_equal(
            disconnected_unsubscribe,
            "unsubscribed",
            "disconnected unsubscribe",
        )
        disconnected_shutdown_ms = _close(resumed_client)
        open_clients.remove(resumed_client)
        _archive(thread_id)
        archived = True

        public_record = {
            "archiveConfirmed": archived,
            "codexVersion": LIVE_CODEX_VERSION,
            "disconnectedResume": disconnected_resume.as_public_json(),
            "disconnectedSawThreadStarted": disconnected_saw_thread_started,
            "disconnectedShutdownMs": disconnected_shutdown_ms,
            "disconnectedUnsubscribe": disconnected_unsubscribe,
            "idleResume": idle_resume.as_public_json(),
            "idleSawThreadStarted": idle_saw_thread_started,
            "idleShutdownMs": idle_shutdown_ms,
            "idleUnsubscribe": idle_unsubscribe,
            "missingResume": missing_resume.as_public_json(),
            "notifications": {
                "disconnectedClient": list(disconnected_notifications),
                "idleClient": list(idle_notifications),
                "primaryCompleted": list(primary_completed_notifications),
                "primarySeed": list(seed_notifications),
                "primaryStarted": list(primary_started_notifications),
                "runningClient": list(running_notifications),
                "runningClientAfterCompletion": list(
                    secondary_completion_notifications
                ),
            },
            "primaryShutdownMs": primary_shutdown_ms,
            "runningResume": running_resume.as_public_json(),
            "runningSawThreadStarted": running_saw_thread_started,
            "runningShutdownMs": running_shutdown_ms,
            "runningUnsubscribe": running_unsubscribe,
            "secondarySawCompletion": secondary_saw_completion,
            "stateError": state_error.as_public_json(),
            "transport": "independent_stdio_processes",
        }
        encoded = json.dumps(public_record, sort_keys=True)
        if any(
            private_value in encoded
            for private_value in (thread_id, str(tmp_path), SEED_PROMPT, WAIT_PROMPT)
        ):
            raise LiveProbeContractError("public record contains private probe data")
        print(encoded)
    finally:
        for client in reversed(open_clients):
            with suppress(AppServerError):
                client.close()
        if thread_id is not None and not archived:
            _archive_best_effort(thread_id)

    if public_record is None:
        raise LiveProbeContractError("public record was not produced")
