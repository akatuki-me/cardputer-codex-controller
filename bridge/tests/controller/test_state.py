from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from cardputer_codex_bridge.controller import ControllerState, LinkState, ServiceState


@dataclass
class RecordingAdapter:
    calls: list[tuple[str, str]] = field(default_factory=list)

    def interrupt(self, thread_id: str, turn_id: str) -> None:
        self.calls.append((thread_id, turn_id))


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


def test_interrupt_deduplication_uses_thread_and_turn_pair() -> None:
    state = ControllerState()
    adapter = RecordingAdapter()
    state.turn_started(0, thread_id="thread-1", turn_id="same-turn")
    state.turn_started(1, thread_id="thread-2", turn_id="same-turn")

    assert state.interrupt_active(adapter) is True
    state.select(1)
    assert state.interrupt_active(adapter) is True
    assert adapter.calls == [("thread-1", "same-turn"), ("thread-2", "same-turn")]


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
