# M1 Cardputer-Adv bring-up

## 状態

非侵襲gate: PASS

hardware gate: 未実施

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

- native fixture: 13 passed、0 failed
- 4,096 byte line: 受理
- 4,097 byte line: 破棄
- 同一sessionの過去sequence: 拒否
- 新sessionの`seq=1`: 受理
- G0 499ms: short
- G0 500ms: longを1回だけ通知
- diagnostic build: PASS、RAM 26,716 bytes、Flash 461,693 bytes
- production build: PASS、RAM 32,188 bytes、Flash 481,469 bytes

Native fixtureはWindows側に`gcc/g++`がないため、同じsourceをWSLの`g++ 13.3.0`でcompileして実行しました。Cardputer向け2 targetはPlatformIO 6.1.18、Espressif32 6.7.0でbuildしました。

## Hardware gate

次は未実施です。build成功からhardware PASSへは昇格しません。

1. diagnostic firmwareの書き込み
2. 最初のCOM port open
3. board IDがCardputer-Advを示すこと
4. device hello → host hello → ready
5. 双方向echo、heartbeat、stale、再接続
6. 数字keyとG0の短押し・長押し
7. 4KiB line、RTT、throughput、heapの実測

書き込みとport openは対象とcommandを提示し、人間の明示承認後にだけ実行します。port番号、serial number、device path、生logは公開証拠へ含めません。
