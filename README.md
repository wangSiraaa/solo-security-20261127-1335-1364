# 数字卷宗受理与阅卷台（取证来源模拟）

这是一个零第三方依赖的 Python 标准库实现，用短文本块、文本目录和 JSONL 操作日志模拟卷宗盘。它把“受理端”和“阅卷端”分开：

- `forensic_desk/intake.py`：保全员/受理端。接收卷宗、计算物理清单哈希、绑定来源/卷代/可读范围/派生权限、生成唯一交接记录、构建写保护阅盘台或委托方 COW 层。
- `forensic_desk/review.py`：调查员/阅卷端。提出查件请求，返回原始块/页观察、日志重放条目、缺证结论和出处。
- `forensic_desk/ledger.py`：两端共用的只追加、哈希链记录簿。结论只新增，不覆盖。
- `forensic_desk/station.py`：第三方离线复核写保护阅盘台。
- `forensic_desk/cow.py`：每名调查员独立的一次性写时复制层。
- `forensic_desk/server.py`：可选的本地 JSON HTTP 两端接口。

## 先回答核心问题

看见一行时，不能只凭“阅卷器现在显示它”判断来源。本系统把结论分成不同证据类别：

| 状态/标识 | 含义 | 能否称为接盘原始行 |
|---|---|---|
| `PHYSICAL_PRESENT_AT_INTAKE` / `PHYSICAL_ORIGINAL` | 在接盘时的物理 `directory.idx` 中逐行核到，附行号、原始行和哈希 | 可以 |
| `REPLAYED_PRESENT` / `DERIVED_FROM_PHYSICAL_JOURNAL_REPLAY` | 由原始目录加未合并日志确定性重放得到，标识为 `DRV-REPLAY-<hash>` | 不可以；只能称旧应用重放/写回后可见 |
| `INVESTIGATOR_WORK_PRODUCT` | 只出现在某调查员私有 COW 上层，且不能由原始日志重放解释 | 不可以 |
| `INVESTIGATOR_FIRST_WRITE_PROVENANCED` | 已追加一条真实来源记录，证明是该调查员首次写入 | 不可以沿用原始存在证明 |
| `ABSENT_IN_AUTHORIZED_SCOPE` | 在当前授权块/页范围、物理目录和日志重放内未找到 | 只能作范围内缺证，不能断言范围外不存在 |
| `AUTHORIZATION_REQUIRED` | 范围或派生权限不足；不返回请求内容 | 需补授权 |

## 运行测试和完整故事

```bash
python3 -m unittest discover -s tests -v
python3 examples/run_story.py
```

测试覆盖：

1. 写保护阅盘台可被第三方重复核验，老阅卷器直接写回被阻止。
2. 未重放改名、日志重放名、调查员再次改名；旧结论记录不被后续写入覆盖。
3. 另一卷中先证明原始行存在，再为调查员首次写入补唯一来源记录。
4. 页范围收窄后，同一块/同一证据不交出邻页文本；旧台账记录也不保存可泄漏的页正文。
5. Alice/Bob 各自 COW 目录互不可见，写入不进原件。

示例盘已生成在 `disks/`：

```bash
python3 -m forensic_desk init-fixtures --out disks
```

## 受理时绑定了什么

一次收件只允许一条 `INTAKE` 交接书记录（案例内 ID 通常为 `REC-000001`），内容包括：

- `source`：来源/封存盘标识；
- `generation`：卷代和接盘快照；
- `signed_by`：保全员和签署交接书的委托人；
- `readable_blocks` / `all_blocks`：可读物理件和块范围，含 `DIRECTORY`、`JOURNAL`、`LAYOUT`、`BLK...`；
- `page_ranges`：可调页范围，`null` 表示不按页限制，`[]` 表示无页；
- `permissions`：准许的派生操作；
- 全部物理文件的路径、大小、SHA-256 清单及 `manifest_sha256`。

交接记录本身进入哈希链台账。之后扩权或缩小页范围只追加 `AUTHORIZATION_AMENDMENT`，并记录其引用的 `source_manifest_sha256`；来源和接盘清单不变。

## 两种采集方式

### A. 准许原始块外移：写保护阅盘台

受理时使用 `--mode write_protected_station`。系统只复制授权块和物理目录/日志到 `write_protected_station/`，随后将文件置为 `0444`、目录置为 `0555`，并生成：

- `handoff.record.json`：唯一交接书/无争议记录；
- `station-manifest.json`：导出件哈希；
- `directory.idx`、`operation_log.jsonl`：供第三方重复核验物理目录和原日志。

第三方不需要本系统的阅卷结论，可直接运行：

```bash
python3 -m forensic_desk verify-station runs/story/cases/1401-station/write_protected_station
# 或不用本项目代码，在阅盘台目录中独立执行：
sha256sum -c SHA256SUMS
```

要求实际写回 `directory.idx` 的老阅卷器不能直接在该台开盘：策略和只读权限都会返回
`LEGACY_DIRECT_OPEN_BLOCKED_WRITE_PROTECTED`。

### B. 卷留委托方：一次性 COW

受理时使用 `--mode custodian_cow`。原始卷复制到委托方侧 `vault/` 并置只读；调查员通过 `ReviewDesk` 或 HTTP API 请求。每名调查员得到：

```text
work/<investigator>/upper/
work/<investigator>/layer.json
```

老阅卷器只把 `directory.idx` 复制到该私有上层并重放、写回。每次写入留下：

- 写入路径；
- 复制上层前的下层哈希；
- 写入前/后哈希；
- 对应日志事务和重放结果哈希；
- 操作人、时间和台账记录 ID。

