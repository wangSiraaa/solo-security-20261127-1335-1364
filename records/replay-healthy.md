# 回放一：以后一直健康

策略：每台机器原则上见到第二次连续合格自检后再烧桥；班点仍未手动承诺的合格第六代由 reconcile 自动接班。

## 自动核对点

- ✅ 乙机掉电命令返回 42
- ✅ M1 最终为第六代已承诺
- ✅ M1 最近实际开机合格
- ✅ M2 最终为第六代已承诺
- ✅ M2 最近实际开机合格
- ✅ M3 最终为第六代已承诺
- ✅ M3 最近实际开机合格
- ✅ 丙机由班点策略自动承诺

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

持久状态: /workspace/state/replay-healthy/controller-state.json
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

### 第五代在第 1 班仍按旧合同接受现场操作

`$ python3 -m museum_update --state-dir <state> operate M2 --operation read-archive-card CARD-1042 --by staff-lin`

```text
🛠️ 已登记 M2 现场操作（field-ops-v5）: read-archive-card CARD-1042
```

`$ python3 -m museum_update --state-dir <state> boot M1 --result pass --note 甲机第二次自检：持续健康`

```text
✅ 开机自检回执已记录。
# M1（甲机 / JIA-0001）

- 当前状态: **第六代已接班但未烧桥；后续失败仍可回退第五代** (`gen6_active_uncommitted`)
- 解释: M1 最近实际启动第 6 代且自检合格，但尚未烧桥；第 5 代仍可按条件回退。
- 班点: 第 1 班；距第 3 班停用点还有 2 个计数单位
- 启动槽: active=b, trial=-, rollback=a
  - a: 第5代, status=good, last_result=pass, set=sha256:c7a665b226ace8ac3…
  - b: 第6代, status=good, last_result=pass, set=sha256:c168f6d09fc1ba243…
- 最低可启动代次: 5（单调上升）；回旧版桥: 仍在
- 第 5 代在当前班点是否还能启动: 是
- 连续合格自检: 2
- 有据可查的回退条件:
  - 仅当 a 槽第五代摘要完好、自检为 good、最低代次 <=5，且未到第 3 班停用点时，才允许原地回退。
- 已经见到的失败: 无

## 历次试启动/恢复回执
- R-M1-0001 班0 b槽 第6代 trial=pass (sig 105ef61970e0…)；起点前已见到的甲机第一次试启合格回执
- R-M1-0002 班1 b槽 第6代 boot_self_check=pass (sig 82cc4b7feffb…)；甲机第二次自检：持续健康

## 班次时间线
- 班0 #2 [trial_pass] 甲机第六代第一次试启自检通过，已登记接班；回旧版的桥仍在。
- 班1 #5 [boot_pass] 第 6 代在 b 槽自检通过（连续合格 2 次）。
```

### 甲机在第二次合格回执后烧掉回旧版的桥

`$ python3 -m museum_update --state-dir <state> commit M1 --by maintainer-zhou --reason 两张连续合格真机回执，烧桥时机成熟`

```text
🔥 M1 已作出不可逆承诺，最低代次=6。
# M1（甲机 / JIA-0001）

- 当前状态: **第六代已永久承诺；最低代次已提高，不能回第五代** (`gen6_committed`)
- 解释: M1 最终运行第 6 代；回旧版的桥已烧，最低代次=6。
- 班点: 第 1 班；距第 3 班停用点还有 2 个计数单位
- 启动槽: active=b, trial=-, rollback=a
  - a: 第5代, status=good, last_result=pass, set=sha256:c7a665b226ace8ac3…
  - b: 第6代, status=good, last_result=pass, set=sha256:c168f6d09fc1ba243…
- 最低可启动代次: 6（单调上升）；回旧版桥: 已烧毁/不可逆
- 第 5 代在当前班点是否还能启动: 否
- 连续合格自检: 2
- 有据可查的回退条件:
  - 仅当 a 槽第五代摘要完好、自检为 good、最低代次 <=5，且未到第 3 班停用点时，才允许原地回退。
- 已经见到的失败: 无
- 不可逆承诺记录:
  - 班1 由 maintainer-zhou 执行；5→6；理由: 两张连续合格真机回执，烧桥时机成熟

## 历次试启动/恢复回执
- R-M1-0001 班0 b槽 第6代 trial=pass (sig 105ef61970e0…)；起点前已见到的甲机第一次试启合格回执
- R-M1-0002 班1 b槽 第6代 boot_self_check=pass (sig 82cc4b7feffb…)；甲机第二次自检：持续健康

## 班次时间线
- 班0 #2 [trial_pass] 甲机第六代第一次试启自检通过，已登记接班；回旧版的桥仍在。
- 班1 #5 [boot_pass] 第 6 代在 b 槽自检通过（连续合格 2 次）。
- 班1 #6 [bridge_burned] 不可逆承诺：最低可启动代次 5→6；此后第 5 代启动会被拒绝。
```

