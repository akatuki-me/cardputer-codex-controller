# Codex app-server contract

## Version policy

初期実装はschemaを照合済みのCodex CLI 0.144.5および0.144.6だけを受け入れます。別versionへ更新するときはschemaを別directoryへ生成し、差分reviewとcontract testを行います。

0.144.6では本bridgeが使用するcommand/file approval responseに加え、`ToolRequestUserInputParams`と`ToolRequestUserInputResponse`をschemaで確認しました。一方、実app-serverのcommand approval requestでは`availableDecisions`、`commandActions`、`environmentId`、amendment関連fieldが提示される場合があります。Bridgeは未知fieldを保持し、response schemaだけからdevice表示可否を推測しません。それ以外のversionは引き続きinitialize時に拒否します。

## Connection

- transportはstdio JSONL
- connectionごとに`initialize`を1回送信し、成功後に`initialized`を送る
- app-server clientの既定は`experimentalApi=false`とし、host controllerだけが`requestUserInput`受信のため明示的に`true`へopt-inする
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

M4のhost回答面は、上記に加えてexperimentalな`item/tool/requestUserInput`を使用します。

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

## Host user-input lifecycle

`item/tool/requestUserInput`はapproval decisionではありません。Host controllerはrequest内の質問ごとに短縮IDを割り当て、非secret質問への`answer <id> <text>`とsecret質問への`secret <id>`を1つのresponseへ集約します。

```json
{
  "answers": {
    "<schema question.id>": {
      "answers": ["<text>"]
    }
  }
}
```

各`answer`は対応する`question.id`だけへ蓄積し、全質問が揃うまでresponseを送りません。選択肢があり`isOther=false`ならoption labelとの完全一致だけを受け入れ、`isOther=true`ならlabelまたは自由文を同じ`string[]`へ保持します。1回答はUTF-8で4 KiB、1 request合計は16 KiBを上限とします。

生のRPC ID、`question.id`、item ID、回答本文は内部相関だけに使い、console、log、deviceへ出しません。Secret回答はTTYで有効になるno-echo readerからだけ受け入れ、通常のecho入力面では拒否します。端末がechoを無効化できない場合もecho入力へfallbackせず、その回答操作を拒否します。Response送信後も`response_sent`としてpendingを維持し、同じ`requestId`と`threadId`の`serverRequest/resolved`でだけ除去します。Serverの`autoResolutionMs`による先行解決、host/device interrupt、turn完了では未送信の部分回答を破棄し、後着回答を拒否します。Interruptまたはturn完了後もrequest ownershipだけは本文を持たないtombstoneとしてmatching resolvedまで保持し、resolvedをapprovalなど別consumerへ誤配送しません。詳細は[`m4/host-user-input.md`](m4/host-user-input.md)を参照してください。

`experimentalApi=true`はexperimental surface全体へのopt-inであるため、controllerが対応していないserver requestを黙って放置しません。未知のtop-level request IDへpayloadを含まない固定のJSON-RPC `-32601` errorを即時応答し、そのrequestだけをfail closedします。Rejected IDはmatching `serverRequest/resolved`まで内部保持してapproval/user-inputへ誤配送せず、event pumpは継続します。これにより未応答requestのためmain threadとturnが入力待ちのまま固まる状態を避けます。

## Shutdown

stdio transportには`session/end` RPCはありません。新規操作を止め、active turnがあれば`turn/interrupt`の応答と`turn/completed`を待ってからstdinを閉じ、EOFを送ります。stdout/stderrのreaderを維持したままOS終了コード0を待ちます。

0.144.6の実装にはRPC drain、background task drain、thread shutdownがあるため、controllerは終了時だけ最大60秒を待ちます。timeout後のkill、非0終了、reader thread残留は正常終了へ昇格しません。`thread/archive`と`thread/delete`は永続状態を変更するためshutdown代替には使用しません。
