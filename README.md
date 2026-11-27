# 值班磁带发行与恢复终端

## 先回答问题

一卷恢复带尾部读坏时，**可以安全提前启用的是已经完整到达、并通过受托封条器回执核验的联系人缩写表 `contacts`**。

值班表 `duty` 不能单独按新柜号值班：它的发行头精确钉住了对应柜号表 `lockers` 的完整 SHA-256 摘要。若接收端只有旧代柜号表，即使新值班表本身已验签，也只能保存为“已验证但未挂载/被依赖阻塞”，查询端不会把它和旧柜号错误连接。

这里实现的是题目允许的第二种办法：**独立内容尽早挂载，依赖组到齐后整体切换**。已经验签、挂载、复制或写入审计链的部分交付，不会因为后来发现带尾错误而被回滚、删除或解释成“从未发出/复制过”。

## 可独立挂载边界

| 资料 | 边界 | 服务方式 |
|---|---|---|
| `contacts` 联系人缩写表 | 无依赖，可独立挂载 | 按 `person_id` 或姓名查缩写；缺尾时的救急资料 |
| `lockers` 柜号对应表 | 自身可验签/保存；作为值班依赖时必须与值班表精确成组 | 柜号 ID → 柜号文本 |
| `duty` 值班表 | 头中包含 `lockers` 的 `artifact_id/generation/batch/digest` | 只有本地存在同一摘要、未撤销且未被旧代锁阻止时，才与柜号表一起挂载 |

联系人表通过稳定的 `person_id` 与值班表连接，因此可以在不破坏旧值班组柜号依赖的情况下提前更新。值班表对柜号表使用**完整摘要钉死**，不是只比表名或代号，所以另一批次的同名柜号表不能混入本批值班。

## 磁带与回执格式

磁带是顺序 JSONL 小记录。每个表为一组：

1. `head`：批次、种类、代号、列、行数、正文哈希、依赖钉；
2. 若干 `line`：顺序到达的短文本 CSV 记录；
3. `receipt`：题内测试封条器对该制品摘要出具的 HMAC-SHA256 核验回执。

封条器实现见 `duty_tape/sealer.py`，默认使用公开测试密钥
`duty-tape-public-test-sealer-key-v1`。这只用于本题的公开测试回执；生产环境应把 HMAC 测试封条器替换成真正的受托签名设备/非对称签名。

恢复端在见到合法回执前不会把制品投入服务。改一条正文会导致正文哈希、制品摘要和回执全部对不上；把另一批次的完整同名制品整片换入虽然可能带有该批次的合法回执，但其摘要不等于本批值班表钉住的依赖，仍不能成本批依赖。也可以在 `feed --expected-batch BATCH` 时更早拒绝外批次记录。

## 状态目录

```text
state/
  tmp/
    staging/         # 未终验的分段重组，可随时删除
    feed-buffer.bin  # 最后一个不完整 JSON 行，也可删除后重发
  store/artifacts/   # 已验签制品和回执，提交事实
  catalogs/cat-*/    # 每次切换生成的不可变目录
  current -> ...     # 原子替换的符号链接，查询端只看这里
  pins.json          # 例如把 lockers 锁在旧摘要/旧代
  revocations.jsonl  # 合法撤销回执
  ledger.jsonl       # 哈希链、只追加的挂载/复制/撤销/清临时区事件
```

`tmp/` 随时可清空；已验签表在 `store/`，正式查询只经过原子切换后的 `current/`。审计账本是带 `prev_hash/event_hash` 的只追加哈希链，可用 `history --verify` 校验。

## 运行公开测试

无需第三方依赖：

```bash
python3 -m unittest discover -s tests -v
python3 demo.py
```

测试覆盖：

- 分段投入、任意字节边界、退出后重开；
- 带尾损坏后只提前启用联系人缩写表；
- 清空临时区不影响已挂载表；
- 修复后恢复正确完整卷并真正切换到新柜号；
- 另一批次同名记录/篡改记录不能作为本批依赖；
- 柜号目录锁旧代时，已验证新值班表保持阻塞；
- 完整且可切换的普通卷能挂上并返回缩写与新柜号；
- 复制事件和导出的副本在撤销后仍存在，撤销只对本终端后续读取产生效力。

## CLI 速览

从示例表发行一卷：

```bash
./duty-tape --root /tmp/duty-state issue \
  --batch B-2026-11-27 \
  --contacts examples/new/contacts.csv:3 \
  --lockers  examples/new/lockers.csv:2 \
  --duty     examples/new/duty.csv:7 \
  --out /tmp/full.jsonl
```

分段恢复时可反复执行 `feed`；每次命令结束都相当于关闭并重开程序，状态在磁盘上：

```bash
./duty-tape --root /tmp/duty-state feed --tape part-01.jsonl
./duty-tape --root /tmp/duty-state feed --tape part-02.jsonl
./duty-tape --root /tmp/duty-state status
```

真实查询端：

```bash
./duty-tape --root /tmp/duty-state query abbreviation 'Chen An'
./duty-tape --root /tmp/duty-state query person 'Chen An' --date 2026-11-27
./duty-tape --root /tmp/duty-state query duty 2026-11-27

# 可选 HTTP/JSON 查询端点
./duty-tape --root /tmp/duty-state serve --host 127.0.0.1 --port 8765
# /health, /v1/abbreviation?name=..., /v1/person?name=...&date=..., /v1/duty?date=...
```

记录带尾事故并清空临时区：

```bash
./duty-tape --root /tmp/duty-state tail-failed --note 'CRC error'
./duty-tape --root /tmp/duty-state clear-tmp
```

把柜号表锁在当前旧代：

```bash
OLD_DIGEST=$(./duty-tape --root /tmp/duty-old status |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["active"]["lockers"]["digest"])')
./duty-tape --root /tmp/duty-old pin --kind lockers --digest "$OLD_DIGEST"
```

此后喂入正确新卷，`status.duty_readiness` 会说明新值班表缺少被钉住的精确柜号摘要或被旧代锁阻止；查询仍只返回旧柜号。新终端不设锁，或对操作上允许切换的终端执行 `unpin --kind lockers` 后，完整正确卷会原子挂载。

复制与撤销：

```bash
./duty-tape --root /tmp/duty-state export --kind contacts --to /tmp/desk-2/
./duty-tape --root /tmp/duty-state new-revocation \
  --artifact-digest sha256:... --reason leaked --out /tmp/rev.json
./duty-tape --root /tmp/duty-state revoke --file /tmp/rev.json
./duty-tape --root /tmp/duty-state history --verify
```

撤销令是后续效力控制和追加审计事件，不是历史橡皮擦：已复制到其他值班台的文件不会被本程序删除，`table_copied/external_copy_noticed` 事件也仍在哈希链账本中。