### 乙机安装：写槽后掉电

`$ python3 -m museum_update --state-dir <state> install orders/M2-gen6.json --power-loss-after written`

```text
⚡ 模拟掉电发生在持久化阶段 `written` 之后；进度已落盘。
   重新上电后运行: python -m museum_update --state-dir /workspace/state/replay-healthy resume
```

### 乙机上电续跑

`$ python3 -m museum_update --state-dir <state> resume M2`

```text
🔁 已接续 TICKET-M2-G6-001 / M2: awaiting_boot (awaiting_boot)
馆内终端本地更新控制器
逻辑班点: 第 1 班 / 统一停用点: 第 3 班 / 剩余班数: 2

机器号  名称  当前状态  现役槽/代次  试启槽  最低代次  桥  最近实际开机
------  ----  --------  -----------  ------  --------  --  ------------
M1  甲机  第六代已永久承诺；最低代次已提高，不能回第五代  b:6代  -  6  已烧  6代/pass@班1
M2  乙机  第六代已切为试启槽，等待开机自检  a:5代  b  5  未烧  无记录
M3  丙机  第五代运行中（未烧桥，可回退）  a:5代  -  5  未烧  无记录
```

`$ python3 -m museum_update --state-dir <state> boot M2 --result pass --note 乙机第一次试启合格`

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
  - 班1 staff-lin: read-archive-card CARD-1042（field-ops-v5）

## 历次试启动/恢复回执
- R-M2-0003 班1 b槽 第6代 boot_self_check=pass (sig f26716f10532…)；乙机第一次试启合格

## 班次时间线
- 班1 #4 [field_operation] 按旧合同 field-ops-v5 接受现场操作：read-archive-card CARD-1042
- 班1 #7 [install_verified] 安装单 TICKET-M2-G6-001 的字节、代次、组件清单和批准摘要核对通过。
- 班1 #8 [slot_written] 第 6 代已写入 b 槽并逐文件复核；写文件不等于接班。
- 班1 #9 [resume_switch] 掉电恢复：根据持久记录重新选择 b 试启槽。
- 班1 #10 [resume_await_boot] 掉电恢复完成：安装单等待开机自检。
- 班1 #11 [boot_pass] 第 6 代在 b 槽自检通过（连续合格 1 次）。
- 班1 #12 [takeover_registered] 非现役 b 槽开机合格，已登记第 6 代接班；旧 a 槽保留为原地回退。
```

### 进入第 2 班

`$ python3 -m museum_update --state-dir <state> advance-shift --by 1`

```text
🕘 已前进到第 2 班。
馆内终端本地更新控制器
逻辑班点: 第 2 班 / 统一停用点: 第 3 班 / 剩余班数: 1

机器号  名称  当前状态  现役槽/代次  试启槽  最低代次  桥  最近实际开机
------  ----  --------  -----------  ------  --------  --  ------------
M1  甲机  第六代已永久承诺；最低代次已提高，不能回第五代  b:6代  -  6  已烧  6代/pass@班1
M2  乙机  第六代已接班但未烧桥；后续失败仍可回退第五代  b:6代  -  5  未烧  6代/pass@班1
M3  丙机  第五代运行中（未烧桥，可回退）  a:5代  -  5  未烧  无记录
```

`$ python3 -m museum_update --state-dir <state> boot M2 --result pass --note 乙机第二次自检合格`

```text
✅ 开机自检回执已记录。
# M2（乙机 / YI-0002）

