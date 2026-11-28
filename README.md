# 文物修复合作社：离线查册器与封件寄送台

一个完全离线、本地生成人物/钥匙/正文的分布式收件钥名册与保密封件系统。
不依赖任何网络服务，密码学全部使用 Node.js 内置的成熟 `node:crypto`（OpenSSL）：

- 名册页签名、封件发件签名、回执签名：**Ed25519**
- 封缄正文：一次性 **X25519 ECDH + HKDF-SHA256 + AES-256-GCM**
- 页标识与链完整性：规范化 JSON（canonical JSON）上的 SHA-256

## 领域映射

| 业务说法 | 系统机制 |
| --- | --- |
| 名册截止页、旧页只能续写 | 哈希链 + 分库 Ed25519 签名的仅追加页（类 git 内容定址） |
| 两处页数相同却接不到同一旧页 | 分叉检测：共有祖先 + 双方各自独立验签的分歧报告 |
| 分库签收一页只表示本地见过 | `intakeReceipts`，不构成跨库共同认可 |
| 独立见证席只签自己旧页的延长 | 见证席持有旧页 id，仅对其延长链、且按收件名限定范围作证 |
| 先受理换钥、碰头对账 | 本地分支 + 双签合并页；撤销取并集且永久黏住 |
| 共同旧页不能让被撤钥复活 | 视图折叠时撤销状态优先，任何后续 add/rotate 都被忽略 |
| 照片/报价交给不同的人 | 条目按 `materialsKinds` 限定允收材料；轮班单按名+材料派件 |
| 封件 | 收件人封装信封 + 发件人签名，AAD 绑定封件头 |
| 旧钥不能打开改寄的新报价 | 重封生成全新临时钥与 AAD；GCM 认证保证旧私钥解密失败 |
| 已交内容按回执对账 | 收件钥签名的回执，验真 + 摘要核对；已交付件不得重封 |
| 一张新局部图重叠两张旧图 | 网格单元剖分，单元只结一次价 |
| 对不准的一笔单独挂起 | `suspended`，不拆入相邻工项 |
| 接班席只能取获准局部/定位点/已结案 | `WorkLedger.seatView` 视图过滤 |

## 目录结构

```
src/
  util.mjs       规范化 JSON、页哈希
  crypto.mjs     Ed25519 / X25519 / HKDF / AES-GCM 薄封装
  roster.mjs     Ledger 哈希链、分叉对账、合并双签、RosterView 路由裁决
  agent.mjs      Vault / Witness / Seat（本地人物与钥匙）
  mail.mjs       MailDesk 封件状态机、ShiftRoster 轮班单、回执对账
  workitems.mjs  局部病害图、网格结价、接班席视图
  cli.mjs        离线命令行查册器/寄送台
test/            node:test 单元测试（23 个）
demo/scenario.mjs 端到端故事场景（44 项断言）
```

## 运行

```bash
node --test test/      # 单元测试
node demo/scenario.mjs # 完整叙事场景
```

### CLI 示例

```bash
# 建名册（生成本地分库签名钥与首个收件席钥）
node src/cli.mjs init-ledger --vault A --file data/A.json --name 照片处 --materials photo
node src/cli.mjs add    --file data/A.json --name 报价处 --materials quote
node src/cli.mjs view   --file data/A.json

# 路由裁决（无见证/双签依据的新地址会以退出码 2 暂扣）
node src/cli.mjs route  --file data/A.json --name 报价处 --material quote

# 封缄与开件
node src/cli.mjs seal   --file data/A.json --name 照片处 --material photo \
  --body '{"photoId":"P1"}' --out data/p1.json
node src/cli.mjs open   --parcel data/p1.json --key <收件私钥文件>

# 两库离线碰头对账
node src/cli.mjs fork   --file data/A.json --other data/B.json
```

## 关键安全规则（由测试与场景强制保证）

1. **仅追加不可篡改**：改任何旧页字节 → 页哈希变化、作者签名失效、后页 `prev` 断链。
2. **撤销永久黏住**：任一分支撤销的钥，合并后仍撤销；共同旧页之后重新 `add` 同名也不复活。
3. **见证按名限定**：见证签名覆盖明确的收件名清单；只有本地签收、无按名见证的单侧地址
   即使进了合并页也保持暂扣，不会被合并动作“凭空升级”为共同认可。
4. **冲突不静默选边**：同名新钥双分支进入合并页冲突清单，裁决前路由一律 `hold`；
   裁决页（`resolution`）经独立见证后才放行。
5. **旧钥打不开新封件**：重封采用新一次性 ECDH 临时钥与新 AAD，旧私钥 GCM 认证失败。
6. **回执只认真正收件席**：他人签名的回执、被改动的回执，对账一律失败。
7. **结价不重复**：网格单元全局唯一归属；已结案单元不被相邻工项重复计价；对不准整笔挂起。

## 设计取舍：为什么选“先受理、后对账”

题面给出两条路线。本实现采用第二条：断联时各分库先本地受理换钥并保留
**可带走的截止页（含作者签名）与续写依据（见证/签收）**，碰头后由合并页对账。
第一条“生效前收齐足够多独立见证确认”在断联时直接无法换钥；第二条在保证
“签名可独立验真、撤销永久、冲突显式暂扣”的前提下，允许断联期继续工作，并把
风险收敛到对账时的明确裁决，而不是静默丢失或覆盖任何一条记录。
