# 声浪录制室 终端聊天记录导出

- 导出终端：B / 声浪录制室（乙方 / 承制及交片方）
- 会话：SES-MISSING-1127
- 导出时间：2026-11-27T04:22:18.376Z
- 终端规则：terminal-rule/v1

> 普通消息仅附双边共享私记；“我收到了”不自动同意改规格。唯一对外归属以具名署名/共同会签为准。

## MSG-001 · SYSTEM · 2026-11-26 11:50

本地谈单会话已建立。普通消息只加盖甲乙共享的双边私记；订单规格变更必须生成引用旧约定及上下文的订正，并按所选办法完成签署。

- 类型：`system`；私记：`34ee266e18414e98…e3e26b`
- 效力标记：formal-boundary-notice

## MSG-002 · B · 2026-11-26 12:00

甲，明天 ORD-1127 的访谈成片还是按刚才确认的老规格交吧？

- 类型：`talk`；私记：`3282cc61b5fe9db9…977083`

## MSG-003 · A · 2026-11-26 12:02

明天交片规格改一下：先出 1920×1080、25fps、H.264 MP4 审片版，4K ProRes 母版往后放。

- 类型：`proposal-talk`；私记：`7813863ebea56b6a…c6ef26`
- 终端提示：普通私聊中的意向表达；尚未引用并替换 AGR-1127-SPEC@v1。

## MSG-004 · B · 2026-11-26 12:03

我收到了。

- 类型：`ack`；私记：`29cb6cc101c1898b…ea8c2a`
- 效力标记：acknowledgment-only
- 终端提示：终端已标记：“收到”只证明送达/知悉，不构成对规格变更的承诺或会签。

## MSG-005 · A · 2026-11-26 12:04

这些私聊就我们俩能核对就行，别将来截一段就拿去对外说是我正式改单。真要改，我们另封一份正式约定。

- 类型：`talk`；私记：`6606c27901f913be…a23417`

## MSG-006 · B · 2026-11-26 12:05

可以。若我联系不上你，至少你发言端直接署名的具名修订单得能让仲裁验出处；没这个，我不能只拿私聊改已接订单。

- 类型：`talk`；私记：`48da7b24ed8a79d6…6a77b0`

## MSG-007 · TERMINAL · 2026-11-26 12:06

终端已形成具名公鉴订正 CR-NAMED-001，引用 AGR-1127-SPEC@v1（3d021a7fc3…）及 5 条上下文。该单尚未完成签署，旧约定暂不换版。

- 类型：`formal-draft`；私记：`52da9bd7fc62890a…02de0a`
- 效力标记：formal-draft-no-variation-until-required-seals
- 关联订正：CR-NAMED-001

## MSG-008 · TERMINAL · 2026-11-26 12:06

A 已对同版订正 CR-NAMED-001（733d6a55f2…）完成具名签署。

- 类型：`formal-signature`；私记：`5d68b62612dee0b4…89c702`
- 效力标记：signature-recorded
- 关联订正：CR-NAMED-001

## MSG-009 · SYSTEM · 2026-11-26 12:08

甲终端现已失联。乙可以提交双边私聊证据，但第三方不能凭共享私记唯一认定 MSG-003 出自甲；CR-NAMED-001 则可凭甲的公鉴署名查验出处。若乙在 21:00 后才接受，同意迟到的后果由仲裁单独判断。

- 类型：`system`；私记：`f1eef193661a58b7…be4d89`
- 效力标记：absence-and-late-acceptance-warning
- 关联订正：CR-NAMED-001

## MSG-010 · TERMINAL · 2026-11-26 12:09

终端已形成双边到场会签订正 CR-MISSING-JOINT-ATTEMPT，引用 AGR-1127-SPEC@v1（3d021a7fc3…）及 5 条上下文。该单尚未完成签署，旧约定暂不换版。

- 类型：`formal-draft`；私记：`a5df59990d41d6c2…5ebba4`
- 效力标记：formal-draft-no-variation-until-required-seals
- 关联订正：CR-MISSING-JOINT-ATTEMPT

## MSG-011 · TERMINAL · 2026-11-26 12:09

B 已对同版订正 CR-MISSING-JOINT-ATTEMPT（e3965a3c7a…）完成到场会签。

- 类型：`formal-signature`；私记：`d4bb403114206a71…775731`
- 效力标记：signature-recorded
- 关联订正：CR-MISSING-JOINT-ATTEMPT

## MSG-012 · TERMINAL · 2026-11-26 12:10

A 终端当前不在场/已失联，不能作出新的有效签署；普通私聊、对方代点或事后补录都不能替代。失联前已经生成的具名署名仍可验出处。

- 类型：`seal-rejected`；私记：`8a6b95e9ef06df09…6ef2ff`
- 效力标记：no-new-seal-while-absent
- 关联订正：CR-MISSING-JOINT-ATTEMPT

# 当前订单/约定

- ORD-1127 次日访谈成片交片单：当前 AGR-1127-SPEC@v1
- ORD-8899 另一场访谈录制交片单（无关订单）：当前 AGR-8899-SPEC@v1

## AGR-1127-SPEC@v1 (effective)

- order: ORD-1127
- hash: `3d021a7fc3952a4168721a01d0e5b7f1f1181e85f2b3afdeaf49f438cb478e7a`
- source: seed

双方确认次日访谈成片交片口径。交片规格：3840×2160、25fps、ProRes 422 HQ 母版，音频为 48kHz/24bit 立体声 WAV，次日 18:00 前交片。 交付方式为录制室内网或加密移动硬盘。

## AGR-8899-SPEC@v1 (effective)

- order: ORD-8899
- hash: `787a977dee6ef80216955c0c681380674cea324ea36ca651ff5e6aa002c9af3b`
- source: seed

ORD-8899 沿用同一套通用交片口径。交片规格：3840×2160、25fps、ProRes 422 HQ 母版，音频为 48kHz/24bit 立体声 WAV，次日 18:00 前交片。 嘉宾字幕名单另行确认；本约定与 ORD-1127 的订正互不覆盖。