- 当前状态: **第六代已接班但未烧桥；后续失败仍可回退第五代** (`gen6_active_uncommitted`)
- 解释: M2 最近实际启动第 6 代且自检合格，但尚未烧桥；第 5 代仍可按条件回退。
- 班点: 第 2 班；距第 3 班停用点还有 1 个计数单位
- 启动槽: active=b, trial=-, rollback=a
  - a: 第5代, status=good, last_result=pass, set=sha256:c7a665b226ace8ac3…
  - b: 第6代, status=good, last_result=pass, set=sha256:c168f6d09fc1ba243…
- 最低可启动代次: 5（单调上升）；回旧版桥: 仍在
- 第 5 代在当前班点是否还能启动: 是
- 连续合格自检: 2
- 有据可查的回退条件:
  - 仅当 a 槽第五代摘要完好、自检为 good、最低代次 <=5，且未到第 3 班停用点时，才允许原地回退。
- 已经见到的失败: 无
- 现场操作:
  - 班1 staff-lin: read-archive-card CARD-1042（field-ops-v5）

## 历次试启动/恢复回执
- R-M2-0003 班1 b槽 第6代 boot_self_check=pass (sig f26716f10532…)；乙机第一次试启合格
- R-M2-0004 班2 b槽 第6代 boot_self_check=pass (sig 432e3e1420f8…)；乙机第二次自检合格

## 班次时间线
- 班1 #4 [field_operation] 按旧合同 field-ops-v5 接受现场操作：read-archive-card CARD-1042
- 班1 #7 [install_verified] 安装单 TICKET-M2-G6-001 的字节、代次、组件清单和批准摘要核对通过。
- 班1 #8 [slot_written] 第 6 代已写入 b 槽并逐文件复核；写文件不等于接班。
- 班1 #9 [resume_switch] 掉电恢复：根据持久记录重新选择 b 试启槽。
- 班1 #10 [resume_await_boot] 掉电恢复完成：安装单等待开机自检。
- 班1 #11 [boot_pass] 第 6 代在 b 槽自检通过（连续合格 1 次）。
- 班1 #12 [takeover_registered] 非现役 b 槽开机合格，已登记第 6 代接班；旧 a 槽保留为原地回退。
- 班2 #14 [boot_pass] 第 6 代在 b 槽自检通过（连续合格 2 次）。
```

`$ python3 -m museum_update --state-dir <state> commit M2 --by maintainer-zhou --reason 第二次连续自检合格`

```text
🔥 M2 已作出不可逆承诺，最低代次=6。
# M2（乙机 / YI-0002）

- 当前状态: **第六代已永久承诺；最低代次已提高，不能回第五代** (`gen6_committed`)
- 解释: M2 最终运行第 6 代；回旧版的桥已烧，最低代次=6。
- 班点: 第 2 班；距第 3 班停用点还有 1 个计数单位
- 启动槽: active=b, trial=-, rollback=a
  - a: 第5代, status=good, last_result=pass, set=sha256:c7a665b226ace8ac3…
  - b: 第6代, status=good, last_result=pass, set=sha256:c168f6d09fc1ba243…
- 最低可启动代次: 6（单调上升）；回旧版桥: 已烧毁/不可逆
- 第 5 代在当前班点是否还能启动: 否
- 连续合格自检: 2
- 有据可查的回退条件:
  - 仅当 a 槽第五代摘要完好、自检为 good、最低代次 <=5，且未到第 3 班停用点时，才允许原地回退。
- 已经见到的失败: 无
- 不可逆承诺记录:
  - 班2 由 maintainer-zhou 执行；5→6；理由: 第二次连续自检合格
- 现场操作:
  - 班1 staff-lin: read-archive-card CARD-1042（field-ops-v5）

## 历次试启动/恢复回执
- R-M2-0003 班1 b槽 第6代 boot_self_check=pass (sig f26716f10532…)；乙机第一次试启合格
- R-M2-0004 班2 b槽 第6代 boot_self_check=pass (sig 432e3e1420f8…)；乙机第二次自检合格

