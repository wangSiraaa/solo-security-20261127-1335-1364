# 合成教室隔音验收与排课交付（sonic_lab）

两间教学音频实验室的**本地隔音验收**与**并班排课**工具。全部离线、全合成：
不使用真人声、不采集真实房间；房间、听席、活动隔板、隔音员测试带、合同与
声学噪声都由代码建模，合同最终**在两班真实渲染出的波形上核验**。

## 规格 → 实现映射

| 需求描述 | 实现 |
|---|---|
| 两间实验室、四个指定听席 | `model.SEATS`（A1/A2/B1/B2，每间一个好席 + 一个近板席） |
| 两扇活动隔板、合法位置 | `model.LEGAL_CONFIGS`：全封 / 开一扇；`open,open` 非法（抛错） |
| 六个频带的串音上界 | `estimate.py`：测试带 → `iso_lower_bounds`（保守下界） |
| 校准偏差 | `RESP_ERROR_DB`、`BIAS_FIXED_DB`（离线）/`BIAS_ADAPTIVE_DB`（现场）；下界覆盖另一组响应 |
| 回声拖尾 | 抽头列表 `TAIL_SHORT`/`TAIL_LONG`；上界把拖尾按功率加回并加余量 |
| 本班可听 / 邻班不可辨 | `schedule.measure_seat`：OWN（私有音 vs 邻班掩蔽泄漏+噪声）与 LEAK（邻班私有泄漏 vs 本班掩蔽+噪声） |
| 两套公共相同、私有不同课件 | `schedule.design_courseware`：同一 `mask_template`，不同 `priv_band`/私有拍 |
| 单音乐型（合成音节） | 倍频程带中心单音；公共掩蔽和弦每带一音；`signals.tone` |
| 独立房间模拟器、另一组误差 | `simulator.render` 固定用响应集 `SET_B`（测量用 `SET_A`） |
| 安排信号及休止、出两条音轨 | `pipeline.render_tracks` → `track_A.wav`/`track_B.wav` |
| 固定排班（脱网并班） | `plan_lesson("fixed")`：仅密封双板 + 留 1 dB 不确定余量，只接宽余量课 |
| 现场核定（窄余量、不交新课录音） | `plan_lesson("onsite")`：可开一扇板、用更紧的现场偏差，接窄余量课；只播测试信号 |
| 改一块板拖尾 → 早班多隔一拍 | `pipeline.perturb_tail`：私有拍间隔 2→3 拍 |
| 换第二班听席 → 原时段/音轨分别核定 | `pipeline.perturb_seat`：同一布局在新席重核，音轨是否可交分别判定 |

## 关键物理

- **异频带不互掩蔽**：两班私有音用不同频带，互不干扰；真正决定合同的是
  「邻班私有泄漏(其频带) vs 本班公共掩蔽和弦(同频带+噪声)」——隔离度阈值约
  15 dB，与数值探针一致。
- **本班信噪比**：本班在私有拍上放私有音（不再是自己的掩蔽和弦），干扰仅来自
  邻班掩蔽泄漏（异频带，很小）+ 噪声，故通常很宽。
- **拖尾**：每拍发射的能耗漏入后续拍。私有拍必须落在对方的掩蔽拍上（含拖尾到达），
  且本班私有拍间距要大于最长拖尾 → 拖尾变长即「多隔一拍」。

## 运行

```bash
python3 -m unittest discover -s tests -v   # 15 项单元测试
python3 run_demo.py                        # 生成 out/report.json + 4 条 wav
```

`run_demo.py` 输出：
- `out/fixed/track_{A,B}.wav`  固定交付（宽余量课）
- `out/onsite/track_{A,B}.wav` 现场核定（窄余量低频课）
- `out/report.json`            两种模式 + 两类扰动复验的完整数值

## 目录

```
sonic_lab/model.py       固定配置：频带/听席/隔板/电平/合同/误差/Excitement 与交错规则
sonic_lab/signals.py     合成、WAV 读写、均方功率、dB 与占空比标定
sonic_lab/estimate.py    测试带 → 逐频带逐席串音上界（校准偏差 + 拖尾）
sonic_lab/simulator.py   独立房间模拟器（响应集 B）→ 四席波形，分带分轨
sonic_lab/schedule.py    合同测量、上界预测、排课求解、课件自动设计
sonic_lab/pipeline.py    两种交付模式、扰动复验、音轨与报告
run_demo.py              演示驱动
tests/test_sonic_lab.py  单元测试
```
