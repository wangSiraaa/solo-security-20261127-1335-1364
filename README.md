# 馆内终端双槽更新控制器（模拟）

这是一个零第三方依赖的 Python 3 本地控制器，用来演示虚构馆内终端的：

- A/B 双启动槽；
- 第六代更新包、批准 SHA-256 摘要、代次和配套组件清单核对；
- 写槽、一次性试启切换、模拟自检、签名回执和失败恢复；
- 单调上升的“最低可启动代次”；
- 掉电后从原子写入的持久 JSON / JSONL 日志继续；
- 按机器号解释最后到底启动了第五代、第六代，还是已停用；
- 三个逻辑班次后的第五代统一停用；
- 同一起点的“一直健康”和“首次成功后又失败”回放。

## 先给结论：什么时候可以烧掉回旧版的桥？

**最稳妥的时机**是：

1. 实际字节逐文件符合发布方批准的摘要，且配套组件清单和整套摘要也一致；
2. 目标机已有匹配本机、本槽、本套字节的有效签名回执；
3. 建议至少再见到一次新班点的连续自检合格（即每台至少第二次合格回执）；
4. 最近一次自检不是失败，第五代回退槽仍完好，并且维护员已查看该机失败记录和剩余班次；
5. 这时再执行不可逆的 `commit`，把最低可启动代次从 `5` 提高到 `6`。

控制器也允许维护员在只有第一次合格回执时提前烧桥，但必须显式给
`--accept-early-risk --reason ...`，把资料室要求、剩余班次和风险判断记入审计。
如果提前烧桥后下一次自检失败，`min_boot_generation=6` 会拒绝第五代，机器无法原地回退。

已经实际生成的三机记录在：

- [`records/summary.md`](records/summary.md)：三种回放的结论表；
- [`records/acceptance-demo.md`](records/acceptance-demo.md)：正确切换、篡改组件中止、试启失败不接班、掉电续跑；
- [`records/replay-healthy.md`](records/replay-healthy.md)：以后一直健康；
- [`records/replay-flaky-wait.md`](records/replay-flaky-wait.md)：首次成功后失败，但暂不提前烧桥；
- [`records/whatif-flaky-burn-early.md`](records/whatif-flaky-burn-early.md)：同样失败未来中首次成功后立即烧桥的反事实。

## 一键运行

```bash
./scripts/demo.sh
```

它会：

1. 重新生成示例第六代包和批准摘要；
2. 运行 8 个单元测试；
3. 实际执行验收脚本并改写 `records/acceptance-demo.md`；
4. 用三台模拟机重放两种未来和一个提前烧桥反事实；
5. 生成 `records/summary.md`。

只跑测试：

```bash
PYTHONPATH=. python3 -m unittest discover -s tests -v
```

## 常用入口

可以设置状态目录，避免每条命令重复 `--state-dir`：

```bash
export MUSEUM_STATE_DIR=/tmp/museum-lab
python3 scripts/build_package.py
python3 -m museum_update init --force
python3 -m museum_update status
```

也可以使用根目录包装脚本 `./mu`。若不设置环境变量，默认状态目录是 `./state`；
下文为了明确仍写出 `--state-dir`。注意全局参数要放在子命令前面：

```bash
python3 -m museum_update --state-dir /tmp/museum-lab status
```

### 1. 可改输入的安装入口

安装单是普通 JSON，例如 [`orders/M2-gen6.json`](orders/M2-gen6.json)：

```json
{
  "order_id": "TICKET-M2-G6-001",
  "package_order_id": "ORD-G6-001",
  "machine": "M2",
  "package": "packages/gen6",
  "target_slot": "b"
}
```

执行：

```bash
python3 -m museum_update --state-dir /tmp/museum-lab install orders/M2-gen6.json
```

顺序固定为：

1. 核对 `approval.json`、`manifest.json`、实际字节和配套组件；
2. 持久化安装单；
3. 写非现役 `b` 槽并复核摘要；
4. 把 `b` 设为一次性试启槽；
5. 等待 `boot` 回执。

**写完文件或完成切换都不算接班。** 只有试启自检 `pass` 后，第六代才成为现役系统；
此时仍未烧桥，后续失败还可回退。

### 2. 可改输入的开机入口

直接用参数：

```bash
python3 -m museum_update --state-dir /tmp/museum-lab boot M2 --result pass \
  --note "第二次班点自检"
python3 -m museum_update --state-dir /tmp/museum-lab boot M2 --result fail \
  --note "外设探针失败"
```

也可以修改 JSON 请求，例如 [`inputs/boot-m3.example.json`](inputs/boot-m3.example.json)：

```bash
python3 -m museum_update --state-dir /tmp/museum-lab \
  boot --input inputs/boot-m3.example.json
```

模拟设备会用每机持久保存的模拟设备密钥签发 HMAC 回执。也可用
`--receipt receipt.json` 提交外部回执；机器号、序列号、槽位、代次、整套摘要或签名
不匹配都会被拒绝。

失败时：

