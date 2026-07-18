from cardputer_codex_bridge.approval import PendingApproval, PendingQueue, device_decisions


def test_pending_queue_keeps_all_items_and_advances_after_resolution() -> None:
    queue = PendingQueue()
    first = PendingApproval("approval-1", "first")
    second = PendingApproval("approval-2", "second")
    queue.add(first)
    queue.add(second)

    assert queue.current == first
    assert queue.remaining_count == 1
    assert queue.hold(first.approval_id) is True
    assert queue.current == first
    assert queue.resolve(first.approval_id) == first
    assert queue.current == second
    assert queue.remaining_count == 0


def test_unsafe_approval_never_presents_accept() -> None:
    high_risk = PendingApproval("high", "high", risk_class="high")
    unknown_risk = PendingApproval("unknown", "unknown", risk_class="unknown")
    incomplete = PendingApproval("incomplete", "incomplete", content_complete=False)

    assert "accept" not in device_decisions(high_risk)
    assert "accept" not in device_decisions(unknown_risk)
    assert "accept" not in device_decisions(incomplete)
    assert "hold" not in device_decisions(high_risk)
    assert "accept" in device_decisions(PendingApproval("safe", "safe"))
