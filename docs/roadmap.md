# Roadmap

## M0 Host app-server proof

stdio接続、lifecycle、thread/turn、model/list、interrupt、accept/decline/resolvedを実測します。実機への書き込みは行いません。

状態: transport、thread/turn、interrupt、approval contract、0.144.6実接続を完了。

## M1 Recovery and CDC echo

factory firmwareの退避・復元経路を確立してから、最小echo firmwareでCDC、RTT、throughput、heapを測定します。

状態: diagnostic firmware v0.1.3で実機受入を完了。Recovery gate通過後、production imageの実機書き込みとhash verificationを完了。

## M2 SCR-HOME vertical slice

app-server eventからhost state、device-link、実機SCR-HOMEまでの縦串を通します。

状態: host runtime、production実機のHOME/RUN、G0 interrupt、stale/reconnect、full snapshot復帰を完了。

## M3 Safe approval E2E

安全guard、pending queue、host response surface、deviceのaccept/decline/hold、interruptを統合します。この段階を実用MVPとします。

状態: 実Codexとproduction実機でhost cancel、物理accept/decline/hold、accept禁止guard、複数pending、自動送りを完了。300ms早押し境界、長文scroll、high-risk表示は追加受入として残す。

## M4 Interaction

選択肢回答、定型返信、steer、model別effort、残画面を追加します。自由文入力は初期版へ含めません。

## M6 Operational v1.0

自動起動、port解放、再接続、sleep復帰、監査log、24時間連続運転を完成させます。

## M5 PTT v1.1

音声録音、転送、文字起こし、turn投入はcore v1.0後の拡張とします。
