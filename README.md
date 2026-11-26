# 两厅匿名意见收集（可重统计、可核实、匿名）

用两个独立的本地收件目录模拟两间展厅，会员台（desk）领证、挂失、查最后计入的
匿名选项；意见箱看不到会员号；每人一票；挂失与「先收妥」次序不同结果不同；断线
窗口内已收妥的选择保留。最后产出一份**可重新统计、也能核实挂失结论**的回执包，
且**不把会员身份与选择合在同一张表里**。

纯 Python 3 标准库，无第三方依赖（HMAC/哈希链/随机凭条用 `hmac`、`hashlib`、
`secrets`）。

## 运行

```bash
./run.sh                     # 36 个单元测试 + 端到端演示
python3 -m unittest discover -s tests -v
python3 -m scenarios.demo    # 打印完整叙事并写出回执包；验证失败则退出码非 0
```

## 目录

```
ballotbox/
  model.py     数据类型、状态常量、错误
  tokens.py    凭条：HMAC 事件绑定、随机、无会员号（上一个测试向量）
  ledger.py    追加式、哈希链 JSONL（每厅/会员台各自一份 → 本岗只能读本岗）
  hall.py      展厅：本地收件目录、投递决策、暂存/最终回执、交接
  control.py   厅<会员台>唯一的控制通道，并模拟"断线"链路
  desk.py      会员台：领证、授予最终接收权/保留流转、挂失、查匿名选项
  report.py    最终回执包（ballots/tally/identity/loss/chains）+ 独立复核
tests/         36 个 stdlib unittest（无 pytest 依赖）
scenarios/demo.py 端到端叙事演示
```

## 关键语义

**凭条**：`v1:<事件>:<随机id>:<随机nonce>:<MAC>`。MAC 覆盖事件号，所以
「把基准凭条的活动改成上一场」时，凭条在**投递前**就因 `EventMismatch` 被拒；
伪造/篡改则 `InvalidSignature`。凭条本身不含会员号。

**一个厅对一次投递的判定**（`hall.submit`）：

| 情形 | 结果 |
|---|---|
| 该厅独占最终接收权 | `收妥回执 FINAL`，立即可计入，**断线也可出**（甲的预订服务能办成） |
| 独占权在另一厅 | `WRONG_HALL`，拒绝 |
| 流转证 `TRANSFER`，在线 | `暂存回条 TEMPORARY`，**待截止前交接**才计入 |
| 流转证，**仍失联** | `PENDING_CONFIRM`，不承诺最终结果 |
| 尚未授予最终接收权 | `PENDING_CONFIRM`（相同投递只能待确认） |
| 已挂失生效 | `VOIDED`，不得新增一票 |
| 已出过最终回执 | `ALREADY_FINAL` / 幂等返回同一回执，绝不再投 |

**挂失次序**（`desk.report_loss`）：

- **先挂失**（流转证，或授权厅在线）→ 立即 `EFFECTIVE`，后续投递作废。
- **先收妥** → `CONSUMED`，票保留，只阻止再用。
- 独占权厅**仍离线**时挂失 → 只能 `PENDING`（「挂失待其交接，不能声称即时生效」）。
  该厅重连 `sync()` 后：若已出最终回执，票保留；否则挂失生效。

**断线**：厅离线时不能问会员台，只能依据**断线前**已推送的接收权与撤销。
`desk.set_hall_online` 重连会 flush 离线期间排队的授予/撤销，并要求厅 `sync()` 交接。

**每人一票**：流转证可被两厅各「暂存」，但只有截止前交接成功的一厅计入；另一厅
的暂存票被作废。重复投递同厅幂等；已最终回执的凭条在另一厅被 `ALREADY_FINAL`。

**保密/隔离**：
- 厅的台账里 ballot 记录只有 `ballot_id/hall/choice/status`，**无 token、无会员**；
  回执记录有 `token_id/ballot_id`，**无 choice**。
- 会员台台账记录 `member↔token`、授予、挂失，**从无 choice**。
- 经办人 `read_post` 只允许读本厅目录，越界抛 `ForeignPost`。

## 最终回执包（`report/`）

- `ballots.json`：匿名选项（`ballot_id/hall/choice/status`），无身份。
- `tally.json`：仅 `COUNTED` 的分类计数（总/按选项/按厅）。
- `identity.json`：`member/token/授权/挂失结论`，**无 choice**；与 ballots 仅
  靠不透明 `ballot_id` 关联（该映射只存在于会员台台账）。
- `loss.json`：每张凭条的挂失结论（请求时间、生效时间、是否先计入）。
- `chains.json`：会员台与两厅哈希链校验结果（篡改可发现）。
- `verify_report()` 从**原始厅台账独立重算**并比对 `tally.json`，校验链完整、
  identity 无 choice 字段、每 token 至多一张计入票、计入票数 ≤ 合资格人数。
  任一不符即 `ok=False` 并给出 `problems`。
