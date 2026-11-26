# 签发交回演练后台

一个**零三方依赖**的本地演练程序，用来回答一个问题：

> 备用站恢复了旧站昨晚的业务镜像后，旧站手里的签发能力，到底什么时候才算交回？

答案不是“镜像恢复完成”，也不是“换代命令已发出/已送达”，而是按柜满足以下两个条件之一：

1. 拿到该接收柜出具的、**持久的旧代停用回执**；或
2. 已发给该柜的旧租约，按可信班次明确到期，由代次围栏把该柜划给新代。

本程序只使用档案代号、站点代号、签发代次和虚构接收柜；不连接真实集群、真实柜子或真实档案系统。

## 启动

需要 Python 3.11+（只用标准库）。

```bash
./drill demo                 # 进程内跑全部走查、两种岔站回放和断言
./drill smoke                # 起真实 HTTP 服务，用少量调用完成切站/领单/签发/重启复查（9 项断言）
python3 -m unittest discover -s tests -v

./drill serve --db handback-drill.db --port 8787
# 后台界面： http://127.0.0.1:8787/ui
```

也可以直接看两种回放：

```bash
./drill replay reconnect     # 乙柜中途回联（严格按“班初唯一窗口，错过等下班”）
./drill replay offline       # 乙柜一直离线
./drill replay old-plan      # 另一边界：旧站先办完双柜，下一班整体接回
./drill replay empty         # 两个空柜的完整规则走查
./drill replay reconnect --late-ack
                             # 反事实：若班初命令确已送达、只是回执迟到，
                             # 乙在第二拍回联时可补同一命令的持久回执并办结联签
```

## 核心规则

对每个柜、每个拍点 `t`，后台推导唯一可签站：

| 条件 | 可签站/代次 | 依据 |
|---|---|---|
| 有该柜旧代停用的持久回执 | 新站 / G2 | `durable_deactivation_receipt` |
| 无回执，且 `t <= 3`（旧租约有效期内） | 旧站 / G1 | `valid_old_lease` 或 `...command_without_receipt...` |
| 无回执，但 `t > 3` | 新站 / G2 | `trusted_lease_expiry` |

因此：

- 恢复镜像中写着“我是签发站”，只记录为 `restored_image`，**不参与授权推导**。
- 换代命令仅 `QUEUED` / `DELIVERED` 而没有停用回执，且旧租约仍有效时，新站开柜会得到 `409 station_not_authorized`。
- 一个柜的回执只解决一个柜。
- 单柜凭条：授权、线路正常、柜回条齐全后办结。
- 两柜联签：同一张凭条必须固定一个签发站；两个柜分别在其当刻有效代次接受后才办结。资格完全分裂且未指定未来签发站时，直接返回 `split_authority`；若明确指定未来负责的同一站（例如尽早切柜后的新站），可以先取得当前已归一柜的半张回条，另一柜留下持久待办，之后只补另一张半条。
- 若只拿到一个柜的回条，凭条为 `partial`，已拿到的半张回条和缺柜待办持久化；重试只补缺失柜，不重签已有半张。
- 已办结凭条有唯一 `order_id` / `voucher_id`；重复请求幂等返回原凭条，`resigned=false`，acceptance 数量不增加。
- `POST /api/image/snapshot` 可保存站业务镜像；`POST /api/image/restore` 带 `snapshot_id` 或 `rewind_station_projection=true` 会真正回滚 `orders/vouchers/acceptances/todos`，但保留模拟柜状态和柜侧持久回执。重提同一单号时，后台从柜侧账本恢复旧凭条，而不是第二次签发。只传 `{}` 时仅设置“我是签发站”的陈旧自我声明。
- SQLite 文件提交即持久事实。`POST /api/admin/restart` 会关闭并重开连接；真实 Ctrl-C 后重新 `./drill serve --db handback-drill.db` 也会复查到同样状态。

## 少量 HTTP 请求的走查