## 班次时间线
- 班1 #4 [field_operation] 按旧合同 field-ops-v5 接受现场操作：read-archive-card CARD-1042
- 班1 #7 [install_verified] 安装单 TICKET-M2-G6-001 的字节、代次、组件清单和批准摘要核对通过。
- 班1 #8 [slot_written] 第 6 代已写入 b 槽并逐文件复核；写文件不等于接班。
- 班1 #9 [resume_switch] 掉电恢复：根据持久记录重新选择 b 试启槽。
- 班1 #10 [resume_await_boot] 掉电恢复完成：安装单等待开机自检。
- 班1 #11 [boot_pass] 第 6 代在 b 槽自检通过（连续合格 1 次）。
- 班1 #12 [takeover_registered] 非现役 b 槽开机合格，已登记第 6 代接班；旧 a 槽保留为原地回退。
- 班2 #14 [boot_pass] 第 6 代在 b 槽自检通过（连续合格 2 次）。
- 班2 #15 [bridge_burned] 不可逆承诺：最低可启动代次 5→6；此后第 5 代启动会被拒绝。
```

### 丙机第 2 班安装

`$ python3 -m museum_update --state-dir <state> install orders/M3-gen6.json`

```text
✅ 安装单 TICKET-M3-G6-001 已完成写槽和切换，当前阶段=awaiting_boot
馆内终端本地更新控制器
逻辑班点: 第 2 班 / 统一停用点: 第 3 班 / 剩余班数: 1

机器号  名称  当前状态  现役槽/代次  试启槽  最低代次  桥  最近实际开机
------  ----  --------  -----------  ------  --------  --  ------------
M1  甲机  第六代已永久承诺；最低代次已提高，不能回第五代  b:6代  -  6  已烧  6代/pass@班1
M2  乙机  第六代已永久承诺；最低代次已提高，不能回第五代  b:6代  -  6  已烧  6代/pass@班2
M3  丙机  第六代已切为试启槽，等待开机自检  a:5代  b  5  未烧  无记录
```

`$ python3 -m museum_update --state-dir <state> boot M3 --result pass --note 丙机第一次试启合格`

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
- R-M3-0005 班2 b槽 第6代 boot_self_check=pass (sig f051098468c2…)；丙机第一次试启合格

## 班次时间线
- 班2 #16 [install_verified] 安装单 TICKET-M3-G6-001 的字节、代次、组件清单和批准摘要核对通过。
- 班2 #17 [slot_written] 第 6 代已写入 b 槽并逐文件复核；写文件不等于接班。
- 班2 #18 [slot_switched] 启动选择器已设为 b 槽一次性试启；尚需开机自检回执。
- 班2 #19 [boot_pass] 第 6 代在 b 槽自检通过（连续合格 1 次）。
- 班2 #20 [takeover_registered] 非现役 b 槽开机合格，已登记第 6 代接班；旧 a 槽保留为原地回退。
```

### 进入第 3 班统一停用点

`$ python3 -m museum_update --state-dir <state> advance-shift --by 1`

```text
🕘 已前进到第 3 班。
馆内终端本地更新控制器
逻辑班点: 第 3 班 / 统一停用点: 第 3 班 / 剩余班数: 0

机器号  名称  当前状态  现役槽/代次  试启槽  最低代次  桥  最近实际开机
------  ----  --------  -----------  ------  --------  --  ------------
M1  甲机  第六代已永久承诺；最低代次已提高，不能回第五代  b:6代  -  6  已烧  6代/pass@班1
M2  乙机  第六代已永久承诺；最低代次已提高，不能回第五代  b:6代  -  6  已烧  6代/pass@班2
M3  丙机  第六代已接班但未烧桥；后续失败仍可回退第五代  b:6代  -  5  未烧  6代/pass@班2
```

`$ python3 -m museum_update --state-dir <state> boot M3 --result pass --note 丙机第二次自检仍健康`

