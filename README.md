# 离线整改台（虚构安全公告 1359）

零第三方依赖的 Python 标准库实现。所有部件号、版本、人员、PID、单据均为纸面虚构。

## 结论

在随仓库附带的三台机器确定性重演中：

| 机号 | 角色 | 当前结论 |
|---|---|---|
| **`TERM-YI`（乙桌）** | 批读 + 单读均有旧 `DCP-710 1.8.4`，今晚获得唯一换件窗口 | **能取得并持有有效的最终整改结单 `CL-YI-FINAL`**。新清点回执证明两个旧 PID 已退出，新 PID 运行 `2.0.1-fictional`。 |
| `TERM-JIA`（甲桌） | 同样有批读/单读旧件；已转人工并停用两个调用功能 | 昨天的临时结单 `CL-JIA-TMP-YDAY` 仍真实有效，范围只到 `SHIFT-2-FRI-PM`，且只承诺今晚人工最多完成 20 件。它还不是无条件最终结单；补一张覆盖两个实例的持久/可靠停用回执后，可按 `FUNCTIONS_DISABLED` 直接最终结单。 |
| `TERM-BING`（丙机） | 旧单读组件、不能停办、原有备用机 | 曾仅凭新现场清点直接结单；回退到旧进程/旧版本后，旧结单作为不可变纸面记录保留，但当前不再有效，工单重新打开。备用机 `SPARE-BING-1` 外借后，不能再许诺“不停业换件”。 |

因此，**按“撤回旧公告并由订正后的 R2 接替”的完整公告订正口径，当前能收到有效无条件最终整改结单的是乙桌 `TERM-YI`。**

如果“撤回旧公告”是指**只撤回、没有任何生效的订正公告**，则严格地说：任何终端都不能新开立“公告整改结单”；只能保留真实维修记录，或开立 `WITHDRAWN_BULLETIN` 公告撤回核销单。已有结单仍是真实历史凭证，但不再是当前生效公告下的整改结论。

## 运行

```bash
python3 -m remediation_desk.seed     # 重新生成 data/desk.json
python3 -m remediation_desk answer   # 查看结论
python3 -m remediation_desk board    # 工程师待办和所需新证据
python3 -m remediation_desk ticket TERM-YI
./desk show TERM-BING                # 等价包装脚本
python3 -m unittest discover -s tests -v
```

默认数据文件是 `data/desk.json`。多数命令也可用 `--data /tmp/other.json` 指定其他纸面台账；全局参数要放在子命令前：

```bash
python3 -m remediation_desk --data /tmp/desk.json show TERM-JIA
```

## 已实现的能力

- **公告订正**：公告按 `code + revision` 追加；旧版本可 `WITHDRAWN` 或 `SUPERSEDED`，不会就地改写历史。版本区间支持 `>=`、`<`、`<=`、`==` 等约束。
- **适用范围**：按部件号、受影响版本区间、功能启用条件匹配。整机镜像版本很新，也不会掩盖旧子部件。
- **终端清单上报**：清单是追加的不可变报告；可记录部件安装版本、功能开关、PID、运行版本和 `RUNNING/EXITED` 状态。
- **运行进程切换校验**：装了修正包但旧 PID/旧运行副本仍在运行时，换件路径不成立。必须证明旧 PID 退出，并有运行修正版的新进程清点回执。
- **维修回执**：记录换件、版本变化、覆盖的部件槽位和纸面回执号。
- **两种供应商认可的整改路径**：
  1. `REPLACED_NEW_PROCESS`：修正部件 + 旧进程退出 + 新进程清点；维修回执不是必需，合格的新清单本身可以作为证据。
  2. `FUNCTIONS_DISABLED`：持久、可靠地停用所有调用旧件的功能，旧进程退出，并有签署/锁定性质的停用回执。
- **临时控制结单**：有时限、容量边界和不依赖替班的范围说明；替班报告变化只追加新报告，不重写旧结单。
- **按机号查单**：`show` / `ticket` 输出整机版本、每个受影响实例、当前状态、解除待办所需证据、结单及当前效力。
- **备用资源事件**：备用机预留/外借只影响“不停业换件”的承诺，不会被误算成目标机已经整改。
- **纯标准库 JSON 存储**：适合离线环境和测试复制。

## 常用写入命令

