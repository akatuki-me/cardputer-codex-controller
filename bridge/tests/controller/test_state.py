from __future__ import annotations

from dataclasses import dataclass, field
from threading import Event, Lock, Thread

import pytest
from cardputer_codex_bridge.controller import ControllerState, LinkState, ServiceState


@dataclass
class RecordingAdapter:
    calls: list[tuple[str, str]] = field(default_factory=list)

    def interrupt(self, thread_id: str, turn_id: str) -> None:
        self.calls.append((thread_id, turn_id))


class BlockingCompletionSlot:
    def __init__(self) -> None:
        self.slot = 1
        self.label = "slot-1"
        self._status = "run"
        self.thread_id: str | None = "thread-1"
        self.turn_id: str | None = "turn-1"
        self.attention_kind: str | None = None
        self.status_written = Event()
        self.release = Event()

    @property
    def status(self) -> str:
        return self._status

    @status.setter
    def status(self, value: str) -> None:
        self._status = value
        if value == "done":
            self.status_written.set()
            if not self.release.wait(timeout=1.0):
                raise TimeoutError("test did not release completion update")

    def as_json(self) -> dict[str, object]:
        return {
            "slot": self.slot,
            "label": self.label,
            "status": self.status,
            "turnActive": self.turn_id is not None,
            "turnId": self.turn_id,
            "attentionKind": self.attention_kind,
        }


@dataclass
class BlockingAdapter:
    calls: list[tuple[str, str]] = field(default_factory=list)
    first_call: Event = field(default_factory=Event)
    second_call: Event = field(default_factory=Event)
    release: Event = field(default_factory=Event)
    lock: Lock = field(default_factory=Lock)

    def interrupt(self, thread_id: str, turn_id: str) -> None:
        with self.lock:
            self.calls.append((thread_id, turn_id))
            if len(self.calls) == 1:
                self.first_call.set()
            else:
                self.second_call.set()
        if not self.release.wait(timeout=1.0):
            raise TimeoutError("test did not release interrupt adapter")


def test_snapshot_has_six_slots_and_preserves_multiple_active_turns() -> None:
    state = ControllerState()
    state.turn_started(0, thread_id="thread-1", turn_id="turn-1")
    state.turn_running(0)
    state.turn_started(1, thread_id="thread-2", turn_id="turn-2")

    snapshot = state.snapshot(seq=10)

    assert snapshot["full"] is True
    assert snapshot["linkState"] == "active"
    assert snapshot["serviceState"] == "ready"
    slots = snapshot["slots"]
    assert isinstance(slots, list)
    assert len(slots) == 6
    assert slots[0]["turnActive"] is True
    assert slots[0]["turnId"] == "turn-1"
    assert slots[0]["status"] == "run"
    assert slots[1]["turnActive"] is True
    assert slots[1]["turnId"] == "turn-2"


def test_selected_active_turn_interrupt_is_forwarded_only_once() -> None:
    state = ControllerState()
    adapter = RecordingAdapter()
    state.turn_started(0, thread_id="thread-1", turn_id="turn-1")

    assert state.interrupt_active(adapter) is True
    assert state.interrupt_active(adapter) is False
    assert adapter.calls == [("thread-1", "turn-1")]


def test_completed_turn_is_inactive_and_surfaces_done_attention() -> None:
    state = ControllerState()
    state.turn_started(0, thread_id="thread-1", turn_id="turn-1")
    state.turn_completed(0)

    slots = state.snapshot(seq=2)["slots"]
    assert isinstance(slots, list)
    assert slots[0]["turnActive"] is False
    assert slots[0]["turnId"] is None
    assert slots[0]["attentionKind"] == "done"


def test_snapshot_waits_for_a_complete_turn_state_update() -> None:
    state = ControllerState()
    slot = BlockingCompletionSlot()
    state.slots[0] = slot
    snapshot_result: list[dict[str, object]] = []
    snapshot_started = Event()
    snapshot_done = Event()

    def take_snapshot() -> None:
        snapshot_started.set()
        snapshot_result.append(state.snapshot(seq=3))
        snapshot_done.set()

    updater = Thread(target=state.turn_completed, args=(0,))
    updater.start()
    assert slot.status_written.wait(timeout=1.0)
    reader = Thread(target=take_snapshot)
    reader.start()
    assert snapshot_started.wait(timeout=1.0)
    snapshot_finished_during_update = snapshot_done.wait(timeout=0.25)
    slot.release.set()
    updater.join(timeout=1.0)
    reader.join(timeout=1.0)

    assert snapshot_finished_during_update is False
    assert updater.is_alive() is False
    assert reader.is_alive() is False
    assert len(snapshot_result) == 1
    slots = snapshot_result[0]["slots"]
    assert isinstance(slots, list)
    assert slots[0]["status"] == "done"
    assert slots[0]["turnActive"] is False


def test_interrupt_deduplication_uses_thread_and_turn_pair() -> None:
    state = ControllerState()
    adapter = RecordingAdapter()
    state.turn_started(0, thread_id="thread-1", turn_id="same-turn")
    state.turn_started(1, thread_id="thread-2", turn_id="same-turn")

    assert state.interrupt_active(adapter) is True
    state.select(1)
    assert state.interrupt_active(adapter) is True
    assert adapter.calls == [("thread-1", "same-turn"), ("thread-2", "same-turn")]


def test_concurrent_interrupt_claim_is_forwarded_only_once() -> None:
    state = ControllerState()
    adapter = BlockingAdapter()
    state.turn_started(0, thread_id="thread-1", turn_id="turn-1")
    results: list[bool] = []

    def claim() -> None:
        results.append(state.interrupt_claimed(1, "turn-1", adapter))

    first = Thread(target=claim)
    second = Thread(target=claim)
    first.start()
    assert adapter.first_call.wait(timeout=1.0)
    second.start()
    duplicate_reached_adapter = adapter.second_call.wait(timeout=0.25)
    adapter.release.set()
    first.join(timeout=1.0)
    second.join(timeout=1.0)

    assert duplicate_reached_adapter is False
    assert first.is_alive() is False
    assert second.is_alive() is False
    assert adapter.calls == [("thread-1", "turn-1")]
    assert sorted(results) == [False, True]


@pytest.mark.parametrize(
    ("link_state", "service_state"),
    [(LinkState.STALE, ServiceState.READY), (LinkState.ACTIVE, ServiceState.DOWN)],
)
def test_interrupt_is_blocked_when_link_or_service_is_unavailable(
    link_state: LinkState,
    service_state: ServiceState,
) -> None:
    state = ControllerState()
    state.link_state = link_state
    state.service_state = service_state
    state.turn_started(0, thread_id="thread-1", turn_id="turn-1")
    adapter = RecordingAdapter()

    assert state.interrupt_active(adapter) is False
    assert adapter.calls == []
