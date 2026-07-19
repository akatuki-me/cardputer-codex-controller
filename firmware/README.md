# Firmware

Cardputer-Adv向けdevice-link v1 firmwareです。PlatformIOとArduino frameworkを使用し、USB CDC上のNDJSONを処理します。

## 実装範囲

- 240×135の`SCR-HOME`、`SCR-RUN`、`SCR-APPROVE`
- 6 slotのfull snapshotと単調増加`seq`
- `hello`、`state`、`approval`、`approval_resolved`、`toast`、`ping`
- 500msのG0長押しによるfocus中slotの`interrupt`
- host→device 4096 byte、device→host 1024 byteの固定上限
- 最大host line 2本分を保持する8192 byteのUSB CDC RX queue
- partial line、multiple line、invalid UTF-8、oversize line、未知`t`の安全な処理
- 6秒無受信のstale overlayと、stale/service非ready時の送信lock
- `contentComplete`、`riskClass`、300ms、本文末尾、ID一致によるaccept guard

credential、prompt履歴、approval本文をNVSまたはmicroSDへ保存する処理はありません。

## 物理操作

- `1`〜`6`: focus slotを選択
- `j` / `k`: approval本文を下 / 上へscroll
- `0`: approvalをlocal holdし、requestを解決しない
- `a` / `d`: accept / declineを選択
- Enter: 選択したapproval responseを確定
- G0を500ms長押し: focus中active turnをinterrupt

EnterはM5Cardputerのprintable keyではなくspecial-key stateとして取得します。Production実機でhold、accept、decline、accept禁止、G0 interruptを受入済みです。

## Buildとfixture

repositoryの開発依存関係を導入した環境で次を実行します。

```powershell
python -m platformio test -d firmware -e native
python -m platformio run -d firmware -e cardputer_adv
```

application imageは`firmware/.pio/build/cardputer_adv/firmware.bin`へ生成されます。build commandはportを開かず、実機へ書き込みません。

## M1 diagnostic firmware

Cardputer-Adv固有のbring-upをCodexから切り離すため、`cardputer_adv_bringup` targetを用意しています。

```powershell
python -m platformio run -d firmware -e cardputer_adv_bringup
```

生成物は`firmware/.pio/build/cardputer_adv_bringup/firmware.bin`です。このfirmwareは次だけを扱います。

- `M5.getBoard()`によるCardputer-Adv判定
- USB CDCのdevice hello、host hello、echo、ping/pong、heartbeat、device計測のstale通知
- printable keyboard eventとG0のpress・short・500ms long・release
- free heap、受信数、送信数、error数

`state`、`approval`、`decision`、`interrupt`などのCodex commandは実装していません。4KiB echoはpayloadを返信せず、byte数とFNV-1a checksumだけを返します。接続ごとのopaqueなhost sessionが変わった場合だけhost側sequenceを初期化します。

USB Serial/JTAGでflasher stub起動後の通信消失を避けるため、productionと診断の両targetは`upload_speed = 115200`と`--no-stub`を継承し、ESP32-S3のROM loaderを使用します。この設定はimage内容には影響せず、upload時だけ適用されます。

書き込みとCOM port openはbuildとは別のhardware gateです。対象deviceとcommandを提示し、人間の明示承認を得るまで実行しません。

## Hardware safety

初回書き込み承認を求める前に、factory firmwareを独立に2回読み出してsizeとSHA-256を照合し、手動download modeと復元commandを手順化します。承認後の最初の書き込みは照合済みbackup imageの復元試験とし、正常起動を確認してからcontroller firmwareへ進みます。

実機表示、USB CDC、keyboard、G0、flash read/write、port openは、実行した結果だけをhardware PASSとして扱います。
