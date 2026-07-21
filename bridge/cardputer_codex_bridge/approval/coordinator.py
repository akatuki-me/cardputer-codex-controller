from __future__ import annotations

import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Literal, cast

from cardputer_codex_bridge.app_server.types import JsonObject, RequestId

from .contract import ApprovalContract, ApprovalEvent, ResponseSender
from .queue import DeviceApproval, device_decisions
from .types import (
    ApprovalRequest,
    ApprovalResolved,
    ApprovalStatus,
    CommandApprovalRequest,
    FileChangeApprovalRequest,
)

type DeviceDecision = Literal["accept", "decline"]
type HostDecision = Literal["accept", "decline", "cancel"]
type DeviceApprovalSender = Callable[[DeviceApproval, int], bool]
type DeviceResolvedSender = Callable[[str, str | None], bool]

# firmwareのapproval描画上限（`%.38s`）を超えない。
_DISPLAY_LINE_BYTES = 38
_DISPLAY_MAX_LINES = 8
_DISPLAY_CWD_BYTES = 48
_FALLBACK_VERSIONS = frozenset({"0.144.5", "0.144.6"})
_HIGH_RISK_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"(?:^|[;&|]\s*)rm\s+[^\n]*(?:-[^\s]*r|--recursive)",
        r"\bremove-item\b[^\n]*\s-recurse(?:\s|:|$)",
        r"\bgit\s+push\b[^\n]*(?:--force(?:-with-lease)?|-f)(?:\s|$)",
        r"(?:^|[;&|]\s*)sudo(?:\s|$)",
        r"\b(?:rd|rmdir)\s+/s(?:\s|$)",
        r"\b(?:del|erase)\b[^\n]*\s/s(?:\s|$)",
    )
)


@dataclass(frozen=True, slots=True)
class HostPendingView:
    device_approval_id: str
    slot: int | None
    kind: str
    summary: str
    details: str
    cwd: str
    content_complete: bool
    risk_class: str
    device_decisions: tuple[DeviceDecision, ...]
    host_decisions: tuple[HostDecision, ...]
    status: str


@dataclass(frozen=True, slots=True)
class _Binding:
    request: ApprovalRequest
    presentation: DeviceApproval
    device_visible: bool
    host_decisions: tuple[HostDecision, ...]


