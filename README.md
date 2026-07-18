# Cardputer Codex Controller

M5Stack Cardputer-Advを、Codexの状態確認・選択・安全な承認・中断に使う物理コントローラーへする非公式のオープンソースプロジェクトです。

> [!WARNING]
> 現在は実装準備段階です。ファームウェアの書き込み手順はまだリリースされていません。

## 目標

- 6つのCodexスレッド状態を240×135の画面へ表示する
- 選択肢回答、定型返信、steer、interruptを物理操作にする
- コマンド・ファイル変更の承認内容を表示してから応答する
- USB切断、Codex停止、再起動から安全に復帰する
- 判断の一次防御をホスト側へ置き、デバイスを二次防御として使う

自由文入力、未知形式の構造化質問、高リスク承認のデバイス許可は初期版の対象外です。音声入力はv1.1候補です。

## 構成

```text
Codex app-server (stdio JSONL)
            ↕
Python host bridge + pending queue
            ↕ USB CDC / NDJSON
Cardputer-Adv firmware
```

詳細は[アーキテクチャ](docs/architecture.md)、[UX仕様](docs/ux-spec.md)、[安全仕様](docs/safety.md)、[ロードマップ](docs/roadmap.md)を参照してください。

## 開発

Python 3.12以降を使用します。

```bash
make install
make ci
```

`make`がないWindows環境では、PowerShellから同じgateを直接実行できます。

```powershell
python -m pip install -e ".[dev]"
python scripts/check_publication.py
python -m mypy bridge/cardputer_codex_bridge
python -m ruff check .
python -m pytest
python -m build
```

作業はIssueから開始し、1 Issue = 1 branch = 1 PRとします。詳しくは[CONTRIBUTING.md](CONTRIBUTING.md)を参照してください。

## 合成デモ

実機やserial portを使わず、host controllerの縦切りを再現できます。

```powershell
python -m pip install -e .
cardputer-codex-controller demo
```

このデモはdevice hello、6 slotのfull snapshot、turnの開始・実行・完了、interrupt、承認の保留とhost解決、安全guardを順に表示します。合成デモ出力中の`codexConnection`は`N/A`です。実Codex eventを同じstate reducerへ流す統合は後続Issueで行い、実portは開きません。

認証済みCodex CLIとの実接続は、次の1 commandで確認できます。一時workspace、ephemeral thread、`sandbox=read-only`、`approvalPolicy=never`を使い、ID、prompt、model名、pathは出力しません。

```powershell
cardputer-codex-controller codex-demo
```

このcommandもhardwareやserial portには接続しません。

## 状態

- 公開開発基盤: 実装済み
- M0 Host app-server transport・thread/turn操作: 実装済み
- Host controller合成デモ: 実装済み（実Codex eventとのreducer統合は未実装）
- M0 approval・multi-client・ADR: 継続中
- 実機書き込み: 未承認・未実施

## 免責

本プロジェクトはOpenAIおよびM5Stackの公式製品ではなく、両社による承認・保証を受けたものではありません。Codex、Cardputerおよび関連する名称は各権利者に帰属します。

## License

Apache License 2.0。第三者依存関係は[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)を参照してください。
