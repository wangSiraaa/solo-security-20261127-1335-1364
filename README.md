# 两层接口板：公开 SVG 出图与外包连通复核

本项目实现题设中的第一种路线：内部先把跨界连接改接到一个完全公开、边界固定的接口区，
再把匿名的层图、焊盘/开窗路径和钻孔坐标交给外包。私有折线始终位于公开边界外，不进入
交付包，也不通过隐藏图层、对象名、网名或未使用路径带出。

## 交付模型

- 两层：`top`、`bottom`。
- 同层接触的矩形铜片或圆形焊盘在几何上导通。
- 只有镀铜通孔（plated hole）的孔壁能把两层连起来。
- 不镀铜定位孔（NPTH）不产生层间边；删网名、改对象名不会影响判定。
- 外包实际提交的 `drill.csv` 与公开包中的期望钻表按坐标、孔径、镀覆状态复核。
- 孔位偏移若使孔壁碰到期望孔未接触的邻格铜，报告 `HOLE_ENTERED_ADJACENT_GRID`；
  若因此形成 P/Q 同分量，再报告 `ISOLATION_LOST`。

## 公开包内容

`build/public-package/` 是可交给外包的目录：

- `top-copper.svg`、`bottom-copper.svg`：仅含普通 SVG `<path>`，可由常见 SVG 阅读器打开。
- `top-soldermask.svg`、`bottom-soldermask.svg`：开窗路径。
- `drill.csv`：实际复核工具使用的匿名钻孔坐标、孔径和镀覆标记。
- `drill.xln`：常见 Excellon 风格钻表，仅坐标和孔径，不含孔名/网名。
- `public-terminals.csv`：公开端子坐标。
- `SHA256SUMS`：交付文件摘要。

`build/local-stack-diagnostic.svg` 以及 `build/fabrication/*/actual-stack-diagnostic.svg`
只是本地/夹具诊断图，不是外发包；`build/internal/` 中的 JSON 含私有路线，也不得交付。

## 演练工单

公开接口中有两个合同要求互相隔离的网络：

- `P`：端子 `P_A`、`P_B`、`P_C`。
- `Q`：端子 `Q_A`、`Q_B`。

正常复原板：

- `P_VIA(5,5)` 镀铜，连接跨层的 `P_A(top)` 与 `P_C(bottom)`。
- `P_A -> P_B` 仅靠顶层铜连接，所以 `P_VIA` 改 NPTH 后仍导通。
- `Q_VIA(15,5)` 镀铜，连接跨层的 `Q_A(top)` 与 `Q_B(bottom)`。

NPTH 工单只允许把 `P_VIA` 改成不镀铜定位孔：

- `P_A -> P_C` 按工单变为开路。
- `P_A -> P_B` 和 `Q_A -> Q_B` 仍须导通。
- `P`、`Q` 仍须隔离。

单夹具偏孔演练中，`P_VIA` 从 `(5,5)` 偏到 `(9.4,5)`，镀铜孔壁同时碰到原 P 铜格和
相邻 Q 铜格。**因此原本隔离的 P 与 Q 两电网被接通**，工具对这块停工；未偏的另一块
仍放行。

## 命令

重新生成演练文件：

```bash
python3 make_fixtures.py
```

复核未偏板，预期退出码 0：

```bash
python3 -m pcb_review.cli review \
  --package build/public-package \
  --actual-drill build/fabrication/boardA-nominal-drill.csv \
  --required P_A:P_C --required P_A:P_B --required Q_A:Q_B \
  --isolate P_A,P_B,P_C:Q_A,Q_B
```

复核允许的 NPTH 工单，预期退出码 0：

```bash
python3 -m pcb_review.cli review \
  --package build/public-package \
  --actual-drill build/fabrication/npth-drill.csv \
  --required P_A:P_B --required Q_A:Q_B \
  --expect-open P_A:P_C --allow-npth 5,5 \
  --isolate P_A,P_B,P_C:Q_A,Q_B
```

复核偏孔板，预期退出码 2（STOP）：

```bash
python3 -m pcb_review.cli review \
  --package build/fabrication/boardB/delivered-package \
  --actual-drill build/fabrication/boardB/actual-drill.csv \
  --required P_A:P_C --required P_A:P_B --required Q_A:Q_B \
  --isolate P_A,P_B,P_C:Q_A,Q_B
```

上面的历史演练路径 `build/fabrication/boardB-drifted-drill.csv` 也保留；正式夹具输入推荐使用
`build/fabrication/boardA|boardB/` 下的“交付包 + 实际钻表”两份文件。

运行测试：

```bash
python3 -m unittest discover -s tests -v
```

## 私有形状不可区分性检查

两个内部源图只改变公开边界外的私有折线（蛇形与阶梯形），重新导出的公开包逐文件相同：

```bash
diff -qr build/public-package build/private-b-package
```

因此外包实际取得的文件不能区分这两种私有形状。
