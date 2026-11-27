# 封条号离线报讯信道（sealctl）

给流动服务队两台离线报讯终端使用的本地收发器：断线前分好 `1..6` 号封条，每台三枚；离站后按本端号码从小到大写信、消耗并持久绑定正文；复联后由带预置可信回执的验证器核验，收端按固定“三号窗口”决定即时入册或转人工对账。

零第三方依赖，Python 3.10+ 标准库即可运行。

## 先回答值班员的问题

直收规则是：有效首信到达时，以“当前已见最大号 `H`”为窗口右端，接受数值窗口 `H-2、H-1、H` 内尚未入册的信；更大的新号会把 `H` 抬到该号。窗口只向上移，所以落到 `H-2` 以下的真信不会消失，进入待对账区。

题目没有指定唯一分法和抵达次序，因此不存在脱离分法的唯一答案。对“同一批信、甲整批先到 / 乙整批先到”两种次序，程序会用相同信封各跑一个全新收端：

| 断线前预分 | 甲先整批到，乙后到 | 乙先整批到，甲后到 | 最坏可直收 |
|---|---:|---:|---:|
| 整段：甲 `[1,2,3]`，乙 `[4,5,6]` | **6/6**，全部直收 | **3/6**：乙 `4,5,6` 直收；甲 `1,2,3` 全部待对账 | 3 |
| 穿插：甲 `[1,3,5]`，乙 `[2,4,6]` | **5/6**：仅 `#2` 待对账 | **4/6**：`#1,#3` 待对账，晚到甲端的 `#5` 仍在窗口 | 4 |
| 推荐：甲 `[1,3,6]`，乙 `[2,4,5]` | **5/6**：仅乙 `#2` 待对账；乙的 `#4,#5` 仍可直收 | **5/6**：仅甲 `#1` 待对账；甲的 `#3,#6` 仍可直收 | **5** |

枚举全部 `C(6,3)=20` 种“甲选三号、乙拿其余”的固定分法后，没有任何分法能在两种整批次序下都做到 6/6；有 4 个对称分法的最坏结果为 5/6。例如：

```text
甲=[1,3,6], 乙=[2,4,5]
甲 → 乙：直收 [1,3,4,5,6]，待对账 [2]；晚到乙端仍有 #4、#5 在窗口
乙 → 甲：直收 [2,3,4,5,6]，待对账 [1]；晚到甲端仍有 #3、#6 在窗口
```

分法在创建/分配时确定，`Planner` 随后才分别运行两种抵达次序；分配接口不接收“哪种次序先到”的参数，不能读完次序再补挑号码。

## 快速运行

```bash
# 完整场景：方案比较、两种次序、重送、篡改登记、大号先到、冲突与人工补录
./sealctl demo --state /tmp/seal-demo

# 枚举全部 20 种分法
./sealctl plans --all

# 运行测试
python3 -m unittest discover -s tests -v
# 或 npm test / make test
```

也可以免执行权限使用 `python3 -m seal_channel ...`。

## 普通命令流程

下面用推荐分法开一个值班用状态目录 `ops/`：

```bash
./sealctl init --state ops

# 方式一：一步完成预分
./sealctl allocate --state ops --alpha 1,3,6 --beta 2,4,5

# 方式二：也保留“申请 → 批准”的普通命令
# ./sealctl apply   --state ops --trip alpha --seals 1,3,6
# ./sealctl apply   --state ops --trip beta  --seals 2,4,5
# ./sealctl approve --state ops --trip alpha
# ./sealctl approve --state ops --trip beta

# 断线前两台必须都已分好；离站后分号冻结
./sealctl depart --state ops --all

# 写信；省略 --seal 时自动选本端最小未耗号，并强制从小到大
./sealctl write --state ops --trip alpha --text "甲一号：西河湾安全。"
./sealctl write --state ops --trip alpha --text "甲三号：断崖岭可通行。" --seal 3
./sealctl write --state ops --trip alpha --text "甲六号：交接完成。" --seal 6
./sealctl write --state ops --trip beta  --text "乙二号：水源已补给。"
./sealctl write --state ops --trip beta  --text "乙四号：石桥完好。" --seal 4
./sealctl write --state ops --trip beta  --text "乙五号：准备折返。" --seal 5

# 复联后也可用 send 把“写下一封信 + 交给收端”合成一步：
# ./sealctl send --state ops --trip alpha --body "即时送抵的电文"

# 同一批信，两种真实抵达次序，分别用两个收端状态运行
./sealctl arrival --state ops --order alpha,beta --receiver a-first
./sealctl arrival --state ops --order beta,alpha --receiver b-first

./sealctl status --state ops --receiver a-first
./sealctl ledger --state ops --receiver b-first
```

