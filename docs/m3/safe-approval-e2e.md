# M3 safe approval E2E

## 目的

App-server request、host pending正本、device表示、host/device response、`serverRequest/resolved`を一つのlifecycleへ統合し、不完全または危険な内容をdeviceからacceptできないようにします。

## 実装契約

- JSON-RPC request IDはhostだけが保持し、deviceには短い`deviceApprovalId`だけを送る
- Pendingは受信順に保持し、deviceには先頭の表示可能な1件と残件数を送る
- Response送信後もresolvedまではpendingと`sending`を維持する
- 同一IDの再送で300ms guard、scroll、最下端到達、選択、sendingを維持する
- `contentComplete=false`または`riskClass=high`ではdevice acceptを削除する
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

## 2026-07-19 validation

- publication boundary: PASS
- Ruff: PASS
- mypy: 30 source files PASS
- pytest: 145 passed、2 skipped
- Python sdist/wheel build: PASS
- Firmware native fixture: 18/18 PASS
- `cardputer_adv` build: PASS、RAM 32,260 bytes、Flash 482,189 bytes
- `cardputer_adv_bringup` build: PASS、RAM 26,716 bytes、Flash 461,917 bytes
- 実Codex 0.144.6 + 合成device turn lifecycle: PASS
- 実Codex 0.144.6 approval host-only + cancel + resolved: PASS
- Production書き込み前の全8 MiB二重backup: size・SHA-256一致
- Production firmware upload: PASS、全書き込み領域のhash verification完了

Production firmwareの書き込みと自動resetまでは完了しています。書き込み後の最初の実port openは別gateのため実行していません。

## 未検証境界

Production実機での起動画面、300ms guard、最下端到達、物理accept/decline/hold、複数pending表示は未受入です。合成PASSを実機PASSとして扱いません。
