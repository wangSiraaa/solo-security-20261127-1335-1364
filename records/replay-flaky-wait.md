# 回放二：首次成功后又失败；维护员暂不提前烧桥

第一次成功不立即提高最低代次。若后续自检在停用点前失败，控制器按各机持久回退条件恢复第五代；到第 3 班后旧版禁止启动。

## 自动核对点

- ✅ 甲机旧版启动被阻止
- ✅ 乙机旧版启动被阻止
- ✅ 甲、乙均未烧桥
- ✅ 甲机在班点后登记停用
- ✅ 乙机在班点后登记停用
- ✅ 丙机因班点无法回退而停用待修
- ✅ 三机均保留已见到的失败记录

## 实际命令与输出

### 同一起点：甲机已有一次第六代合格试启

`$ python3 -m museum_update --state-dir <state> init --force`

```text
馆内终端本地更新控制器
逻辑班点: 第 0 班 / 统一停用点: 第 3 班 / 剩余班数: 3

机器号  名称  当前状态  现役槽/代次  试启槽  最低代次  桥  最近实际开机
------  ----  --------  -----------  ------  --------  --  ------------
M1  甲机  第六代已接班但未烧桥；后续失败仍可回退第五代  b:6代  -  5  未烧  6代/pass@班0
M2  乙机  第五代运行中（未烧桥，可回退）  a:5代  -  5  未烧  无记录
M3  丙机  第五代运行中（未烧桥，可回退）  a:5代  -  5  未烧  无记录

持久状态: /workspace/state/replay-flaky-wait/controller-state.json
```

### 进入第 1 班

`$ python3 -m museum_update --state-dir <state> advance-shift --by 1`

```text
🕘 已前进到第 1 班。
馆内终端本地更新控制器
逻辑班点: 第 1 班 / 统一停用点: 第 3 班 / 剩余班数: 2

机器号  名称  当前状态  现役槽/代次  试启槽  最低代次  桥  最近实际开机
------  ----  --------  -----------  ------  --------  --  ------------
M1  甲机  第六代已接班但未烧桥；后续失败仍可回退第五代  b:6代  -  5  未烧  6代/pass@班0
M2  乙机  第五代运行中（未烧桥，可回退）  a:5代  -  5  未烧  无记录
M3  丙机  第五代运行中（未烧桥，可回退）  a:5代  -  5  未烧  无记录
```

### 乙机第五代在安装前仍按旧合同工作

`$ python3 -m museum_update --state-dir <state> operate M2 --operation locate-box BOX-77 --by staff-chen`

```text
🛠️ 已登记 M2 现场操作（field-ops-v5）: locate-box BOX-77
```

### 甲机失败时尚未烧桥，原地恢复第五代

`$ python3 -m museum_update --state-dir <state> boot M1 --result fail --note 甲机第一次成功后的第二次自检：外设探针失败`

```text
↩️ 试启失败后已按持久回退条件恢复到旧槽，恢复自检通过。
# M1（甲机 / JIA-0001）

- 当前状态: **第六代自检失败，已原地恢复到第五代** (`rolled_back_gen5`)
- 解释: M1 最终实际运行第 5 代（失败后已恢复）；尚未烧桥。
- 班点: 第 1 班；距第 3 班停用点还有 2 个计数单位
- 启动槽: active=a, trial=-, rollback=a
  - a: 第5代, status=good, last_result=pass, set=sha256:c7a665b226ace8ac3…
  - b: 第6代, status=failed, last_result=fail, set=sha256:c168f6d09fc1ba243…
- 最低可启动代次: 5（单调上升）；回旧版桥: 仍在
- 第 5 代在当前班点是否还能启动: 是
- 连续合格自检: 0
- 有据可查的回退条件:
  - 仅当 a 槽第五代摘要完好、自检为 good、最低代次 <=5，且未到第 3 班停用点时，才允许原地回退。
- 已经见到的失败:
  - 班1 b槽/第6代 回执 R-M1-0002: 甲机第一次成功后的第二次自检：外设探针失败

## 历次试启动/恢复回执
- R-M1-0001 班0 b槽 第6代 trial=pass (sig 8e5c6d03c182…)；起点前已见到的甲机第一次试启合格回执
- R-M1-0002 班1 b槽 第6代 boot_self_check=fail (sig 1504365a9f75…)；甲机第一次成功后的第二次自检：外设探针失败
- R-M1-0003 班1 a槽 第5代 recovery_self_check=pass (sig 75adee9d5012…)；控制器切回旧槽后的恢复自检

## 班次时间线
- 班0 #2 [trial_pass] 甲机第六代第一次试启自检通过，已登记接班；回旧版的桥仍在。
- 班1 #5 [boot_fail] 第 6 代在 b 槽自检失败；材料合格不等于可以登记接班。
- 班1 #6 [rollback_recovery] 满足有据可查的回退条件：已切回 a 槽第 5 代，恢复自检通过，服务保留。
```

