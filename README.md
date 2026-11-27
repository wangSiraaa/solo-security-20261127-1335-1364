# 匿名影像上报站接入层（本地文件 / 进程重开演示）

影像在这里只是数段短字符串。系统由两部分组成：

- **网关 CLI**：核对本地模拟无线通道给出的 HMAC 往返见证，受理首问、换线迁移、幂等写段和最终签收。
- **上传柜状态文件**：固定数量的暂存格，用带文件锁的原子 JSON 写入保存；每条 CLI 命令都是一个全新进程，关掉终端或重启网关后状态仍在。

仅使用 Python 3 标准库。

## 一句话回答：席位在谁手里？

在默认的 **有期限预约模式（`reservation`）** 下，首问成功后席位属于上传柜中的那一单，由终端**私下保存的领位条（ticket）**代表；它不属于无线线路、通道回报或出口名称。

- 换了无线线路：持原领位条 + 新通道的有效当前见证执行 `migrate`，即可把同一单迁到新通道，原格不变。
- 只把往返回报上的通道名改成另一条通道、却没有有效签名或没有办理迁移：正文会被拒绝。
- 两台终端即使使用同一个 `--egress` 出口名，也仍然靠各自不同的领位条区分；出口名从不参与授权。
- 已受理的预约在 TTL 内必须占格；超时且执行 `reap` 后才可释放。
- 写了一半的件仍是 `writing` 状态并占格；换线、进程重开、重复回报都不会把它变成“已释放”。
- 重复首段按“同一领位条/同一幂等键 + 同一内容”做幂等确认；同序号但内容不同会报 `SEGMENT_CONFLICT`。不同终端即使首段内容相同，也不会互相合并。
- 最终签收后状态为 `delivered`，成件写入 `deliveries/`；持有关系释放、格子可被新单循环使用，但检查时仍能看到“已交付成件”，也可用 `clear-slot` 显式清格。

系统也实现了对照的 **无逐人状态模式（`onwrite`）**：首问只返回签名凭单，`seat_held=false`；直到“有效当前见证 + 首段正文”在文件锁内原子抢到格子，才产生席位。早拿到凭单的人也可能在首段写入时收到 `FULL_AT_FIRST_SEGMENT`。因此 `retry_allowed=true` 只表示“允许再来试”，绝不表示保住了席位。

## 运行完整情景演示

```bash
python3 demo.py
# 或
./demo.sh
```

演示全部通过子进程执行 `python -m anon_station ...`，覆盖：

1. 两个终端共用同一出口；A 首问后、写入前离开，再从新线回来；未迁移前拒绝，迁移后写同一格；重复/冲突首段、B 与 A 的完整交付。
2. 只有一个暂存格的班次：空格新单 → 当前见证 → 篡改回报通道被拒 → 有效异通道见证但未迁移被拒 → 还原回报后写入 → 迁移后续段 → 正常交付。
3. 新单首问前已有一件欠最后一段：它保持 `writing` 并阻塞新预约；不能清格，也不能算作已释放；只有超过明确的半件期限后 `reap` 才释放。
4. `onwrite` 对照：空问不占格，两个凭单持有者竞争唯一格，后到首段收到客满错误。
5. 8 个进程并发争抢 1 个格时，恰好 1 个成功、7 个得到 `CABINET_FULL`。

演示后的本地状态保留在：

```text
demo-run/
├── two-terminals/
├── single-slot/
├── partial-blocks/
└── onwrite/
```

可反复查看，例如：

```bash
python3 -m anon_station inspect --state demo-run/two-terminals
python3 -m anon_station inspect --state demo-run/partial-blocks --json
```

## 手动走一遍

### 1. 初始化两格预约柜

```bash
STATE=/tmp/anon-manual
rm -rf "$STATE"
python3 -m anon_station init \
  --state "$STATE" \
  --slots 2 \
  --policy reservation \
  --reservation-ttl 3600 \
  --partial-ttl 7200
```

### 2. 本地模拟通道往返并首问

```bash
python3 -m anon_station roundtrip \
  --state "$STATE" --channel line-A --out "$STATE/witness-a.json"

python3 -m anon_station ask \
  --state "$STATE" \
  --witness "$STATE/witness-a.json" \
  --segments 3 \
  --egress shared-exit.example \
  --ticket-out "$STATE/ticket-a.json"
```

