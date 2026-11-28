# 可重演笔记本：记录端 + 复算室独立复算（纯标准库，无第三方依赖）

回答的问题：**两本界面相同、显示同一张趋势图的分析笔记，复算室能签认哪一次计算？**
结论见 `archive/SIGNOFF_REPORT.md`：只有 **NB-A 在复算室随机临时目录中、按勘误后
现行口径完成的洁净重算** 可签认；NB-B 借用未随交的上一轮汇总，所交资料不足以重现，
**无证签认**（其补救版作为新提交通过，不回溯豁免原提交）。

## 目录

```
rechain/                 小型数据运算语言 + 表引擎 + 封存 + 复算（纯 Python 标准库）
  table.py               带单位纵列的表：过滤/分组/汇总/局部更新/连接/维度检查/单位换算
  lang.py                一行一格的小语言解析器 + ast 白名单安全表达式求值
  engine.py              单元执行器：相对路径、同名变量覆写留痕、输入版本与执行次序清单
  charts.py              无依赖 SVG 折线图（面板同高）+ 终端 ASCII 对照图
  sealing.py             最小可重演执行链封存（只封依据，不封旧产物/旧内存）
  recompute.py           随机临时目录 + 最小环境子进程独立复算与签认核对
  runner.py              独立环境内的洁净重跑 CLI
  demo.py                合成资料、两本同界面笔记、正式勘误、栏目单的构造器
tests/test_rechain.py    12 项单元/端到端测试
notebooks/               两本界面相同的笔记本 JSON（仅 c8 一格不同）
data/raw/                带版本和单位声明的合成原始表（CSV，# 元信息头）
archive/                 封存包、两轮清单、勘误、栏目单、签认报告
out/delivered_round2/    按申请重算实际交付的图和表
run_demo.py              一键端到端演练
```

## 一键运行

```bash
python3 run_demo.py                      # 全流程：两轮运行→封存→独立复算→补救→交付
PYTHONPATH=. python3 tests/test_rechain.py
```

## 场景与关键设计

1. **小型运算语言**：`LOAD/FILTER/JOIN/DERIVE/UPDATE/GROUP/SORT/SAVE/PLOT`，
   表达式走 Python `ast` 白名单，禁函数调用、属性访问与导入；带单位列在载入时
   换算到基准单位，`+/-` 做同维度校验，禁止 `Wh + m3` 这类跨维度运算。
2. **可追溯**：每次执行产出 manifest，逐步登记序号、格 id、输入表内容哈希、
   原始输入版本（`elec_monthly/v1.0`、`water_monthly/v1.2`）、文件 sha256 与覆写历史；
   支持重复运行某格，同名变量覆写不静默覆盖。
3. **两种封存口径任选其一**：本实现采用第一种——封存形成图表所需的最小可重演链，
   以带版本中间依赖在洁净环境重新跑原笔记；`demo.py` 同时构造了第二种口径的样本
   （`notebook_b_remediated.json`：作者声明 c8 为可纯重建单元边界）。
4. **正式勘误只改一个同名列单位**：`electricity_kwh` 由误标 `kWh` 修正为 `Wh`
   （同属 energy 维度）；勘误在复算室侧注入，笔记本代码不改。勘误前两图同高同哈希，
   勘误后图/表哈希全部改变（见 `archive/round1_vs_round2_A.json`）。
5. **栏目单允许局部沿用**：用水 `water_m3` 不在勘误范围，凭第一轮封存内容哈希核对
   一致后沿用，并单独导出 `water_series_retained.csv`；用电及含它的派生列一律重算。
6. **“可重现”与“屏上恰好得到”分开**：NB-B 第一轮在作者机上与 A 产出逐位相同，
   但 c8 读取 `tmp/last_round_monthly.csv`（上一轮内存）。封存时 tmp 与声明输出都
   不进包；随机临时目录子进程复算在 c8 断链（FileNotFoundError），故无证签认。
7. **确定性**：两次独立复算（不同随机目录、最小环境变量）产物 sha256 完全一致；
   不带勘误的洁净重算能逐位重现第一轮图哈希，证明分歧仅来自勘误而非环境噪声。

## 复算室命令（手工）

```bash
python -m rechain.runner --notebook <bundle>/notebook_dir/notebook_a.json \
    --erratum archive/erratum_ER-2026-001.json \
    --carry   archive/column_sheet_CS-2026-001.json \
    --label round2-recompute --result result.json
```
