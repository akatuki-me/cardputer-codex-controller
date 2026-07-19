from __future__ import annotations

import json
import sys
import time
from typing import Any


def _read() -> dict[str, Any] | None:
    line = sys.stdin.readline()
    if line == "":
        return None
    value = json.loads(line)
    if not isinstance(value, dict):
        raise TypeError("message must be an object")
    return value


def _write(value: object) -> None:
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "idle"
    initialize = _read()
    if initialize is None or initialize.get("method") != "initialize":
        return 10
    _write(
        {
            "id": initialize.get("id"),
            "result": {
                "codexHome": "synthetic-home",
                "platformFamily": "synthetic",
                "platformOs": "synthetic",
                "userAgent": "cardputer-test/0.144.6 (synthetic)",
            },
        }
    )
    if _read() != {"method": "initialized"}:
        return 11
    thread_start = _read()
    if thread_start is None or thread_start.get("method") != "thread/start":
        return 12
    params = thread_start.get("params")
    if not isinstance(params, dict):
        return 13
    if params.get("approvalPolicy") != "on-request":
        return 14
    if params.get("sandbox") != "read-only" or params.get("ephemeral") is not True:
        return 15
    _write(
        {
            "id": thread_start.get("id"),
            "result": {
                "thread": {"id": "thread-controller"},
                "model": "synthetic-model",
                "modelProvider": "synthetic-provider",
            },
        }
    )
    if mode == "approval":
        _write(
            {
                "id": "rpc-private",
                "method": "item/commandExecution/requestApproval",
                "params": {
                    "threadId": "thread-controller",
                    "turnId": "turn-1",
                    "itemId": "item-1",
                    "startedAtMs": 1000,
                    "command": "tool --check fixture.txt",
                    "cwd": "workspace/fixture",
                },
            }
        )
        if _read() != {"id": "rpc-private", "result": {"decision": "decline"}}:
            return 16
        _write(
            {
                "method": "serverRequest/resolved",
                "params": {
                    "requestId": "rpc-private",
                    "threadId": "thread-controller",
                },
            }
        )
    elif mode in ("turn", "turn_approval", "turn_interrupt"):
        turn_start = _read()
        if turn_start is None or turn_start.get("method") != "turn/start":
            return 17
        _write(
            {
                "method": "turn/started",
                "params": {
                    "threadId": "thread-controller",
                    "turn": {"id": "turn-1", "items": [], "status": "inProgress"},
                },
            }
        )
        _write(
            {
                "id": turn_start.get("id"),
                "result": {
                    "turn": {"id": "turn-1", "items": [], "status": "inProgress"}
                },
            }
        )
        if mode == "turn_interrupt":
            interrupt = _read()
            if interrupt is None or interrupt.get("method") != "turn/interrupt":
                return 18
            interrupt_params = interrupt.get("params")
            if not isinstance(interrupt_params, dict) or interrupt_params.get(
                "turnId"
            ) != "turn-1":
                return 19
            _write({"id": interrupt.get("id"), "result": {}})
        elif mode == "turn_approval":
            time.sleep(0.05)
            _write(
                {
                    "id": "rpc-private",
                    "method": "item/commandExecution/requestApproval",
                    "params": {
                        "threadId": "thread-controller",
                        "turnId": "turn-1",
                        "itemId": "item-1",
                        "startedAtMs": 1000,
                        "command": "tool --check fixture.txt",
                        "cwd": "workspace/fixture",
                    },
                }
            )
            if _read() != {"id": "rpc-private", "result": {"decision": "decline"}}:
                return 20
            _write(
                {
                    "method": "serverRequest/resolved",
                    "params": {
                        "requestId": "rpc-private",
                        "threadId": "thread-controller",
                    },
                }
            )
        _write(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread-controller",
                    "turn": {
                        "id": "turn-1",
                        "items": [],
                        "status": (
                            "interrupted" if mode == "turn_interrupt" else "completed"
                        ),
                    },
                },
            }
        )
    sys.stdin.read()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