```text
✅ 开机自检回执已记录。
# M3（丙机 / BING-0003）

- 当前状态: **第六代已接班但未烧桥；后续失败仍可回退第五代** (`gen6_active_uncommitted`)
- 解释: M3 最近实际启动第 6 代且自检合格，但尚未烧桥；第 5 代仍可按条件回退。
- 班点: 第 3 班；距第 3 班停用点还有 0 个计数单位
- 启动槽: active=b, trial=-, rollback=a
  - a: 第5代, status=good, last_result=pass, set=sha256:c7a665b226ace8ac3…
  - b: 第6代, status=good, last_result=pass, set=sha256:c168f6d09fc1ba243…
- 最低可启动代次: 5（单调上升）；回旧版桥: 仍在
- 第 5 代在当前班点是否还能启动: 否
- 连续合格自检: 2
- 有据可查的回退条件:
  - 仅当 a 槽第五代摘要完好、自检为 good、最低代次 <=5，且未到第 3 班停用点时，才允许原地回退。
- 已经见到的失败: 无

## 历次试启动/恢复回执
- R-M3-0005 班2 b槽 第6代 boot_self_check=pass (sig f051098468c2…)；丙机第一次试启合格
- R-M3-0006 班3 b槽 第6代 boot_self_check=pass (sig 4e4eac5ca155…)；丙机第二次自检仍健康

## 班次时间线
- 班2 #16 [install_verified] 安装单 TICKET-M3-G6-001 的字节、代次、组件清单和批准摘要核对通过。
- 班2 #17 [slot_written] 第 6 代已写入 b 槽并逐文件复核；写文件不等于接班。
- 班2 #18 [slot_switched] 启动选择器已设为 b 槽一次性试启；尚需开机自检回执。
- 班2 #19 [boot_pass] 第 6 代在 b 槽自检通过（连续合格 1 次）。
- 班2 #20 [takeover_registered] 非现役 b 槽开机合格，已登记第 6 代接班；旧 a 槽保留为原地回退。
- 班3 #22 [boot_pass] 第 6 代在 b 槽自检通过（连续合格 2 次）。
```

### 班点清理：丙机不得一直停在试装，自动承诺第六代

`$ python3 -m museum_update --state-dir <state> reconcile`

```text
- M1: already_committed
- M2: already_committed
- M3: deadline_commit
馆内终端本地更新控制器
逻辑班点: 第 3 班 / 统一停用点: 第 3 班 / 剩余班数: 0

机器号  名称  当前状态  现役槽/代次  试启槽  最低代次  桥  最近实际开机
------  ----  --------  -----------  ------  --------  --  ------------
M1  甲机  第六代已永久承诺；最低代次已提高，不能回第五代  b:6代  -  6  已烧  6代/pass@班1
M2  乙机  第六代已永久承诺；最低代次已提高，不能回第五代  b:6代  -  6  已烧  6代/pass@班2
M3  丙机  第六代已永久承诺；最低代次已提高，不能回第五代  b:6代  -  6  已烧  6代/pass@班3
```

### 可按机器号过滤的全班次记录入口；此处显示全部

`$ python3 -m museum_update --state-dir <state> shifts`

