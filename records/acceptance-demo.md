# 验收演示：正确切换、未获准组件中止、试启失败不得接班

本记录由 `scripts/acceptance_demo.py` 实际执行生成。所有状态均来自 CLI 与持久 JSON。

## 自动核对点

- ✅ 乙机 b 槽为第 6 代且有合格回执
- ✅ 乙机已登记第六代接班但未烧桥
- ✅ 核对器指出 reader 摘要未获准
- ✅ 安装命令以退出码 2 停止
- ✅ 丙机未写入 b 槽、未切换试启槽
- ✅ 丙机仍在 a 槽第五代
- ✅ 被拒安装单已持久留痕
- ✅ 原安装单重跑返回 2
- ✅ 原乙机单保留 completed 审计状态
- ✅ 原乙机单追加了拒绝尝试
- ✅ 材料合格
- ✅ 失败后 b 槽标记 failed
- ✅ 没有把第六代登记为现役接班
- ✅ 满足回退条件并恢复到第五代
- ✅ 存在失败回执和恢复自检回执
- ✅ 掉电命令返回 42
- ✅ 持久安装单停在 written
- ✅ 掉电恢复后仍能正确接班

## 实际命令与输出

### 建立三台模拟机的同一起点

`$ python3 -m museum_update --state-dir <state> init --force`

```text
馆内终端本地更新控制器
逻辑班点: 第 0 班 / 统一停用点: 第 3 班 / 剩余班数: 3

机器号  名称  当前状态  现役槽/代次  试启槽  最低代次  桥  最近实际开机
------  ----  --------  -----------  ------  --------  --  ------------
M1  甲机  第六代已接班但未烧桥；后续失败仍可回退第五代  b:6代  -  5  未烧  6代/pass@班0
M2  乙机  第五代运行中（未烧桥，可回退）  a:5代  -  5  未烧  无记录
M3  丙机  第五代运行中（未烧桥，可回退）  a:5代  -  5  未烧  无记录

持久状态: /workspace/state/acceptance/controller-state.json
```

### 发布方第六代包摘要核对

`$ python3 -m museum_update --state-dir <state> verify packages/gen6`

```text
✅ 批准包核对通过: ORD-G6-001 第6代
   文件数=5 组件数=5
   整套摘要=sha256:c168f6d09fc1ba24326da1b000bb264f02204193d784e0d68d77e3b29436e0eb
```

### 第一单：合格第六代写槽并切换

`$ python3 -m museum_update --state-dir <state> install orders/M2-gen6.json`

```text
✅ 安装单 TICKET-M2-G6-001 已完成写槽和切换，当前阶段=awaiting_boot
馆内终端本地更新控制器
逻辑班点: 第 0 班 / 统一停用点: 第 3 班 / 剩余班数: 3

机器号  名称  当前状态  现役槽/代次  试启槽  最低代次  桥  最近实际开机
------  ----  --------  -----------  ------  --------  --  ------------
M1  甲机  第六代已接班但未烧桥；后续失败仍可回退第五代  b:6代  -  5  未烧  6代/pass@班0
M2  乙机  第六代已切为试启槽，等待开机自检  a:5代  b  5  未烧  无记录
M3  丙机  第五代运行中（未烧桥，可回退）  a:5代  -  5  未烧  无记录
```

### 正确回执：试启通过并登记接班

`$ python3 -m museum_update --state-dir <state> boot M2 --result pass --note 验收用第一次试启，回执由模拟设备签名`

