# M2 host controller runtime

## 目的

実Codex app-serverの単一threadをhost stateとdevice-linkへ接続し、SCR-HOME/RUN相当の縦切りをproduction実機まで検証します。

## 実装

- controller-owned threadを`ephemeral=true`、`sandbox=read-only`、`approvalPolicy=on-request`で作成
- 6 slot full snapshotを維持し、slot 1へcontroller-owned threadを明示割当
- `turn/started`と`turn/completed`をSCR-HOME/RUN状態へ反映
- app-server event threadとserial threadが共有するcontroller stateを単一同期境界で更新
- focus中active turnだけを`turn/interrupt`へ転送
- device `hello`後にhost `hello`とfull snapshotを送信し、再接続で再発行
- host consoleから`run`、`wait`、`pending`、approval応答、`interrupt`、`quit`を操作
- `wait`はpending approvalまたは待機上限でREPLへ戻り、controller sessionを維持
- stdin EOFによるapp-server正常終了を最大60秒の有界待機で確認

## 合成受入

```powershell
cardputer-codex-controller control --synthetic --cwd . --label fixture
```

Codex CLI 0.144.6とのlocal acceptanceで、initialize、thread start、device handshake、turn start/completed、full state、wait、正常終了をPASSしました。合成deviceはmemory内だけで動作し、serial portを開きません。

## Production実機受入

Production firmwareと実Codex CLI 0.144.6を接続し、`CODEX HOME`と緑色`UP`、turn開始時のRUN、完了後の状態反映、G0長押しによる`turn/interrupt`を確認しました。Controller終了後は6秒以内にlink staleとなり、再起動後はnew host sessionのhandshakeとfull snapshotでHOMEへ復帰しました。Stale状態からのhost接続開始〜active/full snapshotは1,766msで、6秒基準をPASSしました。

実測中に、長いturnまたはapproval待ちで`wait`がREPLを占有しcontroller全体をtimeout終了させる問題を検出しました。Pending受信時は`BLOCKED pending_approval`、待機上限時は`TIMEOUT`でREPLへ戻す回帰テストを追加し、実Codexのhost cancel、resolved、turn completionで修正を確認しています。

## 未検証境界

- 物理keyによるslot 2〜6の選択
- USB抜去、sleep、長時間運転を含むproduction運用

これらはM4またはM6の運用受入で実施します。
