# Cardputer Codex Controller

M5Stack Cardputer-Advを、Codexの状態確認・選択・安全な承認・中断に使う物理コントローラーへする非公式のオープンソースプロジェクトです。

> [!WARNING]
> 現在は初期実装段階です。ファームウェアの書き込み手順はまだリリースされていません。

## 目標

- 6つのCodexスレッド状態を240×135の画面へ表示する
- 選択肢回答、定型返信、steer、interruptを物理操作にする
- コマンド・ファイル変更の承認内容を表示してから応答する
- USB切断、Codex停止、再起動から安全に復帰する
- 判断の一次防御をホスト側へ置き、デバイスを二次防御として使う

Cardputer上の自由文入力、未知形式の構造化質問、高リスク承認のデバイス許可は初期版の対象外です。音声入力はv1.1候補です。

## 構成

```text
Codex app-server (stdio JSONL)
            ↕
Python host bridge + pending queue
            ↕ USB CDC / NDJSON
Cardputer-Adv firmware
```

詳細は[アーキテクチャ](docs/architecture.md)、[UX仕様](docs/ux-spec.md)、[安全仕様](docs/safety.md)、[ロードマップ](docs/roadmap.md)を参照してください。Controllerの単一起動と終了順序は[M6 lifecycle](docs/m6/controller-lifecycle.md)に記録しています。

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
python -m platformio test -d firmware -e native
python -m platformio run -d firmware -e cardputer_adv
python -m platformio run -d firmware -e cardputer_adv_bringup
```

作業はIssueから開始し、1 Issue = 1 branch = 1 PRとします。詳しくは[CONTRIBUTING.md](CONTRIBUTING.md)を参照してください。

## 合成デモ

実機やserial portを使わず、host controllerの縦切りを再現できます。

```powershell
python -m pip install -e .
cardputer-codex-controller demo
```

このデモはdevice hello、6 slotのfull snapshot、turnの開始・実行・完了、interrupt、承認の保留とhost解決、安全guardを順に表示します。合成デモ出力中の`codexConnection`は`N/A`です。

認証済みCodex CLIとの実接続は、次の1 commandで確認できます。一時workspace、ephemeral thread、`sandbox=read-only`、`approvalPolicy=never`を使い、ID、prompt、model名、pathは出力しません。

```powershell
cardputer-codex-controller codex-demo
```

このcommandもhardwareやserial portには接続しません。

## Firmware build

Cardputer-Adv向けfirmwareは、実機やserial portへ接続せずにbuildできます。

```powershell
python -m platformio test -d firmware -e native
python -m platformio run -d firmware -e cardputer_adv
python -m platformio run -d firmware -e cardputer_adv_bringup
```

production imageは`firmware/.pio/build/cardputer_adv/firmware.bin`、Codex commandを一切送らないM1診断imageは`firmware/.pio/build/cardputer_adv_bringup/firmware.bin`へ生成されます。このrepositoryはbuildとnative fixtureだけを通常gateに含め、upload、flash read/write、port openを実行しません。

## M1 Cardputer-Adv bring-up

実機やCodexへ接続せず、board handshake、4 KiB echo、RTT、throughput、heartbeat、数字key、G0短押し・長押しの診断経路を合成deviceで確認できます。

```powershell
cardputer-codex-controller bringup --synthetic
```

実portは`--port`または`--port-handle`で明示選択します。最初は必ず`--dry-run`を付け、portを開かず選択経路だけを確認します。

```powershell
cardputer-codex-controller bringup --port-handle local-private/device-port.txt --dry-run
```

`--dry-run`を外す操作と診断firmwareの書き込みはhardware承認ゲートの対象です。実portでは物理入力を最大120秒待ちます。詳細な手順と検証済み範囲は[M1 bring-up](docs/m1/cardputer-bringup.md)を参照してください。

## M3 approval安全境界fixture

Codexやcommand実行を起動せず、固定の無害なapprovalだけをproduction firmwareへ送り、300ms guard、本文末尾、high-risk、本文不完全、hold、declineを案内付きで受入できます。最初に合成経路と実portのdry-runを実行します。

```powershell
cardputer-codex-controller approval-fixture --synthetic
cardputer-codex-controller approval-fixture --port-handle local-private/device-port.txt --dry-run
```

実portで`--dry-run`を外す操作はhardware承認ゲートの対象です。実行中は案内に従ってCardputerの物理キーを操作し、各確認後にhost consoleで`ok`と入力して`Enter`で確定します。最初の300ms guardだけは、fixture表示への切替と同時にCardputerで`a`と`Enter`を押せるよう、arm promptで事前準備します。出力は固定stepとPASS/FAILだけで、port、approval ID、本文、cwd、生NDJSONを含めません。正確な299/300ms境界はnative fixture、物理キーと表示はhuman-attested local acceptanceとして組み合わせます。合成PASSやdry-runを実機PASSへ昇格しません。

## USB CDC E2E harness

認証済みCodex app-server、host state reducer、合成USB CDCを1本につなぎ、Cardputerから届いた想定の`interrupt`がactive turnへ一度だけ転送されることを確認できます。

```powershell
cardputer-codex-controller e2e --synthetic
```

合成CDCはdevice `hello`、重複`interrupt`、host側のNDJSONをmemory内で往復させます。実portは開かず、出力にはthread ID、turn ID、prompt、model、pathを含めません。

実portは`--port`またはGit管理外の1行fileを指す`--port-handle`で明示選択します。初回openの承認前は`--dry-run`を必ず付けます。dry-runは選択だけを検証し、serial I/OとCodexを起動しません。

```powershell
cardputer-codex-controller e2e --port-handle local-private/device-port.txt --dry-run
```

`--dry-run`を外す操作はhardware承認ゲートの対象です。実portを使ったE2Eは未検証であり、合成E2EのPASSを実機PASSとして扱いません。

## Host controller runtime

認証済みCodex app-serverと合成Cardputerを接続し、単一のcontroller-owned threadを対話操作できます。合成実行でもCodexは実接続です。

```powershell
cardputer-codex-controller control --synthetic --cwd . --label main
```

起動後は`run <text>`、`wait`、`pending`、`approve <id>`、`decline <id>`、`cancel <id>`、`answer <id> <text>`、`secret <id>`、`interrupt`、`quit`を使用します。1 requestに複数質問がある場合も質問ごとに異なるlocal IDを表示し、全回答が揃うまでapp-serverへresponseを送りません。`isSecret=true`はTTYでだけ有効な`secret <id>`からno-echo入力し、通常の`answer`では拒否します。回答本文はlogやCardputerへ送りません。

`wait`は完了時に`PASS`、未応答approval受信時に`BLOCKED pending_approval`、未応答質問受信時に`BLOCKED pending_question`、待機上限到達時に`TIMEOUT`を返してREPLへ戻り、controller sessionを終了しません。Threadは`ephemeral=true`、`sandbox=read-only`、`approvalPolicy=on-request`で作成されます。Hostはserverが提示したdecisionだけを送り、deviceへ表示できない追加文脈付きrequestはhost-onlyに留めます。

実portの選択経路だけを確認する場合は、Git管理外のhandleと`--dry-run`を使用します。このcommandはserial I/OもCodexも開始しません。

```powershell
cardputer-codex-controller control --port-handle local-private/device-port.txt --cwd . --label main --dry-run
```

`--dry-run`を外すproduction controller接続はhardware承認ゲートの対象です。`quit`はapp-serverへstdin EOFを送り、最大60秒の有界待機後も正常終了しない場合は失敗として扱います。

非dry-runの`control`は1processだけが起動できます。2つ目はprovider生成、serial open、Codex起動より前に`control FAIL ControllerAlreadyRunningError`で終了し、port、PID、lock pathを表示しません。終了時はserialをapp-serverのdrainより先に閉じ、drain完了後にinstance lockを解放します。

## 状態

- 公開開発基盤: 実装済み
- M0 Host app-server transport・thread/turn操作: 実装済み
- Host controller合成デモ: 実装済み
- Cardputer-Adv production firmware MVP: build・native fixture・実機書き込み・hash verificationをPASS
- M1診断firmware・host bring-up harness: v0.1.3でrecovery-first、実機機能、device計測のstale 6秒以内をPASS
- 実Codex・合成USB CDC E2E: 実装済み
- M2 host runtime・SCR-HOME/RUN・G0 interrupt・stale/reconnect: 実Codexとproduction実機で受入済み
- M3 approval coordinator・host response surface・物理accept/decline/hold・accept禁止guard・複数pending: production実機で受入済み
- M3 hardening fixture: 合成・dry-run、300ms早押し・6行本文scroll・high-risk表示の追加実機受入を完了
- M4 host user-input: 複数質問の一括responseとsecret no-echo入力を合成app-server E2Eで受入済み
- 実USB CDC controller E2E: 実Codexとproduction実機で受入済み
- 専用`e2e` commandの実port経路: 未検証（production受入は`control` commandで実施）
- M6 controller単一起動・host-only port解放fixture: 実装済み（実USB portの再open、自動起動、sleep復帰、24時間運転は未受入）
- multi-client・ADR・M4 device interaction・M6残作業: 継続中
- 実機書き込み: M1診断firmware v0.1.3とproduction imageを受入済み

## 免責

本プロジェクトはOpenAIおよびM5Stackの公式製品ではなく、両社による承認・保証を受けたものではありません。Codex、Cardputerおよび関連する名称は各権利者に帰属します。

## License

Apache License 2.0。第三者依存関係は[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)を参照してください。