```text
✅ 开机自检回执已记录。
# M2（乙机 / YI-0002）

- 当前状态: **第六代已接班但未烧桥；后续失败仍可回退第五代** (`gen6_active_uncommitted`)
- 解释: M2 最近实际启动第 6 代且自检合格，但尚未烧桥；第 5 代仍可按条件回退。
- 班点: 第 0 班；距第 3 班停用点还有 3 个计数单位
- 启动槽: active=b, trial=-, rollback=a
  - a: 第5代, status=good, last_result=pass, set=sha256:c7a665b226ace8ac3…
  - b: 第6代, status=good, last_result=pass, set=sha256:c168f6d09fc1ba243…
- 最低可启动代次: 5（单调上升）；回旧版桥: 仍在
- 第 5 代在当前班点是否还能启动: 是
- 连续合格自检: 1
- 有据可查的回退条件:
  - 仅当 a 槽第五代摘要完好、自检为 good、最低代次 <=5，且未到第 3 班停用点时，才允许原地回退。
- 已经见到的失败: 无

## 历次试启动/恢复回执
- R-M2-0002 班0 b槽 第6代 boot_self_check=pass (sig ef60033e3bcf…)；验收用第一次试启，回执由模拟设备签名

## 班次时间线
- 班0 #3 [install_verified] 安装单 TICKET-M2-G6-001 的字节、代次、组件清单和批准摘要核对通过。
- 班0 #4 [slot_written] 第 6 代已写入 b 槽并逐文件复核；写文件不等于接班。
- 班0 #5 [slot_switched] 启动选择器已设为 b 槽一次性试启；尚需开机自检回执。
- 班0 #6 [boot_pass] 第 6 代在 b 槽自检通过（连续合格 1 次）。
- 班0 #7 [takeover_registered] 非现役 b 槽开机合格，已登记第 6 代接班；旧 a 槽保留为原地回退。
```

### 篡改后核对必须失败

`$ python3 -m museum_update --state-dir <state> verify packages/gen6`

```text
错误: 配套字节未获准: components/reader.pack: expected=sha256:3cd4bbcf18c7155619a2eebd55db1f6692934ab1f338c01566dca803f982b329, actual=sha256:71fa674ee2a134b894765f3b79b67f7682c72f7c2b03a869551c169e7ce50e9a
```

### 同一类切换必须在写槽/切换前停止

`$ python3 -m museum_update --state-dir <state> install orders/M3-gen6.json`

```text
错误: 配套字节未获准: components/reader.pack: expected=sha256:3cd4bbcf18c7155619a2eebd55db1f6692934ab1f338c01566dca803f982b329, actual=sha256:71fa674ee2a134b894765f3b79b67f7682c72f7c2b03a869551c169e7ce50e9a
```

### 已经成功过的原乙机单再次遇到被换组件时，仍在核对阶段停止

`$ python3 -m museum_update --state-dir <state> install orders/M2-gen6.json`

```text
错误: 配套字节未获准: components/reader.pack: expected=sha256:3cd4bbcf18c7155619a2eebd55db1f6692934ab1f338c01566dca803f982b329, actual=sha256:71fa674ee2a134b894765f3b79b67f7682c72f7c2b03a869551c169e7ce50e9a
```

### 恢复原件后重新核对通过

`$ python3 -m museum_update --state-dir <state> verify packages/gen6`

```text
✅ 批准包核对通过: ORD-G6-001 第6代
   文件数=5 组件数=5
   整套摘要=sha256:c168f6d09fc1ba24326da1b000bb264f02204193d784e0d68d77e3b29436e0eb
```

### 恢复后丙机安装成功并切换

`$ python3 -m museum_update --state-dir <state> install orders/M3-gen6.json`

```text
✅ 安装单 TICKET-M3-G6-001 已完成写槽和切换，当前阶段=awaiting_boot
馆内终端本地更新控制器
逻辑班点: 第 0 班 / 统一停用点: 第 3 班 / 剩余班数: 3

机器号  名称  当前状态  现役槽/代次  试启槽  最低代次  桥  最近实际开机
------  ----  --------  -----------  ------  --------  --  ------------
M1  甲机  第六代已接班但未烧桥；后续失败仍可回退第五代  b:6代  -  5  未烧  6代/pass@班0
M2  乙机  第六代已接班但未烧桥；后续失败仍可回退第五代  b:6代  -  5  未烧  6代/pass@班0
M3  丙机  第六代已切为试启槽，等待开机自检  a:5代  b  5  未烧  无记录
```

### 目标机试启回报失败

`$ python3 -m museum_update --state-dir <state> boot M3 --result fail --note 只让丙机试启外设探针失败`

