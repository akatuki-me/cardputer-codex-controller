"""Cardputer候補のread-only discovery。"""

from __future__ import annotations

from collections.abc import Iterable

from .errors import DeviceEnumerationError, LocalDeviceFilterError
from .provider import DeviceProvider, PySerialDeviceProvider
from .types import (
    DeviceCandidate,
    DiscoveryResult,
    LocalDeviceFilter,
    LocalDeviceHandle,
    UsbIdentity,
)

CARDPUTER_SERIAL_JTAG_IDENTITY = UsbIdentity(vid=0x303A, pid=0x1001)


def discover_devices(
    *,
    identities: Iterable[UsbIdentity],
    provider: DeviceProvider,
    local_filter: LocalDeviceFilter | None = None,
) -> DiscoveryResult:
    """共通USB識別情報と任意のlocal-only filterで候補を列挙する。

    この関数はproviderの ``list_devices`` 以外を呼ばず、portをopenしない。
    """

    accepted_identities = frozenset(identities)
    enumeration_failed = False
    try:
        devices = provider.list_devices()
    except Exception:
        enumeration_failed = True
        devices = ()
    if enumeration_failed:
        raise DeviceEnumerationError("device enumeration failed")

    matches: list[DeviceCandidate] = []
    for device in devices:
        identity = device.usb_identity
        if identity is None or identity not in accepted_identities:
            continue
        if local_filter is not None:
            filter_failed = False
            try:
                accepted = local_filter(device)
            except Exception:
                filter_failed = True
                accepted = False
            if filter_failed:
                raise LocalDeviceFilterError("local device filter failed")
            if not accepted:
                continue
        matches.append(
            DeviceCandidate(
                scan_index=len(matches) + 1,
                usb_identity=identity,
                local_handle=LocalDeviceHandle(
                    locator=device.locator,
                    serial_number=device.serial_number,
                ),
            )
        )
    return DiscoveryResult(candidates=tuple(matches))


def discover_cardputer_devices(
    *,
    provider: DeviceProvider | None = None,
    local_filter: LocalDeviceFilter | None = None,
) -> DiscoveryResult:
    """CardputerのUSB Serial/JTAG候補をread-onlyで列挙する。"""

    selected_provider = provider if provider is not None else PySerialDeviceProvider()
    return discover_devices(
        identities=(CARDPUTER_SERIAL_JTAG_IDENTITY,),
        provider=selected_provider,
        local_filter=local_filter,
    )
