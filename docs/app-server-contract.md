# Codex app-server contract

## Version policy

初期実装はschemaを照合済みのCodex CLI 0.144.5および0.144.6だけを受け入れます。別versionへ更新するときはschemaを別directoryへ生成し、差分reviewとcontract testを行います。

0.144.6では本bridgeが使用するcommand/file approval responseの互換性をschemaで確認しました。一方、実app-serverのcommand approval requestでは`availableDecisions`、`commandActions`、`environmentId`、amendment関連fieldが提示される場合があります。Bridgeは未知fieldを保持し、response schemaだけからdevice表示可否を推測しません。それ以外のversionは引き続きinitialize時に拒否します。

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

未知値、型不正、version不一致、空集合ではdevice response UIを出さずhostへ送ります。0.144.6を含め、requestが`availableDecisions`を提示した場合はversion fallbackより優先し、文字列decisionを名前どおり扱います。Object decisionを別の文字列へ変換しません。

Command本文やfile一覧だけでは判断材料が完結しないrequest、network approval context、amendment payloadはdeviceで応答せずhostへ送ります。

## Host approval lifecycle

Host bridgeはcommand executionとfile changeを別型で登録し、request受信だけではresponseを
送りません。Hostが明示decisionを送信した後もpendingを維持し、同じ`requestId`と`threadId`の
`serverRequest/resolved`を受信した時点でだけ解決済みにします。

request ID不一致、thread ID不一致、二重応答、request kindに合わないdecision、未知decisionは
protocol/state errorとして拒否します。0.144.5のobject decisionはpayloadを保持したまま
`result.decision`へ格納します。詳細と合成processによるconsumer integrationは
[`m0/approval-contract.md`](m0/approval-contract.md)を参照してください。

Host consoleもserver提示集合との積集合だけを操作として公開します。追加のsemantic contextを表示できないrequestでは`accept`を無効化し、提示されている`decline`または`cancel`だけを同名で送ります。Device表示上の切り詰めだけで、hostが原文全体を保持している場合はhostの`accept`を維持できます。

## Shutdown

stdio transportには`session/end` RPCはありません。新規操作を止め、active turnがあれば`turn/interrupt`の応答と`turn/completed`を待ってからstdinを閉じ、EOFを送ります。stdout/stderrのreaderを維持したままOS終了コード0を待ちます。

0.144.6の実装にはRPC drain、background task drain、thread shutdownがあるため、controllerは終了時だけ最大60秒を待ちます。timeout後のkill、非0終了、reader thread残留は正常終了へ昇格しません。`thread/archive`と`thread/delete`は永続状態を変更するためshutdown代替には使用しません。
