# 双机房离线审计取证台

两个机房（东站、西站）各自独立见证日志摘要链；取证台在离线条件下
重建链、处理分叉/缺页/重页、复核签名，并按**可证事实的边界**出具分级证书。

## 密码学选型（成熟库签名 + 自造钥匙）

- 钥匙：Ed25519 密钥对由本项目自行生成、自行登记公钥，无 PKI、无第三方根；
  key_id = 公钥 DER 的 SHA-256 前 12 位。
- 签名/验签：一律委托系统成熟实现 OpenSSL 3 CLI（`pkeyutl -sign/-verify -rawin`，
  RFC 8032），不自造签名算法。封装见 `auditdesk/crypto.py`。
- 哈希：标准库 `hashlib` SHA-256；所有待签/待摘要字节走 canonical JSON
  （键排序、无空格、UTF-8），保证跨进程字节一致。

## 数据结构

- `Entry`：柜台提交的具体条目，`digest = SHA256(canonical(entry))`。
- `Page`：一个班次一页；页内条目摘要折叠成 `entries_root`，
  `page_digest = SHA256(canonical(页号+班次+prev_digest+entries_root+时间戳))`，
  页与页靠 `prev_digest` 连成链，创世前页为 `GENESIS`。
- `Receipt`（见证站本地收件凭证，两级）：
  - `VERIFIED`：拆包验页（页摘要、条目根、链接关系核对通过）后签收；
  - `DELIVERED`：包裹已送达、尚未验页，**不承担页内容属实**，不能用于共同见证。
- 所有凭证只陈述“本站见过此页”，不声称全局唯一或全局顺序。

## 关键规则

1. **分叉不互斥**：同一前页的两条不同后续各自保留为链头；后收到、日期晚、
   链更长都不会抹掉另一支（见 `Chain.station_views`）。
2. **最后共同历史**：两支链按最长公共前缀计算 LCA，重启后从落盘 JSON 重算。
3. **缺页拒绝验前缀，补页即可恢复**：`verify_prefix` 沿 prev 回放到创世，
   缺页/改动/成环/页号不连续均失败；补页导入后同一链头立即复核通过。
4. **重复材料按内容去重**：导入回执/补页时按摘要与凭证 id 幂等；公钥冲突直接拒绝。
5. **证书分级**（`auditdesk/certifier.py`，每张证书列“承认事实 / 不主张事项 /
   仍须另一站作证的部分”，并由取证台钥匙签名，客户可独立复查）：
   - `LOCAL_RECEIPT`：单站可证“某站确曾收过某页”；
   - `CO_WITNESS`：双站对同一页摘要都有 VERIFIED 且前缀完整，才可签发；
   - `PROVISIONAL_CO`：仅一站 VERIFIED 时的待补临证书；
   - `DELIVERY_ONLY`：DELIVERED 签收能证明的事实，明确不能升级为共同见证。
6. **夜间清柜**：见证站本地柜可被 `wipe_cabinet()` 销毁（身份钥匙保留）；
   第三方寄存柜独立留存，不受清柜影响。

## 目录与运行

```
python3 demo.py            # 全部场景与 33 项断言，完整日志见 full_run.log
python3 -m auditdesk.cli view runs/01_fork/case
python3 -m auditdesk.cli verify-cert <案件目录> <证书.json>
python3 -m auditdesk.cli verify-entry <案件目录> <entry_id> <链头摘要>
python3 -m auditdesk.cli import-bundle <案件目录> <bundle.json>
```

`runs/` 下每个案件含 `pages/ receipt/ certs/`、见证站 `cabinet/` 与寄存柜 `locker/`，
全部为明文 JSON，可脱离本工具用 OpenSSL 命令手工复核。

## 场景索引（demo.py）

| 场景 | 结论 |
| --- | --- |
| 01_fork | 第七页双站共同；第八页东甲/西乙分叉，LCA 停在第 7 页；重页/缺页/补页/重启 |
| 02 重放 | 只去东站：甲可证、乙只有临证书；只去西站：乙可出共同见证、甲不可证 |
| 02_normal | 正常双站共同见证可出证；调换第 4 页后原顺序链断裂、证书全部拒发 |
| 03_delivered | DELIVERED 下单站事实可办，共同见证不得提前，补验 VERIFIED 后才可签发 |