```text
↩️ 试启失败后已按持久回退条件恢复到旧槽，恢复自检通过。
# M3（丙机 / BING-0003）

- 当前状态: **第六代自检失败，已原地恢复到第五代** (`rolled_back_gen5`)
- 解释: M3 最终实际运行第 5 代（失败后已恢复）；尚未烧桥。
- 班点: 第 0 班；距第 3 班停用点还有 3 个计数单位
- 启动槽: active=a, trial=-, rollback=a
  - a: 第5代, status=good, last_result=pass, set=sha256:c7a665b226ace8ac3…
  - b: 第6代, status=failed, last_result=fail, set=sha256:c168f6d09fc1ba243…
- 最低可启动代次: 5（单调上升）；回旧版桥: 仍在
- 第 5 代在当前班点是否还能启动: 是
- 连续合格自检: 0
- 有据可查的回退条件:
  - 仅当 a 槽第五代摘要完好、自检为 good、最低代次 <=5，且未到第 3 班停用点时，才允许原地回退。
- 已经见到的失败:
  - 班0 b槽/第6代 回执 R-M3-0003: 只让丙机试启外设探针失败

## 历次试启动/恢复回执
- R-M3-0003 班0 b槽 第6代 boot_self_check=fail (sig bd7d23ad23e7…)；只让丙机试启外设探针失败
- R-M3-0004 班0 a槽 第5代 recovery_self_check=pass (sig bd70ba816e80…)；控制器切回旧槽后的恢复自检

## 班次时间线
- 班0 #8 [install_rejected] 安装单 TICKET-M3-G6-001 在核对阶段停止，未写槽、未切换：配套字节未获准: components/reader.pack: expected=sha256:3cd4bbcf18c7155619a2eebd55db1f6692934ab1f338c01566dca803f982b329, actual=sha256:71fa674ee2a134b894765f3b79b67f7682c72f7c2b03a869551c169e7ce50e9a
- 班0 #10 [install_verified] 安装单 TICKET-M3-G6-001 的字节、代次、组件清单和批准摘要核对通过。
- 班0 #11 [slot_written] 第 6 代已写入 b 槽并逐文件复核；写文件不等于接班。
- 班0 #12 [slot_switched] 启动选择器已设为 b 槽一次性试启；尚需开机自检回执。
- 班0 #13 [boot_fail] 第 6 代在 b 槽自检失败；材料合格不等于可以登记接班。
- 班0 #14 [rollback_recovery] 满足有据可查的回退条件：已切回 a 槽第 5 代，恢复自检通过，服务保留。
```

### 丙机按机器号的完整解释

`$ python3 -m museum_update --state-dir <state> show M3`

```text
# M3（丙机 / BING-0003）

- 当前状态: **第六代自检失败，已原地恢复到第五代** (`rolled_back_gen5`)
- 解释: M3 最终实际运行第 5 代（失败后已恢复）；尚未烧桥。
- 班点: 第 0 班；距第 3 班停用点还有 3 个计数单位
- 启动槽: active=a, trial=-, rollback=a
  - a: 第5代, status=good, last_result=pass, set=sha256:c7a665b226ace8ac3…
  - b: 第6代, status=failed, last_result=fail, set=sha256:c168f6d09fc1ba243…
- 最低可启动代次: 5（单调上升）；回旧版桥: 仍在
- 第 5 代在当前班点是否还能启动: 是
- 连续合格自检: 0
- 有据可查的回退条件:
  - 仅当 a 槽第五代摘要完好、自检为 good、最低代次 <=5，且未到第 3 班停用点时，才允许原地回退。
- 已经见到的失败:
  - 班0 b槽/第6代 回执 R-M3-0003: 只让丙机试启外设探针失败

## 历次试启动/恢复回执
- R-M3-0003 班0 b槽 第6代 boot_self_check=fail (sig bd7d23ad23e7…)；只让丙机试启外设探针失败
- R-M3-0004 班0 a槽 第5代 recovery_self_check=pass (sig bd70ba816e80…)；控制器切回旧槽后的恢复自检

## 班次时间线
- 班0 #8 [install_rejected] 安装单 TICKET-M3-G6-001 在核对阶段停止，未写槽、未切换：配套字节未获准: components/reader.pack: expected=sha256:3cd4bbcf18c7155619a2eebd55db1f6692934ab1f338c01566dca803f982b329, actual=sha256:71fa674ee2a134b894765f3b79b67f7682c72f7c2b03a869551c169e7ce50e9a
- 班0 #10 [install_verified] 安装单 TICKET-M3-G6-001 的字节、代次、组件清单和批准摘要核对通过。
- 班0 #11 [slot_written] 第 6 代已写入 b 槽并逐文件复核；写文件不等于接班。
- 班0 #12 [slot_switched] 启动选择器已设为 b 槽一次性试启；尚需开机自检回执。
- 班0 #13 [boot_fail] 第 6 代在 b 槽自检失败；材料合格不等于可以登记接班。
- 班0 #14 [rollback_recovery] 满足有据可查的回退条件：已切回 a 槽第 5 代，恢复自检通过，服务保留。
```

