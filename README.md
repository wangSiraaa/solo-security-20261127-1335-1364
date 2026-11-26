# 监测覆盖交班台（watchdesk）

值班板全绿不等于责任区间可承诺。这个虚构工坊交班台只认三类证据：

1. **规则文件/版本与启停回执**：当时登记的是哪份规则、哪一版，谁签收启用和停止；
2. **心跳**：观察员/席台存活，但**不能**证明事件已处理；
3. **逐拍处理回执与实际处理到的事件序号**：这是覆盖区间和处理进度的硬证据。

所有命令追加写入 `--state` 目录下的 `ledger.jsonl`。查询、`rebuild` 和报告都通过重放台账生成投影；没有可手工粉刷的“绿灯状态表”。

## 运行

无需第三方依赖，Python 3.10+：

```bash
cd /workspace
python3 -m watchdesk --help

# 一键跑完题目中的紧急携带矩阵、成功交班、漏回执、撤签收、换版
bash demo.sh

# 或通过 CLI 内置入口
python3 -m watchdesk scenario all --root /tmp/watchdesk-scenarios

# 测试
python3 -m unittest discover -s tests -v
```

`demo.sh` 的结果目录是 `/tmp/watchdesk-demo/`，每个场景另有 `report.txt`。

## 节拍与临界定义

样例规则 `examples/rules-v1.json`：

- `seal3`：`continuous_span`，从 `seal_start` 起连续守 **3 拍**；
- `pair2`：`pair_within_beats`，两拍窗口且要求相邻。第 2 拍收到 `loose` 后，窗口由第 2、3 拍组成，因此：
  - `reset` 在**第 3 拍**：临界拍内，可及时提醒；
  - `reset` 在**第 4 拍**：临界拍外，不发及时提醒，出时效性缺口回单。

设备事件只允许三种 canonical 类型：`seal_start / loose / reset`，也接受中文别名 `封存开始 / 封条松动 / 复位`；其他事件类型会被拒绝。

## 常用命令

```bash
D=/tmp/wd-manual
python3 -m watchdesk --state "$D" init --start 2026-11-26T12:00:00+08:00
python3 -m watchdesk --state "$D" tick --beats 2

python3 -m watchdesk --state "$D" register examples/rules-v1.json
python3 -m watchdesk --state "$D" rules
python3 -m watchdesk --state "$D" enable seal3 \
  --version 2026.11.26-v1 --by a-heng --receipt EN-SEAL --from-beat 1 \
  --shift noon --desk old-seat
python3 -m watchdesk --state "$D" enable pair2 \
  --version 2026.11.26-v1 --by xiao-song --receipt EN-PAIR --from-beat 1 \
  --shift noon --desk old-seat

python3 -m watchdesk --state "$D" heartbeat --beat 2 \
  --by xiao-song --receipt HB-2 --shift noon --desk old-seat
python3 -m watchdesk --state "$D" load examples/events-prefix.jsonl

python3 -m watchdesk --state "$D" ack --seq 1 --rule seal3 \
  --by a-heng --receipt ACK-S1 --shift noon
python3 -m watchdesk --state "$D" ack --seq 2 --rule seal3 \
  --by a-heng --receipt ACK-S2 --shift noon
python3 -m watchdesk --state "$D" ack --seq 2 --rule pair2 \
  --by xiao-song --receipt ACK-P2 --shift noon

# 程序按 risk-first 自动选择携带 pair2；coverage-first 会选择 seal3
python3 -m watchdesk --state "$D" plan --at-beat 2 --slots 1 --emergency \
  --by duty-lead --from-shift noon --from-desk old-seat \
  --to-shift afternoon --to-desk new-seat --to-owner ji-cheng

python3 -m watchdesk --state "$D" execute --plan HO-001 \
  --by duty-lead --receipt CARRY-001

python3 -m watchdesk --state "$D" coverage seal3 --from-beat 1 --to-beat 3
python3 -m watchdesk --state "$D" signout seal3 --from-beat 1 --to-beat 2 \
  --shift noon --by duty-lead --receipt CERT-NOON-SEAL
python3 -m watchdesk --state "$D" audit --rule pair2
python3 -m watchdesk --state "$D" rebuild
python3 -m watchdesk --state "$D" report
```

## 紧急离岗矩阵：一次携带保什么

共同前提已经由签名回执固定：

- `ctx-seal3-1`：持续观察已完成第 1、2 拍，签收人 `a-heng`；
- `ctx-pair2-2`：刚收到第 2 拍 `loose`，待配 `reset`，签收人 `xiao-song`；
- 旧席两人各守一类规则；新席 `ji-cheng` 具备两类资格（见 `examples/desks.json`），但紧急离岗只有一个携带位。

计划器会给上下文打分：

```bash
# 默认安全/时效优先：选择 ctx-pair2-2
python3 -m watchdesk --state "$D" plan ... --emergency

# 覆盖连续性优先：选择 ctx-seal3-1
python3 -m watchdesk --state "$D" plan ... --emergency --policy coverage-first

# 仍有人值守、携带位不足：不强行二选一，要求提前重叠交接
python3 -m watchdesk --state "$D" plan ... --slots 1   # 不带 --emergency
```

执行交接时，被选中的上下文变成 `custody=carried`；未携带上下文变成 `custody=not_carried/status=uncarried`，程序会：

