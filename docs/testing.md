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
- approval requestを短いdevice IDへ投影し、JSON-RPC IDをdeviceへ送らない
- server提示decision、内容完全性、high-risk分類の積集合だけをdeviceへ表示する
- 追加semantic context付きrequestではhost acceptも禁止し、`decline`と`cancel`を変換しない
- hostとdeviceのどちらから応答してもresolvedまでpendingを維持する
- 同じapproval IDの残件数更新と再接続でguard、scroll、sendingを失わない
- controller runtimeがturn、pending、host response、interrupt、正常終了を一つのthreadで処理する
- `requestUserInput`を短いhost IDへ投影し、生のRPC・question IDを表示しない
- 複数質問を別local IDへ相関し、全件が揃った時点だけschemaどおりのnested mapを1回送る
- Secretは注入したno-echo readerだけから受け、通常入力、空・4 KiB超、request合計16 KiB超、未知ID、二重・解決後回答を拒否する
- auto resolution、host/device interrupt、turn完了で未送信の部分回答を破棄する
- question受信、host回答、matching resolved、turn完了を合成app-server E2Eで通す
- approvalとquestionの同時pendingでapprovalを優先し、各resolvedを正しいqueueへ相関してquestion表示へ復帰する
- experimentalな未知server requestを黙ってdropせず、固定のJSON-RPC `-32601` errorでrequestをfail closedする
- `thread/resume`の同一error codeをallowlist済みの安全なkindへ分類し、raw messageを保持しない

## Protocol fixtures

fixtureは合成ID、相対path、架空workspace名だけを使用します。認証済みCodexや実機を必要とする試験はGitHub Actionsへ入れず、local acceptance evidenceとして記録します。

## M1 local bring-up

`cardputer-codex-controller bringup --synthetic`はCodex非依存のM1診断経路を合成deviceで実行します。`bringup --port ... --dry-run`はproviderを生成せず、portを開きません。合成結果は診断protocolとhost harnessの証拠であり、board、keyboard、G0、USB CDCの実機PASSには昇格しません。

Cardputer-Adv実機では診断firmware v0.1.3を用い、recovery-first復元、board、4 KiB echo、RTT、throughput、heap、再接続、数字key、G0 short/longを確認済みです。Staleはdevice自身が最後のhost受信から5,501 msを報告し、6秒以内をPASSしました。実portの物理入力待ちは各event最大120秒、stale event待ちは8秒ですが、合否はdevice報告値5,500〜6,000 msの範囲です。

## Local E2E

`cardputer-codex-controller e2e --synthetic`は認証済みCodexと合成USB CDCを接続します。出力はstep名とPASS/FAIL classだけに限定し、ID、prompt、model、path、portを含めません。

`e2e --port`または`e2e --port-handle`で実portを選択できますが、承認前は`--dry-run`だけを実行します。dry-runはserial I/OとCodexを起動しません。実port未使用の結果はhardware PASSへ昇格しません。

## M2/M3 local controller acceptance

`cardputer-codex-controller control --synthetic --cwd . --label fixture`は実Codex app-serverとmemory内の合成Cardputerを接続します。2026-07-19のlocal acceptanceではCodex CLI 0.144.6を使い、thread作成、turn開始・完了、full state、wait、stdin EOF後の正常終了をPASSしました。

実command approvalでは`availableDecisions`と追加amendment contextを受信し、deviceへ不完全なaccept UIを出さずhost-onlyとしました。Server提示の`cancel`で応答し、`serverRequest/resolved`、turn完了、書き込み未発生、終了コード0を確認します。出力証拠はID、command、cwd、prompt、model、portを含まない固定fieldだけに限定します。

App-server終了は処理中RPCとthread cleanupをdrainするため、controllerは終了時だけ最大60秒を待ちます。60秒後の強制kill、非0終了、event reader残留はFAILです。

M4 host user-inputはCodex CLI 0.144.6生成schemaと合成app-serverで検証します。Controllerだけが`experimentalApi=true`へopt-inし、複数質問の部分蓄積と一括response、secret no-echo adapter、option/Other相関、pending lifecycle、interrupt時の破棄、Deviceへの`question` attentionを受入対象にします。実Codexで`requestUserInput`を発生させる経路は未検証であり、合成結果を実接続PASSへ昇格しません。

Multi-client / thread resumeは`CARDPUTER_CODEX_LIVE_MULTI_CLIENT=1`のopt-in testで、同一`CODEX_HOME`を共有する独立stdio processを実測します。Persistent test threadは測定後にarchiveし、公開recordにはID、本文、path、生notificationを含めません。Idle・active turn中・旧process終了後のresumeを分け、別processへactive lifecycle通知が配送されないこと、missingとstate errorが同じcodeになること、正常終了のdrain時間を記録します。

Production実機のlocal acceptanceでは、実Codex turnのHOME/RUN、G0 interrupt、host-only cancel、incomplete file approvalのhold・accept禁止・物理decline、stale/reconnectを確認します。完全なlow-risk acceptと複数pendingは、実行処理を持たない2件のlocal fixtureで物理accept、残数、自動送り、物理decline、pending zeroを確認します。Stale状態からのhost接続開始〜active/full snapshotは6秒以内を合格とし、今回のbaselineは1,766msです。実測中に検出したEnter special-key判定と`wait`のapproval/timeout復帰には回帰テストがあります。

## Hardware tests

- M1: backup、restore、echo、RTT、4KB line、heap
- M2: SCR-HOME、6 slot、stale、reconnect
- M3: accept、decline、hold、interrupt、pending、high-risk gate
- M4: choices、quick reply、steer、effort、scroll repeat
- M6: sleep復帰、port解放、自動起動、24時間連続運転

実機試験を実行していない場合は、build成功と実機PASSを明確に分けます。

Host response surfaceは「deviceでhold → host queueへ表示 → hostで応答 → deviceがresolvedへ追従」をE2Eで検証します。切り詰め時にdeviceのacceptが消え、hostが原文全体を表示した場合だけacceptできることも確認します。