### 乙机第 1 班安装第六代

`$ python3 -m museum_update --state-dir <state> install orders/M2-gen6.json`

```text
✅ 安装单 TICKET-M2-G6-001 已完成写槽和切换，当前阶段=awaiting_boot
馆内终端本地更新控制器
逻辑班点: 第 1 班 / 统一停用点: 第 3 班 / 剩余班数: 2

机器号  名称  当前状态  现役槽/代次  试启槽  最低代次  桥  最近实际开机
------  ----  --------  -----------  ------  --------  --  ------------
M1  甲机  第六代自检失败，已原地恢复到第五代  a:5代  -  5  未烧  5代/pass@班1
M2  乙机  第六代已切为试启槽，等待开机自检  a:5代  b  5  未烧  无记录
M3  丙机  第五代运行中（未烧桥，可回退）  a:5代  -  5  未烧  无记录
```

`$ python3 -m museum_update --state-dir <state> boot M2 --result pass --note 乙机第一次试启成功`

```text
✅ 开机自检回执已记录。
# M2（乙机 / YI-0002）

- 当前状态: **第六代已接班但未烧桥；后续失败仍可回退第五代** (`gen6_active_uncommitted`)
- 解释: M2 最近实际启动第 6 代且自检合格，但尚未烧桥；第 5 代仍可按条件回退。
- 班点: 第 1 班；距第 3 班停用点还有 2 个计数单位
- 启动槽: active=b, trial=-, rollback=a
  - a: 第5代, status=good, last_result=pass, set=sha256:c7a665b226ace8ac3…
  - b: 第6代, status=good, last_result=pass, set=sha256:c168f6d09fc1ba243…
- 最低可启动代次: 5（单调上升）；回旧版桥: 仍在
- 第 5 代在当前班点是否还能启动: 是
- 连续合格自检: 1
- 有据可查的回退条件:
  - 仅当 a 槽第五代摘要完好、自检为 good、最低代次 <=5，且未到第 3 班停用点时，才允许原地回退。
- 已经见到的失败: 无
- 现场操作:
  - 班1 staff-chen: locate-box BOX-77（field-ops-v5）

## 历次试启动/恢复回执
- R-M2-0004 班1 b槽 第6代 boot_self_check=pass (sig c360b7e08d5d…)；乙机第一次试启成功

## 班次时间线
- 班1 #4 [field_operation] 按旧合同 field-ops-v5 接受现场操作：locate-box BOX-77
- 班1 #7 [install_verified] 安装单 TICKET-M2-G6-001 的字节、代次、组件清单和批准摘要核对通过。
- 班1 #8 [slot_written] 第 6 代已写入 b 槽并逐文件复核；写文件不等于接班。
- 班1 #9 [slot_switched] 启动选择器已设为 b 槽一次性试启；尚需开机自检回执。
- 班1 #10 [boot_pass] 第 6 代在 b 槽自检通过（连续合格 1 次）。
- 班1 #11 [takeover_registered] 非现役 b 槽开机合格，已登记第 6 代接班；旧 a 槽保留为原地回退。
```

### 进入第 2 班

`$ python3 -m museum_update --state-dir <state> advance-shift --by 1`

```text
🕘 已前进到第 2 班。
馆内终端本地更新控制器
逻辑班点: 第 2 班 / 统一停用点: 第 3 班 / 剩余班数: 1

机器号  名称  当前状态  现役槽/代次  试启槽  最低代次  桥  最近实际开机
------  ----  --------  -----------  ------  --------  --  ------------
M1  甲机  第六代自检失败，已原地恢复到第五代  a:5代  -  5  未烧  5代/pass@班1
M2  乙机  第六代已接班但未烧桥；后续失败仍可回退第五代  b:6代  -  5  未烧  6代/pass@班1
M3  丙机  第五代运行中（未烧桥，可回退）  a:5代  -  5  未烧  无记录
```

### 甲机已回退第五代，服务保留并继续接受旧合同操作

`$ python3 -m museum_update --state-dir <state> operate M1 --operation scan-delivery CART-9 --by staff-lin`

```text
🛠️ 已登记 M1 现场操作（field-ops-v5）: scan-delivery CART-9
```

### 第 2 班仍满足回退条件，乙机恢复第五代

`$ python3 -m museum_update --state-dir <state> boot M2 --result fail --note 乙机首次成功后的第二次自检失败`

