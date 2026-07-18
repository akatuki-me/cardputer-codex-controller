# Security Policy

## Supported versions

最初の安定版が公開されるまでは、`main`の最新状態だけを対象にします。

## Reporting a vulnerability

脆弱性、認証情報の漏えい、安全境界の迂回は公開Issueへ投稿しないでください。リポジトリのPrivate vulnerability reportingを利用してください。公開前はリポジトリ所有者へ非公開で連絡してください。

報告には再現手順、影響、対象commitを含め、認証情報、生ログ、実機固有IDは含めないでください。

## Trust boundaries

- Codexとの接続はローカルのapp-server stdioを既定とする。
- デバイス通信はローカルUSB CDCだけを初期対象とする。
- 認証情報をfirmware、serial message、通常ログへ送らない。
- sandboxとapproval policyはホスト側の一次防御である。
- デバイスは表示・誤操作防止・interruptを提供する二次防御であり、一次防御を置き換えない。
- `contentComplete=false`または`riskClass=high`の承認ではdevice側のacceptを禁止する。

## Disclosure process

受領確認、影響評価、修正版の準備、公開の順に対応します。修正版と利用者向け緩和策が用意できる前に詳細を公開しません。
