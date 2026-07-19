"""OS device列挙provider。"""

from __future__ import annotations

from typing import Protocol

from serial.tools import list_ports  # type: ignore[import-untyped]

from .types import EnumeratedDevice


class DeviceProvider(Protocol):
    """Portをopenせず、OSの列挙結果だけを返すprovider。"""

    def list_devices(self) -> tuple[EnumeratedDevice, ...]: ...


class PySerialDeviceProvider:
    """pyserialのread-only ``list_ports`` adapter。"""

    def list_devices(self) -> tuple[EnumeratedDevice, ...]:
        return tuple(
            EnumeratedDevice(
                locator=port.device,
                vid=port.vid,
                pid=port.pid,
                serial_number=port.serial_number,
            )
            for port in list_ports.comports(include_links=False)
        )