此模式默认不给 `read_raw_content` / `export_raw_blocks`，因此不向调查员交出原始块正文，也不承诺异地复验盘的物理布局；物理哈希核验结果会明确给出
`offsite_physical_reverification=false`。

## 哪些请求必须补授权

以下情况返回 `AUTHORIZATION_REQUIRED`，由保全员追加授权，而不是自动放行：

- 请求不在 `readable_blocks` 中的块或 `page_ranges` 外的页；
- COW 模式中请求原始块/页正文或外带原始块；
- 调查员再次改名、新增行等新的写入操作：需要 `write_work_copy`；
- 在写保护台直接执行写回，或未选择 COW 通道却要求受管写回；
- 没有 `replay_journal` 权限却要出具完整缺证结论；
- 页范围变更后访问邻页；可追加 replacement page scope，但不能改接盘来源。

日志重放和老阅卷器首次在 COW 中重放写回属于受理时可预授的派生操作；调查员后续主动改名/写行被建模为新的写入，默认不授。

补授权示例：

```bash
python3 -m forensic_desk amend \
  --case-dir runs/story/cases/1401-cow \
  --target investigator-alice \
  --by 保全员甲 \
  --reason "委托人补准一次性 COW 改名" \
  --add-permission write_work_copy
```

## 常用 CLI

```bash
# 受理：COW，授权目录、日志和三个块，页范围 P100/P201/P301
python3 -m forensic_desk intake \
  --case-dir /tmp/case-1401 \
  --disk disks/case-1401 \
  --case-id CASE-1401 \
  --source "虚构盘 SN-1401" \
  --generation "G1" \
  --signed-by "保全员甲/委托人乙" \
  --mode custodian_cow \
  --blocks DIRECTORY,JOURNAL,LAYOUT,BLK100,BLK200,BLK300 \
  --pages P100,P201,P301

# 查物理旧名 / 日志重放名 / 缺证
python3 -m forensic_desk entry --case-dir /tmp/case-1401 --as alice \
  --name 未重放改名-旧名.txt
python3 -m forensic_desk entry --case-dir /tmp/case-1401 --as alice \
  --name 日志重放-新名.txt
python3 -m forensic_desk absence --case-dir /tmp/case-1401 --as alice \
  --name 不存在.txt

# 老阅卷器在私有 COW 层开盘
python3 -m forensic_desk open-legacy --case-dir /tmp/case-1401 --as alice

# 补 write_work_copy 后再次改名
python3 -m forensic_desk rename --case-dir /tmp/case-1401 --as alice \
  E100 调查员再次改名.txt

# 取旧结论/出处并验证哈希链
python3 -m forensic_desk record --case-dir /tmp/case-1401 REC-000004
python3 -m forensic_desk provenance --case-dir /tmp/case-1401 --as alice REC-000004
python3 -m forensic_desk verify-ledger --case-dir /tmp/case-1401
```

## HTTP 两端

启动本地服务：

```bash
python3 -m forensic_desk serve --cases-root runs/http-cases --port 8765
```

受理端：

```bash
curl -sS -X POST http://127.0.0.1:8765/intake/cases/demo \
  -H 'Content-Type: application/json' \
  -d '{
    "disk_path": "/workspace/disks/second-volume",
    "source": "SN-2",
    "generation": "G3",
    "signed_by": "委托人丙",
    "collection_mode": "custodian_cow",
    "readable_blocks": ["DIRECTORY","JOURNAL","LAYOUT","BLK001"],
    "page_ranges": ["P001","P002"]
  }'
```

补原始页读取授权（COW 默认只给逻辑结果，不给原始正文）：

```bash
curl -sS -X POST http://127.0.0.1:8765/intake/cases/demo/amendments \
  -H 'Content-Type: application/json' \
  -d '{
    "target_investigator": "reviewer-d",
    "authorized_by": "委托人丙/保全员",
    "reason": "准许在委托方屏前观察 P001 原始页，但不外带块",
    "add_permissions": ["read_raw_content"]
  }'
```

阅卷端：

```bash
curl -sS -X POST http://127.0.0.1:8765/review/cases/demo/requests \
  -H 'Content-Type: application/json' \
  -H 'X-Investigator: reviewer-d' \
  -d '{"kind":"page","page_id":"P001"}'
```

共享记录：

```text
GET /cases/demo/records/REC-000001
GET /review/cases/demo/provenance/<record-or-urn>?as=reviewer-d
GET /verify-station?path=<bundle-dir>
```

HTTP 响应中的 `disclosure.text` 是当前授权下可显示的原始正文；`conclusion` 会入台账，但原始页/块正文刻意不写入台账，只写哈希和出处。这样页范围事后收窄时，旧结论不能通过台账副本继续交出邻页。

## 不可变性

`ledger.jsonl` 的每条记录包含：

```json
{
  "seq": 4,
  "id": "REC-000004",
  "type": "CONCLUSION",
  "prev_hash": "...",
  "hash": "..."
}
```

修改任何旧结论都会破坏后续 `prev_hash` / 记录哈希校验。调查员再次改名后，旧的
`PHYSICAL_PRESENT_AT_INTAKE` 结论仍保存接盘时原始行；新事实产生新的 `WORK_WRITE` 和
`CONCLUSION` 记录。

## 模拟边界

这里的“签名交接书”“写保护”和“委托方/调查员隔离”是可运行的程序策略与文件权限模拟，不是真实 PKI、WORM 存储或操作系统用户隔离。真实取证部署应替换为：委托人/保全员数字签名、独立保管账户、WORM/对象锁、安全取证镜像、可信时间戳和正式鉴权服务。本项目证明的是来源分类、授权判定、COW 隔离和不可变结论的数据流。
