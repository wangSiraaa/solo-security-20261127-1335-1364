# 共享修理台外设交接服务

一个零第三方依赖的本地模拟服务。它把“部件身份—接口见证—电路冻结—夹具/纸样—具名工单—接线杆—作业/重置回执”放在同一条证据链里裁决。

## 先回答：换了部件后，那只手柄还能继续收输入吗？

**不能继续把它的输入当成可信输入直接写正式单；但可以、也应当把有保留价值的读数收进待认领区。**

只有以下条件在**读数到达的那一刻**同时成立，输入才进入具名工单：

1. 当前物理路径接通（原台或备用板，接线杆不冲突）；
2. 接口上有实际安装记录，且逐接口见证报告的序列号与实际部件一致；
3. 保管人封存过该 **部件 × 接口 × 能力** 的对应关系，部件仍属受托；
4. 该映射没有被拆除、换件、电路冻结或真实重置回执失效；
5. 对读样号而言，到达时夹具存在、已锁住，且夹具上的纸样与工单一致；
6. 易逝纸样没有已经离架。

端口名 `J2`、外壳号相同，或事后把可信部件装回去，都不能单独证明中间那段不明输入的来源。历史夹具状态也按到达时的快照裁决：读数到达时已经换成下一份纸样，就不能补记到原工单。

## 快速验证

```bash
./handoff demo
python3 -m unittest discover -s tests -v
```

内置场景使用内存状态，包含 **32 项断言**，覆盖：

- 原读头在夹具锁住时读样号 → WO-V 正式记录；
- 同端口改接未确认来源 → 只进 `quarantine`；
- 独立旋钮电路证据连续 → 仍可正式记账；
- 还原可信读头，但到达时夹具已换成下一份纸样 → 不能记 WO-V；
- 历史上下文匹配 WO-R 后才可认领至 WO-R；
- 备用板必须先断原台、单槽占用、未取回易逝读数记为 lost；
- 重置报告早到则提前冻结，迟一拍则在回执到达时冲正那段误记的正式输入。

## 三块部件、两张纸样

`./handoff seed` 会写入：

| ID | 角色 |
|---|---|
| `BLK-DUO-1` | 受托双功能方块：`J1/C-PAGE` 旋钮翻页，`J2/C-SCAN` 读头抄样号 |
| `BLK-READ-2` | 更换上的读头，未受托/未取得有效封存映射 |
| `BLK-BACKUP-9` | 受托备用板，同时只能接一件 |
| `P-VOLATILE` | 离架后无法补读的纸样，在 `F1`，对应 `WO-V` |
| `P-RELOAD` | 可再装纸样，对应 `WO-R` |

状态默认写到当前目录 `.handoff-state.json`；可用全局 `--state PATH` 或环境变量 `HANDOFF_STATE` 覆盖。

## 题目要求的命令序列

```bash
STATE=/tmp/bench.json
./handoff --state "$STATE" seed

# 1. 原读头、夹具仍锁住：正式单记一笔
./handoff --state "$STATE" call --op input \
  --at T1 --interface J2 --capability read_sample \
  --value SPEC-V-001 --order WO-V --fixture F1
# => "decision": "formal", "order_id": "WO-V"

# 2. 同端口换为未确认来源
./handoff --state "$STATE" call --op remove --interface J2 --reason reader_replaced
./handoff --state "$STATE" call --op install \
  --interface J2 --component BLK-READ-2 --circuit C-SCAN
./handoff --state "$STATE" call --op witness \
  --interface J2 --capability read_sample
./handoff --state "$STATE" call --op freeze \
  --circuit C-SCAN --reason identity_unconfirmed

./handoff --state "$STATE" call --op input \
  --at T2 --interface J2 --capability read_sample \
  --value SPEC-V-002 --order WO-V --fixture F1
# => "decision": "quarantined", "order_id": null

# 独立电路上的旋钮不受读头段冻结影响
./handoff --state "$STATE" call --op input \
  --at T3 --interface J1 --capability turn_page \
  --value page=2 --order WO-V --fixture F1
# => "decision": "formal"

# 3. 还原可信部件并重新见证
./handoff --state "$STATE" call --op remove --interface J2
./handoff --state "$STATE" call --op install \
  --interface J2 --component BLK-DUO-1 --circuit C-SCAN
./handoff --state "$STATE" call --op witness \
  --interface J2 --capability read_sample

# 读数到达前，F1 已换成下一份纸样
./handoff --state "$STATE" call --op pattern_unload --fixture F1
./handoff --state "$STATE" call --op fixture_load \
  --fixture F1 --pattern P-RELOAD --locked

./handoff --state "$STATE" call --op input \
  --at T4 --interface J2 --capability read_sample \
  --value SPEC-R-NEXT --order WO-V --fixture F1
# => "decision": "quarantined"，原因 arrival_pattern_differs_from_named_order

# 不能认领回原单；历史夹具与 WO-R 匹配时可认领至 WO-R
./handoff --state "$STATE" call --op claim --quarantine Q-2 --order WO-V
./handoff --state "$STATE" call --op claim --quarantine Q-2 --order WO-R

./handoff --state "$STATE" order WO-V
./handoff --state "$STATE" quarantine
```

