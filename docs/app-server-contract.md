# Codex app-server contract

## Version policy

初期実装はCodex CLI 0.144.5へ固定します。別versionへ更新するときはschemaを別directoryへ生成し、差分reviewとcontract testを行います。

## Connection

- transportはstdio JSONL
- connectionごとに`initialize`を1回送信し、成功後に`initialized`を送る
- request/response IDを完全一致で管理する
- server requestは未解決のまま保持し、既定値で自動回答しない

## Required M0 flows

- `initialize`
- `thread/start`
- `turn/start`
- `turn/steer`
- `turn/interrupt`
- `model/list`
- command execution approval
- file change approval
- `serverRequest/resolved`
- `turn/completed`

## Approval decisions in 0.144.5

0.144.5のapproval requestには`availableDecisions`がありません。生成schemaが定義するresponseの`decision`は次のtagged unionです。

```text
CommandExecutionApprovalDecision =
  "accept"
  | "acceptForSession"
  | {
      "acceptWithExecpolicyAmendment": {
        "execpolicy_amendment": string[]
      }
    }
  | {
      "applyNetworkPolicyAmendment": {
        "network_policy_amendment": {
          "action": "allow" | "deny",
          "host": string
        }
      }
    }
  | "decline"
  | "cancel"

FileChangeApprovalDecision =
  "accept"
  | "acceptForSession"
  | "decline"
  | "cancel"
```

Bridgeは起動時に照合したCodex versionとrequest kindからこのcontractへfallbackします。Object variantは元のpayloadを保持し、tagだけのstringへ変換しません。Deviceへ提示できる集合は、server提示値またはversion固定contractと`{"accept", "decline"}`の積集合です。さらに`contentComplete=false`または`riskClass=high`なら`accept`を除外します。

未知値、型不正、version不一致、空集合ではdevice response UIを出さずhostへ送ります。新しいversionが`availableDecisions`を提示する場合は、その値を優先して同じ正規化を行います。

Command本文やfile一覧だけでは判断材料が完結しないrequest、network approval context、amendment payloadはdeviceで応答せずhostへ送ります。
