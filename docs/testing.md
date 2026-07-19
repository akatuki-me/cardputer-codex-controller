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
- 再接続ごとに`hello`とfull snapshotを再送する
- 合成deviceの重複`interrupt`をactive turnへ一度だけ転送する
- M1診断で接続ごとにopaque sessionを更新し、host sequenceを1へresetする
- M1診断で4 KiB echo、RTT、連続echo throughput、heartbeat/heapを測定する
- M1診断で数字key、G0 499ms short、500ms longを識別する

## Protocol fixtures

fixtureは合成ID、相対path、架空workspace名だけを使用します。認証済みCodexや実機を必要とする試験はGitHub Actionsへ入れず、local acceptance evidenceとして記録します。

## Local E2E

`cardputer-codex-controller e2e --synthetic`は認証済みCodexと合成USB CDCを接続します。出力はstep名とPASS/FAIL classだけに限定し、ID、prompt、model、path、portを含めません。

`e2e --port`または`e2e --port-handle`で実portを選択できますが、承認前は`--dry-run`だけを実行します。dry-runはserial I/OとCodexを起動しません。実port未使用の結果はhardware PASSへ昇格しません。

`cardputer-codex-controller bringup --synthetic`はCodex非依存のM1診断経路を合成deviceで実行します。`bringup --port ... --dry-run`はproviderを生成せず、portを開きません。合成結果は診断protocolとhost harnessの証拠であり、board、keyboard、G0、USB CDCの実機PASSには昇格しません。

Cardputer-Adv実機では診断firmware v0.1.1を用い、board、4 KiB echo、RTT、throughput、heap、stale、再接続、数字key、G0 short/longを受入済みです。実portの物理入力待ちは各event最大120秒です。この結果はfactory復元やproduction controller、実Codex E2Eへ自動的に昇格しません。

## Hardware tests

- M1: backup、restore、echo、RTT、4KB line、heap
- M2: SCR-HOME、6 slot、stale、reconnect
- M3: accept、decline、hold、interrupt、pending、high-risk gate
- M4: choices、quick reply、steer、effort、scroll repeat
- M6: sleep復帰、port解放、自動起動、24時間連続運転

実機試験を実行していない場合は、build成功と実機PASSを明確に分けます。

Host response surfaceは「deviceでhold → host queueへ表示 → hostで応答 → deviceがresolvedへ追従」をE2Eで検証します。切り詰め時にdeviceのacceptが消え、hostが原文全体を表示した場合だけacceptできることも確認します。
