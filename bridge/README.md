# Host bridge

Python 3.12で、Codex app-serverのstdio JSONLとCardputer-AdvのUSB CDC/NDJSONを変換します。

初期実装はM0で追加します。主要責務は次のとおりです。

- app-server lifecycleとserver requestの処理
- 6 slotの状態集約
- pending queueとhost側の最小応答手段
- device linkのserialization、再接続、snapshot復元
- safety guardと監査可能な最小ログ
