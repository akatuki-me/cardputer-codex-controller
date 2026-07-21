# M0 multi-client / thread resume probe

## 判定範囲

この文書は、Codex CLI 0.144.6生成schemaを基準にした決定論的な合成probeと、
認証済みCodexを使った独立stdio process間のlive probeを分けて記録する。

probeはproduction bridgeを変更せず、2つの独立した`AppServerClient`と合成app-server
processを使う。client Aが非ephemeralな合成threadを作成して切断し、client Bが同じ合成状態を
参照して`thread/resume`する。共有ファイルは「合成threadが存在する」という事実だけを保持し、
thread本文や認証情報は保持しない。

## 合成method sequence

1. client A: `initialize` → `thread/start` → `thread/started`観測 → 切断
2. client B: `initialize` → `thread/resume` response観測 → 切断

両connectionは別processであり、client Aのtransportやin-memory台帳をclient Bへ渡さない。
通知は各connectionにつき1つの`NotificationHub`だけが取得する。2つ目のconsumer、同じ
notificationの重複、所有対象外threadのnotificationは固定文言のerrorでfail closedにする。

## 合成結果

| scenario | probe結果 | notification |
| --- | --- | --- |
| 共有状態あり | `resumed` | なし |
| 共有状態なし | `not_found` | なし |
| 合成権限拒否 | `permission_denied` | なし |
| 合成状態拒否 | `invalid_state` | なし |

4つの結果は別のresult classとして保持する。合成fixtureのerror codeは分類器の決定論テスト用で
あり、実Codexのerror codeや発生条件を表す証拠ではない。Permission errorの発生条件はlive未確認で
あり、合成分類を実Codexの発生証拠には昇格しない。

公開可能なprobe recordは`outcome`と正規化した`notificationKinds`だけである。thread ID、
workspace path、共有状態file path、thread本文、認証情報、生notification、stderr本文は出力しない。

## connection切断時の状態境界

| 状態 | client A切断後 | client Bでの候補 | 現時点の根拠 |
| --- | --- | --- | --- |
| JSON-RPC request IDとpending response queue | 失われる | 再利用しない | `AppServerClient`のconnection-local memory |
| inbound notification queue | 失われる | 新connectionで新規購読 | `AppServerClient`のconnection-local memory |
| active turn、starting set、completion tombstone | 失われる | resume responseやread APIから再構築候補 | `AppServerOperations`のconnection-local memory。live probeではactive turnを継承できなかった |
| persistent thread本体 | 合成fixtureでは保持 | `thread/resume`で再取得 | 独立stdio processのidle・切断後resumeをlive確認 |
| ephemeral thread本体 | 対象外 | 再取得可能とは扱わない | production controllerは現在ephemeralを使用 |
| notification ownership | connection終了で失われる | client Bが新しい単一dispatcherを所有 | resume responseは得られるが、別stdio processのlifecycle通知は1秒・3秒の観測窓内では配送されなかった |

transport request、pending response、通知queue、active turn台帳をconnection間で暗黙に引き継ぐ
設計にはしない。live probeで確認したproduction ownership modelは後述する。

## Local acceptance

```powershell
$env:PYTHONPATH = "bridge"
python -m pytest -q bridge/tests/app_server/test_multi_client_resume_probe.py
```

2026-07-21の結果は`10 passed`。次を確認した。

- client A切断後にclient Bが共有された合成threadをresumeする
- 成功、不在、権限拒否、状態拒否を混同しない
- 1 connection 1 notification consumerを維持する
- 同時consumer claimは1件だけ成功し、同じconsumerの同時readも直列化する
- 重複notificationと所有対象外notificationを拒否する
- sanitized recordへthread IDやfile pathを含めない

実機、serial port、firmware、認証済みCodex connectionは使用していない。

## 認証済みCodex live probe

2026-07-21にCodex CLI 0.144.6、同一account、同一`CODEX_HOME`を共有する独立した
app-server stdio processで実測した。専用の一時workspace、`sandbox=read-only`、
`approvalPolicy=never`を使用し、persistent test threadは測定後にarchiveした。

```powershell
$env:CARDPUTER_CODEX_LIVE_MULTI_CLIENT = "1"
$env:PYTHONPATH = "bridge"
python -m pytest -q -s bridge/tests/app_server/test_live_multi_client_resume.py
```

結果は`1 passed`。公開recordはstatus、error code、正規化したnotification kind、shutdown時間だけを
出力し、thread/turn ID、本文、workspace path、認証情報、生notification、stderr本文を含めない。

| scenario | resume結果 | responseのthread状態 | notification |
| --- | --- | --- | --- |
| primary完了後、別processからresume | 成功 | `idle`、既存turnは`completed` | 1秒の観測窓で`thread/started`なし |
| primaryでturn実行中、別processからresume | 成功 | `idle`、実行中turnは`interrupted`として再構築 | 3秒の観測窓でprimaryの`turn/completed`なし |
| primary終了後、新processからresume | 成功 | `idle`、`completed`と`interrupted`を再取得 | 1秒の観測窓で`thread/started`なし |
| 存在しないthread ID | error `-32600`（不在scenario） | なし | なし |
| loaded threadへhistory付きresume | error `-32600`（loaded-state scenario） | なし | なし |

Resumeした各connectionの`thread/unsubscribe`は`unsubscribed`を返し、test threadのarchive responseも
確認した。App-serverは強制終了せずexit code 0で閉じたが、反復実測した終了時間は
9,312〜45,343 msだった。したがってunsubscribeを即時process終了の証拠として扱わず、
controller shutdownのdrain上限を維持する。

## Ownership decision

独立stdio processはpersistent threadを再取得できるが、live probeの観測窓では同時に動く
別processのactive turnとnotification streamを共有しなかった。Bridgeは次を初期ownership modelとする。

1. 1 controller processが1 app-server stdio processと1 notification dispatcherを排他的に所有する。
2. 同じpersistent threadへ複数の独立stdio processから同時に操作しない。
3. 再接続は旧processの終了を確認してから新processを起動し、`thread/resume` responseからthreadを再構築する。
4. 切断時のactive turnは継続中と推測せず、resume responseまたは`thread/read`で再確認する。
5. Resume成功時の`thread/started`を期待せず、request responseを再取得完了の正本とする。

## Confirmed / inference / unconfirmed

- Confirmed: 独立stdio process間のidle・running・切断後resume、1秒・3秒の観測窓内の通知非fan-out、終了時間、error code。
- Inference: 単一ownerと旧process終了後のresumeが、初期USB controllerに最も単純で安全な構成である。
- Unconfirmed: 1つの共有app-server listenerへ複数connectionを張る場合のfan-outとwriter制御、
  別account・権限境界でのpermission error。

## Issue #5へ残すgap

Missingとstate errorはどちらも`-32600`であるため、codeだけではruntime分類できない。
現行`AppServerResponseError`はraw messageを保持せずcodeだけを返すため、安全なruntime分類は
未実装として扱う。分類方式、共有listener、permission errorのlive発生条件をIssue #5に残し、
初期stdio ownershipのNonGoalとして外部化するかを決める。
