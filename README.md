# 两馆数字展权比对与联合排映台（本地模拟）

零第三方依赖的 Node.js 20 应用。它模拟：

1. 东屿资料馆（甲馆）、西潮影像馆（乙馆）**各自导入并持有本地原册**；
2. 本地卖方回执/相等性接口只在两份当前回执的内部版次封签相等时，于两馆分别确认后签发“同版见证”；
3. 一次性比对会话散会即删除 session、一次性句柄和目录行映射；
4. 联合排映台只接收双方确认同版的发布包、准许公开的合同片段，以及排映所必需的最小放映字段；
5. 改名/版次订正后，按方案 B 由两馆再次配合召开新会话，并在**原工单**上追加复办轮次；
6. 可分别导出四个岗位的资料，并用断言检查越界字段没有进入排映台或对馆导出。

## 运行

```bash
npm start
# 打开 http://127.0.0.1:4318
```

另开终端运行无浏览器自检，并重新生成 `exports/`：

```bash
npm test
```

自检会生成：

- `exports/archive-a.json`、`exports/archive-b.json`：两馆最终单独导出；
- `exports/trust.json`：回执/会话岗导出；
- `exports/desk.json`：联合排映台导出；
- `exports/governance.json`：架构与导出矩阵；
- `exports/screening-sheets/*.csv`：几张本地电子放映单；
- `exports/snapshots/01-initial-desk.json` 等：初始撞期、改名订正后、独占期缩短后的排映台快照；
- `exports/export-matrix.md`：各岗位可单独导出/不能还原的资料。

也可以单独导入两馆目录样例：

```bash
curl -X POST http://127.0.0.1:4318/api/archives/a/import-sample
curl -X POST http://127.0.0.1:4318/api/archives/b/import-sample
```

或在甲馆/乙馆页面选择 `imports/archive-a-catalog.json` / `imports/archive-b-catalog.json`。

## 选择的路径：方案 B

本样例选择题目中的第二种：

> 各馆只持本地原册，用一次性会话比对并分别确认命中项，散会即删除跨轮标识。

因此：

- **没有**一个保管室岗位长期保存两本完整原册或跨轮映射；
- 回执持执文本不暴露内部 `lock`，只读两张回执不能自行判断是否同版；
- 只有受托相等性接口比较内部封签，相等且两馆分别确认后才给见证；
- 散会后 `activeOneTimeSessions` 为空，一次性 `H-A-*` / `H-B-*` 句柄和 session 均删除；
- 日后旧别名更正时，**不能仅凭案号重新计算是否误配**。`POST /api/work-orders/WO-HB-1212/recheck-alone` 会返回 `409 NEEDS_BOTH_PARTIES`；但仍可凭案号读取旧工单和已公开卷。两馆须再开一次会话。

业务工单号 `WO-HB-1212`、见证号 `WIT-*` 是有意留给排映台的审计号；被删除的是能跨会话反查两馆原目录行的临时 session/句柄映射。

## 三个虚构节目与预期结果

| 候选 | 名称相似度 | 回执/见证 | 许可与场次 | 最终处理 |
|---|---:|---|---|---|
| HB | 《雾港回声》 vs 《夜航雾港》 | 初始封签相同，有 `WIT-0001`；订正 `CR-2026-011` 后乙馆旧别名改为导演剪辑版封签 `LOCK-HB-DIRECTORS-CUT` | 澜州都会圈；两份独占期重叠；甲馆先授予，乙馆后授予。乙馆 `SCR-B-HB`，2026-12-12 19:00–21:05，已售 63 张，落入交集 | 初始 `WO-HB-1212` 暂停 `SCR-B-HB`；甲馆 19:30 场按种子规则 `FIRST_IN_TIME_PROCEED`。改名订正并复办原工单后，状态变为 `LIFTED_DIFFERENT_CUT`，乙馆场次解除暂停 |
| LT | 《午夜信笺》 vs 《午夜信札》 | 相似度 0.75，但封签 `LOCK-LT-PAPER` ≠ `LOCK-LT-MAIL`，无同版见证 | 即使两边都有同地域独占和相近晚场，也不进入排映台 | 不生成 `REL-LT` / 工单 / 暂停；本地放映单保留 `AWAIT_SAME_EDITION_WITNESS` |
| SP | 《春分特快》 vs 《春分列车》 | 封签相同，有同版见证；之后保持同版 | 澜州都会圈，两份独占。甲馆有 12/19 14:30 下午场（已售 41）和 19:30 晚场（已售 77）。`AMEND-LIC-A-SP-01` 把甲馆独占截止从 22:30 缩短到 18:00，普通放映权仍到 12/31 | 复办 `WO-SP-1219`：下午场 `SCR-A-SP-PM` 仍在双重独占交集内，暂停；晚场 `SCR-A-SP-EVE` 在交集外，`PLAYABLE` |

