# Contributing

## 基本フロー

1. 対応するIssueを選ぶか、新しいIssueで目的と受入基準を合意する。
2. `feat/<issue-number>-<slug>`、`fix/<issue-number>-<slug>`など目的別branchを作る。
3. 変更とテストを同じbranchへ入れる。
4. `make ci`を実行する。`make`がないWindows環境ではREADME記載の同等commandをすべて実行する。
5. Draft PRを作り、Issueへ関連付ける。
6. CI、レビュー、会話解決後にsquash mergeする。

## PRに必要な情報

- `Closes #<issue>`
- 変更理由と責務境界
- Touch inventory
- 実行した検証commandと結果
- 未検証事項
- hardware操作の有無
- 公開情報検査の結果

## 公開情報

IssueやPRへ、認証情報、完全なprompt、ソース本文を含む生ログ、端末固有ID、port番号、ローカル絶対pathを投稿しないでください。再現情報はplaceholderと最小fixtureへ置き換えます。

## Hardware

firmwareのbuildは通常のPR作業に含められますが、実機への書き込み、flashの読出し、portのopenは別の承認対象です。エージェントやCIが自動実行してはいけません。
