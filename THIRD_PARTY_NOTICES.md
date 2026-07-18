# Third-party notices

初期foundation commitには第三者のソースコード、firmware image、回路図、生成schema bundleを同梱しません。

使用する主な上流project:

- OpenAI Codex app-server: OpenAI Codex repository
- M5Cardputer 1.2.0 tag: M5Stack、MIT License、sourceは取得時に解決しvendoringしない
- M5Unified 0.2.18: M5Stack、MIT License、vendoringしない
- M5GFX 0.2.25: M5Stack、MIT Licenseほか同梱componentのlicense、vendoringしない
- ArduinoJson 7.4.2: Benoit Blanchon and contributors、MIT License、vendoringしない
- IRremote 4.7.1: Arduino-IRremote contributors、MIT License、vendoringしない
- PlatformIO Core 6.1.18: PlatformIO、Apache License 2.0、開発依存のみ
- PlatformIO Espressif32 6.7.0: PlatformIO、Apache License 2.0、build時に取得
- pyserial

公開境界のCIではGitleaks 8.30.1（MIT License）を公式releaseからchecksum固定で取得して使用します。binaryはrepositoryへ同梱しません。

依存関係を追加するPRは、version、取得元、license、vendoringの有無を本ファイルへ追記してください。上流素材を複製する場合は、複製前に再配布条件とNOTICE要件を確認します。
