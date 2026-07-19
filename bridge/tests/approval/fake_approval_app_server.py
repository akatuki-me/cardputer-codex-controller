from __future__ import annotations

import json
import sys
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
                "userAgent": "cardputer-test/0.144.5 (synthetic)",
            },
        }
    )
    if _read() != {"method": "initialized"}:
        return 11

    _write(
        {
            "id": "approval-command-1",
            "method": "item/commandExecution/requestApproval",
            "params": {
                "threadId": "thread-synthetic",
                "turnId": "turn-synthetic",
                "itemId": "item-command",
                "startedAtMs": 1000,
                "command": "tool --check fixture.txt",
                "cwd": "workspace",
                "reason": "合成fixtureの確認",
            },
        }
    )
    if _read() != {
        "id": "approval-command-1",
        "result": {"decision": "accept"},
    }:
        return 12
    _write(
        {
            "method": "serverRequest/resolved",
            "params": {
                "requestId": "approval-command-1",
                "threadId": "thread-synthetic",
            },
        }
    )

    _write(
        {
            "id": 2,
            "method": "item/fileChange/requestApproval",
            "params": {
                "threadId": "thread-synthetic",
                "turnId": "turn-synthetic",
                "itemId": "item-file",
                "startedAtMs": 1001,
                "reason": "相対pathの合成変更",
                "grantRoot": "workspace/fixture",
            },
        }
    )
    if _read() != {"id": 2, "result": {"decision": "decline"}}:
        return 13
    _write(
        {
            "method": "serverRequest/resolved",
            "params": {"requestId": 2, "threadId": "thread-synthetic"},
        }
    )
    sys.stdin.read()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
