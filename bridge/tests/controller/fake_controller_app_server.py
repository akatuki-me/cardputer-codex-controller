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
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    mode = sys.argv[1] if len(sys.argv) > 1 else "idle"
    initialize = _read()
    if initialize is None or initialize.get("method") != "initialize":
        return 10
    initialize_params = initialize.get("params")
    if not isinstance(initialize_params, dict):
        return 21
    capabilities = initialize_params.get("capabilities")
    if not isinstance(capabilities, dict):
        return 22
    if capabilities.get("experimentalApi") is not True:
        return 23
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
    elif mode in (
        "turn",
        "turn_approval",
        "turn_interrupt",
        "turn_mixed_pending",
        "turn_user_input",
        "unsupported_request",
    ):
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
        if mode == "unsupported_request":
            _write(
                {
                    "id": "rpc-unsupported-private",
                    "method": "item/tool/unsupportedExperimental",
                    "params": {
                        "threadId": "thread-controller",
                        "turnId": "turn-1",
                    },
                }
            )
            if _read() != {
                "id": "rpc-unsupported-private",
                "error": {
                    "code": -32601,
                    "message": "unsupported server request",
                },
            }:
                return 27
            _write(
                {
                    "method": "serverRequest/resolved",
                    "params": {
                        "requestId": "rpc-unsupported-private",
                        "threadId": "thread-controller",
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
        elif mode == "turn_user_input":
            time.sleep(0.05)
            _write(
                {
                    "id": "rpc-question-private",
                    "method": "item/tool/requestUserInput",
                    "params": {
                        "threadId": "thread-controller",
                        "turnId": "turn-1",
                        "itemId": "item-question-private",
                        "questions": [
                            {
                                "id": "schema-question-private",
                                "header": "方針",
                                "question": "合成方針を選んでください",
                                "isOther": True,
                                "isSecret": False,
                                "options": [
                                    {"label": "A", "description": "最小構成"},
                                    {"label": "B", "description": "拡張構成"},
                                ],
                            }
                        ],
                    },
                }
            )
            if _read() != {
                "id": "rpc-question-private",
                "result": {
                    "answers": {
                        "schema-question-private": {"answers": ["A で進める"]}
                    }
                },
            }:
                return 24
            _write(
                {
                    "method": "serverRequest/resolved",
                    "params": {
                        "requestId": "rpc-question-private",
                        "threadId": "thread-controller",
                    },
                }
            )
        elif mode == "turn_mixed_pending":
            time.sleep(0.05)
            _write(
                {
                    "id": "rpc-question-private",
                    "method": "item/tool/requestUserInput",
                    "params": {
                        "threadId": "thread-controller",
                        "turnId": "turn-1",
                        "itemId": "item-question-private",
                        "questions": [
                            {
                                "id": "schema-question-private",
                                "header": "方針",
                                "question": "合成方針を選んでください",
                                "isSecret": False,
                            }
                        ],
                    },
                }
            )
            _write(
                {
                    "id": "rpc-approval-private",
                    "method": "item/commandExecution/requestApproval",
                    "params": {
                        "threadId": "thread-controller",
                        "turnId": "turn-1",
                        "itemId": "item-approval-private",
                        "startedAtMs": 1000,
                        "command": "tool --check fixture.txt",
                        "cwd": "workspace/fixture",
                    },
                }
            )
            if _read() != {
                "id": "rpc-approval-private",
                "result": {"decision": "decline"},
            }:
                return 25
            _write(
                {
                    "method": "serverRequest/resolved",
                    "params": {
                        "requestId": "rpc-approval-private",
                        "threadId": "thread-controller",
                    },
                }
            )
            if _read() != {
                "id": "rpc-question-private",
                "result": {
                    "answers": {
                        "schema-question-private": {"answers": ["A"]}
                    }
                },
            }:
                return 26
            _write(
                {
                    "method": "serverRequest/resolved",
                    "params": {
                        "requestId": "rpc-question-private",
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
