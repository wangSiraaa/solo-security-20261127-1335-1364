# 录音插件升级服务 + 模拟录音设备

## 结论：不会

新版设备说明把能力拆细，**不会**把老用户当年同意的“录一段”扩张成常驻监听。

旧授权 `classic-recorder-v1 / record_segment` 只还原为两项能力和一项强制义务：

- `capture.segment`：响应一次真实按钮操作，开一个段；
- `clip.save_segment`：把这一段准作普通工程片段保存；
- 义务：`device.input.close_on_stop`，停止、撤销或故障补偿都必须关闭设备输入。

以下都是**新能力**，只展示新说明不构成同意，必须由在场用户逐项明确授予：

- `monitor.prelisten`：预听；
- `capture.scheduled`：定时采集；
- `retain.original`：保留原声/原始包；
- `organize.split_tracks`：分轨。

后台数据库写 `stopped/已停`、恢复同意、展示新说明，都不能代替设备的签名停采回执。

---

## 我选择的迁移路线：保留旧按钮兼容代理

本交付安装一层 `old-button-semantics-proxy`：

```text
一次旧按钮按下
   └─ 服务还原旧授权 + 校验在场/会话/设备生命周期
       └─ 设备 start（一个 operation/segment/session）
           ├─ 文本帧（替代音频）
           ├─ 正常 stop ──────────┐
           ├─ 用户撤销 ───────────┤
           └─ 设备故障 → cleanup ─┴─ 设备 Ed25519 终态回执
                                      └─ 验签 + 包哈希核对
                                          └─ 按“当前”同意准入并进入轨道
```

代理把 **开始、结束、故障清理** 绑定为同一项旧按钮操作：

- 每个段只有一个设备 `operationId` 和用户会话；
- 正常停止、中途撤销、故障都走向同一个终态回执；
- 设备终态后拒绝后续帧；
- 服务重启后不“续录”，只做幂等停止/清理和证据对账。

### 安全交接边界

安全边界是：

> **服务验签通过的设备终态回执（`stop_receipt` 或 `fault_cleanup_receipt`），且文本包哈希与回执一致。**

边界之前，操作仍由旧按钮代理负责，不能当作已完结片段，也不能交给无人在场的新能力。边界之后，才允许：

1. 按当前有效同意把包准入为轨道片段；
2. 使用已经明确授予的项目级分轨等新能力；
3. 给制作人签发“本 operation 在 `finalSeq` 后没有属于本段的帧”的完结证明。

### 重启后谁能继续

- **安全对账器（用户缺席也可）**：只能停止/清理未终态段、接收滞留回执、按原指定轨道和当前同意完成准入；不能开始新采集。
- **已持久化的明确新授权**：例如用户前一天在场时专门授予该新工程的 `organize.split_tracks`，次日后台可以执行分轨。
- **新的在场用户本人**：服务中的 presence token 只存内存，重启即失；次日缺席时不能编辑只有旧按钮标号的老工程、不能补授新能力、不能新开段。重新到播音台前建立 presence 后，可以开始一个新段。
- **任何人都不能“恢复同一个未完结段并继续录”**；只能终态清理，或由新的在场操作另开新段。

---

## 运行

要求 Node.js 20+，无第三方依赖。

```bash
npm start -- --reset
```

打开 <http://127.0.0.1:4399/> ，可在页面中实际完成：

1. 建立在场会话；
2. 开一段；
3. 写入小文本帧；
4. 停一段；
5. 查询 `proj_new_multitrack` 的轨道片段；
6. 测试仅试听/禁保存、回执滞留、后台“已停”、撤销、故障清理和分轨。

服务：<http://127.0.0.1:4399>  
模拟设备：<http://127.0.0.1:4400>

也可以分别启动：

```bash
npm run device
npm run service
```

---

## 自动验收

```bash
npm run accept
npm test
```

当前包含 6 组真实 HTTP 场景：

1. 实际开段、写文本、停段、新工程轨道查询、分轨、完结证明；
2. 旧同意改成仅试听/禁保存后，同一个包不能准入；恢复同意后，设备回执滞留仍不能签完结证明；
3. 次日缺席 + 服务重启：孤儿段被停止/准入，缺席不能编辑老工程或授予能力，已有项目级分轨授权仍有效；
4. 中途主动撤销：撤销传播到设备，后续帧被拒，丢弃段不得保存，但设备回执可出停采证明；
5. 新设备说明只展示不授权，设备退役后旧授权不可继续使用；
6. 故障路径必须完成绑定清理并取得 `fault_cleanup_receipt`。

