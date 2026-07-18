# Firmware

Cardputer-Adv向けfirmwareはM1から実装します。

採用方針:

- PlatformIO
- Arduino framework
- M5Cardputer / M5Unified / M5GFX
- USB Serial/JTAGを維持
- USB CDC上のNDJSON

初回書き込み承認を求める前に、factory firmwareを独立に2回読み出してsizeとSHA-256を照合し、手動download modeと復元commandを手順化します。承認後の最初の書き込みは照合済みbackup imageの復元試験とし、正常起動を確認してからecho firmwareへ進みます。CIはbuildだけを扱い、実機操作を行いません。
