# 校录委托席、校录终端与修订书库

一个仅使用 Python 标准库的可运行模拟，回答这个问题：

> 代理人退出之后，外包校录员已经接手的那册书还能代表谁改？

答案不是“人走了就一律不能改”，也不是“拿到过授权就永远能改”，而取决于**该次送件实际携带的办理依据、书库当地已知的撤回/授权版本，以及本册实际修订号**：

- **可携带代办凭据（portable pass）**：校录员陈墨可在章节、动作、有效期被逐层收窄的范围内，继续代表作者林岚送件；即使书库与编辑站周砚断联，书库也可验签接件。但书库只执行已经进入本书库本地凭据柜/吊销册的撤回。作者已在编辑站撤回、而撤回件尚未到达书库时，存在一个由作者明确接受的最终一致窗口。
- **编辑站托管身份（station-hosted）**：陈墨不持有可直接送书库的代表权，每笔向编辑站申请；站方以责编本人权限办理，并记录实际操作者陈墨。站方收到撤回即可停止新单。书库与站方断联时，陈墨“自称代理”的裸请求没有站方逐笔回执，书库拒绝。
- **删掉转委托条款**：上级授权中的 `permissions.edit_self` 与 `permissions.redelegate` 是两个独立事实。只把 `redelegate` 改为 `false` 后，周砚仍可自行改稿，但陈墨不能再以转委托链代表林岚送件。
- **责编先锁定另一校次并推进版本**：陈墨基于旧修订号的件进入 `awaiting_manual_merge`，不会覆盖现稿。

## 运行

无需安装第三方依赖：

```bash
python3 -m proofdesk demo --state /tmp/proofdesk-demo --fresh
```

演示会使用三个人：

| ID | 姓名 | 角色 |
|---|---|---|
| `alin` | 林岚 | 作者、授权源头、书的主人 |
| `ezhou` | 周砚 | 责任编辑 |
| `chenmo` | 陈墨 | 外包校录员、校录终端前实际操作人 |

以及两章正文：`ch1 灯市`、`ch2 雨巷`。

重启进程后，用修订号查看谁做了什么：

```bash
python3 -m proofdesk report --state /tmp/proofdesk-demo/main
```

运行全部内置测试：

```bash
python3 -m unittest discover -s tests -v
# 或
python3 -m proofdesk test
```

## 一次真实主故事

`python -m proofdesk demo` 会完成并持久化以下修订：

- `rev-0001`：作者林岚亲自给第二章补“隙”字，方式 `author_self`。
- `rev-0002`：陈墨持周砚签发的 `ch1` 代办凭据，把“人声顶沸”改为“人声鼎沸”，方式 `portable_pass`；留痕链为  
  `林岚 → 周砚 → 陈墨`。
- 作者发布授权 `v2`，**只删除 `redelegate`，保留 `edit_self`**：
  - 编辑站拒绝再给陈墨签新凭据；
  - 陈墨持旧凭据送件时，书库依据本地已知的 `v2` 拒绝，代码为 `redelegation_clause_absent`；
  - 周砚本人仍可处理第二章，形成 `rev-0003`。
- 作者发布 `v3` 恢复转委托。周砚先锁定 `ch1` 的“二校”，并落下 `rev-0004`（“停住”→“停下脚步”）。
- 陈墨基于旧底本 `rev-0002` 的新件被收为 `merge-001`，原因是  
  `chapter_locked_for_other_proof_cycle,stale_base_revision`；  
  第一章现稿保留“鼎沸”和“停下脚步”，不会被“走过灯市”覆盖。
- 重新从磁盘加载 `DeskEnvironment` 后，仍可按 `rev-0001...rev-0004` 查看代表作者、实际操作者、经过的手、办理依据和改字。

## 断联后的两种次序

演示分别建立独立状态目录，避免不同时间线互相污染：

```text
/tmp/proofdesk-demo/offline-edit-first
/tmp/proofdesk-demo/offline-revoke-first
/tmp/proofdesk-demo/local-revocation-first
/tmp/proofdesk-demo/hosted
```

| 时间线 | 编辑站/作者台事实 | 书库当地事实 | 实际落字结果 |
|---|---|---|---|
| 先改稿，后撤回 | 第一笔时委托有效；之后作者撤回 | 第一笔时无本地撤回；重连后收到撤回 | “鼎”字已落为 `rev-0001`；同步后的下一笔 `locally_revoked` |
| 先在站方撤回，后改稿 | 改稿前全局/编辑站授权已撤回 | 与编辑站断联，撤回信封未进入书库 | portable 凭据仍可能落“鼎”字；结果中明确给出 `known_revocations=[]` 的边界。撤回同步后下一笔被拒 |
| 撤回件已先投入书库本地收发柜 | 站书链路仍可断开 | 书库本地已有签名撤回 | 陈墨送件立即 `locally_revoked`，错词“顶沸”保留 |
| 托管模式断联，陈墨自称代理 | 陈墨没有站方逐笔回执 | 书库无法向站方核实 | `hosted_claim_not_accepted_offline` |

“先在站方撤回、书库却仍接件”不是被隐藏的漏洞，而是可携带凭据的离线语义。作者在 `delivery-choices.json` 中明确选择 `portable_pass` 并确认该最终一致窗口；缓解措施是章节收窄、短有效期、签名凭据和尽快同步撤回。若作者要求“站方一撤回，任何终端都不能再直接送书库”，应选择 `station_hosted`。

托管模式还有一个断联队列测试：站方已经封存的件留在 `editor_station/outbox`；若撤回先被泵入书库，之后再泵入该件，书库会以 `locally_revoked` 拒绝，不会落稿。

## 本地可信收发柜

`TrustedCabinet` 是题目中“本地可信收发柜”的文件化表示：

```text
<state>/
  trust-keys.json          # 本地 HMAC 密钥，模拟本机信任锚
  cabinet/
    author_desk/
      sent/ outbox/
    editor_station/
      inbox/ outbox/ sent/
    proof_terminal/
      inbox/
    library/
      inbox/ taken/
  library.json             # 书册正文、实际修订号、待合并件、本地授权/撤回册
  audit.jsonl              # 所有关键动作
  delivery-choices.json    # 作者选择的交割方式及边界确认
```

授权书、代办凭据、撤回件、托管回执和修订包均先签名，再作为收发柜信封传递。这里的 HMAC/本机密钥用于演示防篡改和信任边界，不是生产环境 PKI；生产实现可替换为非对称签名、标准令牌、CRL/透明吊销日志等。

## 代码地图

- `proofdesk/environment.py`：三个人、两章初始正文和组件装配。
- `proofdesk/actors.py`：`AuthorDesk`、`EditorStation`、`ProofTerminal`、授权/凭据构造。
- `proofdesk/cabinet.py` / `trust.py`：本地可信收发柜与签名。
- `proofdesk/authz.py`：委托链、两种独立授权事实、有效期/章节收窄、托管回执和本地撤回边界校验。
- `proofdesk/library.py`：按实际修订号落稿、锁、过期底本判定、待合并队列、重启持久化和作者报告。
- `proofdesk/scenario.py`：完整主故事和所有断联次序。
- `tests/test_proofdesk.py`：对应上述要求的 10 个回归测试。
