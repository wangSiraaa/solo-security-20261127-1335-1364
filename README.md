# 隔离式本地备份库（虚构色样）

两家工作室（甲公司、乙工作室）各有一个**逻辑库**，共用机房里的**物理仓库**；
另设第三租户（丙）用于演示。每册是**有序页**，页可以**重复**。

系统需要同时保证：

- **租户间隔离**：甲只能改自己馆藏；同一份“获准预约的上传”在乙的回单上，
  不能因为甲碰巧有相同页而变便宜，也不能暴露“已有此页”。
- **可恢复版本**：删册只移除该册在本户的引用；别家仍可恢复自己的那册。
- **扣额可解释**：预约时“冻结”额度，上载结清；退款、吊销都记成账本变动。
- **共享实块**：机房内部按内容 SHA 做物理去重，但对客户只有“逻辑配额”，
  物理节省单独记在管理员侧。

## 设计与信任边界

| 角色 | 知道什么 | 不知道什么 |
| --- | --- | --- |
| 甲 / 乙 / 丙 客户端（`client.TenantClient`） | 本户的册、页、预约、回单、还原字节 | 别家馆藏；跨户相等关系 |
| 机房受托存储进程（`plant.Plant`） | 全库引用、内容 SHA、物理实块、回收作业、账本 | — |

**库房信任谁**：物理仓库只信任**受托存储进程**。它被明确允许看见
“跨户内容相等关系”，并据此做物理实块去重；两个租户库只在自己作用域内被信任，
对外只有 `TenantClient` 暴露的本户视图。

**退款承诺保护哪项资源**：保护的是**逻辑独占配额**——每户
`quota_bytes - (reserved + committed)`，不是物理实块。共享实块省下的物理空间
不返还客户，只记在 `admin_ledger`（`physical_saving`）。客户删册/撤约时，
退还的是本户逻辑字节，按真实合同变化；别家的物理共享页不受影响。

**客户端不接收全局摘要索引**：回单/还原结果只描述本户，不含别家摘要、
卷号、引用计数，也不提示“别家已有此页”。

## 存储模型

- **物理层**：`physical_blobs` 以内容 SHA 为键，**跨户共享**，按 `refcount` 存活，
  状态 `live → pending_reclaim → 回收删除`。这是管理员清册的真实占盘。
- **逻辑层**：`logical_blobs` 以 `(tenant, sha)` 为键，**户内去重**，是客户计费口径。
  同一页被别家也存过时，本户仍按“首次入账”全价计费，回单不提示别家已有。
- **页**：`pages(tenant, volume_id, page_no)` 保存本户有序页引用；页可重复
  （不同 `page_no`、同一 `sha`），户内重复页只计一次逻辑费。

因此物理占盘 < 逻辑计费，差额即管理员侧的物理节省；两者分账。

## 预约、上载与总容量不足时的受理

```
reserve(tenant, volume_id, [(page_no, spec)...]) -> request_id，冻结 declared 额度
receive(request_id, page_no, page_bytes)          -> 校验 page_id+sha 后落盘
```

- `reserve` 成功条件：`declared <= quota_bytes - reserved - committed`。
- `receive` 先校验字节 SHA 与长度是否匹配预约快照；不匹配报 `page_mismatch`。
- **总容量不足**：若新占盘会超过 `physical_capacity`，该页进入 `pending_capacity`，
  **不入 committed、不占物理块**，但预约冻结额度保留、预约仍有效。
  读取（还原）对这些页返回不可用；空间释放后 `retry_pending(request_id)` 再落盘。
- 上传是否“本户已有此页”**只在本租户内判断**（`already_in_this_tenant`），
  跨户一律按新页处理。

## 账本

- 客户账本 `ledger`（按租户 append-only）：`reserve_hold / commit / commit_repeat /
  release_hold / refund_commit / release_retention / settle_release / refundable_after`。
- 管理员侧 `admin_ledger`：`ingest / physical_saving / reclaim`，物理与逻辑字节分列。

## 保留订约与宽限期

- 上载结算时为本户该卷建立**有效合同**（保留订约）。
- `release_retention`：撤销保留订约，记 `released_at`；**宽限期内数据仍在、仍可还原**，
  客户账不变。
- 宽限期内提前结算报 `grace_not_elapsed`；宽限期满后还原报 `retention_expired`，
  `settle_release` 按真实合同退还本户逻辑额度与物理引用。

## 回收与重开

- 删页/删册时，物理 `refcount` 归零的块转为 `pending_reclaim` 并登记为回收候选。
- `make_reclaim_job` 建立作业；`reclaim_step(count)` 可只完成部分（模拟中断）。
- `reopen_reclaim(job)` 重开未完成作业，完成剩余项。
- `pages_available_to_next_tenant(request_id)`：**凭请求号**查出已释放、
  可交给下家使用的页与尚未释放的数量。

## 文件

- `src/plant.py`：受托存储进程（物理仓库 + 预约/账本/回收）。
- `src/client.py`：租户客户端（只拿本户视图，不接收全局摘要）。
- `src/harness.py`：三册小文件的端到端演示。
- `src/unittest_isolation.py`：性质断言。

## 运行

```bash
python3 src/harness.py
python3 -m unittest discover -s src -p 'unittest_*.py'
```

`harness.py` 依次演示：预约+上载（甲乙碰巧同页 red，丙册内含重复页）→
还原乙册取快照 → 只删甲的红页并验证乙字节与客户账不变 →
保留所有页撤销乙的保留订约、宽限期内可还原、期满按真实合同变化 →
管理员做一次未完成回收再重开、凭请求号查可给下家的页 →
另一机房实例演示总容量不足时的挂起与重试。
