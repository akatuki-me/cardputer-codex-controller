from __future__ import annotations

import importlib
import threading
from typing import Any

import pytest
from cardputer_codex_bridge.device_link import (
    PySerialProvider,
    SerialLink,
    SerialPort,
)


def test_pyserial_provider_disables_flow_control_without_line_toggles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    class FakePort:
        def read(self, size: int = 1) -> bytes:
            del size
            return b""

        def write(self, data: bytes) -> int:
            return len(data)

        def close(self) -> None:
            pass

    class FakeSerialModule:
        @staticmethod
        def Serial(**kwargs: Any) -> FakePort:
            calls.append(kwargs)
            return FakePort()

    monkeypatch.setattr(importlib, "import_module", lambda name: FakeSerialModule())

    PySerialProvider().open("synthetic-port", baudrate=115_200, read_timeout=0.1)

    assert len(calls) == 1
    assert calls[0]["rtscts"] is False
    assert calls[0]["dsrdtr"] is False
    assert "dtr" not in calls[0]
    assert "rts" not in calls[0]


def test_close_contains_attribute_error_from_windows_double_close_race() -> None:
    class CloseRacePort:
        def __init__(self) -> None:
            self.unblock = threading.Event()
            self.close_calls = 0

        def read(self, size: int = 1) -> bytes:
            del size
            self.unblock.wait(timeout=5.0)
            raise OSError("synthetic close")

        def write(self, data: bytes) -> int:
            return len(data)

        def close(self) -> None:
            self.close_calls += 1
            self.unblock.set()
            if self.close_calls > 1:
                raise AttributeError("synthetic Windows serial close race")

    class SinglePortProvider:
        def __init__(self) -> None:
            self.port = CloseRacePort()

        def open(
            self,
            port: str,
            *,
            baudrate: int,
            read_timeout: float,
        ) -> SerialPort:
            del port, baudrate, read_timeout
            return self.port

    provider = SinglePortProvider()
    link = SerialLink(
        port="synthetic",
        provider=provider,
        on_message=lambda message: None,
        on_connected=lambda: None,
        on_stale=lambda: None,
        ping_factory=lambda: {"t": "ping", "seq": 1},
        read_timeout=0.01,
        ping_interval=1.0,
        stale_after=3.0,
        reconnect_delay=0.01,
    )

    link.start()
    assert link.wait_connected(1.0)
    link.close()

    assert provider.port.close_calls == 2
    assert link.last_unexpected_error_type is None