```text
↩️ 试启失败后已按持久回退条件恢复到旧槽，恢复自检通过。
# M2（乙机 / YI-0002）

- 当前状态: **第六代自检失败，已原地恢复到第五代** (`rolled_back_gen5`)
- 解释: M2 最终实际运行第 5 代（失败后已恢复）；尚未烧桥。
- 班点: 第 2 班；距第 3 班停用点还有 1 个计数单位
- 启动槽: active=a, trial=-, rollback=a
  - a: 第5代, status=good, last_result=pass, set=sha256:c7a665b226ace8ac3…
  - b: 第6代, status=failed, last_result=fail, set=sha256:c168f6d09fc1ba243…
- 最低可启动代次: 5（单调上升）；回旧版桥: 仍在
- 第 5 代在当前班点是否还能启动: 是
- 连续合格自检: 0
- 有据可查的回退条件:
  - 仅当 a 槽第五代摘要完好、自检为 good、最低代次 <=5，且未到第 3 班停用点时，才允许原地回退。
- 已经见到的失败:
  - 班2 b槽/第6代 回执 R-M2-0005: 乙机首次成功后的第二次自检失败
- 现场操作:
  - 班1 staff-chen: locate-box BOX-77（field-ops-v5）

## 历次试启动/恢复回执
- R-M2-0004 班1 b槽 第6代 boot_self_check=pass (sig c360b7e08d5d…)；乙机第一次试启成功
- R-M2-0005 班2 b槽 第6代 boot_self_check=fail (sig c92ea7b05000…)；乙机首次成功后的第二次自检失败
- R-M2-0006 班2 a槽 第5代 recovery_self_check=pass (sig 3cedc7bafc77…)；控制器切回旧槽后的恢复自检

## 班次时间线
- 班1 #4 [field_operation] 按旧合同 field-ops-v5 接受现场操作：locate-box BOX-77
- 班1 #7 [install_verified] 安装单 TICKET-M2-G6-001 的字节、代次、组件清单和批准摘要核对通过。
- 班1 #8 [slot_written] 第 6 代已写入 b 槽并逐文件复核；写文件不等于接班。
- 班1 #9 [slot_switched] 启动选择器已设为 b 槽一次性试启；尚需开机自检回执。
- 班1 #10 [boot_pass] 第 6 代在 b 槽自检通过（连续合格 1 次）。
- 班1 #11 [takeover_registered] 非现役 b 槽开机合格，已登记第 6 代接班；旧 a 槽保留为原地回退。
- 班2 #14 [boot_fail] 第 6 代在 b 槽自检失败；材料合格不等于可以登记接班。
- 班2 #15 [rollback_recovery] 满足有据可查的回退条件：已切回 a 槽第 5 代，恢复自检通过，服务保留。
```

### 丙机第 2 班安装

`$ python3 -m museum_update --state-dir <state> install orders/M3-gen6.json`

```text
✅ 安装单 TICKET-M3-G6-001 已完成写槽和切换，当前阶段=awaiting_boot
馆内终端本地更新控制器
逻辑班点: 第 2 班 / 统一停用点: 第 3 班 / 剩余班数: 1

机器号  名称  当前状态  现役槽/代次  试启槽  最低代次  桥  最近实际开机
------  ----  --------  -----------  ------  --------  --  ------------
M1  甲机  第六代自检失败，已原地恢复到第五代  a:5代  -  5  未烧  5代/pass@班1
M2  乙机  第六代自检失败，已原地恢复到第五代  a:5代  -  5  未烧  5代/pass@班2
M3  丙机  第六代已切为试启槽，等待开机自检  a:5代  b  5  未烧  无记录
```

`$ python3 -m museum_update --state-dir <state> boot M3 --result pass --note 丙机第一次试启成功`

```text
✅ 开机自检回执已记录。
# M3（丙机 / BING-0003）

- 当前状态: **第六代已接班但未烧桥；后续失败仍可回退第五代** (`gen6_active_uncommitted`)
- 解释: M3 最近实际启动第 6 代且自检合格，但尚未烧桥；第 5 代仍可按条件回退。
- 班点: 第 2 班；距第 3 班停用点还有 1 个计数单位
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
- R-M3-0007 班2 b槽 第6代 boot_self_check=pass (sig f3fe405dd720…)；丙机第一次试启成功

## 班次时间线
- 班2 #16 [install_verified] 安装单 TICKET-M3-G6-001 的字节、代次、组件清单和批准摘要核对通过。
- 班2 #17 [slot_written] 第 6 代已写入 b 槽并逐文件复核；写文件不等于接班。
- 班2 #18 [slot_switched] 启动选择器已设为 b 槽一次性试启；尚需开机自检回执。
- 班2 #19 [boot_pass] 第 6 代在 b 槽自检通过（连续合格 1 次）。
- 班2 #20 [takeover_registered] 非现役 b 槽开机合格，已登记第 6 代接班；旧 a 槽保留为原地回退。
```