### 关于“先授予优先”

联合排映台种子规则写在 `state.desk.protocol.priorityRule`：双重独占交集内，比较两份公开片段中的 `grantedAt`，后授予方的已售场次标记 `PAUSE`，先授予方标记 `PROCEED_FIRST_GRANT`。

这是两馆可自行替换的本地受案约定，不是法律意见。若两馆没有承认先授予优先，保守做法是把交集内所有已售场次都置为 `PAUSE_PENDING_RIGHTS_CURE`；本样例为了把追溯结果落到明确的“那一场”，采用了先授予优先规则。SP 下午/晚场的区分不依赖该优先级：下午场无论采用哪种规则都在交集内。

## 岗位导出边界

- **东屿/西潮馆**：本馆原目录、自己的回执持执文本、完整许可（含 `feePrivate`、`internalNote`）、本馆放映单和确认记录；不能导出对馆目录、合同或散会句柄。
- **回执/会话岗**：回执号、别名、内部版次封签、订正台账、见证状态、活动会话；最终活动会话为空。它不持有价格、完整合同、放映单或售票数。
- **联合排映台**：确认同版后的 `confirmedReleases`、公开合同片段、最小放映字段（场次号、放映单号、起止时间、已售数）、工单与复办轮次。自检断言其 JSON 不含 `feePrivate`、`internalNote`、LT 近似名标题或 `RCPT-A-LT`。
- **治理导出**：架构选择和完整导出矩阵。

## 主要 HTTP 接口

```text
POST /api/demo/reset-and-prepare
POST /api/demo/correct-cut-and-replay      # CR-2026-011 + 新会话 + WO-HB-1212 复办
POST /api/demo/shorten-sp-and-replay       # AMEND-LIC-A-SP-01 + WO-SP-1219 复办
POST /api/work-orders/:id/recheck-alone    # 方案 B 下返回 409
GET  /api/role/:post                       # archive-a | archive-b | trust | desk | governance
GET  /api/export/:post                     # 同数据并带下载文件名
POST /api/archives/:side/import-catalog    # body 为目录数组或 { catalog: [...] }
```

细粒度会话接口也已保留：`/api/sessions/open`、`/api/sessions/:id/submit`、`/api/sessions/:id/confirm`、`/api/sessions/:id/acknowledge`、`/api/sessions/:id/close`。

## 文件结构

```text
server.js                  零依赖 HTTP 服务/静态页面
src/system.js              状态、比对、边界投影、工单复办和场景规则
public/                    本地控制台页面
imports/                   两馆分别导入的目录 JSON 样例
scripts/selftest.js        断言 + 生成 exports/
exports/                   最近一次自检生成的岗位导出和快照
```

这是逻辑边界清晰的单机模拟：所有状态在同一个 Node 进程中，便于审计和测试。真实部署若要满足更强的岗位隔离，应把两馆原册、受托回执服务和排映台拆成独立进程/存储，并把这里的不透明封签替换成双方认可的密码学 PSI/签名见证或保管室方案。
