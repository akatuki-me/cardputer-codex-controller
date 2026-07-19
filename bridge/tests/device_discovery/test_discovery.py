from __future__ import annotations

from dataclasses import dataclass

import pytest
from cardputer_codex_bridge.device_discovery import (
    CARDPUTER_SERIAL_JTAG_IDENTITY,
    DeviceEnumerationError,
    DiscoveryStatus,
    EnumeratedDevice,
    LocalDeviceFilterError,
    discover_cardputer_devices,
)


def fixture_device(
    name: str,
    *,
    vid: int = CARDPUTER_SERIAL_JTAG_IDENTITY.vid,
    pid: int = CARDPUTER_SERIAL_JTAG_IDENTITY.pid,
    serial_number: str | None = None,
) -> EnumeratedDevice:
    return EnumeratedDevice(
        locator=f"fixture-port-{name}",
        vid=vid,
        pid=pid,
        serial_number=serial_number,
    )


@dataclass
class OpenTrapProvider:
    devices: tuple[EnumeratedDevice, ...]
    list_calls: int = 0
    open_calls: int = 0

    def list_devices(self) -> tuple[EnumeratedDevice, ...]:
        self.list_calls += 1
        return self.devices

    def open(self, _locator: str) -> None:
        self.open_calls += 1
        raise AssertionError("discovery must not open a port")


@pytest.mark.parametrize(
    ("devices", "status", "count"),
    [
        ((), DiscoveryStatus.NOT_FOUND, 0),
        ((fixture_device("only"),), DiscoveryStatus.UNIQUE, 1),
        (
            (fixture_device("first"), fixture_device("second")),
            DiscoveryStatus.AMBIGUOUS,
            2,
        ),
    ],
)
def test_zero_one_and_multiple_candidates_are_explicit(
    devices: tuple[EnumeratedDevice, ...],
    status: DiscoveryStatus,
    count: int,
) -> None:
    provider = OpenTrapProvider(devices)

    result = discover_cardputer_devices(provider=provider)

    assert result.status is status
    assert result.count == count
    assert (result.unique_candidate is not None) is (status is DiscoveryStatus.UNIQUE)
    assert provider.list_calls == 1
    assert provider.open_calls == 0


def test_nonmatching_usb_identity_is_excluded() -> None:
    provider = OpenTrapProvider((fixture_device("other", vid=0xFFFF, pid=0xEEEE),))

    result = discover_cardputer_devices(provider=provider)

    assert result.status is DiscoveryStatus.NOT_FOUND


def test_local_only_filter_selects_without_exposing_private_fields() -> None:
    private_value = "fixture-private-selector"
    provider = OpenTrapProvider(
        (
            fixture_device("first", serial_number="fixture-other-selector"),
            fixture_device("selected", serial_number=private_value),
        )
    )

    result = discover_cardputer_devices(
        provider=provider,
        local_filter=lambda device: device.serial_number == private_value,
    )

    assert result.status is DiscoveryStatus.UNIQUE
    assert result.unique_candidate is not None
    rendered = repr(result)
    assert private_value not in rendered
    assert "fixture-port-selected" not in rendered
    assert result.unique_candidate.usb_identity == CARDPUTER_SERIAL_JTAG_IDENTITY


def test_enumerated_device_and_handle_repr_are_redacted() -> None:
    device = fixture_device("private", serial_number="fixture-private-selector")
    provider = OpenTrapProvider((device,))

    result = discover_cardputer_devices(provider=provider)

    assert "fixture-port-private" not in repr(device)
    assert "fixture-private-selector" not in repr(device)
    assert result.unique_candidate is not None
    assert "fixture-port-private" not in repr(result.unique_candidate.local_handle)


def test_provider_failure_does_not_copy_provider_message() -> None:
    class FailingProvider:
        def list_devices(self) -> tuple[EnumeratedDevice, ...]:
            raise RuntimeError("fixture-port-private")

    with pytest.raises(DeviceEnumerationError, match="^device enumeration failed$") as caught:
        discover_cardputer_devices(provider=FailingProvider())

    assert "fixture-port-private" not in str(caught.value)
    assert caught.value.__context__ is None


def test_local_filter_failure_does_not_copy_private_value() -> None:
    provider = OpenTrapProvider((fixture_device("private"),))

    def failing_filter(_device: EnumeratedDevice) -> bool:
        raise RuntimeError("fixture-private-selector")

    with pytest.raises(LocalDeviceFilterError, match="^local device filter failed$") as caught:
        discover_cardputer_devices(provider=provider, local_filter=failing_filter)

    assert "fixture-private-selector" not in str(caught.value)
    assert caught.value.__context__ is None
