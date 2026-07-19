# M1 Cardputer-Adv bring-up

## 状態

非侵襲gate: PASS

hardware read-only preflight: PASS

hardware機能確認（diagnostic v0.1.1）: PASS

stale 6秒以内の実機計時: 未実施

recovery-first・production controller gate: 未実施

## 目的

Cardputer-Advのboard判定、USB CDC、keyboard、G0をCodex app-serverから切り離し、故障箇所を責務ごとに判定します。診断firmwareはCodex commandを送らず、production controllerとは別targetでbuildします。

## 診断protocol

UTF-8 NDJSONを使用し、全messageに単調増加`seq`を付けます。

| 方向 | `t` | 役割 |
| --- | --- | --- |
| device → host | `hello` | protocol、mode、firmware、board、heapを通知 |
| host → device | `hello` | 接続ごとのopaque sessionでhandshake |
| device → host | `ready` | session一致とCardputer-Adv判定を応答 |
| host → device | `echo` | IDとpayloadを送信 |
| device → host | `echo` | byte数とFNV-1a checksumだけを応答 |
| host → device | `ping` | heartbeat IDを送信 |
| device → host | `pong` | 同じIDを応答 |
| device → host | `heartbeat` | uptime、heap、RX/TX/error件数を通知 |
| device → host | `key` | printable keyを数値codeで通知 |
| device → host | `g0` | press、short、long、releaseを通知 |
| device → host | `error` | 値を含めずfailure classだけを通知 |

Hostはserial openごとに新しいsessionと送信`seq=1`を作ります。Firmwareは異なるsessionの`hello`を受けた場合だけ受信sequenceを初期化します。Device送信sequenceはboot中に単調増加し、host decoderは再openごとに初期化します。

## 非侵襲gate結果

- native fixture: 14 passed、0 failed
- host bring-up fixture: firmware version、board、handshake、4 KiB echo、RTT、throughput、heartbeat、keyboard、G0をPASS
- host reconnect fixture: 接続ごとのsession更新、送信`seq=1`へのreset、継続するdevice sequenceの受理をPASS
- host dry-run fixture: providerを生成せず、serial I/OとCodex接続を行わないことをPASS
- 4,096 byte line: 受理
- 4,097 byte line: 破棄
- 同一sessionの過去sequence: 拒否
- 新sessionの`seq=1`: 受理
- G0 499ms: short
- G0 500ms: longを1回だけ通知
- diagnostic v0.1.2 build: PASS、RAM 26,716 bytes、Flash 461,741 bytes
- production build: PASS、RAM 32,188 bytes、Flash 481,469 bytes
- ROM loader read-only probe: ESP32-S3、8 MB flashを確認
- flasher stub path: stub起動後のflash ID要求で通信が途切れ、erase/write開始前に停止
- USB CDC RX queue: 既定256 byteから最大host line 2本分の8192 byteへ拡張

Native fixtureはWindows側に`gcc/g++`がないため、同じsourceをWSLの`g++ 13.3.0`でcompileして実行しました。Cardputer向け2 targetはPlatformIO 6.1.18、Espressif32 6.7.0でbuildしました。

Host側の合成診断は次のcommandで実行できます。出力はstep名とPASS/FAIL classだけで、session、payload、key code、portを表示しません。

```powershell
cardputer-codex-controller bringup --synthetic
```

throughputは512 byteのechoを8回直列送受信し、合計byte数と経過時間から算出します。実portでは`echo_4096_elapsed_ms`、`rtt_ms`、`throughput_bytes_per_second`、`heap_bytes`を単位付きで出力し、port、session、payload、key codeは出力しません。性能の合否閾値はまだ設けず、値が正で測定経路が成立することを受入条件とします。物理入力は各eventを最大120秒待ちます。

## Hardware gate結果

- diagnostic firmware v0.1.1: ROM loader経由の書き込みと各image hash検証をPASS
- USB CDC: port open、hello、ready、small echo、4,096 byte line、heartbeatをPASS
- Cardputer-Adv判定: PASS
- 再接続: 複数回のport再openと新host sessionでPASS
- stale: host close後の`LINK WAIT/STALE`表示を実機で確認。ただし遷移時間は未計測
- keyboard: 数字keyをPASS
- G0: shortと500ms以上のlongをPASS
- 4,096 byte echo: 63 ms
- RTT: 62 ms
- throughput: 8,192 bytes/second
- free heap: 346,756 bytes

値は初回baselineであり、性能保証値ではありません。firmware既定の256 byte USB CDC RX queueでは4 KiB lineが欠落したため、productionとdiagnosticの双方を最大line 2本分の8,192 byteへ拡張して再試験しました。

書き込みとport openは対象とcommandを提示し、人間の明示承認後に実行しました。port番号、serial number、device path、生logは公開証拠へ含めていません。

診断firmware v0.1.1のstale判定は6,000 msを超えた後に遷移する実装であり、「6秒以内」の受入条件を満たす証拠にはできません。v0.1.2では5,500 ms以上で遷移する境界へ変更し、native fixtureで5,499 ms非遷移・5,500 ms遷移を検証します。v0.1.2の実機書き込みと計時が完了するまで、M1 hardware gate全体は未充足です。

また、factory imageの復元試験を先行できていないため、repositoryのrecovery-first gateも未充足です。診断firmwareの機能確認PASSはこのgateを代替せず、production controller imageや実Codex E2EのPASSへ昇格しません。
