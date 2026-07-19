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

初回の実施結果と公開可能な証拠は[M1 bring-up](m1/cardputer-bringup.md)へ記録します。

## Production controller gate

Production firmwareはbuildとnative fixtureまで検証済みですが、実機への書き込みは未実施です。Production imageの書き込みと、書き込み後の最初のUSB CDC port openは別々の明示承認対象です。

M1でflasher stub起動後のUSB通信消失を確認したため、productionとdiagnosticのuploadは115200 baudのROM loaderと`--no-stub`を共通契約とします。Production uploadがこの設定を持つことはhost testで固定します。

承認前に許可されるのはbuild、合成CDC、`control --dry-run`までです。承認後はSCR-HOME/RUN、approval guard、物理accept/decline/hold、G0 interrupt、stale/reconnect、full snapshot復元を順に受入し、合成結果を実機PASSへ自動昇格しません。