服务启动后，另开终端：

```bash
B=http://127.0.0.1:8787

# 1. 从两个空柜开始
curl -s -X POST $B/api/reset \
  -H 'Content-Type: application/json' \
  -d '{"scenario":"empty"}'

# 2. 只送达甲柜换代令，不拿停用回执
curl -s -X POST $B/api/switch/A/deliver
curl -s "$B/api/authority" | python3 -m json.tool
#    authority.A.station == "old-station"
#    basis == "old_lease_valid_command_without_receipt"

# 3. 建甲柜单；此时新站签发会被拒绝
curl -s -X POST $B/api/orders -H 'Content-Type: application/json' \
  -d '{"id":"O-A","cabinets":["A"],"delivery_site":"new-site","deadline_tick":1}'
curl -s -X POST $B/api/orders/O-A/issue \
  -H 'Content-Type: application/json' -d '{"station":"new-station"}'

# 4. 甲柜持久停用旧代，然后领单、签发
curl -s -X POST $B/api/switch/A/ack
curl -s -X POST $B/api/orders/O-A/claim \
  -H 'Content-Type: application/json' -d '{"station":"new-station"}'
curl -s -X POST $B/api/orders/O-A/issue -H 'Content-Type: application/json' -d '{}'

# 5. 重启复查并再次签发：只返回原凭条，不重签
curl -s -X POST $B/api/admin/restart -d '{}'
curl -s -X POST $B/api/orders/O-A/issue -H 'Content-Type: application/json' -d '{}'
```

要模拟“客户又要求乙柜共同接收”，不要改已办结的 `O-A`（已签收凭条不能重签/改单）；另建一张草稿并追加乙柜：

```bash
curl -s -X POST $B/api/orders -H 'Content-Type: application/json' \
  -d '{"id":"O-DRAFT","cabinets":["A"],"deadline_tick":3}'
curl -s -X PATCH $B/api/orders/O-DRAFT \
  -H 'Content-Type: application/json' \
  -d '{"cabinets":["A","B"]}'

# 不指定站：资格分裂，返回 split_authority
curl -s -X POST $B/api/orders/O-DRAFT/issue \
  -H 'Content-Type: application/json' -d '{}'

# 指定这张联签未来由新站负责：先取甲的半张，乙留下待办
curl -s -X POST $B/api/orders/O-DRAFT/issue \
  -H 'Content-Type: application/json' -d '{"station":"new-station"}'
```

若甲已切新代、乙仍在旧租约，不指定站会得到 `split_authority`；指定同一未来站后可保留甲半张和 `waiting_for_generation_boundary` 待办。若两柜资格已归同一新站但乙线路不通，待办会更新为 `missing_cabinet_acceptance`。

## 岔站剧本与切换边界

剧本内置两单：

- `O-NEW-A`：新址甲柜单，限定新址交付，客人只等到第 1 拍末。
- `O-JOINT-AB`：甲乙两柜联签，第 2 拍材料才齐，第 3 拍（班末）到期。

时间轴：

- 第 0 拍：班初唯一换代窗口。甲可达并取得持久停用回执；乙失联。
- 第 1 拍：新站凭甲柜回执立即签发 `O-NEW-A`。
- 第 2 拍：联签材料齐，但甲=新站/G2、乙=旧站/G1，当前不能办结。指定新站为这张联签的固定签发站后，甲可先给半张回条，乙生成“等待代次边界”的待办。
- 第 3 拍：旧租约有效期最后一拍；分裂仍在，重试不会重签甲的半张，也不能让旧站把同一张凭条换站。
- 第 4 拍：下一班开始，且 `t > lease_through=3`；即使没有乙回执，可信租约围栏也把乙判给新站。

两种回放的差异：

### 乙中途回联（严格窗口）

乙在第 2 拍恢复新址线路，但题面说明本班只有班初一次换代联络。后台因此在非窗口拍点拒绝“新送达”换代命令（`switch_window_closed`，下一窗口为第 4 拍）；班初已送达的同一命令，其持久回执仍可迟到确认。第 2、3 拍仍然：