- 若 `min_boot_generation` 仍允许第五代、`a` 槽完好、且未到第 3 班停用点：
  自动切回 `a` 槽第五代并做恢复自检；
- 若已经烧桥，或已到第 3 班停用点：第五代启动会被策略拒绝，机器登记为停用待修。

### 3. 查机器和班次记录

```bash
# 三机总览
python3 -m museum_update --state-dir /tmp/museum-lab status

# 按机器号解释最终启动了什么（show / explain / records 是同一命令）
python3 -m museum_update --state-dir /tmp/museum-lab show M1
python3 -m museum_update --state-dir /tmp/museum-lab explain M2

# 全部班次记录，或只看一台
python3 -m museum_update --state-dir /tmp/museum-lab shifts
python3 -m museum_update --state-dir /tmp/museum-lab shifts M3

# 机器可读 JSON
python3 -m museum_update --state-dir /tmp/museum-lab shifts M3 --json
python3 -m museum_update --state-dir /tmp/museum-lab show M3 --json

# 全部 Markdown 报告
python3 -m museum_update --state-dir /tmp/museum-lab report > machine-records.md
```

### 4. 班次、停用点和不可逆承诺

```bash
python3 -m museum_update --state-dir /tmp/museum-lab advance-shift --by 1

# 建议策略：已有两次连续合格回执
python3 -m museum_update --state-dir /tmp/museum-lab commit M1 \
  --by maintainer-zhou \
  --reason "两张连续合格真机回执"

# 维护员显式选择提前承诺
python3 -m museum_update --state-dir /tmp/museum-lab commit M1 \
  --accept-early-risk \
  --by maintainer-zhao \
  --reason "资料室要求尽早终止第五代能力"

# 第 3 班后处理旧代次和滞留试装
python3 -m museum_update --state-dir /tmp/museum-lab reconcile
```

`commit` 会单调提高 `min_boot_generation`，没有提供任何降低它的命令。
到第 3 班后，第五代的普通开机请求会返回退出码 `3` 并记录 `boot_blocked`。
如果第六代获准、现役且最近自检合格，`reconcile` 会代维护员作出班点承诺，
避免“获准且能启动的新系统一直停在试装”。

### 5. 旧合同现场操作

第五代仍可运行时，现场操作按 `field-ops-v5` 登记：

```bash
python3 -m museum_update --state-dir /tmp/museum-lab operate M2 \
  --operation "read-archive-card CARD-1042" \
  --by staff-lin
```

机器已停用、最近开机失败/被阻止，或当前代次已被班点策略禁止时，操作会被拒绝。

## 掉电恢复

状态在每个关键阶段后原子写入：

```text
<state-dir>/controller-state.json
<state-dir>/journal.jsonl
<state-dir>/slots/<MACHINE>/<slot>/...
```

安装单阶段为 `verified -> written -> switched -> awaiting_boot`。可以故意在写槽后制造掉电：

```bash
python3 -m museum_update --state-dir /tmp/museum-power init --force
python3 -m museum_update --state-dir /tmp/museum-power \
  install orders/M2-gen6.json --power-loss-after written
# 退出码 42 是模拟掉电；此时安装单已持久化为 written

python3 -m museum_update --state-dir /tmp/museum-power resume M2
python3 -m museum_update --state-dir /tmp/museum-power boot M2 --result pass
```

真实实现中 `--power-loss-after` 是测试注入点；普通命令每次转换后也都落盘，
因此进程意外退出后同样可用 `resume` 接续。

## 手动复现三项首要验收

```bash
# 正确包
python3 -m museum_update verify packages/gen6

# 篡改一个配套组件
cp packages/gen6/files/components/reader.pack /tmp/reader.pack.bak
printf '\nUNAPPROVED\n' >> packages/gen6/files/components/reader.pack
python3 -m museum_update verify packages/gen6     # 非零退出
python3 -m museum_update install orders/M3-gen6.json  # 在写槽/切换前停止

# 恢复
cp /tmp/reader.pack.bak packages/gen6/files/components/reader.pack
python3 -m museum_update verify packages/gen6

# 材料合格，但只让试启失败
python3 -m museum_update install orders/M3-gen6.json
python3 -m museum_update boot M3 --result fail --note "injected failure"
python3 -m museum_update show M3
```

完整实际输出见 `records/acceptance-demo.md`。

## 发布包结构

```text
packages/gen6/
├── approval.json    # 发布方批准的 manifest/文件/组件/整套摘要
├── manifest.json    # 代次、单号、组件清单、文件摘要
└── files/
    ├── system/
    └── components/
```

可用下面的命令重新生成示例小文件和摘要：

```bash
python3 scripts/build_package.py
```

修改任意字节但不更新 `approval.json`，控制器都会以摘要不符拒绝。

## 边界说明

这里的 HMAC、设备密钥和“自检”都是本地模拟，用来把状态机和审计记录做完整；
它不是真实安全启动、TPM 或硬件 A/B 实现。真实系统还需要硬件信任根、
签名链、防回滚计数器和经过认证的自检结果。
