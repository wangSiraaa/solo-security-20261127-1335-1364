# 复算室独立复算与签认报告

## 一、申请事项
两本界面相同的分析笔记（NB-A、NB-B）显示同一张南区月度趋势图，
复算室在独立环境实际重算两个图，并按正式勘误 ER-2026-001（仅修正
electricity_kwh 一个同名列的计量单位：kWh→Wh，同属 energy 维度）重算。

## 二、第一轮（作者机屏上结果）
- 两本笔记产出 trend_chart.svg 内容哈希一致、面板同高：True
- 但‘屏上恰好得到’不等于‘可从所交资料重现’，须分别审查执行链。

## 三、封存与独立复算结论
- NB-A：SEALED：形成所选图表所需的最小可重演执行链完整；随机临时目录洁净重跑（工作目录 recompute_b5dev70a）成功，全部输入哈希与封存记录匹配，signed=True。
- NB-B：NOT_SEALABLE：存在未随交的依赖，洁净重跑必然断链；c8 装入 tmp/last_round_monthly.csv，
  该上一轮汇总未随资料提交，子进程洁净重跑断链：FileNotFoundError — LOAD 失败：tmp/last_round_monthly.csv 不在所交资料内（疑为借用旧内存/临时文件的单元）。
  复算室无法从所交资料重现其图，signed=False，**无证签认**。

## 四、勘误后两图应当分开
- out/region_summary.csv: 第一轮=96c2a69915a8ab54 第二轮=5b2daa89e89aa211 changed=True
- out/trend_chart.svg: 第一轮=c488edbc4f1e5a63 第二轮=e408af2e12fccc69 changed=True
- out/trend_table.csv: 第一轮=0405929a8179e4bc 第二轮=74932a513657fe58 changed=True

## 五、只换一个栏目口径，另一序列单独保留
- water_m3: 允许局部沿用（证据哈希 bae44c07e23509e2）
- 用电 electricity_kwh：按现行口径重算；用水 water_m3：口径未变，
  凭第一轮封存哈希核对一致后局部沿用，并已另存 water_series_retained.csv。

## 六、最终答复：能签认的是哪一次计算？
**只有 NB-A 在复算室随机临时目录中、按勘误后现行口径完成的洁净重算可签认。**
NB-B 第一轮屏上图像虽与 A 同高同哈希，但其关键单元借用未随交的上一轮
内存文件，所交资料不足以重现，不予签认；其补救版（c8 改为自生成中间表）
作为新提交重跑通过 signed=True，不回溯豁免原提交。