# GitHub setup plan

## Milestones

- M0 Host app-server proof
- M1 Recovery and CDC echo
- M2 SCR-HOME vertical slice
- M3 Safe approval E2E
- M4 Interaction
- M6 Operational v1.0
- M5 PTT v1.1

## Initial labels

- `area:bridge`
- `area:firmware`
- `area:protocol`
- `area:docs`
- `area:ci`
- `type:feature`
- `type:fix`
- `type:research`
- `risk:safety`
- `hardware:required`
- `gate:user-approval`
- `status:blocked`

## Initial M0 issues

1. app-server transport、initialize、正常終了とkill
2. thread/turn、model/list、interrupt
3. accept/decline/resolved承認往復
4. device discoveryのread-only実装
5. multi-clientとthread/resumeの実測
6. M0 evidenceと接続方式ADR

同じGitHub accountから複数のlocal agentがPRを作るため、初期rulesetではreview approvalを必須化しません。PR、required CI、会話解決、force push禁止を機械gateとし、メイン担当のreview結果をPR commentへ記録します。

Repository作成直後にGitHub secret scanning、push protection、Private vulnerability reportingを有効化します。Private vulnerability reportingを利用できない場合は公開Issueへ詳細を書かず、repository ownerのGitHub profileに掲載された非公開連絡手段を使用します。