- 新站可签所有“仅甲柜/新址”单，并持有联签的甲半张；
- 旧站可经馆内线签所有“仅乙柜”单；
- 这张联签未齐：乙仍只接受旧站 G1。

到第 4 拍下一班窗口，乙在线，可送达并取得持久停用回执；重试同一张联签时只新增乙回条，甲半张不重签，随后办结。

如果对“中途回联”的解释是：**班初换代命令确已送达乙，只是持久回执当时未回来**，可运行 `./drill replay reconnect --late-ack`。这时第 2 拍可补同一命令的持久回执，材料也恰好齐，联签能在第 2 拍后由新站办结。这正是“已送达但无回执”和“持久回执到手”两种状态的区别。

### 乙一直离线

第 2 拍新站可先取得甲半张；第 4 拍租约到期后，资格推导上乙已归新站，但乙线路仍不通，重试不会新增回条。于是：

- 凭条状态持续为 `partial`；
- 甲的半张回条持久保留；
- 待办从“等待代次边界”更新为“代次已到但线路不可达”；
- 重启后待办仍在；以后乙恢复时，同一站重试只补乙的回条并办结。

### 另一种边界：旧站先办完双柜

如果新址客人明确同意多等一班（`flexible_next_shift=true`），调度器会选择 `old-dual-then-takeback`：

- 班初不切断甲；
- 第 2 拍旧站经馆内线取得甲乙两柜回条，办结联签；
- 第 4 拍下一班再对两柜做持久换代，新站交付延班的甲柜单；
- 旧联签重复请求只幂等返回，不能靠恢复镜像重签。

若客人坚持只等第 1 拍末，则该方案会违反新址单等待期限；调度器默认选择 `early-per-cabinet`，先保 hard deadline 的新址单，并让联签等待资格归一。

## 主要 HTTP 接口

```text
POST /api/reset                         {"scenario":"empty|branch"}
GET  /api/authority?at=2
GET  /api/matrix?at=2                   每单 × 每站的可领/可办结矩阵
GET  /api/plan                          切换边界建议
POST /api/plan/apply                    执行建议中当前可安全执行的切柜
POST /api/clock/advance                 {"to":2} 或 {"beats":1}

POST /api/switch/A/deliver              仅标记换代令送达；非窗口拍点默认拒绝，可传 {"force":true}
POST /api/switch/A/ack                  生成持久停用回执
POST /api/switch/A/succeed              deliver + ack
POST /api/cabinets/B/route              {"route":"new","online":true}

POST /api/orders
GET  /api/orders
GET  /api/orders/{id}
PATCH /api/orders/{id}                  可把单柜请求追加为两柜
POST /api/orders/{id}/claim
POST /api/orders/{id}/issue             首次签发或补半张回条
POST /api/claim-next                    {"station":"new-station"}

POST /api/image/snapshot                保存站业务镜像快照
GET  /api/image/snapshots
POST /api/image/restore                 {}=仅设置“我是签发站”；带 snapshot_id/rewind 可真回滚站表
POST /api/admin/restart                 关闭并重开 SQLite，做重启复查
GET  /api/replay?case=reconnect|offline|empty|old-plan
```

## 代码结构

```text
handback_drill/domain.py    纯规则：代次、租约、共同签发站、线路
handback_drill/store.py     SQLite 持久化和事务/重开
handback_drill/engine.py    切站、领单、签发、半张回条、幂等、重启复查
handback_drill/planner.py   根据期限/未结单/回执选择切换边界
handback_drill/scenario.py  空柜走查、两种岔站回放、旧站先办方案
handback_drill/server.py    标准库 HTTP JSON API
handback_drill/cli.py       demo/replay/smoke/recheck/serve
web/                        无构建依赖的本地演练界面
tests/                      规则与剧本测试
```
