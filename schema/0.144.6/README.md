# Codex app-server schema 0.144.6

Codex CLI 0.144.6が生成するJSON Schemaを、0.144.5からのpatch version差分として確認します。

```powershell
codex app-server generate-json-schema --out <temporary-directory>
```

2026-07-19の差分確認では、bridgeが使用する次の契約に意味上の変更がないことを確認しました。

- `CommandExecutionRequestApprovalResponse`: byte単位で一致
- `FileChangeRequestApprovalParams`: byte単位で一致
- `FileChangeRequestApprovalResponse`: byte単位で一致
- `CommandExecutionRequestApprovalParams`: `threadId`と`turnId`のproperty順だけが変化

2026-07-20にM4 host回答面のため、次の契約も同じ生成bundleで確認しました。

- Server request method: `item/tool/requestUserInput`
- `ToolRequestUserInputParams`: `threadId`、`turnId`、`itemId`、`questions`が必須
- Question: `id`、`header`、`question`が必須、`options`はarrayまたはnull、`isOther`と`isSecret`の既定値はfalse
- `ToolRequestUserInputResponse`: `answers`が必須で、question IDごとの値は`{"answers": string[]}`
- `InitializeCapabilities.experimentalApi`: experimental method/field受信への明示opt-in

生成bundle自体はrepositoryへ同梱しません。0.144.6以外の新versionを追加するときは別directoryで再生成し、使用methodと型の差分reviewを行います。
