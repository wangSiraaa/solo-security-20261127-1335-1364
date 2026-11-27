# 刮刮券来历、库存交割与兑换演练

一个零第三方依赖的本地 Python + SQLite + 原生网页演练系统，覆盖：

1. **材料登记**：来料批次、数量、消耗。
2. **密封来源与封包印制**：本地测试适配器签发独立来源、补种记录和连续使用见证；服务只信签名来历，不按暗码“长得随机”判断来源。
3. **库存交割**：封条、原批次/独立来源核验、库房交接单、柜台收货回执。
4. **顾客领取与兑换**：公开票号可查询，暗码只存 HMAC 指纹；兑换记录对暗码指纹加唯一约束。
5. **事故更正**：同备份导致双机共用旧段、确认重印后，已兑现/已领取/未领取券分别处理；回执可只改指向另一叠，原叠留查但不再算该柜台已签收。

## 运行

需要 Python 3.11+，无需安装依赖。

```bash
# 终端完整演练（默认写入 data/demo.db，并重置该演练库）
python run.py demo

# 或使用模块入口
python -m coupon_demo demo --db data/demo.db

# 本地网页/API（以下两种等价；无参数默认启动 127.0.0.1:8000）
python run.py
python run.py serve --db data/demo.db --host 127.0.0.1 --port 8000
# 或允许端口映射/预览：./start.sh（默认 0.0.0.0:8000，可用 PORT 覆盖）
# 打开 http://127.0.0.1:8000
```

运行测试：

```bash
python -m unittest discover -s tests -v
```

网页右上角可点 **“一键完整演练”**。印制结果会一次性显示几张演示暗码，用于模拟实物覆盖层；通用状态接口和页面不会返回顾客提交的暗码或暗码指纹。

## 业务结论：恢复后哪一叠能发到柜台？

不是简单按“甲机/乙机”或公开票号判断，而是同时满足：

- 来历为以下之一：
  - 亲眼见证装入的**全新独立来源**；或
  - 有旧机专属连续签名见证 + 完好实物封条 + 旧交接单齐全，从而保住的**可证原批次**；
- 该段没有被确认两机共用并重印，券暗码没有跨叠重复；
- 封条完好；
- 库房交接完成，且该叠持有当前有效的柜台收货回执。

因此：

- **新种子站优先路径**：冻结两机旧段后，乙机用新独立来源印出的新叠，在封条和柜台签收齐全后可以发。甲机昨夜旧叠不会被新种子“补证”，仍须旧柜核验和交接单补齐。
- **旧柜优先路径**：若末份甲机专属连续记录和封条核验通过，且之后旧交接单齐全、没有乙机同段重印，甲机实名预订叠可以交割/发给已预订顾客；乙机临时活动券在独立来源建立前不能借用旧段形成可收兑券。
- **一旦确认乙机已从同一旧段印过同一段**：甲、乙两叠的重叠暗码不是两张独立券。未领取券隔离，已领取未兑现券冻结核查，已兑现福利保留原记录且不能靠重启/重开软件再发一份。
- 更正柜台回执指向另一叠后，原叠仍可继续查验，但没有当前有效回执，不能当成该柜台已经领走的券。

一键演练最终可交割的是干净独立叠 `STACK-HAPPY` 和事故后新来源/回执更正叠 `STACK-B-NEW`；`STACK-A-RESERVED` 与 `STACK-B-OLD-REPRINT` 均在核查/隔离中。

## 两种后续的更正矩阵

| 券在发现问题时的状态 | “旧交接单后来齐全” | “确认另一台已重印同一段” |
| --- | --- | --- |
| 未领取（`printed`/`reserved`/`in_inventory`/`at_counter`） | 补交接链；无重印时转 `at_counter`，可正常领取 | `quarantined`，建独立来源换发单，不得发柜台/顾客 |
| 已领取、未兑现（`claimed`） | 补记 `late_handoff_regularized_claimed`，持有事实有效 | `review_hold` + `reprint_claimed_freeze`，冻结兑换并建更正/换发单 |
| 已兑现（`redeemed`） | 兑换和福利记录不变 | 状态仍为 `redeemed`，更正为 `honored_no_reissue`；不建自动换发单，重启数据库也不能二次兑现 |

## 主要本地 API

所有写接口均为 JSON POST；错误返回 `{ok:false,error,message,details}`。

- `POST /api/materials`：登记来料。
- `POST /api/backups` / `POST /api/backups/restore`：建立/恢复共用备份。
- `POST /api/station/new-seed`：新种子站：装独立源并冻结旧段。
- `POST /api/station/old-cabinet`：旧柜核验封条和旧机专属连续见证。
- `POST /api/packets/print`：封包印制并签发连续使用见证。
- `POST /api/handoffs`、`POST /api/handoffs/{id}/complete`：交接单与“后来齐全”。
- `POST /api/deliver`：正常库存交割和柜台签收。
- `POST /api/coupons/{ticket}/claim`：柜台领取；`.../legacy-claim`：补录回执未全前已发生的领取。
- `POST /api/redeem`：顾客暗码兑换。请求暗码不会回显或写审计。
- `POST /api/segments/{id}/witness`：把段见证更正为两机共用/确认重印，并自动办券更正。
- `POST /api/segments/{id}/confirm-reprint`：根据双机签名见证和重复暗码指纹认定重印。
- `POST /api/receipts/{id}/retarget`：只把柜台回执当前指向改成另一叠，保留原叠审计。
- `GET /api/state`：读取完整公开状态（暗码和暗码指纹已从见证读模型中脱敏）。
- `GET /api/evaluate?ticket_no=...&counter_id=...`：查看某券能否领取/兑换及阻塞原因。
- `GET /api/segments/{id}/continuation?machine_id=...`：该机器能否继续原段。

## 设计说明

- `LocalSealedSourceAdapter` 使用本地 HMAC 密钥模拟密封源的签名；真实系统应替换为 HSM/密封服务的验签客户端。服务不把“数值看起来随机”作为来历证明。
- 明文暗码只由印制结果/一次性打印清单返回；数据库仅保存 HMAC 暗码指纹。
- `redemptions.secret_hash` 有唯一约束，因此同一暗码即使贴在两个不同公开票号下，也只能成功兑现一次。
- SQLite 文件持久化；“重开软件”只会重新读取已提交的兑换和更正记录，不会重置福利状态。