---

## 手工 HTTP 走查

下面命令使用 `curl` 和 `jq`。

```bash
BASE=http://127.0.0.1:4399

# npm start 已自动引导；独立服务也可手动幂等引导：
curl -sS -X POST "$BASE/api/bootstrap" -H 'content-type: application/json' -d '{}'

# 在场
TOKEN=$(curl -sS -X POST "$BASE/api/presence" \
  -H 'content-type: application/json' \
  -d '{"userId":"u_legacy"}' | jq -r .token)

# 开一段
OPEN=$(curl -sS -X POST "$BASE/api/agent/old-button/press" \
  -H 'content-type: application/json' \
  -H "x-presence-id: $TOKEN" \
  -d '{"projectId":"proj_new_multitrack","trackId":"trk_take","autoAdmit":true}')
SEG=$(printf '%s' "$OPEN" | jq -r .segment.segmentId)

# 用小文本代替音频
curl -sS -X POST "$BASE/api/segments/$SEG/frames" \
  -H 'content-type: application/json' \
  -d '{"text":"voice: 验收人实际开了一段"}'
curl -sS -X POST "$BASE/api/segments/$SEG/frames" \
  -H 'content-type: application/json' \
  -d '{"text":"room: 停一段后进入新工程轨道"}'

# 停一段，等待设备签名回执并自动准入
curl -sS -X POST "$BASE/api/segments/$SEG/stop" \
  -H 'content-type: application/json' -d '{}'

# 以新工程查询进入轨道的片段
curl -sS "$BASE/api/projects/proj_new_multitrack/tracks" | jq

# 完结证明
curl -sS -X POST "$BASE/api/segments/$SEG/completion-certificate" \
  -H 'content-type: application/json' -d '{}' | jq
```

### “仅试听、禁止保存”和回执滞留

```bash
# 先将旧同意收窄
curl -sS -X POST "$BASE/api/consent/preview-only" -H 'content-type: application/json' -d '{}'

# 对一个已拿到设备回执、但尚未准入的同一个包执行：
curl -i -X POST "$BASE/api/segments/$SEG/admit" \
  -H 'content-type: application/json' -d '{}'
# 403 SCOPE_NOT_GRANTED；轨道查询中不会出现该 segment 的 clip

# 恢复旧同意
curl -sS -X POST "$BASE/api/consent/restore" -H 'content-type: application/json' -d '{}'

# 让设备滞留某个 operation 的停采回执
curl -sS -X POST "$BASE/api/device/hold-receipt" \
  -H 'content-type: application/json' \
  -d '{"operationId":"<op>","hold":true}'

# 后台可以写“已停”
curl -sS -X POST "$BASE/api/segments/$SEG/backend-stopped" \
  -H 'content-type: application/json' -d '{"note":"后台手工已停"}'

# 但没有验签设备回执时，完结证明必须是：
curl -i -X POST "$BASE/api/segments/$SEG/completion-certificate" \
  -H 'content-type: application/json' -d '{}'
# 409 DEVICE_STOP_RECEIPT_REQUIRED

# 放行设备回执、对账后才能签证明
curl -sS -X POST "$BASE/api/device/release-receipt" \
  -H 'content-type: application/json' -d '{"operationId":"<op>"}'
curl -sS -X POST "$BASE/api/segments/$SEG/reconcile" \
  -H 'content-type: application/json' -d '{}'
curl -sS -X POST "$BASE/api/segments/$SEG/completion-certificate" \
  -H 'content-type: application/json' -d '{}'
```

---

## 次日缺席试验

```bash
npm start -- --reset
# 页面中建立在场、授予 proj_new_multitrack 的分轨、录完一段；
# 再开一段并写一帧，但不要点停。

# 重启服务进程（保留 ./data），不要建立 presence。
npm start
```

重启后服务会自动停止孤儿段并核对设备回执。此时：

```bash
# 无 X-Presence-Id：403 FRESH_USER_PRESENCE_REQUIRED
curl -i -X POST "$BASE/api/projects/proj_old_button_only/edit" \
  -H 'content-type: application/json' -d '{"name":"缺席改名"}'

# 无 presence：不能新开段
curl -i -X POST "$BASE/api/agent/old-button/press" \
  -H 'content-type: application/json' -d '{}'

# 旧同意没有预听/定时采集：403 SCOPE_NOT_GRANTED
curl -i -X POST "$BASE/api/monitor/prelisten" -H 'content-type: application/json' -d '{}'
curl -i -X POST "$BASE/api/schedules" -H 'content-type: application/json' -d '{}'
```

