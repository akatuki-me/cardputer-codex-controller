from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Protocol, TextIO

from cardputer_codex_bridge.approval import DeviceApproval
from cardputer_codex_bridge.device_link import SerialProvider, SyntheticSerialProvider

from .device_session import DeviceControllerSession
from .state import ControllerState

_LONG_APPROVAL_ID = "fixture-long"
_HIGH_RISK_APPROVAL_ID = "fixture-high-risk"
_INCOMPLETE_APPROVAL_ID = "fixture-incomplete"
_GUARD_SECONDS = 0.3


class ApprovalFixtureError(RuntimeError):
    """固定approval fixtureの受入条件を満たさなかった。"""


@dataclass(frozen=True, slots=True)
class _DecisionEvent:
    approval_id: str
    decision: str


class _DecisionRecorder:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._events: list[_DecisionEvent] = []

    def handle(self, approval_id: str, decision: str) -> bool:
        with self._condition:
            self._events.append(_DecisionEvent(approval_id, decision))
            self._condition.notify_all()
        return True

    def mark(self) -> int:
        with self._condition:
            return len(self._events)

    def wait_after(self, mark: int, timeout: float) -> _DecisionEvent | None:
        with self._condition:
            received = self._condition.wait_for(
                lambda: len(self._events) > mark,
                timeout=timeout,
            )
            return self._events[mark] if received else None

    def count_after(self, mark: int) -> int:
        with self._condition:
            return len(self._events) - mark


class _FixtureOperator(Protocol):
    def arm(self, fixture: str) -> None: ...

    def confirm(self, checkpoint: str, instruction: str) -> None: ...


class _ManualFixtureOperator:
    def __init__(self, output: TextIO, input_stream: TextIO) -> None:
        self._output = output
        self._input = input_stream

    def arm(self, fixture: str) -> None:
        instructions = {
            "long_low_risk": (
                "Cardputer画面を注視し、現在の画面からfixture approval画面への"
                "切替直後にaとEnterを同時押しできる状態にしてから"
                "host consoleでokと入力しEnterで確定"
            ),
            "high_risk": (
                "Cardputerの表示と物理キーを確認できる状態にしてから"
                "host consoleでokと入力しEnterで確定"
            ),
            "incomplete": (
                "Cardputerの表示と物理キーを確認できる状態にしてから"
                "host consoleでokと入力しEnterで確定"
            ),
        }
        self._read_confirmation(
            f"{fixture}_arm",
            instructions[fixture],
        )

    def confirm(self, checkpoint: str, instruction: str) -> None:
        self._read_confirmation(checkpoint, instruction)

    def _read_confirmation(self, checkpoint: str, instruction: str) -> None:
        self._output.write(f"{checkpoint} ACTION {instruction}: ")
        self._output.flush()
        answer = self._input.readline()
        if answer == "" or answer.strip().lower() != "ok":
            raise ApprovalFixtureError("operator confirmation was not received")


class _SyntheticFixtureOperator:
    def __init__(self, provider: SyntheticSerialProvider) -> None:
        self._provider = provider
        self._sequence = 1

    def arm(self, fixture: str) -> None:
        del fixture

    def confirm(self, checkpoint: str, instruction: str) -> None:
        del instruction
        responses = {
            "long_body_accept": (_LONG_APPROVAL_ID, "accept"),
            "high_risk_decline": (_HIGH_RISK_APPROVAL_ID, "decline"),
            "incomplete_decline": (_INCOMPLETE_APPROVAL_ID, "decline"),
        }
        response = responses.get(checkpoint)
        if response is None:
            return
        self._sequence += 1
        approval_id, decision = response
        self._provider.inject(
            {
                "t": "decision",
                "seq": self._sequence,
                "deviceApprovalId": approval_id,
                "decision": decision,
            }
        )


class _NoInterruptAdapter:
    def interrupt(self, thread_id: str, turn_id: str) -> None:
        del thread_id, turn_id
        raise ApprovalFixtureError("approval fixture has no active turn")


