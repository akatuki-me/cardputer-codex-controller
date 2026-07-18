# M0 app-server operations evidence

## 対象

- Codex CLI: 0.144.5
- Methods: `thread/start`、`turn/start`、`turn/steer`、`turn/interrupt`、`model/list`
- Notifications: `turn/started`、`turn/completed`
- 実測日: 2026-07-18

型とfield名は、Codex CLI 0.144.5が生成したJSON Schemaの各Params、Response、
Notificationを基準にした。生成bundle自体はrepositoryへ同梱していない。

## 相関と状態

transportのJSON-RPC request IDは`AppServerClient`だけが採番・照合し、operations APIへ
公開しない。`ThreadId`と`TurnId`は別の型で保持する。

`turn/start`の成功responseまたは`turn/started`でactive turnを記録する。
`turn/steer`と`turn/interrupt`は、指定したthreadとturnがactive turn台帳へ完全一致する場合
だけ送信する。`turn/completed`は同じ組だけを閉じ、古いturnの遅延通知で新しいactive turnを
消さない。start responseよりcompletionが先に処理される場合も、完了済みturnをactiveへ
戻さない。

## model/list

`model/list`は空pageを正常値として扱い、未知のmodel名とreasoning effortを文字列のまま
保持する。既知modelへの変換や既定modelの捏造は行わない。RPC errorと型不正は呼出側へ
返し、空一覧へ変換しない。

## Local acceptance

認証済みCodex CLIがあるWindows環境で、空の一時workspace、ephemeral thread、
`approvalPolicy=never`、`sandbox=read-only`を使って次を実行した。

```powershell
$env:CARDPUTER_CODEX_LIVE_OPERATIONS = "1"
$env:PYTHONPATH = "bridge"
python -m pytest bridge/tests/app_server/test_live_operations.py -q
```

結果は`1 passed`だった。次を確認した。

- `model/list`が1件以上を返す
- `thread/start`と`turn/start`が別のthread/turn IDを返す
- `turn/started`でactive turnを相関し、同じturnへ`turn/steer`できる
- 同じactive turnだけを`turn/interrupt`できる
- `turn/completed`が同じthread/turn IDを通知し、active turnが解除される
- stdin close後、強制killなし・終了コード0でapp-serverが終了する

prompt本文、model一覧、thread/turn ID、workspace path、生のnotification、stderr本文は記録して
いない。実機、serial port、firmwareは使用していない。

## Deterministic tests

合成app-server fixtureでは次を検証する。

- 5 request methodの成功responseと`turn/started`、`turn/completed`
- JSON-RPC request IDを整数で連番照合し、合成thread/turn IDと分離する
- activeではないturnのinterruptを送信前に拒否する
- stale completionが新しいactive turnを消さない
- `turn/completed`の`inProgress`をprotocol errorにする
- 空のmodel pageを正常に扱う
- 未知modelと未知reasoning effortを欠落させない
- `model/list` errorをfallbackへ変換せず伝播する

## 未検証

- LinuxおよびmacOS上の実Codex CLI
- 同じthreadに対する複数の同時`turn/start`
- app-server再接続後のactive turn復元
- model paginationの複数page実測

これらは今回のPASSへ含めない。
