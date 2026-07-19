from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

SYNTHETIC_THREAD_ID = "thread-synthetic-shared"
FOREIGN_THREAD_ID = "thread-synthetic-foreign"


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


def _synthetic_thread(thread_id: str = SYNTHETIC_THREAD_ID) -> dict[str, Any]:
    return {
        "cliVersion": "0.144.6",
        "createdAt": 1,
        "cwd": "synthetic-workspace",
        "ephemeral": False,
        "id": thread_id,
        "modelProvider": "synthetic-provider",
        "preview": "",
        "sessionId": "session-synthetic-shared",
        "source": "appServer",
        "status": {"type": "idle"},
        "turns": [],
        "updatedAt": 1,
    }


def _initialize() -> bool:
    request = _read_message()
    if request is None or request.get("method") != "initialize":
        return False
    _write(
        {
            "id": request.get("id"),
            "result": {
                "codexHome": "fixture-codex-home",
                "platformFamily": "synthetic",
                "platformOs": "synthetic",
                "userAgent": "cardputer-test/0.144.6 (synthetic)",
            },
        }
    )
    return _read_message() == {"method": "initialized"}


def _thread_started(thread_id: str = SYNTHETIC_THREAD_ID) -> dict[str, Any]:
    return {
        "method": "thread/started",
        "params": {"thread": _synthetic_thread(thread_id)},
    }


def _resume_result() -> dict[str, Any]:
    return {
        "approvalPolicy": "never",
        "approvalsReviewer": "user",
        "cwd": "synthetic-workspace",
        "model": "synthetic-model",
        "modelProvider": "synthetic-provider",
        "sandbox": {"networkAccess": False, "type": "readOnly"},
        "thread": _synthetic_thread(),
    }


def _run_create(state_path: Path, request: dict[str, Any]) -> int:
    if request.get("method") != "thread/start":
        return 20
    params = request.get("params")
    if not isinstance(params, dict) or params.get("ephemeral") is not False:
        return 21
    state_path.write_text("ready\n", encoding="utf-8")
    _write(_thread_started())
    _write({"id": request.get("id"), "result": _resume_result()})
    sys.stdin.read()
    return 0


def _write_error(request_id: object, code: int) -> None:
    _write(
        {
            "id": request_id,
            "error": {"code": code, "message": "synthetic resume rejected"},
        }
    )


def _run_resume(mode: str, state_path: Path, request: dict[str, Any]) -> int:
    if request.get("method") != "thread/resume":
        return 30
    params = request.get("params")
    if not isinstance(params, dict) or params.get("threadId") != SYNTHETIC_THREAD_ID:
        return 31

    if mode == "resume-not-found" or not state_path.is_file():
        _write_error(request.get("id"), -32004)
    elif mode == "resume-permission-denied":
        _write_error(request.get("id"), -32003)
    elif mode == "resume-invalid-state":
        _write_error(request.get("id"), -32002)
    else:
        notification = _thread_started(
            FOREIGN_THREAD_ID if mode == "resume-unowned" else SYNTHETIC_THREAD_ID
        )
        _write(notification)
        if mode == "resume-duplicate":
            _write(notification)
        _write({"id": request.get("id"), "result": _resume_result()})

    sys.stdin.read()
    return 0


def main() -> int:
    if len(sys.argv) != 3:
        return 10
    mode = sys.argv[1]
    state_path = Path(sys.argv[2])
    if not _initialize():
        return 11
    request = _read_message()
    if request is None:
        return 12
    if mode == "create":
        return _run_create(state_path, request)
    if mode.startswith("resume-"):
        return _run_resume(mode, state_path, request)
    return 13


if __name__ == "__main__":
    raise SystemExit(main())