```text
班  序号  机器  结果     代次  事件 / 说明
--  ----  ----  -------  ----  ----------
0   1     -     -              [seed] 初始化同一起点：甲机第六代已通过第一次试启但未烧桥；乙、丙机运行第五代。
0   2     M1    pass     6     [trial_pass] 甲机第六代第一次试启自检通过，已登记接班；回旧版的桥仍在。
1   3     -     -              [shift_advanced] 进入第 1 班。
1   4     M2    -        5     [field_operation] 按旧合同 field-ops-v5 接受现场操作：read-archive-card CARD-1042
1   5     M1    pass     6     [boot_pass] 第 6 代在 b 槽自检通过（连续合格 2 次）。
1   6     M1    commit         [bridge_burned] 不可逆承诺：最低可启动代次 5→6；此后第 5 代启动会被拒绝。
1   7     M2    -              [install_verified] 安装单 TICKET-M2-G6-001 的字节、代次、组件清单和批准摘要核对通过。
1   8     M2    -              [slot_written] 第 6 代已写入 b 槽并逐文件复核；写文件不等于接班。
1   9     M2    -              [resume_switch] 掉电恢复：根据持久记录重新选择 b 试启槽。
1   10    M2    -              [resume_await_boot] 掉电恢复完成：安装单等待开机自检。
1   11    M2    pass     6     [boot_pass] 第 6 代在 b 槽自检通过（连续合格 1 次）。
1   12    M2    -        6     [takeover_registered] 非现役 b 槽开机合格，已登记第 6 代接班；旧 a 槽保留为原地回退。
2   13    -     -              [shift_advanced] 进入第 2 班。
2   14    M2    pass     6     [boot_pass] 第 6 代在 b 槽自检通过（连续合格 2 次）。
2   15    M2    commit         [bridge_burned] 不可逆承诺：最低可启动代次 5→6；此后第 5 代启动会被拒绝。
2   16    M3    -              [install_verified] 安装单 TICKET-M3-G6-001 的字节、代次、组件清单和批准摘要核对通过。
2   17    M3    -              [slot_written] 第 6 代已写入 b 槽并逐文件复核；写文件不等于接班。
2   18    M3    -              [slot_switched] 启动选择器已设为 b 槽一次性试启；尚需开机自检回执。
2   19    M3    pass     6     [boot_pass] 第 6 代在 b 槽自检通过（连续合格 1 次）。
2   20    M3    -        6     [takeover_registered] 非现役 b 槽开机合格，已登记第 6 代接班；旧 a 槽保留为原地回退。
3   21    -     -              [deadline_reached] 第 3 班停用点已到：第 5 代开机将被拒绝；请对合格第六代执行 reconcile/commit，不能让其一直停在试装。
3   22    M3    pass     6     [boot_pass] 第 6 代在 b 槽自检通过（连续合格 2 次）。
3   23    M3    commit         [bridge_burned] 不可逆承诺：最低可启动代次 5→6；此后第 5 代启动会被拒绝。
```

### 每台机器的最终解释和完整记录

`$ python3 -m museum_update --state-dir <state> report`

```text
# 三台模拟机最终记录

```text
馆内终端本地更新控制器
逻辑班点: 第 3 班 / 统一停用点: 第 3 班 / 剩余班数: 0

