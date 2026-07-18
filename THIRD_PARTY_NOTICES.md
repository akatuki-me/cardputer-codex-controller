# Third-party notices

初期foundation commitには第三者のソースコード、firmware image、回路図、生成schema bundleを同梱しません。

今後使用を予定する主な上流プロジェクト:

- OpenAI Codex app-server: OpenAI Codex repository
- M5Cardputer, M5Unified, M5GFX: M5Stack
- ArduinoJson: Benoit Blanchon and contributors
- PlatformIO and Espressif32 platform
- pyserial

公開境界のCIではGitleaks 8.30.1（MIT License）を公式releaseからchecksum固定で取得して使用します。binaryはrepositoryへ同梱しません。

依存関係を追加するPRは、version、取得元、license、vendoringの有無を本ファイルへ追記してください。上流素材を複製する場合は、複製前に再配布条件とNOTICE要件を確認します。
