# 离线染料留样库（虚构配方 + 模拟温度）

纯 Python 标准库实现，无网络、无第三方依赖。所有染料、批号、温度曲线与文书
均为虚构，仅用于演示“温度记录断段时，哪些样管还能作为外地复验基准”的完整判定链。

## 运行

```bash
python3 -m dye_archive.run_demo      # 端到端演示，落 demo_result.json
python3 -m unittest discover -s tests
```

## 规则到代码的映射

| 题设要求 | 实现位置 |
| --- | --- |
| 已封原读数、参考浴校正、失准公告、移架/开门/过秤/跨柜事件 | `archive.build_world` |
| 不可拆换封存件、收件人重算哈希链、来源绑定（管/时段/柜）核验 | `sealing.py`、`lab.independently_verify` |
| 真实回单贴给另一管/另一时段不算有效履历 | `T-LOG-04` 实绑 T-07；`verify_provenance` 判否 |
| 同层参考 + 允许温变速率夹出缺口逐小时上下限 | `archive.hourly_envelope`（`gap-clamp`/`oot-clamp`） |
| 跨架关联不确定度、开门/过秤局部余量 | `CROSS_SHELF_U`、`EVENT_U` |
| 对该管最不利暴露（逐小时上限，5°C 等效小时）与预算 | `worst_exposure`、`BUDGET_EQH` |
| 路线 A（随管封存小组）/ 路线 B（逐管逐段重建）取舍 | `application.build_application`，本演示选 B |
| 正常申请须用两支可领留样 | 基线仅 T-01、T-02 可领，申请 `APP-2026-0042` |
| 实验室自定色差限、步长、剩余体积、共享基液 | `lab.ExternalLab`、`RecheckOrder` |
| 按最坏允许变化实际调模拟色样，独立比色台自持色卡 | `run_order` 出 nominal + worst-case 两张色样 |
| 任一合法履历取值都兑现已签色差 | 两张色样均按 `DELTA_E_LIMIT = 1.5` 验收 |
| 缩窄一个参考浴更正限使一组可领 | `apply_narrow_bath`：T-03 由不可领变可领 |
| 还原履历、单换一支分液器步长 | `restore_t04_receipt`；B 步长 0.02 → 0.14 mL |
| 两张共用基液复验单按实际混色与耗量分别办结 | `base_ledger` 逐笔记账，RV-101 过、RV-102 拒收 |

## 五管基线结论（宽校正窗 ±0.80°C，预算 295 等效小时）

- **T-01（A）/ T-02（B）：可领**，履历完整，外部实验室据此正常申请。
- **T-03（C）：不可领**，P3 缺口 30h 重建后 297.3 等效小时超限；缩窄参考浴
  至 ±0.10°C 后降到 293.6，转为可领。
- **T-04（A）：不可领**，跨柜回单误贴 T-07 的真实回单；还原正确回单后核源通过，
  但运输段最不利温度仍使其超过热预算，故仍不可领（核源与暴露是两道独立关）。
- **T-05（C）：不可领**，同时经过 P3 缺口与 P4 失准段，重建 70h 后显著超预算。

## 关键虚构参数

`archive.py`：240h 模拟、四层等温偏差、`RATE_LIMIT=0.35°C/h`、
`CROSS_SHELF_U=0.60°C`、`BUDGET_EQH=295`。
`lab.py`：`STRENGTH_TAU_EQH=1200`、色差限 `ΔE00 ≤ 1.5`、基液 50mL/色样。
