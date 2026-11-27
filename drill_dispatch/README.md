# 值班发件安全演练（可重放）

把一次真实业务记录带进演练室重放，而**不是**再次处置实物的授权。
生产域与演练域各用一个本地目录模拟，外部保管柜/复检站被实现为
带独立序号、不可倒退计数与哈希链台账的“小程序”。

## 运行

```bash
# 端到端演练（38 条断言，覆盖题面全部场景）
python3 drill_dispatch/demo.py

# 安全不变量单元测试
python3 -m unittest drill_dispatch.tests.test_safety -v

# 验收员 CLI
python3 -m drill_dispatch.cli --root drill_dispatch/runtime counters
python3 -m drill_dispatch.cli --root drill_dispatch/runtime cabinets
python3 -m drill_dispatch.cli --root drill_dispatch/runtime service prod vault-main
python3 -m drill_dispatch.cli --root drill_dispatch/runtime job drill_dispatch/runtime/drill/jobs JOB-D1
python3 -m drill_dispatch.cli --root drill_dispatch/runtime verify-chain drill_dispatch/runtime/prod/jobs JOB-P3
python3 -m drill_dispatch.cli --root drill_dispatch/runtime set-fault <job> take timeout-once
python3 -m drill_dispatch.cli --root drill_dispatch/runtime evidence
```

## 架构决策（题面二选一的取舍）

采用 **方案二：演练域替身柜**，并保留方案一的因果日志作为崩溃恢复基础。

- **切域点**：作业进程调用外部小程序的**第一道交接之前**。
  `Router._resolve` 按作业域解析服务，演练作业只在 `runtime/drill/`
  下找替身柜，物理上拿不到 `runtime/prod/` 的句柄。
- **替身柜来源**：正式柜作业前快照（`baseline_snapshot.json`），
  替身柜以快照哈希为创世锚点，序号 `seq` 与已发件计数 `dispatched`
  从 0 独立起算，在本域真实执行取/退动作。
- **正式出口切断**：`prod/SEALED` 封存标志存在期间，任何正式域调用
  fail-closed；演练域引用未登记的服务名（缓存里残留的正式柜名）
  同样 fail-closed，不存在“悄悄落到正式柜”的路径。
- **历史回条**：复制进 `runtime/evidence/`，文件 `0444` 只读，
  附 SHA-256 清单；角色是历史见证，不参与任何新作业的初态判定。

### 缺回执怎么处理

1. 每次外部调用前先 fsync 落 `INTENT`（含确定性幂等键、意图摘要），
   回调/错误后落 `OBSERVATION`；
2. 超时只表示“结果未知”，重试沿用**原幂等键**，服务端命中幂等
   直接返回原回条，序号与计数不动；
3. 重试有界（3 次），耗尽后置 `UNKNOWN` 停手，悬置 INTENT 保留，
   由人工查台账或后续恢复流程按幂等键重查——禁止把未知当失败重发；
4. 崩溃重启时 `resume_after_crash` 捡起无配对 OBSERVATION 的 INTENT，
   严格沿用原请求重放。

### 缓存里的正式柜名怎么处理

演练工单仍写正式服务名（贴近真实缓存），由 Router 的
`domain_aliases` 显式映射到替身名；映射缺失即拒绝，
杜绝“名字一样就打到正式柜”的隐式行为。

## 为什么正式发件柜绝不能多吐一张单

一张回条就是一次实物领出的法律/业务凭证，发件计数是不可倒退的事实。
多吐一张会造成：同一物件两次“合法出库”、账实不符、领用责任无法追溯。
本系统用三重机制保证至多一次真实发件：

1. **服务端幂等键**：重放请求在业务校验之前先查幂等，命中即回原回条；
2. **不可倒退计数**：`dispatched` 只在新取件落账时 +1，回退/重放均不动；
3. **因果日志 + 哈希链**：每条 INTENT/OBSERVATION 与台账记录可验链，
   任何重复发件或事后篡改都会在验链/证据核验中暴露。

## 目录结构

```
runtime/
├── prod/                 # 正式域（演练期 SEALED，只读）
│   ├── vault-main/       # 正式保管柜：state.json + ledger.jsonl + receipts/
│   ├── reinspect-1/      # 正式复检站
│   ├── jobs/<JOB>/       # 作业因果日志 journal.jsonl、领物条 slip.json
│   └── baseline_snapshot.json
├── drill/                # 演练域：替身柜/替身站，独立序号与计数
│   ├── shadow-vault-N/
│   ├── shadow-reinspect/
│   └── jobs/<JOB>/
├── evidence/             # 只读历史回条 + 哈希清单 manifest.json
├── fault_policy.json     # 故障注入策略（可随时调整模拟返回）
└── drill_conclusion.md   # 给操作员的演练结论（含可验证范围）
```

## 模块

| 文件 | 职责 |
| --- | --- |
| `codec.py` | 规范化哈希、原子写、JSONL 追加、哈希链原语 |
| `external.py` | 保管柜/复检站：序号、幂等回条、不可倒退计数、台账验链 |
| `journal.py` | 作业因果日志（INTENT/OBSERVATION/LIFECYCLE）与领物条 |
| `router.py` | 域守卫、服务名映射、故障注入（超时/崩溃）、fail-closed |
| `runner.py` | 取件→复检→签条执行器，超时重试与崩溃恢复 |
| `drillmaster.py` | 封存、快照替身柜、证据封存、改单初态声明校验 |
| `inspector.py` | 验收员查询、验链、结论出单 |
| `cli.py` | 命令行入口 |
| `demo.py` | 端到端演练脚本 |
| `tests/test_safety.py` | 8 项安全不变量测试 |
