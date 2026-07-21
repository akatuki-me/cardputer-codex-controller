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

## M3 approval fixture

```powershell
cardputer-codex-controller approval-fixture --synthetic
```

`approval-fixture`はapp-serverやcommand executorを起動せず、長文low-risk、high-risk、本文不完全の固定3件だけを`DeviceControllerSession`へ送ります。合成deviceは状態機械とsanitized outputを検証し、実portではoperatorがCardputerの画面と物理キーを確認してからhost consoleで`ok`と入力し`Enter`で確定します。最初の300ms guardはarm promptで早押しを事前準備します。`--port ... --dry-run`または`--port-handle ... --dry-run`はproviderを生成せず、serial I/Oを開始しません。実portでdry-runを外す操作にはhardware承認が必要です。

## USB CDC E2E

```powershell
cardputer-codex-controller e2e --synthetic
```

`SerialLink`はprovider injectionによりpyserialと合成transportを切り替えます。read threadでNDJSONを受信し、handshake後の2秒周期`ping`、6秒のstale判定、再接続時のopaque session付き`hello`とfull snapshot再送を行います。合成E2Eでは実Codexのactive turnへ、slotと`turnId`が一致するdevice `interrupt`を一度だけ転送し、実serial I/Oは行いません。

実portは`--port`またはGit管理外の`--port-handle`でだけ選択できます。`--dry-run`ではportを開かず、Codexも起動しません。pyserial providerはhardware flow controlを無効化し、DTR/RTSを個別に操作しません。

## Host controller runtime

```powershell
cardputer-codex-controller control --synthetic --cwd . --label main
```

非dry-runの`control`はprocess間lockをprovider生成とCodex起動より前に取得し、同時に1processだけを許可します。二重起動は固定のerror classだけを表示し、port、PID、lock pathを出しません。終了時はdevice sessionとserial read threadをapp-server shutdown待機より先に閉じ、client cleanup完了後にlockを解放します。`--dry-run`はinstance lockを取得しません。
