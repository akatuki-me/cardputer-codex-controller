# Testing strategy

## Host tests

- JSONL framingとrequest ID対応
- lifecycleと異常終了
- state reducer
- pending queue
- decision normalization
- truncationとrisk gate
- reconnect snapshot
- 未知decisionを別decisionへ変換しない
- request ID不一致と二重応答を拒否する
- 2件目のpendingが表示中requestを差し替えない
- `model/list`失敗・空・model不一致時にeffort変更を無効化する
- service down時にresponse、steer、interrupt、effort変更をlockする
- provider injectionしたserial read threadがNDJSON、ping/pong、stale、再接続を処理する
- M1診断で接続ごとにopaque sessionを更新し、host sequenceを1へresetする
- M1診断で4 KiB echo、RTT、連続echo throughput、heartbeat/heapを測定する
- M1診断で数字key、G0 499ms short、500ms longを識別する
- M1診断でdevice報告のstale経過時間を使い、通常の`hello`と区別して6秒境界を判定する
- 再接続ごとに`hello`とfull snapshotを再送する
- 合成deviceの重複`interrupt`をactive turnへ一度だけ転送する
- handshake前はhost送信を0件に保ち、新しいopaque sessionごとにhost `seq`を1へresetする
- snapshotとheartbeatの並行送信でも採番順とwrite順を一致させる
- `interrupt`のslotと`turnId`がfocus中のactive turnへ完全一致しない場合は転送しない
- Codex CLI 0.144.5とschema照合済み0.144.6だけをinitializeで受け入れる

## Protocol fixtures

fixtureは合成ID、相対path、架空workspace名だけを使用します。認証済みCodexや実機を必要とする試験はGitHub Actionsへ入れず、local acceptance evidenceとして記録します。

## M1 local bring-up

`cardputer-codex-controller bringup --synthetic`はCodex非依存のM1診断経路を合成deviceで実行します。`bringup --port ... --dry-run`はproviderを生成せず、portを開きません。合成結果は診断protocolとhost harnessの証拠であり、board、keyboard、G0、USB CDCの実機PASSには昇格しません。

Cardputer-Adv実機では診断firmware v0.1.3を用い、recovery-first復元、board、4 KiB echo、RTT、throughput、heap、再接続、数字key、G0 short/longを確認済みです。Staleはdevice自身が最後のhost受信から5,501 msを報告し、6秒以内をPASSしました。実portの物理入力待ちは各event最大120秒、stale event待ちは8秒ですが、合否はdevice報告値5,500〜6,000 msの範囲です。

## Local E2E

`cardputer-codex-controller e2e --synthetic`は認証済みCodexと合成USB CDCを接続します。出力はstep名とPASS/FAIL classだけに限定し、ID、prompt、model、path、portを含めません。

`e2e --port`または`e2e --port-handle`で実portを選択できますが、承認前は`--dry-run`だけを実行します。dry-runはserial I/OとCodexを起動しません。実port未使用の結果はhardware PASSへ昇格しません。

## Hardware tests

- M1: backup、restore、echo、RTT、4KB line、heap
- M2: SCR-HOME、6 slot、stale、reconnect
- M3: accept、decline、hold、interrupt、pending、high-risk gate
- M4: choices、quick reply、steer、effort、scroll repeat
- M6: sleep復帰、port解放、自動起動、24時間連続運転

実機試験を実行していない場合は、build成功と実機PASSを明確に分けます。

Host response surfaceは「deviceでhold → host queueへ表示 → hostで応答 → deviceがresolvedへ追従」をE2Eで検証します。切り詰め時にdeviceのacceptが消え、hostが原文全体を表示した場合だけacceptできることも確認します。
