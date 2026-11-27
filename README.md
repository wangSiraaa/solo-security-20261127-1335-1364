# 成对公平交换的本地代存柜 (paired fair-exchange vault)

一个可重启的本地「代存处」：接收双方盖章的封包，在**同一个事务**里让双方的领取权
成对生效，并记录谁在何时取得权利、谁真正打开了哪一版。纯 Python + SQLite，无外部
依赖。

## 两条底线如何被强制

1. **先读后交不允许。** 领取凭证只在与「对方那版也已不可撤回入柜」同一事务内生成
   （`_settle`），因此甲方首次能读乙方原件的那一刻，甲方的对应原件也已成对生效；
   只交一边时订单停在 `PENDING`，根本不存在凭证。

2. **代存不能代签。** 代存处只保存**公钥**与**密文**（外加一份用收件人公钥加密的
   包密钥信封）。开封凭据必须由当事人终端用自己的私钥签名（`Terminal.open_claimed`
   → `report_open`）；`Vault` 没有私钥，结构上无法伪造凭据。

3. **保留期保证。** 成对生效时把两版 `retained_until` 统一设为「生效拍 + 保留拍数」，
   领取凭证的 `expiry_tick` 与之一致。约定保留期内复联即可 `claim` 取得密文与信封。

4. **重传不能覆盖已成对生效的交换。** 生效后再 `deposit`/`withdraw` 都被
   `ALREADY_SETTLED` 拒绝。

5. **旧授权不能释放新摘要。** 委托/确认被绑定到精确的
   `(a_ver, b_ver, a_digest, b_digest)`。任何一版被重传或就地篡改摘要，旧授权要么
   在重传时被撤销，要么因摘要不再匹配而无法组成「成对委托」——订单停在 `PENDING`。

## 交割者的自由裁量

`set_policy(order, policy, hold_ticks, expiry_action)` 决定：

| policy | 行为 |
|---|---|
| `NONE` | 只有双方**最终确认**（`CONFIRMATION`）才成对生效 |
| `EARLY_LOCK` | 双方已有有效**委托**（「即使我离线也可先锁两件」）即立即成对生效 |
| `HOLD_FOR_CONFIRMATION` | 预留 `hold_ticks` 窗口，期间不定案，给离线方改稿机会 |

窗口到期时按 `expiry_action` 处理：

* `ABORT`：无交换关闭，双方可取回（未生效的）原件；
* `FALLBACK_LOCK`：若双方委托仍然有效，此刻成对生效。

期间收到的**撤稿**（`revoke_auth` / `withdraw`）、**复联后的最终确认**
（`CONFIRMATION`）、**到期**（`advance`）都在同一个状态机里处理。

## 同样的入柜事实，两种政策重放

`python3 -m fair_exchange.scenarios` 在**同一组入柜事实**上重放：

> tick 0 开单、tick 1 双方各入柜一件、tick 2 双方各自给出有效委托后乙离线、甲在线、
> 保留 30 拍。

**A. 乙回来要改一字**

| | EARLY_LOCK | HOLD_FOR_CONFIRMATION |
|---|---|---|
| 返场时状态 | `EFFECTIVE`（已成对） | `PENDING`（窗口内） |
| 甲能否立即读 | 能 | 否（`WAITED:NO_TOKEN`） |
| 乙改稿 | `REJECTED:ALREADY_SETTLED` | `ALLOWED`（撤稿→重传→双方重新确认） |

**B. 乙仅在保留期末回来**

| | 结果 |
|---|---|
| EARLY_LOCK | 保留期内最后一拍可读；`retained_until+1` 起 `REJECTED` |
| HOLD + ABORT | 窗口到期无确认 → `ABORTED`，`tokens` 为空，`NOTHING_TO_READ` |
| HOLD + FALLBACK_LOCK | 到期时双方委托仍有效 → 迟延成对生效，乙在期末最后一拍仍可读 |

**强制交割**：在线双方都给出最终确认时，`NONE` 与 `HOLD` 都在同一拍成对生效
（`mandatory_both_online_confirm`）。

## 过期凭证不能记「已读」

`report_open` 先校验凭据由当事人签名，再校验令牌未过期（`tick > expiry_tick`
即 `TOKEN_EXPIRED` 并写 `OPEN_REJECTED` 台账），**不会**写入 `opens`。即使摘要是同
版同包的，过期领取凭证也记不了它已读。

## 台账分得清

每条 `ledger` 记录有 `tick / event / party / ver / digest`：

* `PAIR_EFFECTIVE` —— 成对生效（含 `reason`、两版 id/摘要、`retained_until`）
* `RIGHT_EFFECTIVE`（每人一条，同一拍）—— **甲/乙何时取得权利**
* `TOKENS_ISSUED` —— 成对凭证签发
* `CLAIM_RELEASED` —— 哪方用哪张凭证取料
* `OPENED` / `OPEN_REJECTED` —— **谁真正打开了哪版**（或被拒）

`opens` 表只记真正完成的读取。

## 快速开始

```bash
python3 -m unittest discover -s tests -v      # 22 个测试
python3 -m fair_exchange.scenarios            # 四种政策/复联组合重放
```

CLI（每一步可独立进程，天然可重启，状态落在 `--root` 的 `vault.db`）：

```bash
python3 -m fair_exchange.cli --root W init --parties jia,yi
python3 -m fair_exchange.cli --root W order    --order o --a jia --b yi --retention 30 --tick 0
python3 -m fair_exchange.cli --root W deposit  --order o --party jia --text-file jia.txt --tick 1
python3 -m fair_exchange.cli --root W deposit  --order o --party yi  --text-file yi.txt  --tick 1
python3 -m fair_exchange.cli --root W policy   --order o --policy EARLY_LOCK --tick 2
python3 -m fair_exchange.cli --root W delegate --order o --party jia --tick 2
python3 -m fair_exchange.cli --root W delegate --order o --party yi  --tick 2   # 成对生效
python3 -m fair_exchange.cli --root W open     --order o --party jia --tick 3   # 读乙原件
python3 -m fair_exchange.cli --root W status   --order o
python3 -m fair_exchange.cli --root W ledger   --order o
python3 -m fair_exchange.cli --root W opens    --order o
```

## 目录

| 文件 | 职责 |
|---|---|
| `fair_exchange/vault.py` | 状态机、成对生效、凭证、开封校验、台账、查单 |
| `fair_exchange/terminal.py` | 各当事人私钥、封装、签名凭据、开封 |
| `fair_exchange/crypto.py` | RSA-1024 / PKCS#1 v1.5（纯 Python，仅用于演示分离） |
| `fair_exchange/seal.py` | 封包对称加密（HMAC-SHA256 流 + 标签） |
| `fair_exchange/session.py` | 绑定 Vault 与文件化 Terminal 的编排层 |
| `fair_exchange/scenarios.py` | 同一入柜事实的四种政策/复联重放 |
| `fair_exchange/cli.py` | 分步 CLI，支持跨进程重启 |

> 密码学说明：环境无可用密码库，`crypto.py` 为纯 Python 的 RSA-1024 + PKCS#1
> v1.5，仅为在本地练习中强制「公钥在代存、私钥在终端」的**键分离结构**；不是生产
> 密码学（无盲化、非常量时间、密钥偏小）。