机器号  名称  当前状态  现役槽/代次  试启槽  最低代次  桥  最近实际开机
------  ----  --------  -----------  ------  --------  --  ------------
M1  甲机  第六代已永久承诺；最低代次已提高，不能回第五代  b:6代  -  6  已烧  6代/pass@班1
M2  乙机  第六代已永久承诺；最低代次已提高，不能回第五代  b:6代  -  6  已烧  6代/pass@班2
M3  丙机  第六代已永久承诺；最低代次已提高，不能回第五代  b:6代  -  6  已烧  6代/pass@班3
```

# M1（甲机 / JIA-0001）

- 当前状态: **第六代已永久承诺；最低代次已提高，不能回第五代** (`gen6_committed`)
- 解释: M1 最终运行第 6 代；回旧版的桥已烧，最低代次=6。
- 班点: 第 3 班；距第 3 班停用点还有 0 个计数单位
- 启动槽: active=b, trial=-, rollback=a
  - a: 第5代, status=good, last_result=pass, set=sha256:c7a665b226ace8ac3…
  - b: 第6代, status=good, last_result=pass, set=sha256:c168f6d09fc1ba243…
- 最低可启动代次: 6（单调上升）；回旧版桥: 已烧毁/不可逆
- 第 5 代在当前班点是否还能启动: 否
- 连续合格自检: 2
- 有据可查的回退条件:
  - 仅当 a 槽第五代摘要完好、自检为 good、最低代次 <=5，且未到第 3 班停用点时，才允许原地回退。
- 已经见到的失败: 无
- 不可逆承诺记录:
  - 班1 由 maintainer-zhou 执行；5→6；理由: 两张连续合格真机回执，烧桥时机成熟

## 历次试启动/恢复回执
- R-M1-0001 班0 b槽 第6代 trial=pass (sig 105ef61970e0…)；起点前已见到的甲机第一次试启合格回执
- R-M1-0002 班1 b槽 第6代 boot_self_check=pass (sig 82cc4b7feffb…)；甲机第二次自检：持续健康

## 班次时间线
- 班0 #2 [trial_pass] 甲机第六代第一次试启自检通过，已登记接班；回旧版的桥仍在。
- 班1 #5 [boot_pass] 第 6 代在 b 槽自检通过（连续合格 2 次）。
- 班1 #6 [bridge_burned] 不可逆承诺：最低可启动代次 5→6；此后第 5 代启动会被拒绝。

# M2（乙机 / YI-0002）

- 当前状态: **第六代已永久承诺；最低代次已提高，不能回第五代** (`gen6_committed`)
- 解释: M2 最终运行第 6 代；回旧版的桥已烧，最低代次=6。
- 班点: 第 3 班；距第 3 班停用点还有 0 个计数单位
- 启动槽: active=b, trial=-, rollback=a
  - a: 第5代, status=good, last_result=pass, set=sha256:c7a665b226ace8ac3…
  - b: 第6代, status=good, last_result=pass, set=sha256:c168f6d09fc1ba243…
- 最低可启动代次: 6（单调上升）；回旧版桥: 已烧毁/不可逆
- 第 5 代在当前班点是否还能启动: 否
- 连续合格自检: 2
- 有据可查的回退条件:
  - 仅当 a 槽第五代摘要完好、自检为 good、最低代次 <=5，且未到第 3 班停用点时，才允许原地回退。
- 已经见到的失败: 无
- 不可逆承诺记录:
  - 班2 由 maintainer-zhou 执行；5→6；理由: 第二次连续自检合格
- 现场操作:
  - 班1 staff-lin: read-archive-card CARD-1042（field-ops-v5）

## 历次试启动/恢复回执
- R-M2-0003 班1 b槽 第6代 boot_self_check=pass (sig f26716f10532…)；乙机第一次试启合格
- R-M2-0004 班2 b槽 第6代 boot_self_check=pass (sig 432e3e1420f8…)；乙机第二次自检合格

## 班次时间线
- 班1 #4 [field_operation] 按旧合同 field-ops-v5 接受现场操作：read-archive-card CARD-1042
- 班1 #7 [install_verified] 安装单 TICKET-M2-G6-001 的字节、代次、组件清单和批准摘要核对通过。
- 班1 #8 [slot_written] 第 6 代已写入 b 槽并逐文件复核；写文件不等于接班。
- 班1 #9 [resume_switch] 掉电恢复：根据持久记录重新选择 b 试启槽。
- 班1 #10 [resume_await_boot] 掉电恢复完成：安装单等待开机自检。
- 班1 #11 [boot_pass] 第 6 代在 b 槽自检通过（连续合格 1 次）。
- 班1 #12 [takeover_registered] 非现役 b 槽开机合格，已登记第 6 代接班；旧 a 槽保留为原地回退。
- 班2 #14 [boot_pass] 第 6 代在 b 槽自检通过（连续合格 2 次）。
- 班2 #15 [bridge_burned] 不可逆承诺：最低可启动代次 5→6；此后第 5 代启动会被拒绝。

# M3（丙机 / BING-0003）

- 当前状态: **第六代已永久承诺；最低代次已提高，不能回第五代** (`gen6_committed`)
- 解释: M3 最终运行第 6 代；回旧版的桥已烧，最低代次=6。
- 班点: 第 3 班；距第 3 班停用点还有 0 个计数单位
- 启动槽: active=b, trial=-, rollback=a
  - a: 第5代, status=good, last_result=pass, set=sha256:c7a665b226ace8ac3…
  - b: 第6代, status=good, last_result=pass, set=sha256:c168f6d09fc1ba243…
- 最低可启动代次: 6（单调上升）；回旧版桥: 已烧毁/不可逆
- 第 5 代在当前班点是否还能启动: 否
- 连续合格自检: 2
- 有据可查的回退条件:
  - 仅当 a 槽第五代摘要完好、自检为 good、最低代次 <=5，且未到第 3 班停用点时，才允许原地回退。
- 已经见到的失败: 无
- 不可逆承诺记录:
  - 班3 由 deadline-policy 执行；5→6；理由: 到班点后，获准且最近自检合格的新系统不得继续停在试装，策略自动作出代次承诺

## 历次试启动/恢复回执
- R-M3-0005 班2 b槽 第6代 boot_self_check=pass (sig f051098468c2…)；丙机第一次试启合格
- R-M3-0006 班3 b槽 第6代 boot_self_check=pass (sig 4e4eac5ca155…)；丙机第二次自检仍健康

## 班次时间线
- 班2 #16 [install_verified] 安装单 TICKET-M3-G6-001 的字节、代次、组件清单和批准摘要核对通过。
- 班2 #17 [slot_written] 第 6 代已写入 b 槽并逐文件复核；写文件不等于接班。
- 班2 #18 [slot_switched] 启动选择器已设为 b 槽一次性试启；尚需开机自检回执。
- 班2 #19 [boot_pass] 第 6 代在 b 槽自检通过（连续合格 1 次）。
- 班2 #20 [takeover_registered] 非现役 b 槽开机合格，已登记第 6 代接班；旧 a 槽保留为原地回退。
- 班3 #22 [boot_pass] 第 6 代在 b 槽自检通过（连续合格 2 次）。
- 班3 #23 [bridge_burned] 不可逆承诺：最低可启动代次 5→6；此后第 5 代启动会被拒绝。

## 全班次记录

```text
班  序号  机器  结果     代次  事件 / 说明
--  ----  ----  -------  ----  ----------
0   1     -     -              [seed] 初始化同一起点：甲机第六代已通过第一次试启但未烧桥；乙、丙机运行第五代。
0   2     M1    pass     6     [trial_pass] 甲机第六代第一次试启自检通过，已登记接班；回旧版的桥仍在。
1   3     -     -              [shift_advanced] 进入第 1 班。
1   4     M2    -        5     [field_operation] 按旧合同 field-ops-v5 接受现场操作：read-archive-card CARD-1042
1   5     M1    pass     6     [boot_pass] 第 6 代在 b 槽自检通过（连续合格 2 次）。
1   6     M1    commit         [bridge_burned] 不可逆承诺：最低可启动代次 5→6；此后第 5 代启动会被拒绝。
1   7     M2    -              [install_verified] 安装单 TICKET-M2-G6-001 的字节、代次、组件清单和批准摘要核对通过。
1   8     M2    -              [slot_written] 第 6 代已写入 b 槽并逐文件复核；写文件不等于接班。
1   9     M2    -              [resume_switch] 掉电恢复：根据持久记录重新选择 b 试启槽。
1   10    M2    -              [resume_await_boot] 掉电恢复完成：安装单等待开机自检。
1   11    M2    pass     6     [boot_pass] 第 6 代在 b 槽自检通过（连续合格 1 次）。
1   12    M2    -        6     [takeover_registered] 非现役 b 槽开机合格，已登记第 6 代接班；旧 a 槽保留为原地回退。
2   13    -     -              [shift_advanced] 进入第 2 班。
2   14    M2    pass     6     [boot_pass] 第 6 代在 b 槽自检通过（连续合格 2 次）。
2   15    M2    commit         [bridge_burned] 不可逆承诺：最低可启动代次 5→6；此后第 5 代启动会被拒绝。
2   16    M3    -              [install_verified] 安装单 TICKET-M3-G6-001 的字节、代次、组件清单和批准摘要核对通过。
2   17    M3    -              [slot_written] 第 6 代已写入 b 槽并逐文件复核；写文件不等于接班。
2   18    M3    -              [slot_switched] 启动选择器已设为 b 槽一次性试启；尚需开机自检回执。
2   19    M3    pass     6     [boot_pass] 第 6 代在 b 槽自检通过（连续合格 1 次）。
2   20    M3    -        6     [takeover_registered] 非现役 b 槽开机合格，已登记第 6 代接班；旧 a 槽保留为原地回退。
3   21    -     -              [deadline_reached] 第 3 班停用点已到：第 5 代开机将被拒绝；请对合格第六代执行 reconcile/commit，不能让其一直停在试装。
3   22    M3    pass     6     [boot_pass] 第 6 代在 b 槽自检通过（连续合格 2 次）。
3   23    M3    commit         [bridge_burned] 不可逆承诺：最低可启动代次 5→6；此后第 5 代启动会被拒绝。
```
```
