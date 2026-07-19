# M0 multi-client / thread resume合成probe

## 判定範囲

この文書は、Codex CLI 0.144.6生成schemaの`thread/resume`形状を基準にした、
決定論的な合成probeの証拠である。認証済みCodexのmulti-client挙動を実測した記録ではない。

probeはproduction bridgeを変更せず、2つの独立した`AppServerClient`と合成app-server
processを使う。client Aが非ephemeralな合成threadを作成して切断し、client Bが同じ合成状態を
参照して`thread/resume`する。共有ファイルは「合成threadが存在する」という事実だけを保持し、
thread本文や認証情報は保持しない。

## 合成method sequence

1. client A: `initialize` → `thread/start` → `thread/started`観測 → 切断
2. client B: `initialize` → `thread/resume` → `thread/started`観測 → 切断

両connectionは別processであり、client Aのtransportやin-memory台帳をclient Bへ渡さない。
通知は各connectionにつき1つの`NotificationHub`だけが取得する。2つ目のconsumer、同じ
notificationの重複、所有対象外threadのnotificationは固定文言のerrorでfail closedにする。

## 合成結果

| scenario | probe結果 | notification |
| --- | --- | --- |
| 共有状態あり | `resumed` | `thread_started`を1回 |
| 共有状態なし | `not_found` | なし |
| 合成権限拒否 | `permission_denied` | なし |
| 合成状態拒否 | `invalid_state` | なし |

4つの結果は別のresult classとして保持する。合成fixtureのerror codeは分類器の決定論テスト用で
あり、実Codexのerror codeや発生条件を表す証拠ではない。

公開可能なprobe recordは`outcome`と正規化した`notificationKinds`だけである。thread ID、
workspace path、共有状態file path、thread本文、認証情報、生notification、stderr本文は出力しない。

## connection切断時の状態境界

| 状態 | client A切断後 | client Bでの候補 | 現時点の根拠 |
| --- | --- | --- | --- |
| JSON-RPC request IDとpending response queue | 失われる | 再利用しない | `AppServerClient`のconnection-local memory |
| inbound notification queue | 失われる | 新connectionで新規購読 | `AppServerClient`のconnection-local memory |
| active turn、starting set、completion tombstone | 失われる | resume responseやread APIから再構築候補 | `AppServerOperations`のconnection-local memory。再構築は未実装 |
| persistent thread本体 | 合成fixtureでは保持 | `thread/resume`で再取得候補 | 合成PASSのみ。live未確認 |
| ephemeral thread本体 | 対象外 | 再取得可能とは扱わない | production controllerは現在ephemeralを使用 |
| notification ownership | connection終了で失われる | client Bが新しい単一dispatcherを所有 | 合成collector契約 |

このmatrixから、Bridgeのproduction ownership modelを確定することはまだできない。少なくとも
transport request、pending response、通知queue、active turn台帳をconnection間で暗黙に引き継ぐ
設計にはしない。persistent threadの再取得可否と必要なstate再構築はlive probeの結果で決める。

## Local acceptance

```powershell
$env:PYTHONPATH = "bridge"
python -m pytest -q bridge/tests/app_server/test_multi_client_resume_probe.py
```

2026-07-20の結果は`10 passed`。次を確認した。

- client A切断後にclient Bが共有された合成threadをresumeする
- 成功、不在、権限拒否、状態拒否を混同しない
- 1 connection 1 notification consumerを維持する
- 同時consumer claimは1件だけ成功し、同じconsumerの同時readも直列化する
- 重複notificationと所有対象外notificationを拒否する
- sanitized recordへthread IDやfile pathを含めない

実機、serial port、firmware、認証済みCodex connectionは使用していない。

## Issue #5へ残すlive probe

Issue #5をcloseする前に、認証済みCodexで次を実測する。

- 1つのpersistent test threadへ複数clientが関与できる条件
- running / idle / missing threadごとの`thread/resume`応答とerrorの意味
- resume時にどのclientへどのnotificationが配送されるか
- 切断後に再取得できるthread / turn状態と、再構築できないconnection-local状態
- 観測結果に基づくBridgeの推奨connection・thread ownership model

live probeでも実thread ID、本文、workspace path、認証情報、生notificationは保存しない。
