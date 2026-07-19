"""Device discovery固有の例外。"""


class DeviceDiscoveryError(RuntimeError):
    """Device discoveryを安全に完了できなかった。"""


class DeviceEnumerationError(DeviceDiscoveryError):
    """OSのdevice列挙に失敗した。"""


class LocalDeviceFilterError(DeviceDiscoveryError):
    """Local-only filterの評価に失敗した。"""