class ApprovalCoordinator:
    """app-server approval、host正本、device表示を一つのlifecycleへ接続する。"""

    def __init__(
        self,
        *,
        send_response: ResponseSender,
        send_device_approval: DeviceApprovalSender,
        send_device_resolved: DeviceResolvedSender,
        slots_by_thread: Mapping[str, int],
        codex_version: str,
    ) -> None:
        checked_slots = dict(slots_by_thread)
        if any(not 1 <= slot <= 6 for slot in checked_slots.values()):
            raise ValueError("approval slot must be between 1 and 6")
        self._contract = ApprovalContract(send_response)
        self._send_device_approval = send_device_approval
        self._send_device_resolved = send_device_resolved
        self._slots_by_thread = checked_slots
        self._codex_version = codex_version
        self._bindings: list[_Binding] = []
        self._by_request_id: dict[RequestId, _Binding] = {}
        self._by_device_id: dict[str, _Binding] = {}
        self._next_device_id = 0
        self._lock = threading.RLock()

    @property
    def pending(self) -> tuple[HostPendingView, ...]:
        with self._lock:
            result: list[HostPendingView] = []
            for binding in self._bindings:
                pending = self._contract.get(binding.request.request_id)
                if pending is None:
                    continue
                details, cwd = _host_details(binding.request)
                current_presentation = replace(
                    binding.presentation,
                    sending=pending.status is ApprovalStatus.RESPONSE_SENT,
                )
                visible_decisions = (
                    device_decisions(current_presentation)
                    if binding.device_visible
                    else ()
                )
                result.append(
                    HostPendingView(
                        device_approval_id=binding.presentation.approval_id,
                        slot=(
                            binding.presentation.slot if binding.device_visible else None
                        ),
                        kind=binding.presentation.kind,
                        summary=binding.presentation.summary,
                        details=details,
                        cwd=cwd,
                        content_complete=binding.presentation.content_complete,
                        risk_class=binding.presentation.risk_class,
                        device_decisions=cast(
                            tuple[DeviceDecision, ...],
                            visible_decisions,
                        ),
                        host_decisions=binding.host_decisions,
                        status=pending.status.value,
                    )
                )
            return tuple(result)

    def handle_message(self, message: JsonObject) -> ApprovalEvent | None:
        event = self._contract.handle_message(message)
        if isinstance(event, (CommandApprovalRequest, FileChangeApprovalRequest)):
            self._register(event)
        elif isinstance(event, ApprovalResolved):
            self._resolve(event)
        return event

    def handle_device_decision(self, approval_id: str, decision: str) -> bool:
        if decision not in ("accept", "decline"):
            return False
        checked_decision = cast(DeviceDecision, decision)
        with self._lock:
            current = self._current_device_binding_locked()
            if current is None or current.presentation.approval_id != approval_id:
                return False
            if checked_decision not in device_decisions(current.presentation):
                return False
            return self._respond_locked(current, checked_decision)

    def respond_host(self, approval_id: str, decision: HostDecision) -> bool:
        """Hostは原requestを確認できるため、危険分類でも明示応答できる。"""
        with self._lock:
            binding = self._by_device_id.get(approval_id)
            if binding is None:
                return False
            return self._respond_locked(binding, decision)

    def republish(self) -> bool:
        """再接続時にpending正本の先頭と残数を再送する。"""
        with self._lock:
            return self._publish_current_locked()

    def _register(self, request: ApprovalRequest) -> None:
        with self._lock:
            if request.request_id in self._by_request_id:
                return
            self._next_device_id += 1
            approval_id = f"approval-{self._next_device_id:06d}"
            slot = self._slots_by_thread.get(request.thread_id)
            visible = slot is not None
            presentation, host_content_complete = _project_request(
                request,
                approval_id=approval_id,
                slot=slot if slot is not None else 1,
                codex_version=self._codex_version,
            )
            host_decisions = _normalized_host_decisions(request, self._codex_version)
            if not host_content_complete:
                host_decisions = tuple(
                    decision for decision in host_decisions if decision != "accept"
                )
            visible = visible and bool(device_decisions(presentation))
            binding = _Binding(
                request=request,
                presentation=presentation,
                device_visible=visible,
                host_decisions=host_decisions,
            )
            self._bindings.append(binding)
            self._by_request_id[request.request_id] = binding
            self._by_device_id[approval_id] = binding
            self._publish_current_locked()

    def _resolve(self, event: ApprovalResolved) -> None:
        with self._lock:
            binding = self._by_request_id.pop(event.request.request_id, None)
            if binding is None:
                return
            current = self._current_device_binding_locked()
            was_current = current is binding
            self._bindings.remove(binding)
            self._by_device_id.pop(binding.presentation.approval_id, None)
            decision = _resolved_decision(event.response_result)
            if was_current:
                self._send_device_resolved(binding.presentation.approval_id, decision)
            self._publish_current_locked()

    def _respond_locked(self, binding: _Binding, decision: HostDecision) -> bool:
        pending = self._contract.get(binding.request.request_id)
        if pending is None or pending.status is not ApprovalStatus.AWAITING_DECISION:
            return False
        if decision not in binding.host_decisions:
            return False
        if isinstance(binding.request, CommandApprovalRequest):
            self._contract.respond_command(binding.request.request_id, decision)
        else:
            self._contract.respond_file_change(binding.request.request_id, decision)
        if self._current_device_binding_locked() is binding:
            self._publish_current_locked()
        return True

    def _current_device_binding_locked(self) -> _Binding | None:
        return next(
            (binding for binding in self._bindings if binding.device_visible),
            None,
        )

    def _publish_current_locked(self) -> bool:
        visible = [binding for binding in self._bindings if binding.device_visible]
        if not visible:
            return False
        pending = self._contract.get(visible[0].request.request_id)
        if pending is None:
            return False
        presentation = replace(
            visible[0].presentation,
            sending=pending.status is ApprovalStatus.RESPONSE_SENT,
        )
        return self._send_device_approval(
            presentation,
            len(visible) - 1,
        )


