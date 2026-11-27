# 声浪录制室 终端聊天记录导出

- 导出终端：B / 声浪录制室（乙方 / 承制及交片方）
- 会话：SES-PRESENT-1127
- 导出时间：2026-11-27T04:22:18.370Z
- 终端规则：terminal-rule/v1

> 普通消息仅附双边共享私记；“我收到了”不自动同意改规格。唯一对外归属以具名署名/共同会签为准。

## MSG-001 · SYSTEM · 2026-11-26 11:50

本地谈单会话已建立。普通消息只加盖甲乙共享的双边私记；订单规格变更必须生成引用旧约定及上下文的订正，并按所选办法完成签署。

- 类型：`system`；私记：`44ffbc9e48530448…0fcc69`
- 效力标记：formal-boundary-notice

## MSG-002 · B · 2026-11-26 12:00

甲，明天 ORD-1127 的访谈成片还是按刚才确认的老规格交吧？

- 类型：`talk`；私记：`93b650d8b428f646…a9a728`

## MSG-003 · A · 2026-11-26 12:02

明天交片规格改一下：先出 1920×1080、25fps、H.264 MP4 审片版，4K ProRes 母版往后放。

- 类型：`proposal-talk`；私记：`353cc4378cedff25…4d00a1`
- 终端提示：普通私聊中的意向表达；尚未引用并替换 AGR-1127-SPEC@v1。

## MSG-004 · B · 2026-11-26 12:03

我收到了。

- 类型：`ack`；私记：`e8a91d6b39c5789e…9f83ea`
- 效力标记：acknowledgment-only
- 终端提示：终端已标记：“收到”只证明送达/知悉，不构成对规格变更的承诺或会签。

## MSG-005 · A · 2026-11-26 12:04

这些私聊就我们俩能核对就行，别将来截一段就拿去对外说是我正式改单。真要改，我们另封一份正式约定。

- 类型：`talk`；私记：`27a7fa7be8d2f00f…0ef648`

## MSG-006 · B · 2026-11-26 12:05

可以。若我联系不上你，至少你发言端直接署名的具名修订单得能让仲裁验出处；没这个，我不能只拿私聊改已接订单。

- 类型：`talk`；私记：`67d8b57808c87963…429f8a`

## MSG-007 · TERMINAL · 2026-11-26 12:08

终端已形成双边到场会签订正 CR-PRESENT-001，引用 AGR-1127-SPEC@v1（3d021a7fc3…）及 5 条上下文。该单尚未完成签署，旧约定暂不换版。

- 类型：`formal-draft`；私记：`a8292c9a1313220f…acae32`
- 效力标记：formal-draft-no-variation-until-required-seals
- 关联订正：CR-PRESENT-001

## MSG-008 · TERMINAL · 2026-11-26 12:08

A 已对同版订正 CR-PRESENT-001（f8c448533c…）完成到场会签。

- 类型：`formal-signature`；私记：`750fd060ed7f6d9b…2ba485`
- 效力标记：signature-recorded
- 关联订正：CR-PRESENT-001

## MSG-009 · TERMINAL · 2026-11-26 12:09

B 已对同版订正 CR-PRESENT-001（f8c448533c…）完成到场会签。

- 类型：`formal-signature`；私记：`3ec092bf84951fb9…4ac1dd`
- 效力标记：signature-recorded
- 关联订正：CR-PRESENT-001

## MSG-010 · TERMINAL · 2026-11-26 12:09

订正 CR-PRESENT-001 已由双方对同版哈希 f8c448533c… 完成接受。仅 AGR-1127-SPEC@v1 换版为 v2；相同字句不影响其他订单。

- 类型：`version-effective`；私记：`58df3e28c8865f6a…0a9e07`
- 效力标记：cited-agreement-superseded-only
- 关联订正：CR-PRESENT-001

# 当前订单/约定

- ORD-1127 次日访谈成片交片单：当前 AGR-1127-SPEC@v2
- ORD-8899 另一场访谈录制交片单（无关订单）：当前 AGR-8899-SPEC@v1

## AGR-1127-SPEC@v1 (superseded)

- order: ORD-1127
- hash: `3d021a7fc3952a4168721a01d0e5b7f1f1181e85f2b3afdeaf49f438cb478e7a`
- source: seed

双方确认次日访谈成片交片口径。交片规格：3840×2160、25fps、ProRes 422 HQ 母版，音频为 48kHz/24bit 立体声 WAV，次日 18:00 前交片。 交付方式为录制室内网或加密移动硬盘。

## AGR-8899-SPEC@v1 (effective)

- order: ORD-8899
- hash: `787a977dee6ef80216955c0c681380674cea324ea36ca651ff5e6aa002c9af3b`
- source: seed

ORD-8899 沿用同一套通用交片口径。交片规格：3840×2160、25fps、ProRes 422 HQ 母版，音频为 48kHz/24bit 立体声 WAV，次日 18:00 前交片。 嘉宾字幕名单另行确认；本约定与 ORD-1127 的订正互不覆盖。

## AGR-1127-SPEC@v2 (effective)

- order: ORD-1127
- hash: `7055dbdafab5b934d3c3ceb7ac164dec16a8884dbb4b66e5bb4d5a4158586aff`
- source: CR-PRESENT-001

双方确认：2026-11-27T18:00:00+08:00 前先交付 1920×1080、25fps、H.264 MP4 审片版；3840×2160/ProRes 422 HQ 母版改于 2026-11-28T12:00:00+08:00 前交付。本订正仅替换 AGR-1127-SPEC@v1 中与之冲突的交片规格。
