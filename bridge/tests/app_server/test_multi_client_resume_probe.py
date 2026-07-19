from __future__ import annotations

import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar
from weakref import WeakKeyDictionary

import pytest
from cardputer_codex_bridge.app_server import (
    AppServerClient,
    AppServerResponseError,
    ClientInfo,
)

FIXTURE = Path(__file__).with_name("fake_multi_client_app_server.py")
CLIENT_INFO = ClientInfo(name="cardputer-multi-client-probe", version="0.0.0")
SYNTHETIC_THREAD_ID = "thread-synthetic-shared"


class ProbeContractError(RuntimeError):
    """The deterministic probe observed an ambiguous ownership condition."""


@dataclass(frozen=True, slots=True)
class ResumeSucceeded:
    notification_kinds: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ResumeNotFound:
    pass


@dataclass(frozen=True, slots=True)
class ResumePermissionDenied:
    pass


@dataclass(frozen=True, slots=True)
class ResumeInvalidState:
    pass


type ResumeProbeResult = (
    ResumeSucceeded | ResumeNotFound | ResumePermissionDenied | ResumeInvalidState
)


class NotificationHub:
    """Own the only notification consumer for one synthetic connection."""

    _connection_owners: ClassVar[WeakKeyDictionary[AppServerClient, object]] = (
        WeakKeyDictionary()
    )
    _connection_owners_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, client: AppServerClient, *, owned_thread_id: str) -> None:
        with self._connection_owners_lock:
            if client in self._connection_owners:
                raise ProbeContractError(
                    "connection already owns a notification hub"
                )
            self._connection_owners[client] = object()
        self._client = client
        self._owned_thread_id = owned_thread_id
        self._consumer_claimed = False
        self._consumer_claim_lock = threading.Lock()
        self._collect_lock = threading.Lock()
        self._seen: set[tuple[str, str]] = set()

    def claim_consumer(self) -> NotificationConsumer:
        with self._consumer_claim_lock:
            if self._consumer_claimed:
                raise ProbeContractError("notification consumer is already claimed")
            self._consumer_claimed = True
            return NotificationConsumer(self)

    def _collect(self, count: int) -> tuple[str, ...]:
        with self._collect_lock:
            kinds: list[str] = []
            for _ in range(count):
                message = self._client.next_message(timeout=1.0)
                if message.get("method") != "thread/started":
                    raise ProbeContractError("unexpected notification method")
                params = message.get("params")
                if not isinstance(params, dict):
                    raise ProbeContractError("notification params are invalid")
                thread = params.get("thread")
                if not isinstance(thread, dict):
                    raise ProbeContractError("notification thread is invalid")
                thread_id = thread.get("id")
                if not isinstance(thread_id, str) or not thread_id:
                    raise ProbeContractError("notification thread is invalid")
                if thread_id != self._owned_thread_id:
                    raise ProbeContractError("notification owner is unknown")
                fingerprint = ("thread/started", thread_id)
                if fingerprint in self._seen:
                    raise ProbeContractError("duplicate notification")
                self._seen.add(fingerprint)
                kinds.append("thread_started")
            return tuple(kinds)


class NotificationConsumer:
    def __init__(self, hub: NotificationHub) -> None:
        self._hub = hub

    def collect(self, count: int = 1) -> tuple[str, ...]:
        if count <= 0:
            raise ValueError("notification count must be positive")
        return self._hub._collect(count)


def _client(mode: str, state_path: Path) -> AppServerClient:
    return AppServerClient(
        command=(sys.executable, "-u", str(FIXTURE), mode, str(state_path)),
        request_timeout=1.0,
        shutdown_timeout=0.2,
    )


def _response_thread_id(value: object) -> str:
    if not isinstance(value, dict):
        raise ProbeContractError("response thread is invalid")
    thread = value.get("thread")
    if not isinstance(thread, dict):
        raise ProbeContractError("response thread is invalid")
    thread_id = thread.get("id")
    if not isinstance(thread_id, str) or not thread_id:
        raise ProbeContractError("response thread is invalid")
    return thread_id


def _create_shared_thread(state_path: Path) -> ResumeSucceeded:
    client = _client("create", state_path)
    client.start()
    try:
        client.initialize(CLIENT_INFO)
        result = client.request("thread/start", {"ephemeral": False})
        thread_id = _response_thread_id(result)
        notifications = NotificationHub(
            client,
            owned_thread_id=thread_id,
        ).claim_consumer().collect()
        return ResumeSucceeded(notification_kinds=notifications)
    finally:
        shutdown = client.close()
        if shutdown.forced or shutdown.exit_code != 0:
            raise ProbeContractError("creator connection did not close cleanly")


def _classify_resume_error(error: AppServerResponseError) -> ResumeProbeResult:
    if error.code == -32004:
        return ResumeNotFound()
    if error.code == -32003:
        return ResumePermissionDenied()
    if error.code == -32002:
        return ResumeInvalidState()
    raise ProbeContractError("resume error is not classified") from error


def _resume_shared_thread(
    mode: str,
    state_path: Path,
    *,
    notification_count: int = 1,
) -> ResumeProbeResult:
    client = _client(mode, state_path)
    client.start()
    try:
        client.initialize(CLIENT_INFO)
        try:
            result = client.request(
                "thread/resume",
                {"threadId": SYNTHETIC_THREAD_ID},
            )
        except AppServerResponseError as error:
            return _classify_resume_error(error)
        thread_id = _response_thread_id(result)
        notifications = NotificationHub(
            client,
            owned_thread_id=thread_id,
        ).claim_consumer().collect(notification_count)
        return ResumeSucceeded(notification_kinds=notifications)
    finally:
        shutdown = client.close()
        if shutdown.forced or shutdown.exit_code != 0:
            raise ProbeContractError("resumer connection did not close cleanly")