也可以直接传完整 JSON：

```bash
./handoff call '{"op":"advise"}'
echo '{"op":"show"}' | ./handoff call --stdin
```

## 本地 HTTP 接口

```bash
./handoff serve --host 127.0.0.1 --port 8765
# 或 ./start.sh                 # 默认 0.0.0.0:8000，可用 PORT 覆盖
# 或 python3 server.py --port 8765
```

```bash
curl -s http://127.0.0.1:8765/health
curl -s http://127.0.0.1:8765/api/state
curl -s -X POST http://127.0.0.1:8765/api/dispatch \
  -H 'Content-Type: application/json' \
  -d '{"op":"input","interface":"J2","capability":"read_sample","value":"X","order":"WO-V","fixture":"F1"}'
```

兼容路径：`POST /v1/command`、`POST /command`。

## 物理交接与回执语义

### 分段冻结

- `freeze --circuit C-SCAN` 只冻结读头所在段；
- `C-PAGE/J1` 的封存、安装和见证连续，因此旋钮翻页仍可正式入账；
- 受影响通道重新 `witness` 成功后可恢复，或显式 `unfreeze --force`（会留下审计记录）。

### 备用板和接线杆

```bash
./handoff call --op buffer_read --interface J2 --value VOLATILE-X \
  --order WO-V --fixture F1
./handoff call --op advise

# 有未取回易逝读数时，第一次切换会被拒绝并列出 buffer id
./handoff call --op bus_switch_backup --board BLK-BACKUP-9

# 明确承担/记录损失后执行：先断原台，丢失未取回读数，再接备用板
./handoff call --op bus_switch_backup \
  --board BLK-BACKUP-9 --accept-loss
```

- 原台仍接通时，`bus_connect_backup` 返回 `lever_conflict`；
- 备用板同时只能 `backup_attach` 一个部件，第二个返回 `backup_slot_occupied`；
- 已被服务接收（formal 或 quarantine）的读数视为已取回，切杆不会丢；
- 只有仍在原台设备缓冲、且纸样不可再读的读数会在断原台时记入 `losses`。

建议时机由 `advise` 给出：若锁住的易逝纸样尚未安全读出，或有未取回缓冲，先留在原台；读数进入正式单或待认领区后，再切备用板。

### 重置报告早到/迟拍

```bash
./handoff call --op receipt_record \
  --id RST-1 --kind reset --scope backplane \
  --effective-seq 17
```

- 报告先到：旧 witness 立即失效，之后的输入进入 quarantine，重新逐接口见证后恢复；
- 报告迟来一拍且 `effective_seq` 指向之前的重置点：回执处理时会把那段依赖旧映射的 formal entry 标记为 `reversed`，复制进 quarantine，并在工单中留下冲正引用；
- 重置点之前的正式记录保持有效；重置后已经重新见证的通道不会被冲正。

这保证最终状态仍满足底线：**归属不明的读数不能作为有效正式记录留在具名工单上。**

## 待认领区保留策略

- `read_sample`、`turn_page` 等离散作业事件默认 `retained`，因为它们可能支持后续认领，重新采集或重建上下文成本高；
- 无法识别的高频/噪声事件标记为 `discardable`，可在审查后清理；
- 认领不是“按当前信任洗白旧数据”：系统使用事件中保存的**到达时来源证据和夹具快照**裁决；
- 来源段缺失时，必须另有保管人 `attestation_add` 对该历史事件作证；仅有“现在装回了可信部件”不够。

## 主要操作

`component_register`、`seal_add`、`install`、`remove`、`witness`、`freeze`、`unfreeze`、`pattern_register`、`fixture_load`、`fixture_lock`、`pattern_unload`、`order_create`、`input`、`buffer_read`、`collect`、`bus_disconnect_original`、`bus_connect_backup`、`bus_switch_backup`、`backup_attach`、`backup_detach`、`handoff_create`、`receipt_record`、`attestation_add`、`claim`、`return_channel`、`advise`、`show`、`reset`。

所有状态变化都追加到 `audit`，完整状态可经 `show` 或 `GET /api/state` 导出。
