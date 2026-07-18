from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TextIO

from cardputer_codex_bridge.app_server.types import JsonObject, JsonValue
from cardputer_codex_bridge.approval import PendingApproval, PendingQueue, device_decisions
from cardputer_codex_bridge.controller.state import ControllerState


@dataclass(slots=True)
class SyntheticHostAdapter:
    commands: list[JsonObject] = field(default_factory=list)

    def interrupt(self, thread_id: str, turn_id: str) -> None:
        self.commands.append(
            {
                "command": "turn/interrupt",
                "threadId": thread_id,
                "turnId": turn_id,
            }
        )


def run_demo(output: TextIO) -> None:
    """実portを使わない決定的なhost controller縦切り。"""
    state = ControllerState()
    adapter = SyntheticHostAdapter()
    pending = PendingQueue()

    _emit(
        output,
        "device_hello",
        {"t": "hello", "seq": 1, "proto": 1, "device": "synthetic"},
    )
    _emit(output, "home_full", state.snapshot(seq=2))

    state.turn_started(0, thread_id="thread-demo-001", turn_id="turn-demo-001")
    _emit(output, "turn_started", state.snapshot(seq=3))
    state.turn_running(0)
    _emit(output, "turn_running", state.snapshot(seq=4))

    interrupted = state.interrupt_active(adapter)
    _emit(
        output,
        "device_interrupt",
        {"t": "interrupt", "seq": 5, "forwarded": interrupted},
    )
    _emit(output, "host_command", adapter.commands[0])

    state.turn_completed(0)
    _emit(output, "turn_completed", state.snapshot(seq=6))

    approval = PendingApproval("approval-demo-001", "テスト用の安全な変更")
    pending.add(approval)
    _emit(
        output,
        "approval_pending",
        {
            "t": "approval",
            "seq": 7,
            "deviceApprovalId": approval.approval_id,
            "decisions": list(device_decisions(approval)),
            "holdAvailable": True,
            "pendingCount": pending.remaining_count,
        },
    )
    _emit(
        output,
        "device_hold_local",
        {"action": "hold", "retained": pending.hold(approval.approval_id)},
    )
    resolved = pending.resolve(approval.approval_id)
    _emit(
        output,
        "host_resolved",
        {
            "t": "approval_resolved",
            "seq": 8,
            "deviceApprovalId": resolved.approval_id,
            "decision": "decline",
        },
    )

    high_risk = PendingApproval("approval-high", "危険操作", risk_class="high")
    incomplete = PendingApproval("approval-truncated", "切り詰め済み", content_complete=False)
    _emit(output, "guard_high_risk", _guard_result(high_risk, seq=9))
    _emit(output, "guard_incomplete", _guard_result(incomplete, seq=10))
    _emit(
        output,
        "adapter_status",
        {"codexConnection": "N/A", "mode": "synthetic", "hardwarePortOpened": False},
    )


def _guard_result(approval: PendingApproval, *, seq: int) -> JsonObject:
    decisions: list[JsonValue] = list(device_decisions(approval))
    return {
        "t": "approval",
        "seq": seq,
        "deviceApprovalId": approval.approval_id,
        "riskClass": approval.risk_class,
        "contentComplete": approval.content_complete,
        "decisions": decisions,
        "holdAvailable": True,
        "acceptPresented": "accept" in decisions,
    }


def _emit(output: TextIO, step: str, payload: JsonObject) -> None:
    output.write(f"{step} {json.dumps(payload, ensure_ascii=False, sort_keys=True)}\n")