### 进入第 3 班；第五代统一停用生效

`$ python3 -m museum_update --state-dir <state> advance-shift --by 1`

```text
🕘 已前进到第 3 班。
馆内终端本地更新控制器
逻辑班点: 第 3 班 / 统一停用点: 第 3 班 / 剩余班数: 0

机器号  名称  当前状态  现役槽/代次  试启槽  最低代次  桥  最近实际开机
------  ----  --------  -----------  ------  --------  --  ------------
M1  甲机  第六代自检失败，已原地恢复到第五代  a:5代  -  5  未烧  5代/pass@班1
M2  乙机  第六代自检失败，已原地恢复到第五代  a:5代  -  5  未烧  5代/pass@班2
M3  丙机  第六代已接班但未烧桥；后续失败仍可回退第五代  b:6代  -  5  未烧  6代/pass@班2
```

### 停用点已到，第五代也不能作为恢复启动，丙机停用待修

`$ python3 -m museum_update --state-dir <state> boot M3 --result fail --note 丙机首次成功后的第二次自检失败`

```text
🛑 试启失败且不能原地恢复: 第 5 代已在第 3 班后统一停用（当前班点 3）
# M3（丙机 / BING-0003）

- 当前状态: **已停用：无法原地恢复，需本地修复** (`disabled_repair_required`)
- 解释: M3 已停用，没有系统在运行；原因：第 5 代已在第 3 班后统一停用（当前班点 3）。
- 班点: 第 3 班；距第 3 班停用点还有 0 个计数单位
- 启动槽: active=b, trial=-, rollback=a
  - a: 第5代, status=good, last_result=pass, set=sha256:c7a665b226ace8ac3…
  - b: 第6代, status=failed, last_result=fail, set=sha256:c168f6d09fc1ba243…
- 最低可启动代次: 5（单调上升）；回旧版桥: 仍在
- 第 5 代在当前班点是否还能启动: 否
- 连续合格自检: 0
- 有据可查的回退条件:
  - 仅当 a 槽第五代摘要完好、自检为 good、最低代次 <=5，且未到第 3 班停用点时，才允许原地回退。
- 已经见到的失败:
  - 班3 b槽/第6代 回执 R-M3-0008: 丙机首次成功后的第二次自检失败

## 历次试启动/恢复回执
- R-M3-0007 班2 b槽 第6代 boot_self_check=pass (sig f3fe405dd720…)；丙机第一次试启成功
- R-M3-0008 班3 b槽 第6代 boot_self_check=fail (sig 69e323b56983…)；丙机首次成功后的第二次自检失败

## 班次时间线
- 班2 #16 [install_verified] 安装单 TICKET-M3-G6-001 的字节、代次、组件清单和批准摘要核对通过。
- 班2 #17 [slot_written] 第 6 代已写入 b 槽并逐文件复核；写文件不等于接班。
- 班2 #18 [slot_switched] 启动选择器已设为 b 槽一次性试启；尚需开机自检回执。
- 班2 #19 [boot_pass] 第 6 代在 b 槽自检通过（连续合格 1 次）。
- 班2 #20 [takeover_registered] 非现役 b 槽开机合格，已登记第 6 代接班；旧 a 槽保留为原地回退。
- 班3 #22 [boot_fail] 第 6 代在 b 槽自检失败；材料合格不等于可以登记接班。
- 班3 #23 [machine_disabled] 不能烧掉回旧版的桥后再找桥：无法原地恢复，机器停用待本地修复。原因：第 5 代已在第 3 班后统一停用（当前班点 3）
```

`$ python3 -m museum_update --state-dir <state> boot M1 --result pass --note 班点后尝试启动旧版`

