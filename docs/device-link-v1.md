# Device link v1 draft

本書はM1〜M2で実測して確定するdraftです。

Host bridge MVPはframing、方向別byte上限、単調増加`seq`、未知`t`と過去`seq`の安全無視を実装しています。`SerialLink`はprovider injection、read thread、heartbeat、stale判定、再接続時の`hello`とfull snapshot再送を実装しています。合成USB CDCで検証済みですが、実portでの検証は未実施です。

## Framing

- UTF-8 NDJSON、1行1message、LF終端
- hostからdeviceは最大4096 byte、deviceからhostは最大1024 byte
- `t`の完全一致でdispatchし、未知typeと未知fieldは無視する
- 非JSON行はprotocolから分離して処理する
- `hello.proto`でversionを交換し、不一致時は操作を停止する
- stateは差分ではなく単調増加`seq`付きfull snapshotとする
- hostは2秒周期で`ping`し、6秒無受信をstaleとする初期値から始める

Deviceはheartbeatから`LinkState`をlocal判定します。`state` snapshotは`ServiceState`と、各slotの`turnActive`、`turnId`、`attentionKind`を別fieldで保持します。この4軸を表示用の単一状態へ統合してはなりません。

`attentionKind`の表示優先順位は`approval > question > error > done`です。再接続時のfull snapshotにはpendingの先頭1件、表示中の1件を除いた`pendingCount`、表示対象の`deviceApprovalId`を含めます。操作確定後は`resolved`または対応するserver eventを受けるまでlocal表示を`sending`とし、楽観的に完了状態へ遷移しません。

## Host to device

- `hello`
- `state`
- `detail`
- `approval`
- `approval_resolved`
- `toast`
- `ping`

JSON-RPCの`rpcRequestId`はhostだけが保持します。Deviceにはbridgeが採番した短い`deviceApprovalId`を送り、hostが両者を一対一で対応付けます。`approval`はdevice表示用に次を含めます。

- `deviceApprovalId`
- `slot`
- `kind`
- `lines`
- `cwd`
- `decisions`
- `contentComplete`
- `riskClass`
- `pendingCount`

## Device to host

- `hello`
- `select`
- `decision`: `accept | decline`
- `interrupt`
- `quickReply`
- `effort`
- `pong`
- 開発時だけの`log`

保留はlocal操作であり、pending queueからrequestを除去しません。自由文`input`は初期版へ含めません。

`decision`は現在表示中の`deviceApprovalId`をechoします。Hostは未解決かつ表示中のIDとの完全一致を確認し、対応する`rpcRequestId`へ一度だけ応答します。Approval params内の任意`approvalId`とJSON-RPC request IDを混同しません。

## Approval safety

Deviceの`accept`表示条件は、server提示または固定version fallbackのdecision集合、device allowlist、内容完全性、risk分類のすべてを満たすことです。未知decisionを別decisionへ変換してはいけません。

`contentComplete=false`または`riskClass=high`の場合、device側の`accept`を削除し、`decline`とhost escalationだけを提供します。

4096 byteへ収めるためapproval本文を切り詰めたbridgeは、必ず`contentComplete=false`を設定します。Pending正本には切り詰め前のpayloadを保持し、host response surfaceだけが全文確認後のacceptを提供します。Device表示の断片から全文を推測したり、`contentComplete=true`へ戻したりしてはいけません。
