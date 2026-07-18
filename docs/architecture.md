# Architecture

## System boundary

```text
Codex app-server
  stdio / JSONL
        ↕
Host bridge
  lifecycle / state reducer / pending queue / safety policy
        ↕
USB CDC / device-link v1 (NDJSON)
        ↕
Cardputer-Adv firmware
  display / keys / local safety guards
```

## Responsibilities

Codexの認証、sandbox、approval policy、thread/turn lifecycleはホスト側に置きます。Host bridgeはapp-server eventをdevice向けsnapshotへ変換します。JSON-RPCの`rpcRequestId`はhostだけが保持し、deviceへ送る短い`deviceApprovalId`との一対一対応を検証してからresponseを返します。

Deviceはcredentialやconversation historyを保存しません。表示中の状態、選択slot、未確定操作だけを保持し、再接続時はhostのfull snapshotから復元します。

## Initial transport choices

- app-server: stdio JSONL
- device: USB Serial/JTAG CDC
- device framing: UTF-8 NDJSON
- network transport: initial releaseでは使用しない

## Failure domains

USB linkとCodex serviceを別状態として扱います。USBがactiveでもCodexがreadyでなければ、応答、steer、interruptを送信しません。snapshotが古い場合も送信操作をlockします。
