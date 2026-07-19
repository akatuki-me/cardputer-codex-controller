from __future__ import annotations

import json
import os
import sys
import time
from typing import Any


def _read_message() -> dict[str, Any] | None:
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


def _read_request(method: str, expected_id: int) -> dict[str, Any]:
    request = _read_message()
    if request is None:
        raise RuntimeError(f"missing {method} request")
    if request.get("method") != method or request.get("id") != expected_id:
        raise RuntimeError(f"unexpected {method} request")
    return request


def _synthetic_thread() -> dict[str, Any]:
    return {
        "cliVersion": "0.144.5",
        "createdAt": 1,
        "cwd": "synthetic-workspace",
        "ephemeral": True,
        "id": "thread-synthetic-1",
        "modelProvider": "synthetic-provider",
        "preview": "",
        "sessionId": "session-synthetic-1",
        "source": "appServer",
        "status": {"type": "idle"},
        "turns": [],
        "updatedAt": 1,
    }


def _run_operations(mode: str) -> int:
    if mode == "operations-error":
        request = _read_request("model/list", 2)
        _write({"id": request["id"], "error": {"code": -32602, "message": "synthetic"}})
        sys.stdin.read()
        return 0

    if mode == "model-list-empty":
        request = _read_request("model/list", 2)
        _write({"id": request["id"], "result": {"data": [], "nextCursor": None}})
        sys.stdin.read()
        return 0

    thread_request = _read_request("thread/start", 2)
    thread_params = thread_request.get("params")
    if not isinstance(thread_params, dict) or thread_params.get("ephemeral") is not True:
        return 20
    _write(
        {
            "id": thread_request["id"],
            "result": {
                "thread": _synthetic_thread(),
                "approvalPolicy": "never",
                "approvalsReviewer": "user",
                "cwd": "synthetic-workspace",
                "model": "synthetic-model",
                "modelProvider": "synthetic-provider",
                "sandbox": {"type": "readOnly", "networkAccess": False},
            },
        }
    )

    model_request = _read_request("model/list", 3)
    _write(
        {
            "id": model_request["id"],
            "result": {
                "data": [
                    {
                        "defaultReasoningEffort": "medium",
                        "description": "Synthetic future model",
                        "displayName": "Synthetic Future",
                        "hidden": False,
                        "id": "future-model-id",
                        "isDefault": False,
                        "model": "future-model",
                        "supportedReasoningEfforts": [
                            {
                                "description": "Synthetic effort",
                                "reasoningEffort": "future-effort",
                            }
                        ],
                    }
                ],
                "nextCursor": None,
            },
        }
    )

    turn_request = _read_request("turn/start", 4)
    turn_params = turn_request.get("params")
    if not isinstance(turn_params, dict) or turn_params.get("threadId") != (
        "thread-synthetic-1"
    ):
        return 21
    _write(
        {
            "method": "turn/started",
            "params": {
                "threadId": "thread-synthetic-1",
                "turn": {
                    "id": "turn-synthetic-1",
                    "items": [],
                    "status": "inProgress",
                },
            },
        }
    )
    _write(
        {
            "id": turn_request["id"],
            "result": {
                "turn": {
                    "id": "turn-synthetic-1",
                    "items": [],
                    "status": "inProgress",
                }
            },
        }
    )

    steer_request = _read_request("turn/steer", 5)
    steer_params = steer_request.get("params")
    if not isinstance(steer_params, dict) or steer_params.get("expectedTurnId") != (
        "turn-synthetic-1"
    ):
        return 22
    _write({"id": steer_request["id"], "result": {"turnId": "turn-synthetic-1"}})

    interrupt_request = _read_request("turn/interrupt", 6)
    interrupt_params = interrupt_request.get("params")
    if not isinstance(interrupt_params, dict) or interrupt_params.get("turnId") != (
        "turn-synthetic-1"
    ):
        return 23
    _write({"id": interrupt_request["id"], "result": {}})
    _write(
        {
            "method": "turn/completed",
            "params": {
                "threadId": "thread-synthetic-1",
                "turn": {
                    "id": "turn-synthetic-1",
                    "items": [],
                    "status": "interrupted",
                },
            },
        }
    )
    sys.stdin.read()
    return 0


def main() -> int:
    mode = sys.argv[1]
    request = _read_message()
    if request is None:
        return 10
    request_id = request.get("id")

    if mode == "early-eof":
        return 0
    if mode == "timeout":
        time.sleep(60)
        return 0
    if mode == "oversized-stdout":
        sys.stdout.write("x" * 4096)
        sys.stdout.flush()
        time.sleep(60)
        return 0
    if mode == "oversized-stderr":
        sys.stderr.write("x" * 4096)
        sys.stderr.flush()
        time.sleep(60)
        return 0
    if mode == "invalid-json":
        sys.stdout.write("not-json\n")
        sys.stdout.flush()
        sys.stdin.read()
        return 0
    if mode == "wrong-id":
        _write({"id": 999, "result": {}})
        sys.stdin.read()
        return 0
    if mode == "malformed-response":
        _write({"id": request_id})
        sys.stdin.read()
        return 0

    if request.get("method") != "initialize":
        return 11
    if mode == "experimental-capability":
        params = request.get("params")
        if not isinstance(params, dict):
            return 15
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return 16
        if capabilities.get("experimentalApi") is not True:
            return 17
    if mode == "environment" and any(
        os.environ.get(name)
        for name in (
            "CODEX_ACCESS_TOKEN",
            "DATABASE_URL",
            "DOCKER_AUTH_CONFIG",
            "GH_TOKEN",
            "GITHUB_TOKEN",
            "OPENAI_API_KEY",
            "SESSION_COOKIE",
        )
    ):
        return 14
    if mode == "environment" and not os.environ.get("PATH"):
        return 16
    if mode == "sensitive-environment" and os.environ.get("CODEX_ACCESS_TOKEN") != (
        "fixture-token"
    ):
        return 15
    if mode == "explicit-environment" and os.environ.get("DATABASE_URL") != (
        "fixture-database-url"
    ):
        return 17

    if mode == "stderr":
        sys.stderr.write("warning: synthetic warning\n")
        sys.stderr.write("error: synthetic error with credential marker\n")
        sys.stderr.flush()

    if mode == "version-mismatch":
        version = "9.9.9"
    elif mode == "version-0.144.6":
        version = "0.144.6"
    else:
        version = "0.144.5"
    _write(
        {
            "id": request_id,
            "result": {
                "codexHome": "fixture-codex-home",
                "platformFamily": "synthetic",
                "platformOs": "synthetic",
                "userAgent": f"cardputer-test/{version} (synthetic)",
            },
        }
    )

    notification = _read_message()
    if mode == "version-mismatch":
        return 0 if notification is None else 12
    if notification != {"method": "initialized"}:
        return 13

    if mode in {"operations", "operations-error", "model-list-empty"}:
        return _run_operations(mode)

    if mode == "ignore-eof":
        time.sleep(60)
        return 0
    if mode == "eof-after-initialized":
        time.sleep(0.1)
        return 0
    if mode == "invalid-after-initialized":
        time.sleep(0.1)
        sys.stdout.write("not-json\n")
        sys.stdout.flush()
        time.sleep(60)
        return 0
    if mode == "delayed-request-response":
        delayed_request = _read_message()
        if delayed_request is None:
            return 18
        time.sleep(0.2)
        _write({"id": delayed_request.get("id"), "result": {"ok": True}})
        sys.stdin.read()
        return 0

    sys.stdin.read()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
