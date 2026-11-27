# 档案合作社：本地发份柜与恢复工作台

四名保管人（东库：东甲、东乙；西库：西甲、西乙），各人一只柜。封卷时由可信
**开封柜（IssuingCabinet）** 生成卷钥并按门限分发份额；恢复时由柜外
**恢复工作台（Workbench）** 收集实有封套、委托柜内合份，再从实际卷副本取页核对。

## 模块

| 文件 | 职责 |
| --- | --- |
| `gf256.py` | GF(2⁸)（Rijndael 多项式 0x11b，生成元 3）四则运算与拉格朗日插值 |
| `secretshare.py` | GF(256) 上逐字节 Shamir 门限份；同份位重复自动去重 |
| `crypto.py` | 页内容摘要 sha256（绑定卷号+页号）、页级 HMAC-SHA256 标签 |
| `storage.py` | 原子写 JSON、封套/回单/封存页/清单分目录、跨进程账本文件锁 |
| `cabinet.py` | 可信开封柜：封卷发行、交接回单、补发换代、柜内合份；卷钥不出柜 |
| `workbench.py` | 柜外恢复台：只收实有份、从副本取页、双重核对、落恢复单 |
| `policy.py` | 两种互斥保障单（SITE_RECOVERY 2-of-4 / SPLIT_SITE 3-of-4） |
| `cli.py` | 普通入口 |
| `demo.py` | 任务规定的全套演练（33 项断言） |

## 关键边界（对应任务约束）

1. **门限即底线**：门限 ≥ 2，任何单人只有 1 份，不能独自开卷。
2. **两份回单/两只盒子/两名到场人 ≠ 两份材料**：合份按封套唯一 ID 与份位去重；
   回单只是交接凭证，在途封套 `status=in_transit`，不计入任何一方柜存。
3. **柜外没有原钥**：卷钥在柜内内存生成，永不写入磁盘（可用 `grep -rl key data/` 验证）；
   工作台只能提交实有封套，合份结果仅用于页标签核验，不存在“测试原钥”入口。
4. **页必须从实际副本取且双重核对**：摘要清单（防串卷/换页）+ 当前代 HMAC 标签与代际
   （防旧页顶替）。任一不过，该页进 `failed_pages`，不能签“两页均已恢复”。
5. **旧版份额照实登记**：补发换代后旧封套标 `superseded` 并保留 `still_held_by` 留痕，
   旧份不能开新卷；跨卷份额在补发与合份两处都被拒，不能拿别卷新份补本卷。
6. **两种保证互斥**：
   - `SITE_RECOVERY`（2-of-4）：西库断线东库可自力恢复；但东库整批交管时代管方拿齐 2 份可开卷。
   - `SPLIT_SITE`（3-of-4）：任一处只拿 2 份都不足门限，整库交管安全；但西库断线时东库无法独力恢复。
   - 同一静态份额配置里两个方向相反，保障单只能二选一，档主签收或退件。
7. **在途材料的灾备承诺**：交接未签收前该份位从有效柜存剔除，只按“缺这一份”评估态势。

## 运行

```bash
# 全套演练（含西库失联、东库交管、回单顶替失败、旧页顶替、跨进程查单）
python -m archive_coop.cli --root ./archive_data demo

# 单元测试
python -m unittest archive_coop.tests.test_core -v

# 日常操作
python -m archive_coop.cli --root ./data seal V1 --pages 1=第一页 2=第二页 --policy SITE_RECOVERY
python -m archive_coop.cli --root ./data guarantee V1 --sign
python -m archive_coop.cli --root ./data status
python -m archive_coop.cli --root ./data handover ENV_xxx --from 东甲 --to 东乙
python -m archive_coop.cli --root ./data receive RCP_xxx --as 东乙      # 或 --cancel
python -m archive_coop.cli --root ./data recover V1 --custodians 东甲 西甲
python -m archive_coop.cli --root ./data order ORD_xxxx                 # 重启后查回交页
```

## 持久化布局

```
data/
  ledger.lock                      跨进程独占锁
  ledger.json                      卷、保管人柜存、在途交接、恢复单、保障单
  envelopes/ENV_*.json             份额封套（卷号、发行代、份位、份体、状态、留痕）
  receipts/RCP_*.json              交接回单（pending/completed/void）
  manifests/<vid>.json             封卷时另存的页号+摘要+标签清单
  copies/<vid>/page_<n>__<r>.json  封存页：正文 + 该代 HMAC 标签 + 代际
  copies/<vid>/_gen<k>_snapshot/   补发时封存的旧代页（旧页演练与审计）
```
