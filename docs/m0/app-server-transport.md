# M0 app-server transport evidence

## 対象

- Codex CLI: 0.144.5
- Transport: `codex app-server --stdio`
- Protocol: 1行1messageのJSONL
- Client capability: `experimentalApi=false`
- 実測日: 2026-07-18

型の基準は、Codex CLI 0.144.5が`generate-json-schema`で生成する
`InitializeParams`と`InitializeResponse`である。生成bundle自体はrepositoryへ同梱しない。
app-serverはexperimental surfaceであるため、version一致を初期化時のgateとする。

## Local acceptance

認証済みCodex CLIがあるWindows環境で、次のlocal acceptance testを実行した。

```powershell
$env:CARDPUTER_CODEX_LIVE_TEST = "1"
python -m pytest bridge/tests/app_server/test_live_app_server.py -q
```

結果は`1 passed`だった。次を確認した。

- `initialize`へ`clientInfo`と非experimental capabilityを送信できる
- response IDがrequest IDと一致する
- responseに`codexHome`、`platformFamily`、`platformOs`、`userAgent`が存在する
- Codex versionが0.144.5である
- 成功responseの後だけ、paramsを持たない`initialized`を送信する
- stdinをcloseするとprocessが終了コード0で正常終了する

`codexHome`は型の検証後に破棄し、test結果や通常の戻り値へ含めない。認証情報、prompt、
thread本文、ローカルpath、生のstderrは記録していない。

子processへはPATHなどの非credential環境だけを継承する。名前が`_TOKEN`、`_API_KEY`、
`_SECRET`、`_PASSWORD`で終わる環境変数と既知のcredential名は既定で除外する。
`CODEX_ACCESS_TOKEN`などが必要な運用では、呼出側が値を個別指定し、
`allow_sensitive_env=True`へ明示的にopt-inする。値を通常logやexceptionへ含めない。

Windowsでは対話wrapperを誤起動しないよう、PATHから`codex.exe`だけを解決する。
見つからない場合は`codex.cmd`へfallbackせず起動前に失敗する。

さらに、buildしたwheelを空の仮想環境へ依存なしでinstallし、その公開APIだけを使って同じ
live initializeと正常終了を実行した。結果は`wheel consumer live acceptance: PASS`だった。

## Deterministic tests

CIでは合成app-server processを使い、次を認証済みCodexから独立して検証する。

- 初期化順序と二重初期化の拒否
- 初期化前requestの拒否
- 非JSON行と不完全responseの拒否
- response ID不一致の検出
- response前EOFの検出
- request timeout
- stdin closeを無視するprocessのkillとreap
- 同時start、start中close、initialize中closeの直列化とreap
- stderr本文を保持せず、件数とseverityだけに縮約すること
- credential環境の既定除外と明示opt-in
- Windowsで対話wrapperへfallbackしないcommand解決
- Codex version不一致時に`initialized`を送らないこと

## 未検証

- LinuxおよびmacOS上の実Codex CLI
- thread、turn、model、approvalの各method
- app-server daemon、WebSocket、Unix socket
- 複数clientと再接続

これらをPASSとして扱わず、後続のM0 Issueで個別に実測する。
