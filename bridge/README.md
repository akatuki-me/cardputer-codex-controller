# Host bridge

Python 3.12で、Codex app-serverのstdio JSONLとCardputer-AdvのUSB CDC/NDJSONを変換します。

主要責務は次のとおりです。

- app-server lifecycleとserver requestの処理
- 6 slotの状態集約
- pending queueとhost側の最小応答手段
- device linkのserialization、再接続、snapshot復元
- safety guardと監査可能な最小ログ

## 合成controllerデモ

```powershell
cardputer-codex-controller demo
```

合成adapterを使用するため`codexConnection`は`N/A`で、実portは開きません。`codex-demo`はapp-server操作を独立に実測します。

## M1 bring-up harness

```powershell
cardputer-codex-controller bringup --synthetic
```

`BringupSession`はprovider injectionでpyserialと合成transportを切り替える`SerialLink`を使い、M1専用decoder/encoderだけを注入します。device `hello`を受けるまでhostから送信せず、接続ごとに新しいopaque sessionと`seq=1`を作ります。合成fixtureはCardputer-Adv identity、4 KiB echo、RTT、連続echo throughput、heartbeat/heap、数字key、G0短押し・長押しを検証します。Codex app-serverは起動しません。

実portでは`bringup --port ... --dry-run`または`--port-handle ... --dry-run`を先に実行します。dry-runはproviderを作らず、serial I/Oを開始しません。pyserial providerはhardware flow controlを無効化し、DTR/RTSを個別に操作しません。

## USB CDC E2E

```powershell
cardputer-codex-controller e2e --synthetic
```

`SerialLink`はprovider injectionによりpyserialと合成transportを切り替えます。read threadでNDJSONを受信し、2秒周期の`ping`、6秒のstale判定、再接続時の`hello`とfull snapshot再送を行います。合成E2Eでは実Codexのactive turnへdevice `interrupt`を一度だけ転送し、実serial I/Oは行いません。

実portは`--port`またはGit管理外の`--port-handle`でだけ選択できます。`--dry-run`ではportを開かず、Codexも起動しません。pyserial providerはhardware flow controlを無効化し、DTR/RTSを個別に操作しません。