```text
⛔ 开机被阻止: 第 5 代已在第 3 班后统一停用（当前班点 3）
# M1（甲机 / JIA-0001）

- 当前状态: **已到统一停用班点：第五代禁止启动，且无合格第六代可承诺** (`retired_stopped`)
- 解释: M1 到班点后停用：第 5 代被禁止启动，第 6 代没有最近合格记录可承诺。
- 班点: 第 3 班；距第 3 班停用点还有 0 个计数单位
- 启动槽: active=a, trial=-, rollback=a
  - a: 第5代, status=good, last_result=pass, set=sha256:c7a665b226ace8ac3…
  - b: 第6代, status=failed, last_result=fail, set=sha256:c168f6d09fc1ba243…
- 最低可启动代次: 5（单调上升）；回旧版桥: 仍在
- 第 5 代在当前班点是否还能启动: 否
- 连续合格自检: 0
- 有据可查的回退条件:
  - 仅当 a 槽第五代摘要完好、自检为 good、最低代次 <=5，且未到第 3 班停用点时，才允许原地回退。
- 已经见到的失败:
  - 班1 b槽/第6代 回执 R-M1-0002: 甲机第一次成功后的第二次自检：外设探针失败
- 现场操作:
  - 班2 staff-lin: scan-delivery CART-9（field-ops-v5）

## 历次试启动/恢复回执
- R-M1-0001 班0 b槽 第6代 trial=pass (sig 8e5c6d03c182…)；起点前已见到的甲机第一次试启合格回执
- R-M1-0002 班1 b槽 第6代 boot_self_check=fail (sig 1504365a9f75…)；甲机第一次成功后的第二次自检：外设探针失败
- R-M1-0003 班1 a槽 第5代 recovery_self_check=pass (sig 75adee9d5012…)；控制器切回旧槽后的恢复自检
- R-M1-0009 班3 a槽 第5代 boot_self_check=pass (sig d65e65a4b642…)；班点后尝试启动旧版

## 班次时间线
- 班0 #2 [trial_pass] 甲机第六代第一次试启自检通过，已登记接班；回旧版的桥仍在。
- 班1 #5 [boot_fail] 第 6 代在 b 槽自检失败；材料合格不等于可以登记接班。
- 班1 #6 [rollback_recovery] 满足有据可查的回退条件：已切回 a 槽第 5 代，恢复自检通过，服务保留。
- 班2 #13 [field_operation] 按旧合同 field-ops-v5 接受现场操作：scan-delivery CART-9
- 班3 #24 [boot_blocked] 开机被启动策略拒绝：第 5 代已在第 3 班后统一停用（当前班点 3）。
```

`$ python3 -m museum_update --state-dir <state> boot M2 --result pass --note 班点后尝试启动旧版`

```text
⛔ 开机被阻止: 第 5 代已在第 3 班后统一停用（当前班点 3）
# M2（乙机 / YI-0002）

- 当前状态: **已到统一停用班点：第五代禁止启动，且无合格第六代可承诺** (`retired_stopped`)
- 解释: M2 到班点后停用：第 5 代被禁止启动，第 6 代没有最近合格记录可承诺。
- 班点: 第 3 班；距第 3 班停用点还有 0 个计数单位
- 启动槽: active=a, trial=-, rollback=a
  - a: 第5代, status=good, last_result=pass, set=sha256:c7a665b226ace8ac3…
  - b: 第6代, status=failed, last_result=fail, set=sha256:c168f6d09fc1ba243…
- 最低可启动代次: 5（单调上升）；回旧版桥: 仍在
- 第 5 代在当前班点是否还能启动: 否
- 连续合格自检: 0
- 有据可查的回退条件:
  - 仅当 a 槽第五代摘要完好、自检为 good、最低代次 <=5，且未到第 3 班停用点时，才允许原地回退。
- 已经见到的失败:
  - 班2 b槽/第6代 回执 R-M2-0005: 乙机首次成功后的第二次自检失败
- 现场操作:
  - 班1 staff-chen: locate-box BOX-77（field-ops-v5）

## 历次试启动/恢复回执
- R-M2-0004 班1 b槽 第6代 boot_self_check=pass (sig c360b7e08d5d…)；乙机第一次试启成功
- R-M2-0005 班2 b槽 第6代 boot_self_check=fail (sig c92ea7b05000…)；乙机首次成功后的第二次自检失败
- R-M2-0006 班2 a槽 第5代 recovery_self_check=pass (sig 3cedc7bafc77…)；控制器切回旧槽后的恢复自检
- R-M2-0010 班3 a槽 第5代 boot_self_check=pass (sig e67a1e31a817…)；班点后尝试启动旧版

## 班次时间线
- 班1 #4 [field_operation] 按旧合同 field-ops-v5 接受现场操作：locate-box BOX-77
- 班1 #7 [install_verified] 安装单 TICKET-M2-G6-001 的字节、代次、组件清单和批准摘要核对通过。
- 班1 #8 [slot_written] 第 6 代已写入 b 槽并逐文件复核；写文件不等于接班。
- 班1 #9 [slot_switched] 启动选择器已设为 b 槽一次性试启；尚需开机自检回执。
- 班1 #10 [boot_pass] 第 6 代在 b 槽自检通过（连续合格 1 次）。
- 班1 #11 [takeover_registered] 非现役 b 槽开机合格，已登记第 6 代接班；旧 a 槽保留为原地回退。
- 班2 #14 [boot_fail] 第 6 代在 b 槽自检失败；材料合格不等于可以登记接班。
- 班2 #15 [rollback_recovery] 满足有据可查的回退条件：已切回 a 槽第 5 代，恢复自检通过，服务保留。
- 班3 #25 [boot_blocked] 开机被启动策略拒绝：第 5 代已在第 3 班后统一停用（当前班点 3）。
```

