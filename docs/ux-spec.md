# UX specification v0.4

## Purpose

Cardputer-Advを文章入力端末ではなく、常設の監視・選択・安全操作面として使います。番号、定型文、shortcutを中心にし、自由記述が必要な判断はhostへ移します。

## Safety state

最低限、次の4軸を独立して保持し、単一の表示用`st`へ潰しません。

- `LinkState`: `active | stale`
- `ServiceState`: `initializing | ready | down | auth_required`
- `turnActive`: active turnの有無と`turnId`
- `attentionKind`: `none | approval | question | error | done`

`LinkState=active`かつ`ServiceState=ready`のときだけ送信操作を許可します。interruptにはさらに`turnActive=true`を必要とします。閲覧、戻る、保留はlocal操作なのでlinkがstaleでも利用できます。

要対応が同時に成立した場合の優先順位は`approval > question > error > done`です。Deviceには最優先の1件と「他の要対応+n」を表示し、hostのpending正本から項目を消しません。

## Slots and effort

初期版は6つのstatic slotを使用し、controllerから明示的に割り当てたthreadだけを対象にします。`thread/list`から未知threadを自動追加せず、hostのcwd allowlist外はdevice操作対象にしません。

Effort候補は現在modelに対応する`model/list.data[].supportedReasoningEfforts[].reasoningEffort`だけを使います。取得失敗、空集合、model不一致、または`ServiceState!=ready`ではeffort変更を無効化します。

## Screens

- `SCR-HOME`: 6 slotの状態一覧
- `SCR-ASK`: device対応可能な選択肢
- `SCR-RUN`: active turnの詳細とinterrupt
- `SCR-DONE`: 完了要約と定型返信
- `SCR-APPROVE`: 安全判定後の承認
- `SCR-ERROR`: errorとhost escalation
- `SCR-THREADS`: slot一覧とeffort表示
- `OVL-LINKDOWN`: stale link
- `OVL-SERVICEDOWN`: Codex service非ready

## Interaction rules

- 選択肢と定型返信は選択後にEnterで確定する。
- Approvalは`0`でlocal hold、`a`でaccept、`d`でdeclineを選び、Enter special-key stateで確定する。
- Approval本文は`j` / `k`で下 / 上へscrollする。
- G0の500ms長押しは、表示画面ではなくfocus中slotの`turnActive`でinterrupt対象を決める。
- app-serverの構造化質問は初期版でdeviceから回答しない。
- `acceptForSession`、`cancel`、policy amendmentはhost側だけで扱う。
- pendingの正本はhostがID順に保持し、deviceには先頭1件と残数を表示する。

## Host response surface

Deviceで`0`を選ぶ保留はrequestを解決せず、host bridgeのpending queueへ残します。Bridgeは未解決のapproval、構造化質問、deviceで保留した項目を、ID、slot、種別、要約、経過時間、`contentComplete`、`riskClass`とともに常時表示します。

初期版は`approve <id>`、`decline <id>`、`cancel <id>`のapproval操作面を提供します。Serverが提示したdecisionだけを同名で送り、semantic contextを完全表示できない場合はhostでもapproveを出しません。`answer <id> <text>`、`acceptForSession`、policy amendment、構造化質問はM4のhost surface拡張で扱います。Deviceで禁止された切り詰めまたはhigh-risk approvalのacceptは、hostが原requestを完全表示できる場合だけ提供します。

Deviceまたはhostのどちらで解決しても同じpending正本を更新し、`serverRequest/resolved`を受けて他方の表示を追従させます。受入試験では「deviceで保留 → host queueに表示 → hostで応答 → deviceがresolvedへ追従」を一連で確認します。
