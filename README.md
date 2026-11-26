# 共用听打台交班与具名刊次程序

一个零外部依赖的 Python 本地终端程序：

- `desk`：工作站 CLI，负责在场确认、个人签认、输入段、交班、异步补送、口述人复核/取消、档案查询、唯一刊次与勘误。
- `demo.sh`：实际录入、交班、补送迟到签字/取消、查询、刊布、篡改与顶替反例。
- `check_demo.py`：对演练结果做断言。
- `runs/`：每次演练的 JSON 状态、只追加 `events.jsonl`、签认电文、刊次和追溯 JSON。

## 运行

```bash
cd /workspace
./demo.sh
```

完整终端记录在 `DEMO_TRANSCRIPT.txt`。主成稿：

```bash
./desk --root runs/01-main archive query --date 2026-11-26
cat runs/01-main/editions/ED-0001.body.md
```

## 身份模型

1. `presence confirm` 是集体工位的“有人在”凭证：只能证明工作站前至少有一人，`identity_proven=false`，不能署名甲或乙。
2. `badge tap --signer a|b|n` 和 `sign ... --signer ...` 使用本地 `keys.json` 中的模拟个人签认器密钥生成 HMAC-SHA256 信封。无需声音或真实笔迹。
3. 输入段有 `attribution_lock`。甲的私稿 `seg-0002` 只能由甲的合格签字确认；乙签该段会得到 `ATTRIBUTION_LOCK`，乙也不能在封入状态下调阅正文。
4. 每次签字绑定：
   - 目标段 `segment_sha256`；
   - 签字器实际展示的完整审阅文本 `review_sha256` 和清单；
   - 签字时的在场凭证、已刷个人签认器名单；
   - 同意收入当日具名刊次的 release 声明。
5. 刊次签发员乙另外对完整刊次正文的 `body_sha256` 签字。档案查询端会复核签发信封、刊次哈希和各段签字。

## 交班分段规则

`handover create` 同时要求：

- 交班时刻的集体在场凭证；
- 后任乙的个人签认器/资格；
- 工作站连续有效的本人签字范围。

边界停在第一个未取得有效本人签字的输入段之前。缓冲半句会被封成
`unfinished_buffer_fragment` / `sealed_private_pending_signature`，不会被误当成甲已确认，也不会变成乙的笔录。

甲的签认器应答可以因掉线而在交班后才通过同一个 `HO-0001` 补送；程序验签和哈希通过后才接受，并记录 `late_after_handover=true`。

## 实际演练结果（日期固定为 2026-11-26）

主流程 `runs/01-main` 最终刊次为 `ED-0001`，包含：

| 段 | 署名 | 字符数 | 说明 |
|---|---:|---:|---|
| `seg-0001` | 甲 | 42 | 甲独自工作时已核对并签字的原段 |
| `seg-0002` | 甲 | 16 | 缓冲中的未完半句；迟到的甲签字经 `HO-0001` 补送后收入，程序不补写原意 |
| `seg-0003` | 乙 | 30 | 乙以自己身份新录、签字，并经口述人本人复核 |

同一日同一 `work_id=oral-001` 第二次 `publish` 会被 `EDITION_ALREADY_PUBLISHED` 拒绝。刊后甲的更正只能形成 `ER-0001`，冻结的 `ED-0001.body.md` 哈希不变。

分支比较：

| 运行目录 | 通过同一 `HO-0001` 补送的事件 | 可进具名成稿 | 字符数 |
|---|---|---|---:|
| `runs/03-cmp-late` | 甲迟到签字 | `seg-0001 + seg-0002` | 58 |
| `runs/04-cmp-cancel` | 口述人取消复核预约 | 只有 `seg-0001`；半句仍不署名 | 42 |
| `runs/05-cmp-both` | 取消预约 + 甲迟到签字 | `seg-0001 + seg-0002`；甲的原意不被口述人对未来复核的取消抹掉 | 58 |
| `runs/06-consent-withdrawal` | 口述人撤回乙新录段的刊布同意 + 甲迟到签字 | 甲的 `seg-0001/0002` 可留；乙的 `seg-0003` 被同意锁挡住 | — |
| `runs/07-late-after-publication` | 刊次先发，甲签字后到 | 冻结刊次仍只有 `seg-0001`；甲的 `seg-0002` 只能由 `ER-0001` 勘误/补遗附着 | 刊次 42 |

“取消”被区分为两种有审计意义的范围：

- `review_appointment`：口述人不来复核；不当然撤回已经作出的签字或刊布同意。乙若已以本人身份签字，仍可具名负责，但不能声称“口述人已复核”。
- `publication_consent`：口述人撤回指定段的刊布同意；即使乙已签字，该段也不进具名刊次。

## 两项必做反例

`runs/02-negative` 中：

1. 把集体工位 `PR-...` 在场凭证送去 `receive`，返回 `NOT_PERSONAL_CONFIRMATION`；把有效个人信封替换成在场凭证，返回 `BAD_ENVELOPE`。`seg-0002` 始终不能署甲。
2. 保留 `seg-0003` 的真实签字电文，只把队列正文中的第一个“三”改成“五”，再投递会返回 `SIGNED_TEXT_MISMATCH`。原文电文随后可正常接收，证明原确认只替原文本背书。

## 追溯命令

```bash
./desk --root runs/01-main trace seg-0001
./desk --root runs/01-main --json trace seg-0002
```

追溯内容包括录入/缓冲/签字时的在场凭证、当时已刷个人签认器的人、后任乙后来调阅已签段的事件、是否经交班请求迟到补送，以及完整审计事件流。

## 常用命令

```bash
D=./desk
R=/workspace/runs/manual

$D --root "$R" init --force
$D --root "$R" presence confirm
$D --root "$R" badge tap --signer a
$D --root "$R" segment add --by a --text '...'
$D --root "$R" sign --segment seg-0001 --signer a --out "$R/a1.json"
$D --root "$R" receive "$R/a1.json"

$D --root "$R" buffer set --by a --text '半句...'
$D --root "$R" buffer seal --as a
$D --root "$R" badge leave --signer a

$D --root "$R" presence confirm
$D --root "$R" badge tap --signer b
$D --root "$R" handover create --from a --to b --presence latest
$D --root "$R" handover deliver HO-0001 "$R/a-seg2-late.json"

$D --root "$R" archive query --date 2026-11-26
$D --root "$R" publish --date 2026-11-26 --clerk b
$D --root "$R" erratum add --edition ED-0001 --signer a --correction '...'
```

这是本地流程与完整性模拟，不是生产级身份系统；`keys.json` 保存在本机，HMAC 用于演示签字与防篡改绑定。