def _public_record(result: ResumeProbeResult) -> dict[str, object]:
    if isinstance(result, ResumeSucceeded):
        return {
            "notificationKinds": list(result.notification_kinds),
            "outcome": "resumed",
        }
    if isinstance(result, ResumeNotFound):
        return {"notificationKinds": [], "outcome": "not_found"}
    if isinstance(result, ResumePermissionDenied):
        return {"notificationKinds": [], "outcome": "permission_denied"}
    return {"notificationKinds": [], "outcome": "invalid_state"}


def test_client_b_resumes_shared_thread_after_client_a_disconnect(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "shared-state"

    created = _create_shared_thread(state_path)
    resumed = _resume_shared_thread("resume-success", state_path)

    assert created.notification_kinds == ("thread_started",)
    assert isinstance(resumed, ResumeSucceeded)
    assert resumed.notification_kinds == ("thread_started",)


@pytest.mark.parametrize(
    ("mode", "expected_type", "requires_state"),
    [
        ("resume-not-found", ResumeNotFound, False),
        ("resume-permission-denied", ResumePermissionDenied, True),
        ("resume-invalid-state", ResumeInvalidState, True),
    ],
)
def test_resume_failures_remain_distinct(
    mode: str,
    expected_type: type[ResumeProbeResult],
    requires_state: bool,
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "shared-state"
    if requires_state:
        _create_shared_thread(state_path)

    result = _resume_shared_thread(mode, state_path)

    assert isinstance(result, expected_type)


def test_connection_rejects_a_second_hub_before_duplicates_can_split(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "shared-state"
    _create_shared_thread(state_path)
    client = _client("resume-duplicate", state_path)
    client.start()
    try:
        client.initialize(CLIENT_INFO)
        result = client.request(
            "thread/resume",
            {"threadId": SYNTHETIC_THREAD_ID},
        )
        thread_id = _response_thread_id(result)
        hub = NotificationHub(client, owned_thread_id=thread_id)

        with pytest.raises(ProbeContractError, match="already owns"):
            NotificationHub(client, owned_thread_id=thread_id)

        with pytest.raises(ProbeContractError, match="duplicate notification"):
            hub.claim_consumer().collect(2)
    finally:
        shutdown = client.close()

    assert shutdown.forced is False
    assert shutdown.exit_code == 0


def test_concurrent_claim_allows_exactly_one_consumer(tmp_path: Path) -> None:
    state_path = tmp_path / "shared-state"
    _create_shared_thread(state_path)
    client = _client("resume-success", state_path)
    client.start()
    try:
        client.initialize(CLIENT_INFO)
        hub = NotificationHub(client, owned_thread_id=SYNTHETIC_THREAD_ID)
        barrier = threading.Barrier(2)

        def claim() -> str:
            barrier.wait()
            try:
                hub.claim_consumer()
            except ProbeContractError:
                return "rejected"
            return "accepted"

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(claim) for _ in range(2)]
            outcomes = sorted(future.result() for future in futures)

        assert outcomes == ["accepted", "rejected"]
        client.request("thread/resume", {"threadId": SYNTHETIC_THREAD_ID})
    finally:
        shutdown = client.close()

    assert shutdown.forced is False
    assert shutdown.exit_code == 0


def test_concurrent_reads_on_one_consumer_detect_duplicate(tmp_path: Path) -> None:
    state_path = tmp_path / "shared-state"
    _create_shared_thread(state_path)
    client = _client("resume-duplicate", state_path)
    client.start()
    try:
        client.initialize(CLIENT_INFO)
        result = client.request(
            "thread/resume",
            {"threadId": SYNTHETIC_THREAD_ID},
        )
        thread_id = _response_thread_id(result)
        consumer = NotificationHub(
            client,
            owned_thread_id=thread_id,
        ).claim_consumer()
        barrier = threading.Barrier(2)

        def collect() -> str:
            barrier.wait()
            try:
                consumer.collect()
            except ProbeContractError as error:
                if str(error) != "duplicate notification":
                    raise
                return "duplicate"
            return "accepted"

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(collect) for _ in range(2)]
            outcomes = sorted(future.result() for future in futures)

        assert outcomes == ["accepted", "duplicate"]
    finally:
        shutdown = client.close()

    assert shutdown.forced is False
    assert shutdown.exit_code == 0


def test_duplicate_notification_fails_closed(tmp_path: Path) -> None:
    state_path = tmp_path / "shared-state"
    _create_shared_thread(state_path)

    with pytest.raises(ProbeContractError, match="duplicate notification"):
        _resume_shared_thread(
            "resume-duplicate",
            state_path,
            notification_count=2,
        )


def test_unowned_notification_fails_closed(tmp_path: Path) -> None:
    state_path = tmp_path / "shared-state"
    _create_shared_thread(state_path)

    with pytest.raises(ProbeContractError, match="owner is unknown"):
        _resume_shared_thread("resume-unowned", state_path)


def test_public_record_excludes_thread_and_environment_details(tmp_path: Path) -> None:
    state_path = tmp_path / "shared-state"
    created = _create_shared_thread(state_path)
    result = _resume_shared_thread("resume-success", state_path)

    record = json.dumps(_public_record(result), sort_keys=True)

    assert isinstance(created, ResumeSucceeded)
    assert record == (
        '{"notificationKinds": ["thread_started"], "outcome": "resumed"}'
    )
    assert SYNTHETIC_THREAD_ID not in record
    assert str(state_path) not in record
