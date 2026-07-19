from __future__ import annotations

from types import SimpleNamespace

import pytest
import serial
from cardputer_codex_bridge.device_discovery import PySerialDeviceProvider
from cardputer_codex_bridge.device_discovery import provider as provider_module


def test_pyserial_provider_only_calls_list_ports(monkeypatch: pytest.MonkeyPatch) -> None:
    open_calls = 0

    def fail_if_opened(*_args: object, **_kwargs: object) -> None:
        nonlocal open_calls
        open_calls += 1
        raise AssertionError("serial port must not be opened")

    def synthetic_ports(*, include_links: bool) -> list[SimpleNamespace]:
        assert include_links is False
        return [
            SimpleNamespace(
                device="fixture-port-provider",
                vid=0x303A,
                pid=0x1001,
                serial_number="fixture-private-selector",
            )
        ]

    monkeypatch.setattr(serial, "Serial", fail_if_opened)
    monkeypatch.setattr(provider_module.list_ports, "comports", synthetic_ports)

    devices = PySerialDeviceProvider().list_devices()

    assert len(devices) == 1
    assert open_calls == 0