### 另取干净状态演示掉电续跑

`$ python3 -m museum_update --state-dir <state> init --force`

```text
馆内终端本地更新控制器
逻辑班点: 第 0 班 / 统一停用点: 第 3 班 / 剩余班数: 3

机器号  名称  当前状态  现役槽/代次  试启槽  最低代次  桥  最近实际开机
------  ----  --------  -----------  ------  --------  --  ------------
M1  甲机  第六代已接班但未烧桥；后续失败仍可回退第五代  b:6代  -  5  未烧  6代/pass@班0
M2  乙机  第五代运行中（未烧桥，可回退）  a:5代  -  5  未烧  无记录
M3  丙机  第五代运行中（未烧桥，可回退）  a:5代  -  5  未烧  无记录

持久状态: /workspace/state/power-resume/controller-state.json
```

### 写完槽后立即模拟掉电

`$ python3 -m museum_update --state-dir <state> install orders/M2-gen6.json --power-loss-after written`

```text
⚡ 模拟掉电发生在持久化阶段 `written` 之后；进度已落盘。
   重新上电后运行: python -m museum_update --state-dir /workspace/state/power-resume resume
```

### 重新上电后从持久记录接续到等待开机

`$ python3 -m museum_update --state-dir <state> resume M2`

```text
🔁 已接续 TICKET-M2-G6-001 / M2: awaiting_boot (awaiting_boot)
馆内终端本地更新控制器
逻辑班点: 第 0 班 / 统一停用点: 第 3 班 / 剩余班数: 3

机器号  名称  当前状态  现役槽/代次  试启槽  最低代次  桥  最近实际开机
------  ----  --------  -----------  ------  --------  --  ------------
M1  甲机  第六代已接班但未烧桥；后续失败仍可回退第五代  b:6代  -  5  未烧  6代/pass@班0
M2  乙机  第六代已切为试启槽，等待开机自检  a:5代  b  5  未烧  无记录
M3  丙机  第五代运行中（未烧桥，可回退）  a:5代  -  5  未烧  无记录
```

`$ python3 -m museum_update --state-dir <state> boot M2 --result pass --note 掉电恢复后的试启`

```text
✅ 开机自检回执已记录。
# M2（乙机 / YI-0002）

- 当前状态: **第六代已接班但未烧桥；后续失败仍可回退第五代** (`gen6_active_uncommitted`)
- 解释: M2 最近实际启动第 6 代且自检合格，但尚未烧桥；第 5 代仍可按条件回退。
- 班点: 第 0 班；距第 3 班停用点还有 3 个计数单位
- 启动槽: active=b, trial=-, rollback=a
  - a: 第5代, status=good, last_result=pass, set=sha256:c7a665b226ace8ac3…
  - b: 第6代, status=good, last_result=pass, set=sha256:c168f6d09fc1ba243…
- 最低可启动代次: 5（单调上升）；回旧版桥: 仍在
- 第 5 代在当前班点是否还能启动: 是
- 连续合格自检: 1
- 有据可查的回退条件:
  - 仅当 a 槽第五代摘要完好、自检为 good、最低代次 <=5，且未到第 3 班停用点时，才允许原地回退。
- 已经见到的失败: 无

## 历次试启动/恢复回执
- R-M2-0002 班0 b槽 第6代 boot_self_check=pass (sig b0d90db86048…)；掉电恢复后的试启

## 班次时间线
- 班0 #3 [install_verified] 安装单 TICKET-M2-G6-001 的字节、代次、组件清单和批准摘要核对通过。
- 班0 #4 [slot_written] 第 6 代已写入 b 槽并逐文件复核；写文件不等于接班。
- 班0 #5 [resume_switch] 掉电恢复：根据持久记录重新选择 b 试启槽。
- 班0 #6 [resume_await_boot] 掉电恢复完成：安装单等待开机自检。
- 班0 #7 [boot_pass] 第 6 代在 b 槽自检通过（连续合格 1 次）。
- 班0 #8 [takeover_registered] 非现役 b 槽开机合格，已登记第 6 代接班；旧 a 槽保留为原地回退。
```