自动化版本见 `absentNextDayScenario`。

---

## 中途撤销试验

开段后记录返回的 `sessionId`，写入若干帧，然后：

```bash
curl -sS -X POST "$BASE/api/sessions/$SESSION/revoke" \
  -H 'content-type: application/json' \
  -d '{"reason":"user_clicked_revoke_mid_take","discard":true}'
```

服务会调用设备 revoke/stop，设备返回带 `revocation` 的签名终态回执。之后：

- 服务追加帧：`403 SESSION_REVOKED`；
- 直连设备追加帧：`409 INPUT_CLOSED`；
- 准入保存：`403 SESSION_REVOKED`，轨道中无此段；
- 完结证明仍可签发，因为它证明的是**设备确实停采**，不是许可保存。

---

## 主要 HTTP 接口

| 方法/路径 | 作用 |
| --- | --- |
| `POST /api/bootstrap` | 幂等导入虚构老用户、旧同意、设备、老/新工程和兼容代理 |
| `POST /api/presence` | 建立仅存内存的“用户在播音台前”令牌 |
| `GET /api/authorization` | 从事件流重放当前作用域、来源、会话和设备生命周期 |
| `GET /api/upgrade/assessment` | 升级路径、安全边界、重启策略、老工程和未终态段清单 |
| `POST /api/notices/new-device-capabilities/shown` | 记录新说明展示；明确不授权 |
| `POST /api/consent/preview-only` | 把旧同意收窄为仅试听、禁止保存 |
| `POST /api/consent/restore` | 恢复原始旧同意范围 |
| `POST /api/consent/revoke-all` | 撤回用户全部同意 |
| `POST /api/migration/capabilities` | 在场用户逐项授予新能力 |
| `POST /api/agent/old-button/press` | 旧按钮语义开段，要求 presence 和 `capture.segment` |
| `POST /api/segments/:id/frames` | 写入小文本帧；持续重验授权/会话/终态 |
| `POST /api/segments/:id/stop` | 请求设备停采并接收签名回执 |
| `POST /api/segments/:id/reconcile` | 拉取滞留回执或幂等停止/清理 |
| `POST /api/segments/:id/admit` | 按当前同意和哈希把包准作可存片段 |
| `POST /api/segments/:id/backend-stopped` | 只写后台“已停”标记，不能当设备证据 |
| `POST /api/segments/:id/completion-certificate` | 验签设备回执后签发服务完结证明 |
| `POST /api/sessions/:id/revoke` | 中途撤销会话并传播设备停采 |
| `POST /api/segments/:id/fault` | 注入故障并执行代理绑定清理 |
| `POST /api/projects/:id/edit` | 编辑老工程；要求新的在场令牌 |
| `POST /api/projects/:id/split` | 使用已授予的分轨能力拆分文本片段 |
| `GET /api/projects/:id/tracks` | 查询轨道和已进入轨道的片段 |
| `GET /api/device/state` | 经服务代理读取模拟设备真实状态 |

设备自身还有 `/device/operations/:op/stop|revoke|cleanup|receipt|package` 和 debug 回执滞留/故障接口。

---

## 数据与信任边界

- `data/service/service-events.jsonl`：只追加事件流；授权由全部事件重放得到，不依赖某个可能被后台改写的当前状态字段。
- `data/service/service-key.json`：服务 Ed25519 密钥，用于完结证明签名。
- `data/device/device-state.json`：模拟设备 operation、帧哈希、发件箱和真实采集状态。
- `data/device/device-key.json`：设备 Ed25519 密钥，用于停采/清理回执签名。
- 服务与设备间使用本地配对令牌 `X-Pairing-Token`；默认值仅用于演示，可用 `PAIRING_TOKEN` 覆盖。
- 测试使用系统临时目录，不污染仓库 `./data`。

## 目录

```text
public/index.html       验收台 UI
src/core.mjs            加密、JSON/HTTP 工具
src/event-store.mjs     只追加事件存储
src/model.mjs           授权重放、作用域、轨道视图
src/device.mjs          可接收录音操作的模拟设备
src/service.mjs         升级/授权/对账/证明服务
src/scenarios.mjs       可复用验收场景
src/acceptance.mjs      npm run accept
test/system.test.mjs    Node 测试
```
