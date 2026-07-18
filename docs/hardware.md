# Hardware

## Target

- M5Stack Cardputer-Adv
- USB Serial/JTAG mode
- USB CDC communication
- 240×135 display

実機のport番号、serial number、machine固有のdevice pathをsourceやIssueへ記録しません。検出は共通のUSB識別情報と、local-only設定を組み合わせます。

Host bridgeは実portを`--port`またはGit管理外のlocal handleで明示選択した場合だけopenします。dry-runは選択を検証してもserial I/Oを開始しません。通常のUSB CDC接続ではDTR/RTSを個別に操作しません。

## Toolchain

- PlatformIO
- Arduino framework
- M5Cardputer
- M5Unified
- M5GFX

第三者codeは初期commitへvendorせず、version固定した依存関係として取得します。

## Recovery first

初回書き込み承認を求める前に、全flashを独立に2回読み出してsizeとSHA-256の一致を確認し、backupを保護し、手動download modeと復元commandを手順化します。承認後の最初の書き込みを照合済みbackup imageのwrite-backによる復元試験とし、factory firmwareの正常起動を確認してからecho firmwareへ進みます。Backupと実機IDは公開しません。
