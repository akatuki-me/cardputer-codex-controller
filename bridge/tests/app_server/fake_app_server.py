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
    if mode == "environment" and any(
        os.environ.get(name)
        for name in ("CODEX_ACCESS_TOKEN", "GH_TOKEN", "GITHUB_TOKEN", "OPENAI_API_KEY")
    ):
        return 14
    if mode == "sensitive-environment" and os.environ.get("CODEX_ACCESS_TOKEN") != (
        "fixture-token"
    ):
        return 15

    if mode == "stderr":
        sys.stderr.write("warning: synthetic warning\n")
        sys.stderr.write("error: synthetic error with credential marker\n")
        sys.stderr.flush()

    version = "9.9.9" if mode == "version-mismatch" else "0.144.5"
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

    if mode == "ignore-eof":
        time.sleep(60)
        return 0

    sys.stdin.read()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
