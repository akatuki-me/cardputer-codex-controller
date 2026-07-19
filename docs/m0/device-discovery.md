# Read-only device discovery

Issue #4では、Cardputer-AdvのUSB Serial/JTAG候補を、portをopenせずに列挙する境界を実装します。

## 採用規則

既定では共通USB識別情報`303a:1001`に一致するdeviceだけを候補にします。同じ識別情報のdeviceが複数ある環境では、source管理外のlocal設定から渡す`local_filter`で絞り込みます。Machine固有のlocatorやserial numberをsource、通常log、永続化データ、公開evidenceへ出してはいけません。

結果は候補数に応じて次の3状態を返します。

| 候補数 | `status` | 自動選択 |
| ---: | --- | --- |
| 0 | `not_found` | しない |
| 1 | `unique` | `unique_candidate`として返す |
| 2以上 | `ambiguous` | しない |

候補にはscan内の連番、共通USB識別情報、memory内だけで使う`LocalDeviceHandle`が含まれます。Handleは`repr`でredactされますが、serial numberやlocatorを保持するため、serializeまたはlogへ渡してはいけません。

## 安全境界

- 既定providerはpyserialの`list_ports.comports()`だけを呼びます。
- `serial.Serial`は生成せず、DTR/RTSを含むport操作を行いません。
- Providerとlocal filterの例外messageは公開例外へ転記しません。
- 合成fixtureは、openを呼ぶと即失敗するtrapで非open性を検証します。
- 実通信、port open、flash read/writeはこのIssueの対象外です。

## Local acceptance

実機確認で公開してよいのは、適用した共通識別規則、候補件数、3状態のいずれになったか、portをopenしていない事実だけです。Machine固有値は記録しません。

2026-07-18のread-only実測では、規則`303a:1001`に対して候補1件、`unique`でした。実測に使用したcode pathは列挙だけを行い、port open、通信、flash操作を行っていません。
