# Testing strategy

## Host tests

- JSONL framingとrequest ID対応
- lifecycleと異常終了
- state reducer
- pending queue
- decision normalization
- truncationとrisk gate
- reconnect snapshot
- 未知decisionを別decisionへ変換しない
- request ID不一致と二重応答を拒否する
- 2件目のpendingが表示中requestを差し替えない
- `model/list`失敗・空・model不一致時にeffort変更を無効化する
- service down時にresponse、steer、interrupt、effort変更をlockする

## Protocol fixtures

fixtureは合成ID、相対path、架空workspace名だけを使用します。認証済みCodexや実機を必要とする試験はGitHub Actionsへ入れず、local acceptance evidenceとして記録します。

## Hardware tests

- M1: backup、restore、echo、RTT、4KB line、heap
- M2: SCR-HOME、6 slot、stale、reconnect
- M3: accept、decline、hold、interrupt、pending、high-risk gate
- M4: choices、quick reply、steer、effort、scroll repeat
- M6: sleep復帰、port解放、自動起動、24時間連続運転

実機試験を実行していない場合は、build成功と実機PASSを明確に分けます。

Host response surfaceは「deviceでhold → host queueへ表示 → hostで応答 → deviceがresolvedへ追従」をE2Eで検証します。切り詰め時にdeviceのacceptが消え、hostが原文全体を表示した場合だけacceptできることも確認します。
