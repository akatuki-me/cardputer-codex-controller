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

子processへは`PATH`、OSのhome/config/temp、locale、TLS certificate pathなど、起動に必要な
allowlist内の環境変数だけを継承する。allowlist外の値は、既知の名前やsuffixに一致しない
credentialも含めて暗黙には継承しない。必要な値は呼出側が`env`へ個別指定する。
`CODEX_ACCESS_TOKEN`など、名前が`_TOKEN`、`_API_KEY`、`_SECRET`、`_PASSWORD`で終わる値と
既知のcredential名を指定する場合は、さらに`allow_sensitive_env=True`へ明示的にopt-inする。
値を通常logやexceptionへ含めない。

newlineまでの無制限bufferingを避けるため、stdoutのJSONL frameは1行16 Mi文字、stderrは
1行64 Ki文字を上限とする。超過はfatal protocol errorとして待機中のconsumerへ通知し、
子processを正常終了またはtimeout後のkillでreapする。kill失敗またはkill後の再timeoutは
`AppServerShutdownError`として明示し、無期限には待機しない。EOF、fatal error、明示closeも
`next_message()`の待機を直ちに解除し、timeoutへ誤変換しない。

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
- stdout/stderrの1行上限超過時のfatal化とprocess reap
- kill失敗とkill後timeoutの有界なshutdown error
- EOF、fatal error、closeによるmessage待機の即時解除
- Windowsで対話wrapperへfallbackしないcommand解決
- Codex version不一致時に`initialized`を送らないこと

## 未検証

- LinuxおよびmacOS上の実Codex CLI
- approvalの各method
- app-server daemon、WebSocket、Unix socket
- 複数clientと再接続

thread、turn、modelの実測結果は`app-server-operations.md`へ分離した。上記はPASSとして
扱わず、後続のM0 Issueで個別に実測する。
