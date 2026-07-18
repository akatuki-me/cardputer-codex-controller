from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from cardputer_codex_bridge.app_server.types import JsonObject


class LinkState(Enum):
    ACTIVE = "active"
    STALE = "stale"


class ServiceState(Enum):
    INITIALIZING = "initializing"
    READY = "ready"
    DOWN = "down"
    AUTH_REQUIRED = "auth_required"


class HostCommandAdapter(Protocol):
    """Codex app-serverまたは合成hostへcommandを渡す境界。"""

    def interrupt(self, thread_id: str, turn_id: str) -> None: ...


@dataclass(slots=True)
class SlotState:
    slot: int
    label: str
    status: str = "idle"
    thread_id: str | None = None
    turn_id: str | None = None
    attention_kind: str | None = None

    def as_json(self) -> JsonObject:
        return {
            "slot": self.slot,
            "label": self.label,
            "status": self.status,
            "turnActive": self.turn_id is not None,
            "turnId": self.turn_id,
            "attentionKind": self.attention_kind,
        }


class ControllerState:
    def __init__(self) -> None:
        self.link_state = LinkState.ACTIVE
        self.service_state = ServiceState.READY
        self.slots = [SlotState(slot=index + 1, label=f"slot-{index + 1}") for index in range(6)]
        self._selected_index = 0
        self._interrupted_turns: set[tuple[str, str]] = set()

    def turn_started(self, index: int, *, thread_id: str, turn_id: str) -> None:
        slot = self._slot(index)
        slot.status = "run"
        slot.thread_id = thread_id
        slot.turn_id = turn_id
        slot.attention_kind = None

    def turn_running(self, index: int) -> None:
        self._slot(index).status = "run"

    def turn_completed(self, index: int) -> None:
        slot = self._slot(index)
        slot.status = "done"
        slot.turn_id = None
        slot.attention_kind = "done"

    def interrupt_active(self, adapter: HostCommandAdapter) -> bool:
        if self.link_state is not LinkState.ACTIVE or self.service_state is not ServiceState.READY:
            return False
        slot = self._slot(self._selected_index)
        if slot.thread_id is None or slot.turn_id is None:
            return False
        key = (slot.thread_id, slot.turn_id)
        if key in self._interrupted_turns:
            return False
        adapter.interrupt(slot.thread_id, slot.turn_id)
        self._interrupted_turns.add(key)
        return True

    def select(self, index: int) -> None:
        self._slot(index)
        self._selected_index = index

    def snapshot(self, *, seq: int) -> JsonObject:
        return {
            "t": "state",
            "seq": seq,
            "full": True,
            "linkState": self.link_state.value,
            "serviceState": self.service_state.value,
            "selectedSlot": self._selected_index + 1,
            "slots": [slot.as_json() for slot in self.slots],
        }

    def _slot(self, index: int) -> SlotState:
        if not 0 <= index < len(self.slots):
            raise IndexError(index)
        return self.slots[index]
