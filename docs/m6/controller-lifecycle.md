# M6 controller lifecycle

`control`の非dry-run実行は、provider生成とCodex app-server起動より前にprocess間lockを取得します。初期実装は1台のCardputerと1つのcontroller-owned threadだけを所有するため、合成・実portを問わず同時に動作できるcontrollerは1processです。

## 単一起動

Lockはuser固有の一時sub directory（POSIXはUID、Windowsはuser名でscopeし、`0700`で作成）へ置く1 byteのfileへ、Windowsでは`msvcrt.locking`、POSIXでは`flock`を適用します。共有tmp直下の固定名を避けることで、別userによる先取りやsymlinkすり替えを防ぎます。所有権はfileの内容ではなくOS lockにあり、PID、port、command、cwdを保存しません。Lock file自体は再利用し、正常終了時に削除しません。

2つ目のprocessはproviderを生成せず、serial I/OとCodex app-serverを開始する前に拒否されます。公開stderrは次の固定値だけです。

```text
control FAIL ControllerAlreadyRunningError
```

`--dry-run`はserial I/OとCodexを開始しないため、instance lockを取得しません。

## 終了順序

Controllerは終了時に次の順序を守ります。

1. event pumpへ停止を通知する
2. device sessionを閉じ、serial read threadとportを解放する
3. app-serverのstdinを閉じ、最大60秒のgraceful shutdownを待つ
4. event pumpの終了を確認する
5. controller instance lockを解放する

この順序により、app-serverのdrain中も新しいcontrollerの起動は拒否しつつ、serial資源は先に解放します。App-server起動途中で例外が発生した場合もclient cleanupを実行します。Processが強制終了した場合はOSがlockを回収します。

## Host-only受入

自動試験では次を確認します。

- 別processが保持中のlockを2つ目のprocessが取得できない
- 正常終了、例外、process強制終了後にlockを再取得できる
- 二重起動時にproviderとcontroller runtimeを生成しない
- app-server起動途中の失敗でもclient cleanupを実行する
- app-server shutdownを意図的に待たせても、終了開始から3秒以内に排他的な合成serial資源を再openできる
- 公開失敗出力へPID、port、lock pathを含めない

これはhost lifecycleとcleanup順序の証拠です。実USB CDC portの3秒以内の再open、自動起動、sleep復帰、24時間連続運転は未受入であり、hardware承認後のM6 local acceptanceへ残します。
