from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DeviceApproval:
    approval_id: str
    summary: str
    risk_class: str = "normal"
    content_complete: bool = True


def device_decisions(approval: DeviceApproval) -> tuple[str, ...]:
    """一次防御をhostへ残し、危険・不完全な本文ではacceptを隠す。"""
    if approval.risk_class != "normal" or not approval.content_complete:
        return ("decline",)
    return ("accept", "decline")


class PendingQueue:
    def __init__(self) -> None:
        self._items: list[DeviceApproval] = []

    def add(self, approval: DeviceApproval) -> None:
        if any(item.approval_id == approval.approval_id for item in self._items):
            return
        self._items.append(approval)

    @property
    def current(self) -> DeviceApproval | None:
        return self._items[0] if self._items else None

    @property
    def remaining_count(self) -> int:
        return max(0, len(self._items) - 1)

    def hold(self, approval_id: str) -> bool:
        return any(item.approval_id == approval_id for item in self._items)

    def resolve(self, approval_id: str) -> DeviceApproval:
        for index, item in enumerate(self._items):
            if item.approval_id == approval_id:
                return self._items.pop(index)
        raise KeyError(approval_id)
