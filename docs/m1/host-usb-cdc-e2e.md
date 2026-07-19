# Host USB CDC E2E evidence

## 対象

- Codex CLI: 0.144.5
- Device transport: 合成USB CDC、UTF-8 NDJSON
- Host: Windows、Python 3.12
- 実測日: 2026-07-18

## Local acceptance

認証済みCodex app-serverと合成USB CDCを同じcontroller consumerへ接続した。

```powershell
cardputer-codex-controller e2e --synthetic
```

次の15段階がPASSした。

- app-server起動とinitialize
- ephemeral・read-only・approval neverのthread作成
- model一覧の取得
- 合成serial linkとdevice `hello`
- turn開始とfull state snapshot
- active turnへのsteer
- deviceからの重複`interrupt`受信
- active turnへの`turn/interrupt`一回だけの転送
- turn完了と完了snapshot
- serial linkとapp-serverの正常終了

出力はstep名とPASSだけに限定した。thread ID、turn ID、prompt、model、workspace path、port、serial number、生のnotification、stderr本文は記録していない。実port、firmware、flashにはアクセスしていない。

dry-runも独立に実行した。

```powershell
cardputer-codex-controller e2e --synthetic --dry-run
```

`serial_io_opened false`と`codex_connection N/A`を確認した。dry-runはserial providerの`open`とCodex app-server起動を呼ばないことをconsumer testでも固定した。

## Windows同等gate

README記載のWindows commandを実行した。

- publication boundary: PASS
- mypy: 21 source files、PASS
- ruff: PASS
- pytest: 77 passed、2 skipped
- sdistとwheel build: PASS

skipした2件は既存の環境変数opt-in型live app-server testである。同じ実Codex経路は上記E2E commandで別途PASSしている。

## Deterministic consumer tests

合成app-serverとprovider injectionしたserial transportで次を検証した。

- read threadによる部分・複数NDJSON frameのconsumer接続
- `hello`、full snapshot、`ping`、`pong`
- serial切断後の再openとfull snapshot再送
- 再接続ごとのdevice sequence decoder初期化
- 同じactive turnへの重複`interrupt`を一回だけ転送
- pyserial providerがhardware flow controlを無効化し、DTR/RTSを個別操作しない
- 明示`--port`とGit管理外の`--port-handle`
- dry-runがserial I/OとCodexを起動しない
- failure出力がexception classだけで、値を含まない

## 未検証

- 実USB CDC portのopen、読み書き、切断復帰
- Cardputer-Adv firmwareとのhandshakeと物理key入力
- 実機での6秒stale latency
- firmware書き込み、flash読出し、factory firmware復元

これらは今回のPASSへ含めない。

## 実機診断で判明した契約修正

実USB CDCでdeviceが先に`hello`を送ることを確認した。接続直後にhostから`hello`とfull snapshotを送る実装では、device側の受信準備前に初期frameを失う可能性があるため、handshakeを次の順序へ固定した。

1. deviceが`hello`を送る
2. hostが`hello`を返す
3. hostがfull snapshotを送る

hostは同一接続中の重複`hello`へ再応答せず、再接続時だけhandshake状態を初期化する。Windowsでread thread終了とserial closeが競合した場合も、close処理から`AttributeError`を漏らさない回帰試験を追加した。

実port単体ではdevice `hello`受信、link active、正常closeまで確認した。一方、実機画面でfull snapshotが反映されることと物理key入力は未受入であり、M1のCodex非依存diagnosticsで別々に検証する。
