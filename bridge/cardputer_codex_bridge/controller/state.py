from __future__ import annotations

import threading
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
        self._lock = threading.RLock()
        self._link_state = LinkState.ACTIVE
        self._service_state = ServiceState.READY
        self.slots = [SlotState(slot=index + 1, label=f"slot-{index + 1}") for index in range(6)]
        self._selected_index = 0
        self._interrupted_turns: set[tuple[str, str]] = set()

    @property
    def link_state(self) -> LinkState:
        with self._lock:
            return self._link_state

    @link_state.setter
    def link_state(self, value: LinkState) -> None:
        with self._lock:
            self._link_state = value

    @property
    def service_state(self) -> ServiceState:
        with self._lock:
            return self._service_state

    @service_state.setter
    def service_state(self, value: ServiceState) -> None:
        with self._lock:
            self._service_state = value

    def set_link_state(self, value: LinkState) -> None:
        self.link_state = value

    def configure_slot(self, index: int, *, label: str, thread_id: str) -> None:
        with self._lock:
            slot = self._slot(index)
            slot.label = label
            slot.thread_id = thread_id

    def set_attention(
        self,
        index: int,
        attention_kind: str | None,
        *,
        require_active: bool = False,
    ) -> bool:
        with self._lock:
            slot = self._slot(index)
            if require_active and slot.turn_id is None:
                return False
            slot.attention_kind = attention_kind
            return True

    def turn_started(self, index: int, *, thread_id: str, turn_id: str) -> None:
        with self._lock:
            slot = self._slot(index)
            slot.status = "run"
            slot.thread_id = thread_id
            slot.turn_id = turn_id
            slot.attention_kind = None

    def turn_running(self, index: int) -> None:
        with self._lock:
            self._slot(index).status = "run"

    def turn_completed(self, index: int) -> None:
        with self._lock:
            slot = self._slot(index)
            slot.status = "done"
            slot.turn_id = None
            slot.attention_kind = "done"

    def interrupt_active(self, adapter: HostCommandAdapter) -> bool:
        with self._lock:
            slot_number = self._selected_index + 1
            turn_id = self._slot(self._selected_index).turn_id
        if turn_id is None:
            return False
        return self.interrupt_claimed(slot_number, turn_id, adapter)

    def interrupt_claimed(
        self,
        slot_number: int,
        turn_id: str,
        adapter: HostCommandAdapter,
    ) -> bool:
        with self._lock:
            if (
                self._link_state is not LinkState.ACTIVE
                or self._service_state is not ServiceState.READY
            ):
                return False
            if slot_number != self._selected_index + 1:
                return False
            slot = self._slot(self._selected_index)
            if slot.thread_id is None or slot.turn_id != turn_id:
                return False
            key = (slot.thread_id, slot.turn_id)
            if key in self._interrupted_turns:
                return False
            self._interrupted_turns.add(key)
        try:
            adapter.interrupt(*key)
        except BaseException:
            with self._lock:
                self._interrupted_turns.discard(key)
            raise
        return True

    def select(self, index: int) -> None:
        with self._lock:
            self._slot(index)
            self._selected_index = index

    def snapshot(self, *, seq: int) -> JsonObject:
        with self._lock:
            return {
                "t": "state",
                "seq": seq,
                "full": True,
                "linkState": self._link_state.value,
                "serviceState": self._service_state.value,
                "selectedSlot": self._selected_index + 1,
                "slots": [slot.as_json() for slot in self.slots],
            }

    def _slot(self, index: int) -> SlotState:
        if not 0 <= index < len(self.slots):
            raise IndexError(index)
        return self.slots[index]
