# M0 approval contract

## 対象

- Codex CLI schema: 0.144.5
- command request: `item/commandExecution/requestApproval`
- file request: `item/fileChange/requestApproval`
- resolution: `serverRequest/resolved`
- hardware操作: なし

`codex app-server generate-json-schema`で生成した0.144.5のschemaを型の基準にする。
生成bundle自体はrepositoryへ同梱しない。

## 状態遷移

```text
server request
  -> awaiting_decision
  -> hostが明示decisionをresponse
  -> response_sent（pendingを保持）
  -> serverRequest/resolved
  -> pendingから除去
```

request受信時に既定値を送らない。responseの送信成功だけでは解決済みとみなさず、
同じ`requestId`と`threadId`の`serverRequest/resolved`まで保持する。別ID、別thread、
二重応答、request kindに合わない応答、未知decisionは拒否する。

commandとfile changeは別のrequest型・response methodで扱う。command decisionのobject
variantは次のpayloadをそのままJSONへ変換し、tag文字列へ縮退させない。

- `acceptWithExecpolicyAmendment.execpolicy_amendment`
- `applyNetworkPolicyAmendment.network_policy_amendment.action`
- `applyNetworkPolicyAmendment.network_policy_amendment.host`

## Deterministic acceptance

合成app-server processとのintegration testで、次の2経路をstdio JSONL上で通す。

1. command request → `accept` response → matching resolved
2. file change request → `decline` response → matching resolved

fixtureは合成ID、相対path、架空commandだけを使用する。request受信直後はresponseがなく、
response送信後もstatusが`response_sent`のまま残り、resolved後にだけpendingから消えることを
consumerである`AppServerClient`との結合面から確認する。

## Local schema acceptance

Codex CLI 0.144.5の実行fileを使ってschema bundleを一時directoryへ生成し、上記3 method、
requestの必須field、responseのdecision union、resolvedの`requestId` / `threadId`を照合した。
生成物、ローカルpath、認証情報、生messageは保存していない。

認証済みmodel turnを発生させる実app-server approval往復は自動testに含めない。
この境界は合成processのconsumer integrationで検証し、実turnの再確認はM0の手動acceptanceとして
別に記録する。