- 开一张 `custody_lineage_break` 断链回单；
- 自动把该上下文**已有处理回执支持的前缀签出**，例如封存第 1–2 拍；
- 不允许新席把旧上下文中的待配 `loose` 当作自己已携带的现场状态来发及时提醒；
- 原始事件仍在台账中，可供 `audit` 事后核对，但事后核对不会补造当时的提醒或送达签收。

| 复位位置 | 唯一携带 | 及时提醒/谁能签收 | 封存覆盖承诺 | 另一项的只能得到什么 |
|---|---|---|---|---|
| 第 3 拍（临界拍内） | `pair2`（默认） | `ALR-pair2-3` 由新席 `ji-cheng` 及时命中；`seal-duty-group` 凭 `DLV-inside-pair` 正式签收 | 逐拍回执可能第 1–3 拍都有，但封存上下文断链；只能承诺午班第 1–2 拍、新席重建后第 3 拍 | 封存不能主张“同一上下文连续三拍”；有断链回单和分段证书 |
| 第 3 拍（临界拍内） | `seal3`（`--select ctx-seal3-1` 或 `coverage-first`） | **无人能及时收到**；新席只见 `reset`，无已携带的待配 `loose` | `CERT-SEAL-CHAIN` 证明同一上下文第 1–3 拍连续守满 | `audit` 可查窗口内事件对，但结论是“事后核对：无现场提醒/送达证据” |
| 第 4 拍（临界拍外） | `pair2` | 即使携带了 `loose`，也不产生及时提醒；`ALR-...` 为 `outside_window`，禁止正式及时送达签收 | 若封存未携带，第 1–2 拍和第 3 拍重建段分别签出 | `timeliness_gap` 缺口回单；事后核对确认事件对存在但跨 2 拍、在窗口外 |
| 第 4 拍（临界拍外） | `seal3` | 短规则未携带且复位出窗，无人及时收到 | `CERT-SEAL-CHAIN` 仍可证明封存第 1–3 拍 | 短规则只剩断链/窗口外记录和事后核对 |

第 3 拍没有新设备事件时，demo 用下面的轮询回执证明“这一拍仍被处理”：

```bash
python3 -m watchdesk --state "$D" ack --beat 3 --through-seq 2 \
  --rule seal3 --rule pair2 --by ji-cheng --receipt ACK-POLL-BEAT3
```

## 成功交班的完整记录

`demo.sh` 的 `success/` 场景具备：

- v1 规则文件 SHA256 登记；
- `EN-SEAL-V1`、`EN-PAIR-V1` 启用回执和 `STOP-SEAL`、`STOP-PAIR` 停止回执；
- 第 1、2、3 拍逐拍处理回执；
- 有人值守时 `--slots 2`，两个上下文均 `carry_all`；
- 第 3 拍 `loose→reset` 及时命中；
- `dispatch` 消息派发与 `DLV-SUCCESS` 接收组正式签收；
- `CERT-SUCCESS-SEAL`、`CERT-SUCCESS-PAIR` 第 1–3 拍签出。

因此报告才会写：`可写已正式通知 seal-duty-group`。

## 漏中间拍、还原进度、撤签收

`missing-middle/` 中第 2 拍只有心跳 `HB-MISSING-MIDDLE`，没有 `processing.ack`：

```text
第2拍 ❌缺口 ... 原因: missing_processing_receipt
可签出的连续区间: 第1–1拍, 第3–3拍
结论: 不能保证请求区间的持续覆盖；只能签出上述合法区间
```

签出命令不会把第 1–3 拍写成一张完整证书，而是生成：

- `CERT-MISSING-...-1-1`
- `CERT-MISSING-...-3-3`
- 第 2 拍 `GAP-.../coverage_gap`

`rebuild` 从 ledger 重放后显示 `acked_beats=[1,3]`、连续拍高水位为 `1`；即使最后一张回执声称 `through_seq=3`，也不能跳过中间缺口。

`revoke-delivery/` 复制成功台账并追加补偿记录：

```bash
python3 -m watchdesk --state /tmp/watchdesk-demo/revoke-delivery \
  revoke-delivery DLV-SUCCESS --by compliance \
  --reason "该组正式收讫章系误盖；未取得接收组签收" \
  --receipt REVOKE-DLV-1
```

这不会删除历史：规则命中 `ALR-pair2-3` 和审计结果仍可查；但有效正式签收组变为空，报告明确写：

```text
不得写成已正式通知任何接收组
```

## 换版

`examples/rules-v2.json` 把 `pair2` 从“相邻两拍”放宽为“三拍窗口”，并把责任主体改为新席 `ji-cheng`。版本切换必须保留各自启停回执：

```bash
python3 -m watchdesk --state "$V" register examples/rules-v2.json
python3 -m watchdesk --state "$V" stop pair2 --through-beat 2 \
  --by duty-lead --receipt STOP-P-V1
python3 -m watchdesk --state "$V" enable pair2 --version 2026.11.26-v2 \
  --from-beat 3 --by ji-cheng --receipt EN-P-V2
python3 -m watchdesk --state "$V" coverage pair2 --from-beat 1 --to-beat 4
```

查询会逐拍显示实际生效版本；已经开始的上下文保留其规则版本，不会因换版被静默重判。

## 设计边界

- 这是本地教学/值班交接工具，不连接真实设备或外部通知服务；`dispatch` 只记录派发证据。
- “携带上下文”是显式交接事实，不是因为所有人都能读同一个台账就自动发生。台账可供事后审计，但不能追溯变成新席当时的现场状态。
- 心跳、规则绿灯、原始事件存在性，都不与“该拍已由某责任班组处理”互相替代。