领位条文件权限为 `0600`。网关状态中只保存它的 SHA-256 指纹。

### 3. 关机/换线后，新通道见证不能直接写

```bash
python3 -m anon_station roundtrip \
  --state "$STATE" --channel line-A-new --out "$STATE/witness-a-new.json"

# 未迁移：退出码 7，CHANNEL_MIGRATION_REQUIRED
python3 -m anon_station write \
  --state "$STATE" \
  --ticket-file "$STATE/ticket-a.json" \
  --witness "$STATE/witness-a-new.json" \
  --index 0 --data 'seg-0;'
```

### 4. 持私下领位条办理迁移，然后续办

```bash
python3 -m anon_station migrate \
  --state "$STATE" \
  --ticket-file "$STATE/ticket-a.json" \
  --witness "$STATE/witness-a-new.json"

python3 -m anon_station write \
  --state "$STATE" \
  --ticket-file "$STATE/ticket-a.json" \
  --witness "$STATE/witness-a-new.json" \
  --index 0 --data 'seg-0;'
```

重复同一命令会返回 `"duplicate": true`，段数仍为 1；把 `--data` 改掉则返回 `SEGMENT_CONFLICT`。

继续写入并签收：

```bash
python3 -m anon_station write --state "$STATE" \
  --ticket-file "$STATE/ticket-a.json" --witness "$STATE/witness-a-new.json" \
  --index 1 --data 'seg-1;'

python3 -m anon_station write --state "$STATE" \
  --ticket-file "$STATE/ticket-a.json" --witness "$STATE/witness-a-new.json" \
  --index 2 --data 'seg-2'

python3 -m anon_station deliver --state "$STATE" \
  --ticket-file "$STATE/ticket-a.json" --signoff '值班签收人'

python3 -m anon_station inspect --state "$STATE"
```

## 暂存格状态

`inspect` / `status` 会明确区分：

| 状态 | 含义 | 是否占席位 |
| --- | --- | --- |
| `empty` | 空格 | 否 |
| `reserved` | 已受理的有期限占位，尚无正文 | 是 |
| `writing` | 已写入若干段，仍缺段，可能是半件 | 是 |
| `complete` | 片段齐全，等待最终签收 | 是 |
| `delivered` | 已最终签收并交出成件 | 持有关系已释放，记录仍可见，格子可循环或显式清理 |
| `expired` / `abandoned` | 预约或半件超过明确期限，`reap` 后释放 | 清理后否 |

关键错误码：

- `CABINET_FULL`（退出码 3）：预约模式首问时已满，或无状态模式首段原子分格时已满。
- `REPORT_CHANNEL_MISMATCH`（退出码 4）：回报外层通道名与签名见证不一致。
- `INVALID_TOKEN`（退出码 4）：领位条/凭单/见证伪造或过期。
- `CONFLICT` / `SEGMENT_CONFLICT` / `SLOT_NOT_RELEASABLE`（退出码 5）。
- `CHANNEL_MIGRATION_REQUIRED`（退出码 7）：见证来自另一通道且本单未迁移。

## 无逐人状态模式

```bash
STATE=/tmp/anon-onwrite
rm -rf "$STATE"
python3 -m anon_station init --state "$STATE" --slots 1 --policy onwrite

python3 -m anon_station roundtrip --state "$STATE" --channel cafe --out w.json
python3 -m anon_station ask --state "$STATE" --witness w.json \
  --segments 2 --voucher-out v.json
# 响应中 seat_held=false

python3 -m anon_station write --state "$STATE" \
  --voucher-file v.json --witness w.json \
  --index 0 --data first --ticket-out t.json
```

如果另一个请求已在文件锁内先占了唯一格，这里会返回 `FULL_AT_FIRST_SEGMENT`；此前的凭单不是席位。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试全部使用临时目录，并包含真实子进程并发争抢单格的用例。

## 文件

```text
anon_station/
├── cli.py          # argparse JSON/人类可读命令层
├── channels.py     # 本地模拟通道往返见证与核对
├── service.py      # 预约、迁移、幂等写段、交付、清格、TTL
├── store.py        # 文件锁、原子 JSON、本地状态初始化
├── security.py     # HMAC token、指纹、0600 私钥/领位条
└── errors.py
demo.py             # 题目情景的端到端断言演示
tests/              # unittest 行为与并发测试
```
