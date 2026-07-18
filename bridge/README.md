# Host bridge

Python 3.12で、Codex app-serverのstdio JSONLとCardputer-AdvのUSB CDC/NDJSONを変換します。

主要責務は次のとおりです。

- app-server lifecycleとserver requestの処理
- 6 slotの状態集約
- pending queueとhost側の最小応答手段
- device linkのserialization、再接続、snapshot復元
- safety guardと監査可能な最小ログ

## 合成controllerデモ

```powershell
cardputer-codex-controller demo
```

合成adapterを使用するため`codexConnection`は`N/A`で、実portは開きません。`codex-demo`はapp-server操作を独立に実測します。`HostCommandAdapter`境界へ実Codex eventを接続する作業は後続Issueで行います。
