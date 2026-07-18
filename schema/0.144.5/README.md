# Codex app-server schema 0.144.5

実装はCodex CLI 0.144.5が生成するJSON Schemaを型の基準にします。

生成例:

```text
codex app-server generate-json-schema --out schema/0.144.5/generated
```

CLIが生成するbundleは実行したCLI versionに対応します。初期foundation commitにはbundleを含めません。再配布条件とNOTICE要件の確認後、hash manifestとともに別PRで追加します。
