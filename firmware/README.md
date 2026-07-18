# Firmware

Cardputer-Adv向けdevice-link v1 firmwareです。PlatformIOとArduino frameworkを使用し、USB CDC上のNDJSONを処理します。

## 実装範囲

- 240×135の`SCR-HOME`、`SCR-RUN`、`SCR-APPROVE`
- 6 slotのfull snapshotと単調増加`seq`
- `hello`、`state`、`approval`、`approval_resolved`、`toast`、`ping`
- 500msのBtnA長押しによるfocus中slotの`interrupt`
- host→device 4096 byte、device→host 1024 byteの固定上限
- partial line、multiple line、invalid UTF-8、oversize line、未知`t`の安全な処理
- 6秒無受信のstale overlayと、stale/service非ready時の送信lock
- `contentComplete`、`riskClass`、300ms、本文末尾、ID一致によるaccept guard

credential、prompt履歴、approval本文をNVSまたはmicroSDへ保存する処理はありません。

## Buildとfixture

repositoryの開発依存関係を導入した環境で次を実行します。

```powershell
python -m platformio test -d firmware -e native
python -m platformio run -d firmware -e cardputer_adv
```

application imageは`firmware/.pio/build/cardputer_adv/firmware.bin`へ生成されます。build commandはportを開かず、実機へ書き込みません。

## Hardware safety

初回書き込み承認を求める前に、factory firmwareを独立に2回読み出してsizeとSHA-256を照合し、手動download modeと復元commandを手順化します。承認後の最初の書き込みは照合済みbackup imageの復元試験とし、正常起動を確認してからcontroller firmwareへ進みます。

このbranchで検証するのはbuildと合成fixtureだけです。実機表示、USB CDC、BtnA、flash read/write、port openは未検証です。
