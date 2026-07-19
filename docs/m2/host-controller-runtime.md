# M2 host controller runtime

## 目的

実Codex app-serverの単一threadをhost stateとdevice-linkへ接続し、SCR-HOME/RUN相当の縦切りをproduction実機へ書き込む前に検証します。

## 実装

- controller-owned threadを`ephemeral=true`、`sandbox=read-only`、`approvalPolicy=on-request`で作成
- 6 slot full snapshotを維持し、slot 1へcontroller-owned threadを明示割当
- `turn/started`と`turn/completed`をSCR-HOME/RUN状態へ反映
- focus中active turnだけを`turn/interrupt`へ転送
- device `hello`後にhost `hello`とfull snapshotを送信し、再接続で再発行
- host consoleから`run`、`wait`、`pending`、approval応答、`interrupt`、`quit`を操作
- stdin EOFによるapp-server正常終了を最大60秒の有界待機で確認

## 合成受入

```powershell
cardputer-codex-controller control --synthetic --cwd . --label fixture
```

Codex CLI 0.144.6とのlocal acceptanceで、initialize、thread start、device handshake、turn start/completed、full state、wait、正常終了をPASSしました。合成deviceはmemory内だけで動作し、serial portを開きません。

## 未検証境界

- Production firmwareを実機へ書き込んだSCR-HOME/RUN表示
- 物理keyによるslot選択とG0 interrupt
- USB切断、sleep、長時間運転を含むproduction運用

これらはproduction image書き込みと初回port openの承認後に実施します。