def _project_request(
    request: ApprovalRequest,
    *,
    approval_id: str,
    slot: int,
    codex_version: str,
) -> tuple[DeviceApproval, bool]:
    if isinstance(request, CommandApprovalRequest):
        kind = "command"
        source = request.command or request.reason or "Command details unavailable"
        cwd_value = request.params.get("cwd")
        cwd = cwd_value if isinstance(cwd_value, str) else ""
        semantic_complete = request.command is not None and not any(
            name in request.params and request.params.get(name) is not None
            for name in (
                "networkApprovalContext",
                "proposedExecpolicyAmendment",
                "proposedNetworkPolicyAmendments",
            )
        )
        risk_class = "high" if _is_high_risk(source) else "normal"
    else:
        kind = "file"
        source = request.reason or request.grant_root or "File change details unavailable"
        cwd = request.grant_root or ""
        semantic_complete = False
        risk_class = "normal"

    lines, lines_complete = _display_lines(source)
    display_cwd, cwd_complete = _truncate_utf8(cwd, _DISPLAY_CWD_BYTES)
    decisions = _normalized_device_decisions(request, codex_version)
    return (
        DeviceApproval(
            approval_id=approval_id,
            summary=" ".join(source.split())[:120],
            risk_class=risk_class,
            content_complete=semantic_complete and lines_complete and cwd_complete,
            slot=slot,
            kind=kind,
            lines=lines,
            cwd=display_cwd,
            decisions=decisions,
        ),
        semantic_complete,
    )


def _normalized_device_decisions(
    request: ApprovalRequest,
    codex_version: str,
) -> tuple[str, ...]:
    return tuple(
        decision
        for decision in _normalized_simple_decisions(request, codex_version)
        if decision in ("accept", "decline")
    )


def _normalized_host_decisions(
    request: ApprovalRequest,
    codex_version: str,
) -> tuple[HostDecision, ...]:
    values = tuple(
        decision
        for decision in _normalized_simple_decisions(request, codex_version)
        if decision in ("accept", "decline", "cancel")
    )
    return cast(tuple[HostDecision, ...], values)


def _normalized_simple_decisions(
    request: ApprovalRequest,
    codex_version: str,
) -> tuple[str, ...]:
    raw = request.params.get("availableDecisions")
    if raw is None:
        if codex_version not in _FALLBACK_VERSIONS:
            return ()
        return ("accept", "decline", "cancel")
    if not isinstance(raw, list) or any(
        not isinstance(value, (str, dict)) for value in raw
    ):
        return ()
    result: list[str] = []
    for value in raw:
        if (
            isinstance(value, str)
            and value in ("accept", "acceptForSession", "decline", "cancel")
            and value not in result
        ):
            result.append(value)
    return tuple(result)


def _host_details(request: ApprovalRequest) -> tuple[str, str]:
    if isinstance(request, CommandApprovalRequest):
        details = request.command or request.reason or "Command details unavailable"
        cwd_value = request.params.get("cwd")
        cwd = cwd_value if isinstance(cwd_value, str) else ""
        return details, cwd
    details = request.reason or request.grant_root or "File change details unavailable"
    return details, request.grant_root or ""


def _display_lines(value: str) -> tuple[tuple[str, ...], bool]:
    lines: list[str] = []
    complete = True
    paragraphs = value.splitlines() or [value]
    for paragraph in paragraphs:
        remaining = paragraph or " "
        while remaining:
            chunk, rest = _take_utf8_prefix(remaining, _DISPLAY_LINE_BYTES)
            if len(lines) >= _DISPLAY_MAX_LINES:
                return tuple(lines), False
            lines.append(chunk)
            remaining = rest
    if not lines:
        lines.append("Details unavailable")
        complete = False
    return tuple(lines), complete


def _truncate_utf8(value: str, maximum_bytes: int) -> tuple[str, bool]:
    prefix, rest = _take_utf8_prefix(value, maximum_bytes)
    return prefix, not rest


def _take_utf8_prefix(value: str, maximum_bytes: int) -> tuple[str, str]:
    used = 0
    index = 0
    for index, character in enumerate(value):
        width = len(character.encode("utf-8"))
        if used + width > maximum_bytes:
            return value[:index], value[index:]
        used += width
    return value, ""


def _is_high_risk(command: str) -> bool:
    return any(pattern.search(command) is not None for pattern in _HIGH_RISK_PATTERNS)


def _resolved_decision(result: JsonObject | None) -> str | None:
    if result is None:
        return None
    decision = result.get("decision")
    return decision if isinstance(decision, str) else None


__all__ = [
    "ApprovalCoordinator",
    "DeviceDecision",
    "HostDecision",
    "HostPendingView",
]
