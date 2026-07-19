# M4 host user-input

## 目的

Codex app-serverの`item/tool/requestUserInput`をhost pending queueへ取り込み、通常のREPLで安全に表現できる最小範囲だけへユーザーが明示回答できるようにします。Hardware操作、serial port open、firmware変更は行いません。

## Schema contract

Codex CLI 0.144.6が生成したJSON Schemaで、request method、必須field、question型、response型、experimental capabilityを照合しました。生成bundle自体はrepositoryへ保存しません。

Host controllerはinitialize時だけ`experimentalApi=true`へopt-inします。App-server client単体の既定値は`false`のままです。

Request内の各質問へ別のlocal IDを割り当て、生のRPC IDとschema上のquestion IDは内部だけで相関します。非secretは通常REPL、secretはTTYのno-echo readerを使います。

```text
answer question-000001 <text>
secret question-000002
  -> 全質問が揃うまでhost memory内に蓄積
  -> {
       "answers": {
         "<first-schema-question-id>": {"answers": ["<text>"]},
         "<second-schema-question-id>": {"answers": ["<no-echo-input>"]}
       }
     }
```

## Lifecycle

```text
item/tool/requestUserInput
  -> 質問ごとにawaiting_answer
  -> hostが各回答を明示送信
  -> 未完の質問があればanswer_staged
  -> 全質問が揃った時だけresponseを1回送信
  -> response_sent（pendingを保持）
  -> serverRequest/resolved
  -> pendingから除去
```

`autoResolutionMs`によりserverが先に解決した場合も、matching resolvedで除去します。Response送信成功だけでは解決済みにせず、未知ID、thread不一致、二重回答、resolved後の後着回答を拒否します。Response送信前の部分回答はmatching resolved、host/device interrupt、turn完了で破棄します。Interruptまたはturn完了で破棄した場合も、回答本文とUI bindingだけを消し、request ownershipのtombstoneはmatching resolvedまで保持して別queueへの誤配送を防ぎます。

## Safety boundary

- 複数質問へ衝突しないlocal IDを割り当て、1つの回答を別質問へ複製しない
- `isSecret=true`はTTYで有効になる`secret <local-id>`だけを許可し、通常の`answer`では拒否。端末がechoを無効化できない場合もfallbackせず拒否
- 選択肢があり`isOther=false`ならoption labelとの完全一致だけを許可し、`isOther=true`はlabelまたは自由文を保持
- 空回答、UTF-8で1回答4 KiB超、1 request合計16 KiB超を拒否
- 生のRPC ID、schema question ID、item ID、回答本文をconsoleやdeviceへ出さない
- `answer_staged`には回答本文を含めず、response送信後はhost内の回答copyを破棄
- Question本文のterminal control characterをescapeする
- Deviceへは`attentionKind=question`だけを送り、質問・選択肢・回答本文は送らない
- Approvalと同時にpendingなら`approval > question`の表示優先順位を維持する
- `experimentalApi=true`で受けた未知server requestは黙ってdropせず、固定のJSON-RPC `-32601` errorでrequestだけをfail closedする

Cardputer上の選択肢UIは後続M4です。Deviceへ質問本文や回答面を追加せず、host側のpending正本と安全境界だけを拡張します。

## Deterministic acceptance

合成app-serverと合成Cardputerを使い、次を自動試験します。

1. initializeでcontrollerだけがexperimental capabilityへopt-in
2. turn中に複数質問とsecret質問を受信
3. host consoleへ質問ごとに異なる短縮ID、質問、選択肢を表示
4. Device stateへ`attentionKind=question`を反映
5. 1問目だけではresponseを送らず`answer_staged`へ遷移
6. secretを注入したno-echo readerだけで読み、全質問をschemaどおりのnested responseへ変換
7. matching resolved後にpending zeroへ遷移
8. auto resolutionとhost/device interruptで部分回答を破棄
9. approval/question同時pendingの優先、個別resolved、question表示復帰
10. 未対応server requestのfail-closed
11. turn completedとcontrollerの正常終了

Fixtureは合成IDと合成本文だけを使用します。実Codexで`requestUserInput`を発生させるlocal acceptanceは未実施であり、この合成結果を実接続PASSとして扱いません。
