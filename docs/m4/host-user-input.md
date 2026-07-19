# M4 host user-input

## 目的

Codex app-serverの`item/tool/requestUserInput`をhost pending queueへ取り込み、通常のREPLで安全に表現できる最小範囲だけへユーザーが明示回答できるようにします。Hardware操作、serial port open、firmware変更は行いません。

## Schema contract

Codex CLI 0.144.6が生成したJSON Schemaで、request method、必須field、question型、response型、experimental capabilityを照合しました。生成bundle自体はrepositoryへ保存しません。

Host controllerはinitialize時だけ`experimentalApi=true`へopt-inします。App-server client単体の既定値は`false`のままです。

単一質問への回答は、生のRPC IDとschema上のquestion IDを内部で相関し、次の形で1回だけ送ります。

```text
answer <local-id> <text>
  -> {
       "answers": {
         "<schema-question-id>": {"answers": ["<text>"]}
       }
     }
```

## Lifecycle

```text
item/tool/requestUserInput
  -> awaiting_answer
  -> hostがanswerを明示送信
  -> response_sent（pendingを保持）
  -> serverRequest/resolved
  -> pendingから除去
```

`autoResolutionMs`によりserverが先に解決した場合も、matching resolvedで除去します。Response送信成功だけでは解決済みにせず、未知ID、thread不一致、二重回答、resolved後の後着回答を拒否します。

## Safety boundary

- 対応範囲は質問が1件かつ`isSecret=false`の場合だけ
- 複数質問は`multiple_questions`、secret質問は`secret_input`として応答不能のまま表示
- 通常REPLへsecretを入力させず、複数質問へ同じ回答を複製しない
- 空回答とUTF-8で4 KiBを超える回答を拒否
- 生のRPC ID、schema question ID、item ID、回答本文をconsoleやdeviceへ出さない
- Question本文のterminal control characterをescapeする
- Deviceへは`attentionKind=question`だけを送り、質問・選択肢・回答本文は送らない
- Approvalと同時にpendingなら`approval > question`の表示優先順位を維持する
- `experimentalApi=true`で受けた未知server requestは黙ってdropせず、固定のJSON-RPC `-32601` errorでrequestだけをfail closedする

複数質問の回答蓄積と一括response、secret用no-echo入力は[Issue #21](https://github.com/akatuki-me/cardputer-codex-controller/issues/21)へ分離します。Cardputer上の選択肢UIも後続M4とし、未対応requestは自動解決せず、現版ではhostの`interrupt`でturnを中断します。

## Deterministic acceptance

合成app-serverと合成Cardputerを使い、次を自動試験します。

1. initializeでcontrollerだけがexperimental capabilityへopt-in
2. turn中に単一・非secret質問を受信
3. host consoleへ短縮ID、質問、選択肢を表示
4. Device stateへ`attentionKind=question`を反映
5. `answer`をschemaどおりのnested responseへ変換
6. matching resolved後にpending zeroへ遷移
7. turn completedとcontrollerの正常終了
8. approval/question同時pendingの優先、個別resolved、question表示復帰
9. 未対応server requestのfail-closed

Fixtureは合成IDと合成本文だけを使用します。実Codexで`requestUserInput`を発生させるlocal acceptanceは未実施であり、この合成結果を実接続PASSとして扱いません。