```bash
# 上报一份完整清单（JSON 中给 components 数组，或 {"components": [...]}）
python3 -m remediation_desk inventory TERM-JIA --file /tmp/inv.json

# 维修回执
python3 -m remediation_desk receipt TERM-YI \
  --receipt-id RR-1 --part DCP-710 \
  --from-version 1.8.4 --to-version 2.0.1-fictional \
  --uid YI-batch --uid YI-single

# 持久停用回执；临时控制加 --temporary --until SHIFT-2-FRI-PM
python3 -m remediation_desk disable TERM-JIA \
  --uid JIA-batch --uid JIA-single \
  --function BATCH_READ --function SINGLE_READ

# 人员/备用机报告均为追加事件
python3 -m remediation_desk staffing --desk DESK-YI --shift SHIFT-1-FRI-AM --unavailable
python3 -m remediation_desk resource SPARE-BING-1 --event LOANED_OUT \
  --linked-machine TERM-OTHER-9 --available false

# 证据足够时结单
python3 -m remediation_desk close TERM-JIA --kind final --route FUNCTIONS_DISABLED
python3 -m remediation_desk close TERM-JIA --kind temporary

# 撤回公告，或用 JSON 追加订正公告
python3 -m remediation_desk withdraw ADV-DCP710-R2 --reason "supplier recall"
python3 -m remediation_desk correct-advisory --file examples/correct-advisory.json
```

## 今晚窗口和班次承诺

确定性排班结论由 `remediation_desk/scheduling.py` 生成，也存于 `data/desk.json` 的 `plans`：

1. **唯一换件窗口给乙桌 `TERM-YI`**：明早 10 件已约，当前无可靠人工代录；不停业/不换件无法满足明早正常开台。换件后必须拿到 `INV-YI-NEW-PROCESS` 才承诺明早 normal service。
2. **甲桌 `TERM-JIA` 先做功能性停用并转人工**：它正在暴露中，但有安全人工通道；批读和单读两个调用方都必须停用，旧 PID 4101/4102 必须退出。
3. **承诺边界**：甲桌今晚只能承诺人工完成 20 件，剩余 20 件结转；不能承诺今晚清完 40 件。临时控制持续到 `SHIFT-2-FRI-PM`，下一个换件窗口 `WINDOW-NEXT-AFTER-TWO-SHIFTS` 后再恢复完整功能或最终结单。甲桌明天休息，因此不对外承诺明早 scanner service。
4. **替班报告变化**：追加新的排班报告并重算当前能力；昨天的 `CL-JIA-TMP-YDAY` 因明确写了 `staffing_dependency=NONE_REQUIRED` 和容量上限，不会因替班消息翻转而被改写或夸大。未经确认的反复报告不足以移动今晚已经按硬约束做出的窗口决定。
5. **底线**：任何一桌照常开放时，要么有当前有效的最终结单，要么所有受影响调用功能保持可靠停用且旧进程已退出。

## 丙机的状态迁移

测试和种子数据完整覆盖题目要求的三段：

1. 有备用机且不能停办 → 可开立 `NO_DOWNTIME_REPLACE` 工单/承诺。
2. 新的现场清单证明旧 PID `6101 EXITED`、新 PID `6201` 运行 `2.0.0-fictional` → 即使没有单独维修回执，也直接开立过 `CL-BING-BRIEF-FINAL`。
3. 后续清单显示旧版本 `1.9.4` 和旧运行副本 PID `6301 RUNNING`，且备用机外借 → 工单重开，旧结单当前无效，`promise.status=IMPOSSIBLE`。借备用机给别人不是整改，也不能继续承诺不停业换件。

## 代码结构

- `remediation_desk/versions.py`：虚构版本和区间。
- `remediation_desk/models.py`：枚举、JSON 存储默认值。
- `remediation_desk/desk.py`：写入侧/仓储操作。
- `remediation_desk/rules.py`：公告匹配、实例同步、结单资格、结单效力核验。
- `remediation_desk/scheduling.py`：今晚窗口和临时停用决策。
- `remediation_desk/views.py`：按机号查单和工程师看板。
- `remediation_desk/seed.py`：三台机器的完整纸面重演。
- `main.py` / `desk`：根目录入口和 shell 包装脚本。
- `tests/test_desk.py`：14 个标准库单元测试。
