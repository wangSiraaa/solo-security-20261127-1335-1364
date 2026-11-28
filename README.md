# 共享光纤离线教具（Shared Fiber Offline Teaching Aid）

一条**完全离线**、无真实设备、仅依赖 Python3 标准库的共享光路调度教具。
档案室在约定短窗收取两份带校验的目录页，访客站租用剩余时段；系统必须在
**回波有歧义、单端测不到各段损耗**的前提下，只凭真正收到过的区间观测，
推出仍可能的端到端损耗组合，并据此离线调度、核验内容/回执/逾窗。

## 一、题目约束 → 教具落点对照

| 题目要素 | 教具实现 | 关键文件 |
| --- | --- | --- |
| 分支、不同长度接头、同距离格回波 | 虚构 PON 拓扑，OTDR 0.5km 距离盒，接头 j1–j4 损耗为未知量 | `src/plant.py` |
| 单端向量不到每段损失 | 回波只以两两求和出现（x2+x4、x2+x3），独立约束秩 2 < 未知量 3 | `src/plant.py: otamar_bins` |
| 两端巡测回波区间、分支切换记录、校正参考件 | 区间型观测 + 切换日志 + 参考件一致性/跨端相交核验 | `tools/make_observations.py`、`src/patrol_analysis.py` |
| 据真实观测推哪些端到端损耗组合仍可能 | 分数坐标凸多面体，自研两阶段单纯形求范围、枚举顶点 | `src/fractional_lp.py`、`src/polytope.py` |
| 同一接头误差约束多条光路，交接入台核用 | 共享变量半平面导出，接入台独立重建并核对范围 | `outputs/handoff_constraints.json`、`src/verify.py: handoff_cross_check` |
| 有限发射档（不可无限增大功率） | 固定 5 档 `[-6,-2,2,6,10] dBm`，无档可配即拒绝 | `src/plant.py: TX_LEVELS_DBM` |
| 连续发送后的接收恢复时间 | 半双工模型，发送后需 `recovery_slots` 空闲格才能接收 | `src/scheduler.py` |
| 买下的访客基础窗口 | 时隙 9–18，边缘时隙 10/11 有条件借出 | `src/scheduler.py` |
| 全空闲/全拒/无限功率都不能交差 | 自适应方案在容量、拒绝、有限档之间取平衡，压力请求显式拒绝 | `src/scheduler.py`、`tests/test_end_to_end.py` |
| 两方案，选一路 | 实现 conservative 与 adaptive，**选定 adaptive 并签发** | 下文“方案选择” |
| 按真实允许损耗与前一帧强度发送 | 数据帧按可行域最差档，回执按前一帧实测 RSSI 闭环降档 | `src/driver.py` |
| 缓冲区重建目录页/样件、回执、逾窗核实 | CRC32 分片重组 + SHA256 清单核对 + 截止期审计 | `src/framing.py`、`src/driver.py` |
| 反事实一：换一笔回波所属分支 | 叠合回波改挂 solo，边缘时隙收回，承诺件仍按时 | `src/training.py: reassign_echo_branch` |
| 反事实二：仅换慢恢复接收器 | r=2 顶掉 ACK 窗强制留空并登记逾窗，目录页仍按时 | `inputs/stage2_receivers_slow.json` |

## 二、两阶段与门禁

教具严格分两阶段，体现“**签发维护依据后，接入台才收到接收器与收件单**”：

1. **维护阶段（只吃区间，不碰真值）**
   `patrol_analysis.py` 仅读取 `inputs/*observations/switch/reference/topology`，
   把每条区间翻成多面体半平面，输出维护依据。
2. **接入台阶段（门禁）**
   `verify.py` 启动即检查 `outputs/maintenance_basis.json` 是否存在，
   缺失则 `SystemExit("门禁失败…")`（见 `tests/test_end_to_end.py: test_gate_blocks_without_basis`）。

真值隔离：`inputs/ground_truth.json` 只被观测生成器与物理层 `PhysicalPlant` 读取，
分析/调度/驱动只能消费区间与实测 RSSI。

## 三、方案选择（题目要求“选一路”）

- **conservative（保守离线档）**：按巡测最差损耗预留固定功率档与守卫间隔，
  全程离线可执行，但边缘时隙不借出，访客容量 = 1 个发送时隙，访客档 `-2 dBm`。
- **adaptive（自适应，本教具选定签发）**：先保档案室保底时隙（绝不被借出），
  在接收窗发一个短训练脉冲（时隙 8）收窄整体损耗范围，再在“回波归属签名一致 +
  档位覆盖收窄后最差损耗”两条件下借出边缘时隙 10/11。访客容量升到 3，
  访客档降到 `-6 dBm`；回波仍有歧义（签名不符）时强制收回边缘、保住先前承诺。

选定理由：在不牺牲档案室两份目录页保底、不突破有限发射档与恢复时间的前提下，
自适应方案相对保守方案多容纳 2 个访客发送时隙并降低发射功率，且保留了
“歧义未消即收回”的安全回退。两方案结果都在 `outputs/verification_report.json` 可对照。

## 四、目录结构

```
inputs/                         离线输入（观测、拓扑、接收器、收件单、隐藏真值）
src/
  fractional_lp.py              分数两阶段单纯形（Bland 防循环）
  polytope.py                   区间约束多面体：范围/顶点/二维凸包/约束导出
  plant.py                      虚构光路、距离格、发射档、接收器
  patrol_analysis.py            维护分析 → 维护依据（阶段一）
  training.py                   训练脉冲收束 + 回波改挂反事实
  framing.py                    CRC32 分帧、重组缓冲、SHA256 清单
  scheduler.py                  半双工时隙调度（两方案）
  driver.py                     物理层仿真 + 双独立驱动 + 闭环 ACK 档
  verify.py                     接入台总编排与核验（阶段二）
tools/make_observations.py      由虚构真值生成区间观测
tests/                          数值核心自测 + 端到端回归（含门禁测试）
outputs/                        维护依据、凸包、移交约束、载荷、核验报告
run_all.sh                      一键离线复跑
```

## 五、运行

```bash
./run_all.sh
# 或分步
python3 tools/make_observations.py
python3 src/patrol_analysis.py
python3 src/verify.py
python3 tests/test_lp_core.py
python3 tests/test_end_to_end.py
```

## 六、关键结论（一次复跑的代表性输出）

- 接头歧义域非退化，例如 `x2 ∈ [0, 0.85] dB`、`x3 ∈ [0, 1.10] dB`；
  端到端 `(P1,P2)` 是 6 顶点凸包而非单点。
- 自适应 vs 保守：访客容量 3 vs 1，访客档 `-6` vs `-2 dBm`，目录页均按时、清单全通过。
- 回波改挂：边缘时隙 10/11 被收回，容量回落到 1，档案室承诺不受影响。
- 慢恢复接收器：访客 ACK 窗（时隙 15）强制留空并登记逾窗，两份目录页与访客数据帧仍按时送达。
- 压力请求：无满足恢复时间与截止期的空闲时隙，被显式拒绝（而非全拒或抬高功率）。

所有数值经 `tests/test_lp_core.py` 的 2000 个随机可行 LP（可行性、目标值与解严格
一致、随机可行点最优性）与顶点/范围交叉一致性校验。
