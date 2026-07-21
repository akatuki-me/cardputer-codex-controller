from __future__ import annotations

import multiprocessing
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from cardputer_codex_bridge.controller import instance_guard as instance_guard_module
from cardputer_codex_bridge.controller.instance_guard import (
    ControllerAlreadyRunningError,
    ControllerInstanceGuard,
)


def _hold_guard(lock_path: str, ready: Any, release: Any) -> None:
    with ControllerInstanceGuard(Path(lock_path)):
        ready.set()
        release.wait(timeout=10.0)


def _start_guard_process(lock_path: Path) -> tuple[Any, Any, Any]:
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_guard,
        args=(str(lock_path), ready, release),
    )
    process.start()
    assert ready.wait(timeout=5.0)
    return process, ready, release


def test_guard_rejects_a_second_process_and_releases_normally(tmp_path: Path) -> None:
    lock_path = tmp_path / "controller.lock"
    process, _, release = _start_guard_process(lock_path)
    try:
        with (
            pytest.raises(ControllerAlreadyRunningError),
            ControllerInstanceGuard(lock_path),
        ):
            pass
    finally:
        release.set()
        process.join(timeout=5.0)
        if process.is_alive():
            process.kill()
            process.join(timeout=5.0)

    assert process.exitcode == 0
    with ControllerInstanceGuard(lock_path):
        pass


def test_guard_releases_after_context_exception(tmp_path: Path) -> None:
    lock_path = tmp_path / "controller.lock"

    with (
        pytest.raises(RuntimeError, match="fixture failure"),
        ControllerInstanceGuard(lock_path),
    ):
        raise RuntimeError("fixture failure")

    with ControllerInstanceGuard(lock_path):
        pass


def test_operating_system_releases_guard_after_process_termination(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "controller.lock"
    bridge_root = Path(__file__).parents[2]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        value
        for value in (str(bridge_root), environment.get("PYTHONPATH"))
        if value
    )
    script = (
        "import sys, time; "
        "from pathlib import Path; "
        "from cardputer_codex_bridge.controller.instance_guard import "
        "ControllerInstanceGuard; "
        "guard = ControllerInstanceGuard(Path(sys.argv[1])); "
        "guard.acquire(); print('ready', flush=True); time.sleep(60)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(lock_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline() == "ready\n"
        process.kill()
        process.wait(timeout=5.0)
        with ControllerInstanceGuard(lock_path):
            pass
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5.0)


def test_default_lock_path_is_user_scoped_and_private(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(instance_guard_module.tempfile, "gettempdir", lambda: str(tmp_path))

    lock_path = instance_guard_module._default_lock_path()

    assert lock_path.name == "controller.lock"
    lock_directory = lock_path.parent
    assert lock_directory.parent == tmp_path
    assert lock_directory != tmp_path
    assert lock_directory.name.startswith("cardputer-codex-controller-")
    assert lock_directory.is_dir()
    if os.name != "nt":
        assert (lock_directory.stat().st_mode & 0o777) == 0o700