全局 `--state` 也可放在子命令前：`./sealctl --state ops status`。大多数命令支持 `--json`。

过窗真信可在人工核对后补录：

```bash
./sealctl reconcile --state ops --seal 1 --receiver b-first \
  --note "与行程单和可信回执核对一致"
```

## 安全与持久 invariant

### 1. 发端重启不能把耗过的号转给新正文

每台终端的 `terminals/<trip>.json` 持久保存：

```json
"consumed": {
  "1": {
    "body_sha256": "...",
    "bound_at": "...",
    "envelope_path": "outbox/alpha/1.json"
  }
}
```

每条 CLI 命令都是新进程并从磁盘重载状态；也可以显式运行：

```bash
./sealctl restart --state ops --trip alpha
```

若尝试用已消耗的 `#1` 写另一正文，会得到 `SEAL_ALREADY_CONSUMED`，原绑定和 `outbox/alpha/1.json` 不被覆盖。

### 2. 可信回执核对归属与正文

`trusted_receipts.json` 在离站/批准时预置，包含：

- 每个号归哪一台；
- 钉住“某号已交某台”登记内容的 `grant_digest`；
- 每个封条的模拟密钥 `seal_key_hex`，用于验证 `HMAC(seal_key, 封条号/归属/正文摘要)`；
- 可选的 `expected_body_sha256`，用于更严格地预置“该号应认哪封正文”。

真实部署中这些密钥/回执应在离站前通过可信渠道装入验证器；本项目用本地 HMAC 和预置回执模拟。

### 3. “改成尚未交甲”后原信不能核验通过

```bash
./sealctl tamper-unissue --state ops --seal 1
./sealctl receive --state ops \
  --envelope ops/outbox/alpha/1.json \
  --receiver tamper
# 退出码 3；code=SEAL_NOT_HANDED_TO_OWNER
```

该故障命令把 `registry.json` 中 `#1` 改为：

```json
{ "status": "unissued", "issued": false, "holder": null, "state": "尚未交甲" }
```

但不改预置可信回执。验证器会拒绝同一封原信。按可信回执修复登记、且人工确认归属仍为甲后：

```bash
./sealctl repair-grant --state ops --seal 1
```

### 4. 大号先到、原件过窗时不消失、不许异文占号

收端 `body_index` 会记住每个有效封条号第一次认下的正文摘要：

- 窗口内首信：写唯一账册行，状态 `recorded`；
- 过窗有效首信：进 `pending`，状态 `pending_reconciliation`，保存完整原件；
- 同一信重送：不新增账册行，只追加 `RCPT-*` 回条；
- 同号异文：返回 `conflict / SEAL_BODY_CONFLICT`，原 pending 或账册行不变；
- 人工对账后：以 `source=manual_reconciliation` 补录入册。

`./sealctl demo` 会实际执行这些分支。

## 作为 Python API 使用

```python
from seal_channel import SealCase, Planner

case = SealCase.init("ops")
case.allocate(alpha=[1, 3, 6], beta=[2, 4, 5])
case.depart_all()
case.write("alpha", "第一封")
# ...

result = case.receive_file("ops/outbox/alpha/1.json", receiver_name="r")
print(result.status, result.message)

report = Planner.evaluate([1, 3, 6], [2, 4, 5])
print(report.worst_direct, report.runs["beta,alpha"].pending)
```

## 目录结构

```text
seal_channel/
  models.py      # 常量、参数解析、结果类型
  security.py    # SHA-256/HMAC/规范化 JSON
  store.py       # 原子 JSON 持久化
  verifier.py    # 预置可信回执与验证器
  terminal.py    # 离线发端、消耗绑定、重启持久化
  receiver.py    # 三号窗口、账册、待对账、幂等重送、冲突保护
  case.py        # 申请/批准/分号/离站/投递/故障修复门面
  planner.py     # 固定分法 × 两种抵达次序的真实模拟与枚举
  cli.py         # sealctl 命令
tests/           # unittest 行为测试
sealctl          # 免安装 CLI 入口
```