class _ApprovalFixtureRunner:
    def __init__(
        self,
        *,
        output: TextIO,
        operator: _FixtureOperator,
        session: DeviceControllerSession,
        recorder: _DecisionRecorder,
        step_timeout: float,
        settle_timeout: float,
    ) -> None:
        self._output = output
        self._operator = operator
        self._session = session
        self._recorder = recorder
        self._step_timeout = step_timeout
        self._settle_timeout = settle_timeout
        self._active_approval_id: str | None = None

    @property
    def active_approval_id(self) -> str | None:
        return self._active_approval_id

    def run(self) -> None:
        self._run_long_low_risk()
        self._run_high_risk()
        self._run_incomplete()
        _pass(self._output, "approval_fixture")

    def _run_long_low_risk(self) -> None:
        approval = DeviceApproval(
            approval_id=_LONG_APPROVAL_ID,
            summary="固定の長文表示確認fixture",
            lines=tuple(f"SAFE FIXTURE LINE {index}/6" for index in range(1, 7)),
        )
        shown_at = self._present("long_low_risk", approval)
        self._expect_no_decision(
            "guard_before_300ms",
            "Cardputerでfixture approval画面への切替直後300ms未満に"
            "scrollせずaとEnterを同時に押し、完了後host consoleでokと入力し"
            "Enterで確定",
        )
        remaining = shown_at + _GUARD_SECONDS - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)
        self._expect_no_decision(
            "body_end_gate",
            "Cardputerで300ms経過後も本文末尾へ移動せずaとEnterを押し、"
            "完了後host consoleでokと入力しEnterで確定",
        )
        self._expect_decision(
            "long_body_accept",
            "Cardputerでjを3回押して本文末尾へ進み、aとEnterを押した後"
            "host consoleでokと入力しEnterで確定",
            approval.approval_id,
            "accept",
        )
        self._resolve(approval.approval_id, "accept")

    def _run_high_risk(self) -> None:
        approval = DeviceApproval(
            approval_id=_HIGH_RISK_APPROVAL_ID,
            summary="固定のhigh-risk表示確認fixture",
            risk_class="high",
            lines=("SAFE FIXTURE", "HIGH RISK DISPLAY"),
        )
        self._present("high_risk", approval)
        self._expect_no_decision(
            "high_risk_accept_hidden",
            "CardputerでA ACCEPTが表示されないことを確認し、aとEnterを"
            "押した後host consoleでokと入力しEnterで確定",
        )
        self._expect_decision(
            "high_risk_decline",
            "CardputerでdとEnterを押した後host consoleでokと入力しEnterで確定",
            approval.approval_id,
            "decline",
        )
        self._resolve(approval.approval_id, "decline")

    def _run_incomplete(self) -> None:
        approval = DeviceApproval(
            approval_id=_INCOMPLETE_APPROVAL_ID,
            summary="固定の不完全本文表示確認fixture",
            content_complete=False,
            lines=("SAFE FIXTURE", "INCOMPLETE DISPLAY"),
        )
        self._present("incomplete", approval)
        self._expect_no_decision(
            "incomplete_accept_hidden",
            "CardputerでA ACCEPTが表示されないことを確認し、aとEnterを"
            "押した後host consoleでokと入力しEnterで確定",
        )
        self._expect_no_decision(
            "incomplete_hold",
            "Cardputerで0を押してlocal holdのままであることを確認し、"
            "host consoleでokと入力しEnterで確定",
        )
        self._expect_decision(
            "incomplete_decline",
            "CardputerでdとEnterを押した後host consoleでokと入力しEnterで確定",
            approval.approval_id,
            "decline",
        )
        self._resolve(approval.approval_id, "decline")

    def _present(self, fixture: str, approval: DeviceApproval) -> float:
        self._operator.arm(fixture)
        if not self._session.send_approval(approval, pending_count=0):
            raise ApprovalFixtureError("approval fixture could not be sent")
        self._active_approval_id = approval.approval_id
        return time.monotonic()

    def _expect_no_decision(self, checkpoint: str, instruction: str) -> None:
        mark = self._recorder.mark()
        self._operator.confirm(checkpoint, instruction)
        event = self._recorder.wait_after(mark, self._settle_timeout)
        if event is not None:
            raise ApprovalFixtureError("unexpected device decision")
        _pass(self._output, checkpoint)

    def _expect_decision(
        self,
        checkpoint: str,
        instruction: str,
        approval_id: str,
        decision: str,
    ) -> None:
        mark = self._recorder.mark()
        self._operator.confirm(checkpoint, instruction)
        event = self._recorder.wait_after(mark, self._step_timeout)
        if event is None:
            raise ApprovalFixtureError("expected device decision was not received")
        if event.approval_id != approval_id or event.decision != decision:
            raise ApprovalFixtureError("device decision did not match the fixture")
        time.sleep(self._settle_timeout)
        if self._recorder.count_after(mark) != 1:
            raise ApprovalFixtureError("fixture produced multiple decisions")
        _pass(self._output, checkpoint)

    def _resolve(self, approval_id: str, decision: str) -> None:
        if not self._session.send_approval_resolved(approval_id, decision):
            raise ApprovalFixtureError("approval fixture resolution could not be sent")
        self._active_approval_id = None


def run_approval_fixture_dry_run(output: TextIO) -> None:
    """実portもCodexも起動せず、固定fixture経路の選択だけを確認する。"""
    _pass(output, "transport_selection")
    output.write("serial_io_opened false\n")
    output.write("codex_connection N/A\n")
    _pass(output, "approval_fixture_dry_run")


def run_approval_fixture(
    output: TextIO,
    input_stream: TextIO,
    *,
    port: str,
    provider: SerialProvider,
    synthetic_device: SyntheticSerialProvider | None = None,
    step_timeout: float = 120.0,
    settle_timeout: float = 0.3,
) -> None:
    """Codex非依存の固定fixtureでapprovalの物理安全境界を案内・記録する。"""
    if min(step_timeout, settle_timeout) <= 0:
        raise ValueError("approval fixture timeouts must be positive")
    recorder = _DecisionRecorder()
    operator: _FixtureOperator
    if synthetic_device is None:
        operator = _ManualFixtureOperator(output, input_stream)
    else:
        operator = _SyntheticFixtureOperator(synthetic_device)
    session = DeviceControllerSession(
        state=ControllerState(),
        adapter=_NoInterruptAdapter(),
        provider=provider,
        port=port,
    )
    session.configure_approval(
        decision_handler=recorder.handle,
        ready_handler=lambda: None,
    )
    runner = _ApprovalFixtureRunner(
        output=output,
        operator=operator,
        session=session,
        recorder=recorder,
        step_timeout=step_timeout,
        settle_timeout=settle_timeout,
    )
    session.start()
    try:
        if not session.wait_connected(step_timeout):
            raise TimeoutError("serial link did not connect")
        _pass(output, "serial_link")
        if synthetic_device is not None:
            synthetic_device.inject({"t": "hello", "seq": 1, "proto": 1})
        if not session.wait_for_device_hello(step_timeout):
            raise TimeoutError("device hello was not received")
        _pass(output, "device_hello")
        runner.run()
    finally:
        active = runner.active_approval_id
        if active is not None:
            session.send_approval_resolved(active, None)
        session.close()


def _pass(output: TextIO, step: str) -> None:
    output.write(f"{step} PASS\n")
    output.flush()


__all__ = [
    "ApprovalFixtureError",
    "run_approval_fixture",
    "run_approval_fixture_dry_run",
]
