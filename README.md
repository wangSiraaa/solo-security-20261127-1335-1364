# 钟琴外借音色与校音工作台

纯 Python 标准库实现（无 numpy/scipy），离线运行。回答的问题：

> 乐器厂外借半套钟琴音色后，学员自己的两根琴条能否据此调成同一组和弦？

结论：**可以，但只在授权音域内**。本仓库把“授权边界、不可区分外借、
两路外发方案、拍频调律判断、撤权与换样本的局部影响”全部做成可执行、
可哈希复核的代码与测试。

## 目录

```
glock/
  tuning.py      音律数学：等分/纯律/中庸全音律，参考音可现场重定
  dsp.py         零依赖 FFT/Goertzel/谱峰/双段衰减拟合/重采样/WAV 读写
  synth.py       琴条加性合成：分键区、4 力度层交叠、音分微调、双段衰减、释放尾音
  bank.py        工厂母库 + 授权清单（留厂资产与外借白名单的唯一边界）
  exporter.py    外借导出程序：路线 A（可重排公开库）/ 路线 B（限域连续片段）
  chords.py      获准音域内的和弦枚举
  analysis.py    两根琴条录音分析（基音、泛音列、衰减）
  workbench.py   拍频判断、重调决策、练习伴奏合成
  reader.py      普通 WAV 读器：单音频差/衰减对比 + 合响数拍
  cli.py         命令行入口
tests/           19 个 unittest（含一条完整端到端叙事）
```

## 授权模型如何落地

- 母库 `body/<键>/<力度层>.wav` 与 `tail/<键>.wav` 只存在于工厂侧；
  元数据里的 `released_notes` / `released_tails` 是工厂单方持有的放行表。
- `License` 只描述申请范围；`open_loaned_materials` 同时校验申请与放行表，
  索要保留音或留厂尾音一律 `PermissionError`，原采样与释放尾音不会随意外发。
- 导出只读取 `LoanedMaterial` 中已授权的字节，产物带 SHA-256 清单。
- **两份只在保留音上不同的母库，导出的外借包哈希完全一致**
  （`tests/test_export.py::test_01`）——下家无法区分。

## 两路外发方案（任选其一，都支持事后选和弦）

| | 路线 A 公开可重排库 | 路线 B 合约连续响应片段 |
|---|---|---|
| 产物 | SFZ + 每层 WAV + 重制的力度交叠过渡 | 五档力度网格的预渲染片段 |
| 音高变化 | 映射层 tune，不发原采样 | 限域内重采样移频，超域拒绝并提示回厂 |
| 尾音 | 不含（留厂），用本地短 WAV 重制余韵 | 不含 |
| 撤一层边界授权 | 该层与相邻过渡移除/换材料，其它键不变 | 缺层自动退到最近授权层 |

两路的清单都带 `chords.selectable`：只列出组成音全部授权的和弦。

## 校音工作台

1. 校音员拿到外借件后才提供参考音（如 A=219.5Hz）与非等分律目标
   （纯律/中庸全音律/自定义 12 音分表）。
2. 分析两根独立录音 WAV：谐波支持度评分估基音（对敲击噪声稳健，
   实测误差 ≤3 音分），谱峰归入泛音序号，拟合双段衰减。
3. 按和弦所含音程（3:2、5:4、6:5…）只取相关的重合泛音对，数当前拍频，
   再在 ±40 音分网格上搜索让拍频失配最小的微调量，给出
   “哪根须重调、调多少音分、能否维持目标拍频”。
4. 用本地合成器渲染可听练习伴奏 WAV；普通读器可独立复核频差、衰减与拍频。

### 换第二泛音样本的局部性

`tests/test_workbench.py::test_06` 与端到端测试验证：把 B 换成
“基音相同、第二泛音偏移约 50 音分”的样本后，只有真正使用第二泛音
重合的和弦（maj/min）调律判断改变；aug/dim 的判断逐位不变。

## 运行

```bash
python3 -m unittest discover -s tests -v     # 19 个测试，约 1 分钟

PYTHONPATH=. python3 -m glock.cli build-bank factory --variant A --retained 76,79
PYTHONPATH=. python3 -m glock.cli make-license license.json \
    --notes "60:0,1,2,3;62:0,1,2,3;64:0,1,2;65:0,1,2,3;67:0,1,2,3"
PYTHONPATH=. python3 -m glock.cli export factory license.json loan
PYTHONPATH=. python3 -m glock.cli workbench barA.wav barB.wav temp.json chords.json wb_out
PYTHONPATH=. python3 -m glock.cli reader barA.wav barB.wav
PYTHONPATH=. python3 -m glock.cli retune segment.wav out.wav 25 --max-shift 50
```

## 设计取舍

- FFT 用手写基 2 Cooley–Tukey，频谱读数零填充到 131072 点提高频率精度；
  采样率 32kHz、16-bit PCM，任何普通 WAV 读器都能打开。
- 拍频重合对要求“第 ra 号泛音 对 第 rb 号泛音”精确成员，不做整除放宽，
  否则同音比 (1,1) 会让无关泛音污染所有和弦的判断。
- 纯解释器性能下逐泛音带通衰减过贵，泛音 tau 采用整信号长段时间常数
  （代码注释中已如实说明）。
