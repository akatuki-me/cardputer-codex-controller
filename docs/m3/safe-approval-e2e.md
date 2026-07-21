# M3 safe approval E2E

## 目的

App-server request、host pending正本、device表示、host/device response、`serverRequest/resolved`を一つのlifecycleへ統合し、不完全または危険な内容をdeviceからacceptできないようにします。

## 実装契約

- JSON-RPC request IDはhostだけが保持し、deviceには短い`deviceApprovalId`だけを送る
- Pendingは受信順に保持し、deviceには先頭の表示可能な1件と残件数を送る
- Response送信後もresolvedまではpendingと`sending`を維持する
- 同一IDの再送で300ms guard、scroll、最下端到達、選択、sendingを維持する
- `contentComplete=false`または`riskClass=high`ではdevice acceptを削除する
- Device表示用本文を1行38 UTF-8 bytes以下・最大8行に分割し、表示できない内容があれば`contentComplete=false`にする
- Server提示decisionを上限とし、`decline`と`cancel`を変換しない
- Hostが追加semantic contextを完全表示できないrequestではhost acceptも禁止する
- 再接続時はpending正本からfull approval snapshotを再発行する

## 実Codex受入

Codex CLI 0.144.6の実command approvalでは、文字列decisionとamendment objectを含む`availableDecisions`、command action、environment、amendment関連fieldを受信しました。追加contextを完全表示できないためdeviceへacceptを出さずhost-onlyとし、server提示の`cancel`を応答しました。

受入結果はrequest受信、pending表示、response、resolved、turn完了、対象書き込み未発生、app-server終了コード0です。公開証拠にはID、command、cwd、prompt、model、port、生logを含めません。

## Test coverage

- low-risk accept/declineと二重応答拒否
- recursive delete、force push、sudo等のhigh-risk分類
- UTF-8 byte単位の8行切り詰めとhost原文保持
- 複数pendingの残数更新、自動送り、再接続復元
- host responseとdevice responseの同一pending正本
- server提示cancelと追加semantic context時のhost accept禁止
- device-linkのapproval、sending、resolved往復
- approval待ちで`wait`がREPLへ戻ることと、待機上限でsessionを終了しないこと
- Cardputer Enter special-key stateによるresponse確定

## 2026-07-19 validation

- publication boundary: PASS
- Ruff: PASS
- mypy: 30 source files PASS
- pytest: 149 passed、2 skipped
- Python sdist/wheel build: PASS
- Firmware native fixture: 18/18 PASS
- `cardputer_adv` build: PASS、RAM 32,260 bytes、Flash 482,185 bytes
- `cardputer_adv_bringup` build: PASS、RAM 26,716 bytes、Flash 461,917 bytes
- 実Codex 0.144.6 + 合成device turn lifecycle: PASS
- 実Codex 0.144.6 approval host-only + cancel + resolved: PASS
- Production書き込み前の全8 MiB二重backup: size・SHA-256一致
- Production firmware upload: PASS、全書き込み領域のhash verification完了
- Production実機 + 実CodexのHOME/RUN、host-only cancel、G0 interrupt: PASS
- Incomplete file approvalのlocal hold、accept禁止、物理decline、対象書き込み未発生: PASS
- 完全なlow-risk fixtureの物理accept、複数pending残数、自動送り、2件目decline、pending zero: PASS
- Controller終了後のstaleとnew host session/full snapshotによる再接続: PASS
- Stale状態からactive/full snapshot: 1,766ms、6秒以内をPASS

Production firmwareの書き込み、自動reset、最初の実port open、実Codex controller E2Eまで完了しています。実測で検出したEnter special-key判定と`wait`のapproval/timeout復帰を修正し、再書き込み後に物理操作を再受入しました。

## Issue #18 hardening fixture

`cardputer-codex-controller approval-fixture`はCodex app-serverとcommand executorを起動せず、1行38 bytes以下の固定ASCII本文を持つ無害な3 fixtureだけをproduction device-linkへ送ります。

- 6行の完全・low-risk: 表示直後300ms未満と本文末尾未到達ではdecisionなし、末尾到達後だけaccept
- 完全・high-risk: accept非表示・送信なし、declineだけ送信
- 本文不完全・normal-risk: accept非表示・送信なし、local holdはdecisionなし、declineだけ送信

`--synthetic`はhost harnessとsanitized outputだけを検証します。実portは`--port`またはGit管理外の`--port-handle`で明示し、承認前は`--dry-run`だけを実行します。dry-runはproviderを生成せず、serial I/Oを開始しません。公開結果は固定stepとPASS/FAILだけに限定し、port、approval ID、本文、cwd、生NDJSON、時刻を含めません。

300ms未満の物理キー操作とaccept非表示はwireだけでは自動証明できません。299/300ms境界のnative fixture、operatorの画面・キー確認、host recorderのdecision有無を組み合わせてlocal acceptanceとします。

## 2026-07-21 pre-hardware validation

- `approval-fixture --synthetic`: PASS
- 実port選択の`approval-fixture --dry-run`: PASS、serial I/Oなし、Codex接続なし
- publication boundary: PASS
- Ruff: PASS
- mypy: 42 source files PASS
- pytest: 222 passed、3 skipped
- Python sdist/wheel build: PASS
- Firmware native fixture: 18/18 PASS
- `cardputer_adv` build: PASS、RAM 32,252 bytes、Flash 482,165 bytes
- `cardputer_adv_bringup` build: PASS、RAM 26,716 bytes、Flash 461,917 bytes

この記録はhost harness、既存firmware境界、buildのpreflightです。実portは開いておらず、hardware PASSではありません。

## 2026-07-21 hardware validation

Hardware承認後、production firmwareへ固定3 fixtureを実portで送信し、次を受入しました。Codex app-server、command executor、flash/uploadは起動していません。

- serial linkとdevice hello: PASS
- 表示切替直後のaccept試行: decisionなし
- 300ms経過後・本文末尾未到達のaccept試行: decisionなし
- 6行本文の末尾到達後accept: 1回だけ送信
- high-risk: `A ACCEPT`非表示、accept試行はdecisionなし、declineは1回だけ送信
- 本文不完全: `A ACCEPT`非表示、accept試行とlocal holdはdecisionなし、declineは1回だけ送信

正確な299/300ms境界はnative fixture、物理表示とキー操作はoperator確認、decision有無と件数はhost recorderで分担して判定しました。公開記録にはport、device ID、approval ID、本文、cwd、生NDJSONを含めません。

## 未検証境界

長時間運転は未受入です。合成fixtureとdry-runは引き続きhardware PASSの代用にしません。
