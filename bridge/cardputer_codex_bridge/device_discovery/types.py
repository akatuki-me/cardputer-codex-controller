"""Read-only device discoveryの値型。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class UsbIdentity:
    """公開可能なUSB VID/PID識別情報。"""

    vid: int
    pid: int

    def __post_init__(self) -> None:
        for name, value in (("vid", self.vid), ("pid", self.pid)):
            if not 0 <= value <= 0xFFFF:
                raise ValueError(f"{name} must be between 0x0000 and 0xffff")

    @property
    def label(self) -> str:
        """Machine固有情報を含まない正規化済み表現。"""

        return f"{self.vid:04x}:{self.pid:04x}"


class EnumeratedDevice:
    """Providerが返すlocal-only情報。

    locatorとserial numberはfilterおよび後続接続用にmemory内だけで保持する。
    ``repr`` と ``str`` には出さず、永続化可能な変換APIも提供しない。
    """

    __slots__ = ("_locator", "_serial_number", "pid", "vid")

    def __init__(
        self,
        *,
        locator: str,
        vid: int | None,
        pid: int | None,
        serial_number: str | None = None,
    ) -> None:
        self._locator = locator
        self.vid = vid
        self.pid = pid
        self._serial_number = serial_number

    @property
    def locator(self) -> str:
        """後続接続だけが使用するlocal locator。"""

        return self._locator

    @property
    def serial_number(self) -> str | None:
        """Local-only filterだけが使用する端末識別情報。"""

        return self._serial_number

    @property
    def usb_identity(self) -> UsbIdentity | None:
        """VID/PIDが揃っていれば公開可能な共通識別情報を返す。"""

        if self.vid is None or self.pid is None:
            return None
        return UsbIdentity(self.vid, self.pid)

    def __repr__(self) -> str:
        return (
            "EnumeratedDevice("
            f"vid={self.vid!r}, pid={self.pid!r}, local_fields=<redacted>)"
        )

    __str__ = __repr__


LocalDeviceFilter = Callable[[EnumeratedDevice], bool]


class LocalDeviceHandle:
    """後続接続へ渡す、永続化禁止のlocal-only handle。"""

    __slots__ = ("_locator", "_serial_number")

    def __init__(self, *, locator: str, serial_number: str | None = None) -> None:
        self._locator = locator
        self._serial_number = serial_number

    @property
    def locator(self) -> str:
        return self._locator

    @property
    def serial_number(self) -> str | None:
        return self._serial_number

    def __repr__(self) -> str:
        return "LocalDeviceHandle(<redacted>)"

    __str__ = __repr__


@dataclass(frozen=True, slots=True)
class DeviceCandidate:
    """1回のscan内でのみ有効な候補。"""

    scan_index: int
    usb_identity: UsbIdentity
    local_handle: LocalDeviceHandle = field(repr=False, compare=False)


class DiscoveryStatus(StrEnum):
    """候補数を曖昧化しないdiscovery状態。"""

    NOT_FOUND = "not_found"
    UNIQUE = "unique"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    """Sanitized discovery結果。"""

    candidates: tuple[DeviceCandidate, ...]

    @property
    def count(self) -> int:
        return len(self.candidates)

    @property
    def status(self) -> DiscoveryStatus:
        if not self.candidates:
            return DiscoveryStatus.NOT_FOUND
        if len(self.candidates) == 1:
            return DiscoveryStatus.UNIQUE
        return DiscoveryStatus.AMBIGUOUS

    @property
    def unique_candidate(self) -> DeviceCandidate | None:
        if self.status is DiscoveryStatus.UNIQUE:
            return self.candidates[0]
        return None
