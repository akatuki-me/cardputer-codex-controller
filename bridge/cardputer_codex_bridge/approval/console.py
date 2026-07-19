from __future__ import annotations

from .coordinator import ApprovalCoordinator, HostDecision


class HostApprovalConsole:
    """Pending正本を表示し、短いdevice IDでhost明示応答を受け付ける。"""

    def __init__(self, coordinator: ApprovalCoordinator) -> None:
        self._coordinator = coordinator

    def render(self) -> str:
        pending = self._coordinator.pending
        if not pending:
            return "pending approvals: 0\n"
        lines = [f"pending approvals: {len(pending)}"]
        for item in pending:
            slot = "host-only" if item.slot is None else str(item.slot)
            complete = str(item.content_complete).lower()
            lines.append(
                f"[{item.device_approval_id}] slot={slot} kind={item.kind} "
                f"status={item.status} risk={item.risk_class} complete={complete}"
            )
            actions = {
                "accept": "approve",
                "decline": "decline",
                "cancel": "cancel",
            }
            available_actions = [actions[decision] for decision in item.host_decisions]
            lines.append(
                "  actions: " + (", ".join(available_actions) or "none")
            )
            lines.append(f"  summary: {item.summary}")
            if item.cwd:
                lines.append(f"  cwd: {item.cwd}")
            lines.append("  details:")
            for detail_line in item.details.splitlines() or [""]:
                lines.append(f"    | {detail_line}")
        return "\n".join(lines) + "\n"

    def execute(self, command: str) -> bool:
        parts = command.split()
        if len(parts) != 2 or parts[0] not in ("approve", "decline", "cancel"):
            raise ValueError(
                "command must be: approve <id>, decline <id>, or cancel <id>"
            )
        decisions: dict[str, HostDecision] = {
            "approve": "accept",
            "decline": "decline",
            "cancel": "cancel",
        }
        decision = decisions[parts[0]]
        return self._coordinator.respond_host(parts[1], decision)


__all__ = ["HostApprovalConsole"]
