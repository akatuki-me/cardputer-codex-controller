# Safety

## Defense in depth

一次防御はhost側のsandbox、cwd allowlist、approval policy、network制御です。Deviceは状態の可視化、誤操作防止、interruptを提供する二次防御です。PCのscreen lockは単独の防御とみなさず、bridge停止または入力lockと組み合わせます。

## Approval guards

1. 表示開始から300msは応答を無効にする。
2. 最下端へ到達するまで応答を無効にする。
3. 表示中の`deviceApprovalId`だけを受け、hostの対応表から完全な`rpcRequestId`へ一度だけ応答する。
4. resolved通知まで処理中表示を維持する。
5. 表示中requestを解決前に差し替えない。
6. pendingの正本をhostが保持し、解決後に次件を表示する。
7. 切り詰めまたはhigh-riskのrequestではdevice側acceptを禁止する。

初期high-risk分類には、再帰削除、force push、権限昇格などの保守的な静的patternを使用します。分類不能な要求はhost側へ送ります。

Bridgeがdevice-linkの4096 byte上限に合わせて本文を切り詰めた場合、`contentComplete=false`を必ず設定し、pending正本には切り詰め前のpayloadを保持します。Deviceではacceptを消し、host側だけで全文確認後の応答を許可します。

## Link and service guards

stale linkではすべての送信をlockします。Codex serviceがreadyでない場合も応答、steer、interruptを送信しません。

## Hardware gate

初回書き込み承認を求める前にfactory imageを全域から独立に2回読み出し、sizeとSHA-256の一致を確認し、手動download modeと復元commandを手順化します。承認後の最初の書き込みを照合済みbackup imageのwrite-backによる復元試験とし、factory firmwareの正常起動を確認するまでapplication firmwareを書き込みません。