### 班点核对：无最近合格第六代，不允许伪装成仍在试装

`$ python3 -m museum_update --state-dir <state> reconcile`

```text
- M1: retired_stopped
- M2: retired_stopped
- M3: remains_disabled
馆内终端本地更新控制器
逻辑班点: 第 3 班 / 统一停用点: 第 3 班 / 剩余班数: 0

机器号  名称  当前状态  现役槽/代次  试启槽  最低代次  桥  最近实际开机
------  ----  --------  -----------  ------  --------  --  ------------
M1  甲机  已到统一停用班点：第五代禁止启动，且无合格第六代可承诺  a:5代  -  5  未烧  5代/blocked@班3
M2  乙机  已到统一停用班点：第五代禁止启动，且无合格第六代可承诺  a:5代  -  5  未烧  5代/blocked@班3
M3  丙机  已停用：无法原地恢复，需本地修复  b:6代  -  5  未烧  6代/fail@班3
```

### 每台机器的实际运行/停用记录

`$ python3 -m museum_update --state-dir <state> report`

```text
# 三台模拟机最终记录

```text
馆内终端本地更新控制器
逻辑班点: 第 3 班 / 统一停用点: 第 3 班 / 剩余班数: 0

机器号  名称  当前状态  现役槽/代次  试启槽  最低代次  桥  最近实际开机
------  ----  --------  -----------  ------  --------  --  ------------
M1  甲机  已到统一停用班点：第五代禁止启动，且无合格第六代可承诺  a:5代  -  5  未烧  5代/blocked@班3
M2  乙机  已到统一停用班点：第五代禁止启动，且无合格第六代可承诺  a:5代  -  5  未烧  5代/blocked@班3
M3  丙机  已停用：无法原地恢复，需本地修复  b:6代  -  5  未烧  6代/fail@班3
```

# M1（甲机 / JIA-0001）

- 当前状态: **已到统一停用班点：第五代禁止启动，且无合格第六代可承诺** (`retired_stopped`)
- 解释: M1 到班点后停用：第 5 代被禁止启动，第 6 代没有最近合格记录可承诺。
- 班点: 第 3 班；距第 3 班停用点还有 0 个计数单位
- 启动槽: active=a, trial=-, rollback=a
  - a: 第5代, status=good, last_result=pass, set=sha256:c7a665b226ace8ac3…
  - b: 第6代, status=failed, last_result=fail, set=sha256:c168f6d09fc1ba243…
- 最低可启动代次: 5（单调上升）；回旧版桥: 仍在
- 第 5 代在当前班点是否还能启动: 否
- 连续合格自检: 0
- 有据可查的回退条件:
  - 仅当 a 槽第五代摘要完好、自检为 good、最低代次 <=5，且未到第 3 班停用点时，才允许原地回退。
- 已经见到的失败:
  - 班1 b槽/第6代 回执 R-M1-0002: 甲机第一次成功后的第二次自检：外设探针失败
- 现场操作:
  - 班2 staff-lin: scan-delivery CART-9（field-ops-v5）

## 历次试启动/恢复回执
- R-M1-0001 班0 b槽 第6代 trial=pass (sig 8e5c6d03c182…)；起点前已见到的甲机第一次试启合格回执
- R-M1-0002 班1 b槽 第6代 boot_self_check=fail (sig 1504365a9f75…)；甲机第一次成功后的第二次自检：外设探针失败
- R-M1-0003 班1 a槽 第5代 recovery_self_check=pass (sig 75adee9d5012…)；控制器切回旧槽后的恢复自检
- R-M1-0009 班3 a槽 第5代 boot_self_check=pass (sig d65e65a4b642…)；班点后尝试启动旧版

## 班次时间线
- 班0 #2 [trial_pass] 甲机第六代第一次试启自检通过，已登记接班；回旧版的桥仍在。
- 班1 #5 [boot_fail] 第 6 代在 b 槽自检失败；材料合格不等于可以登记接班。
- 班1 #6 [rollback_recovery] 满足有据可查的回退条件：已切回 a 槽第 5 代，恢复自检通过，服务保留。
- 班2 #13 [field_operation] 按旧合同 field-ops-v5 接受现场操作：scan-delivery CART-9
- 班3 #24 [boot_blocked] 开机被启动策略拒绝：第 5 代已在第 3 班后统一停用（当前班点 3）。
- 班3 #26 [retired_without_new_commit] 届期后拒绝启动旧版；又无最近合格的第六代，登记为停用待修，而不是继续试装。

# M2（乙机 / YI-0002）

- 当前状态: **已到统一停用班点：第五代禁止启动，且无合格第六代可承诺** (`retired_stopped`)
- 解释: M2 到班点后停用：第 5 代被禁止启动，第 6 代没有最近合格记录可承诺。
- 班点: 第 3 班；距第 3 班停用点还有 0 个计数单位
- 启动槽: active=a, trial=-, rollback=a
  - a: 第5代, status=good, last_result=pass, set=sha256:c7a665b226ace8ac3…
  - b: 第6代, status=failed, last_result=fail, set=sha256:c168f6d09fc1ba243…
- 最低可启动代次: 5（单调上升）；回旧版桥: 仍在
- 第 5 代在当前班点是否还能启动: 否
- 连续合格自检: 0
- 有据可查的回退条件:
  - 仅当 a 槽第五代摘要完好、自检为 good、最低代次 <=5，且未到第 3 班停用点时，才允许原地回退。
- 已经见到的失败:
  - 班2 b槽/第6代 回执 R-M2-0005: 乙机首次成功后的第二次自检失败
- 现场操作:
  - 班1 staff-chen: locate-box BOX-77（field-ops-v5）

## 历次试启动/恢复回执
- R-M2-0004 班1 b槽 第6代 boot_self_check=pass (sig c360b7e08d5d…)；乙机第一次试启成功
- R-M2-0005 班2 b槽 第6代 boot_self_check=fail (sig c92ea7b05000…)；乙机首次成功后的第二次自检失败
- R-M2-0006 班2 a槽 第5代 recovery_self_check=pass (sig 3cedc7bafc77…)；控制器切回旧槽后的恢复自检
- R-M2-0010 班3 a槽 第5代 boot_self_check=pass (sig e67a1e31a817…)；班点后尝试启动旧版

## 班次时间线
- 班1 #4 [field_operation] 按旧合同 field-ops-v5 接受现场操作：locate-box BOX-77
- 班1 #7 [install_verified] 安装单 TICKET-M2-G6-001 的字节、代次、组件清单和批准摘要核对通过。
- 班1 #8 [slot_written] 第 6 代已写入 b 槽并逐文件复核；写文件不等于接班。
- 班1 #9 [slot_switched] 启动选择器已设为 b 槽一次性试启；尚需开机自检回执。
- 班1 #10 [boot_pass] 第 6 代在 b 槽自检通过（连续合格 1 次）。
- 班1 #11 [takeover_registered] 非现役 b 槽开机合格，已登记第 6 代接班；旧 a 槽保留为原地回退。
- 班2 #14 [boot_fail] 第 6 代在 b 槽自检失败；材料合格不等于可以登记接班。
- 班2 #15 [rollback_recovery] 满足有据可查的回退条件：已切回 a 槽第 5 代，恢复自检通过，服务保留。
- 班3 #25 [boot_blocked] 开机被启动策略拒绝：第 5 代已在第 3 班后统一停用（当前班点 3）。
- 班3 #27 [retired_without_new_commit] 届期后拒绝启动旧版；又无最近合格的第六代，登记为停用待修，而不是继续试装。

# M3（丙机 / BING-0003）

- 当前状态: **已停用：无法原地恢复，需本地修复** (`disabled_repair_required`)
- 解释: M3 已停用，没有系统在运行；原因：第 5 代已在第 3 班后统一停用（当前班点 3）。
- 班点: 第 3 班；距第 3 班停用点还有 0 个计数单位
- 启动槽: active=b, trial=-, rollback=a
  - a: 第5代, status=good, last_result=pass, set=sha256:c7a665b226ace8ac3…
  - b: 第6代, status=failed, last_result=fail, set=sha256:c168f6d09fc1ba243…
- 最低可启动代次: 5（单调上升）；回旧版桥: 仍在
- 第 5 代在当前班点是否还能启动: 否
- 连续合格自检: 0
- 有据可查的回退条件:
  - 仅当 a 槽第五代摘要完好、自检为 good、最低代次 <=5，且未到第 3 班停用点时，才允许原地回退。
- 已经见到的失败:
  - 班3 b槽/第6代 回执 R-M3-0008: 丙机首次成功后的第二次自检失败

## 历次试启动/恢复回执
- R-M3-0007 班2 b槽 第6代 boot_self_check=pass (sig f3fe405dd720…)；丙机第一次试启成功
- R-M3-0008 班3 b槽 第6代 boot_self_check=fail (sig 69e323b56983…)；丙机首次成功后的第二次自检失败

## 班次时间线
- 班2 #16 [install_verified] 安装单 TICKET-M3-G6-001 的字节、代次、组件清单和批准摘要核对通过。
- 班2 #17 [slot_written] 第 6 代已写入 b 槽并逐文件复核；写文件不等于接班。
- 班2 #18 [slot_switched] 启动选择器已设为 b 槽一次性试启；尚需开机自检回执。
- 班2 #19 [boot_pass] 第 6 代在 b 槽自检通过（连续合格 1 次）。
- 班2 #20 [takeover_registered] 非现役 b 槽开机合格，已登记第 6 代接班；旧 a 槽保留为原地回退。
- 班3 #22 [boot_fail] 第 6 代在 b 槽自检失败；材料合格不等于可以登记接班。
- 班3 #23 [machine_disabled] 不能烧掉回旧版的桥后再找桥：无法原地恢复，机器停用待本地修复。原因：第 5 代已在第 3 班后统一停用（当前班点 3）

## 全班次记录

```text
班  序号  机器  结果     代次  事件 / 说明
--  ----  ----  -------  ----  ----------
0   1     -     -              [seed] 初始化同一起点：甲机第六代已通过第一次试启但未烧桥；乙、丙机运行第五代。
0   2     M1    pass     6     [trial_pass] 甲机第六代第一次试启自检通过，已登记接班；回旧版的桥仍在。
1   3     -     -              [shift_advanced] 进入第 1 班。
1   4     M2    -        5     [field_operation] 按旧合同 field-ops-v5 接受现场操作：locate-box BOX-77
1   5     M1    fail     6     [boot_fail] 第 6 代在 b 槽自检失败；材料合格不等于可以登记接班。
1   6     M1    pass           [rollback_recovery] 满足有据可查的回退条件：已切回 a 槽第 5 代，恢复自检通过，服务保留。
1   7     M2    -              [install_verified] 安装单 TICKET-M2-G6-001 的字节、代次、组件清单和批准摘要核对通过。
1   8     M2    -              [slot_written] 第 6 代已写入 b 槽并逐文件复核；写文件不等于接班。
1   9     M2    -              [slot_switched] 启动选择器已设为 b 槽一次性试启；尚需开机自检回执。
1   10    M2    pass     6     [boot_pass] 第 6 代在 b 槽自检通过（连续合格 1 次）。
1   11    M2    -        6     [takeover_registered] 非现役 b 槽开机合格，已登记第 6 代接班；旧 a 槽保留为原地回退。
2   12    -     -              [shift_advanced] 进入第 2 班。
2   13    M1    -        5     [field_operation] 按旧合同 field-ops-v5 接受现场操作：scan-delivery CART-9
2   14    M2    fail     6     [boot_fail] 第 6 代在 b 槽自检失败；材料合格不等于可以登记接班。
2   15    M2    pass           [rollback_recovery] 满足有据可查的回退条件：已切回 a 槽第 5 代，恢复自检通过，服务保留。
2   16    M3    -              [install_verified] 安装单 TICKET-M3-G6-001 的字节、代次、组件清单和批准摘要核对通过。
2   17    M3    -              [slot_written] 第 6 代已写入 b 槽并逐文件复核；写文件不等于接班。
2   18    M3    -              [slot_switched] 启动选择器已设为 b 槽一次性试启；尚需开机自检回执。
2   19    M3    pass     6     [boot_pass] 第 6 代在 b 槽自检通过（连续合格 1 次）。
2   20    M3    -        6     [takeover_registered] 非现役 b 槽开机合格，已登记第 6 代接班；旧 a 槽保留为原地回退。
3   21    -     -              [deadline_reached] 第 3 班停用点已到：第 5 代开机将被拒绝；请对合格第六代执行 reconcile/commit，不能让其一直停在试装。
3   22    M3    fail     6     [boot_fail] 第 6 代在 b 槽自检失败；材料合格不等于可以登记接班。
3   23    M3    stop           [machine_disabled] 不能烧掉回旧版的桥后再找桥：无法原地恢复，机器停用待本地修复。原因：第 5 代已在第 3 班后统一停用（当前班点 3）
3   24    M1    blocked        [boot_blocked] 开机被启动策略拒绝：第 5 代已在第 3 班后统一停用（当前班点 3）。
3   25    M2    blocked        [boot_blocked] 开机被启动策略拒绝：第 5 代已在第 3 班后统一停用（当前班点 3）。
3   26    M1    stop           [retired_without_new_commit] 届期后拒绝启动旧版；又无最近合格的第六代，登记为停用待修，而不是继续试装。
3   27    M2    stop           [retired_without_new_commit] 届期后拒绝启动旧版；又无最近合格的第六代，登记为停用待修，而不是继续试装。
```
```
