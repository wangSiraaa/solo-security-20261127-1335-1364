# 两校学员号、同持示证与本地实操实验台

这是一个零第三方依赖的可运行模拟系统。它回答的问题是：

> 两所学校各自维护学号，姓名还可能相同，实验台怎样确认两张有效结业卡属于同一个此刻到场的人？

答案是：**不能靠姓名，也不能把两张卡直接拼在一起；必须另有“同一主体 + 本次到场 + 指定本台”的证明。**

系统实现了题目给出的两种合作方式，并默认演示隐私更强的 **学员持卡器模式**；登记处关联模式也完整实现并测试。

---

## 1. 运行

```bash
./run_demo.sh
```

或分别运行：

```bash
# 9 个单元/安全属性测试
python3 -m unittest discover -s tests -v

# 完整剧情：持卡器模式 + 持卡器遗失补绑
python3 -m labbench demo --mode holder --dir runtime/holder-demo

# 完整剧情：登记处可更正关联模式
python3 -m labbench demo --mode registry --dir runtime/registry-demo

# 两种模式都跑
python3 -m labbench demo --mode both --dir runtime/demo

# 根目录便捷脚本
python3 demo.py
```

演示全部通过时会打印：

```text
全部演示与内置断言通过。
```

各校有权看到的记录分别写到：

```text
runtime/demo/holder/exports/school_a_export.json
runtime/demo/holder/exports/school_b_export.json
runtime/demo/holder/exports/registry_only_export.json
```

公开回单在：

```text
runtime/demo/holder/public/receipts.jsonl
```

本地样件状态在：

```text
runtime/demo/holder/workspaces/bench_a/
runtime/demo/holder/workspaces/bench_b/
```

---

## 2. 我替你做的责任放置决定

| 事项 | 登记处关联模式 | 学员持卡器模式（默认选择） |
|---|---|---|
| 学号编排、发证、普通结业重办 | 各学校 | 各学校 |
| 换卡与换卡记录 | 学校保留旧卡/新卡记录；登记处保留**可更正关联及更正历史** | 学校保留换卡记录；新卡由原持卡器重绑。若旧持卡器遗失，则两校补卡后绑定到新持卡器 |
| 两校共同人员号 | 不需要交给学校；登记处内部保存两校账号连接 | **不存在共同人员号**；每校只得到一个互不相同的 `school_handle` |
| “两张卡属于同一人”证明 | 登记处针对指定实验台、nonce 出一次性证明，验证只返回 bool | 学员自己的持卡器检查两张卡都在本机绑定，针对指定实验台、nonce 出一次性证明 |
| 本次在场证明留在哪一方 | **实验台**：实验台保存本台 challenge/confirmation/席位记录；一次性票据验证后删除 | **实验台**：同左；持卡器只签署/生成当次票据，不形成学校可见的行程档案 |
| 旧台未结操作回单 | 旧台签发、指名新台、一次性交接；它只证明检查点，不证明新台到场 | 同左 |
| 公开回单 | 只含随机回单号、种类、结果；不含姓名、学号、卡号、台号、席位号、人员句柄 | 同左 |
| 持卡器彻底遗失且无备份 | 不适用，登记处仍可按身份核验后更正关联 | **登记处查不到也恢复不了跨校关联**；只能两校分别补卡，再用新持卡器重办绑定 |

关键边界：

1. **姓名从不参与授权判断。** `Bench._valid_cards()` 只检查卡状态、有效期、资格项和学校是否不同；没有 `name == name`。
2. **同校/异主卡不能生成同一主体证明。** 同名同学的 B 校真卡绑定在另一个持卡器上，持卡器拒绝把它和主角的 A 卡签入同一证明；伪造的证明编号在模拟验证器中只得到 `False`。
3. **证明绑定实验台和一次性 nonce。** A 台到场证明拿到 B 台或新 nonce 验证为 `False`；成功验证后票据立即删除，重放也是 `False`。
4. **旧台“未结操作回单”不是新台到场证明。** B 台接手必须另外取得 B 台 challenge 对应的本次同持/到场 bundle。
5. **学校导出最小化。** A 校导出不含 B 校学号/卡号、台号、席位、交接码；B 校导出同理。测试会扫描这些标记。
6. **公开回单不可串联行程。** 每条回单使用新的随机编号，不含人员、卡片、学校账号、实验台或席位标识。

> 这是题目允许的“模拟持卡器”：`ProofOracle.verify_bundle()` / `verify_one()` 对外只返回 `True` 或 `False`，用不可猜测的 proof ID 和验证后删除模拟一次性密码协议。它不是生产级密码学实现；真实系统应替换为受众绑定签名、随机 nonce、密钥保管和身份核验。

---

## 3. 完整剧情与题目要求的对应

`labbench/scenarios.py::run_core_scenario()` 自动执行并断言以下流程：

1. 学校 A 发学号 `A-1001` 的卡，学校 B 发学号 `B-2046` 的卡；两人姓名都叫“张伟”。
2. 学校 B 还给同名同学 `B-2099` 发了一张真实有效卡。
3. 主角的两张卡：
   - holder 模式绑定同一台 `HolderDevice`；或
   - registry 模式由登记处见证并保存关联。
4. A 台生成新 nonce；学员出具绑定 `bench_a + nonce` 的“同一主体 + 本次到场”证明。
5. A 台确认两卡当前有效、资格正确、证明成立后开席。
6. A 台对本地 JSON 样件执行 `inspect`、`measure` 两道工序。
7. 主动中断操作，生成指名 `bench_b` 的未结操作回单，剩余两道工序和检查点摘要随回单迁移。
8. 攻击：只把第二项换成 `B-2099` 的真卡。
   - 两张卡单独都有效；
   - 姓名相同；
   - 但同一主体证明不成立；
   - B 台拒绝开席，且不产生席位。
9. A 台到场证明改投 B 台：验证为 `False`；同一票据在 A 台正确 nonce 下原本成立，证明不是票据本身无效，而是受众/本次性不匹配。
10. 恢复主角原卡，在 B 台申请 handoff challenge：
    - 缺少 B 台本次到场依据时，旧回单也不能接手，且旧回单不被消费；
    - 补齐 B 台本次同持与到场证明后，B 台接受旧回单并继承检查点。
11. 同一旧回单再次重放被拒绝。
12. B 台 `resume` 后继续 `trim`、`verify`，工序索引从 2、3 连续完成，样件累计值为 `112`。
13. 正常退席；用同一席位凭证重复退席会返回同一个公开回单，不产生重复记录。
14. 重新取号、重新示证后可再次借台开席并退席。
15. 普通结业发证用同一幂等键重办，返回同一张卡。
16. 换卡后：
    - holder 模式由原持卡器重绑新卡；
    - registry 模式由当事人申请，登记处追加更正记录并把当前关联指向新卡。
17. 分别导出两校记录，并自动扫描跨校、跨台、席位令牌、交接码等隐私标记。

`run_holder_loss_demo()` 另外演示：

- 持卡器文件彻底删除且无备份后，无法解锁；
- 登记处的跨校关联查询返回 `None`；
- 两校分别核验本人并补卡；
- 学员用新持卡器绑定两张新卡后，才能重新开席、退席。

---

## 4. 代码结构

```text
labbench/
  store.py       # 随机 ID、摘要、带文件锁的 JSON 存储、JSONL
  identity.py    # School、HolderDevice、模拟 ProofOracle、Registry
  privacy.py     # 最小公开回单簿
  bench.py       # 实验台策略、开席/操作/中断/交席/接手/退席状态机
  lab.py         # 参与方装配、发证、换卡、学校导出
  scenarios.py   # 题目完整剧情、攻击用例、隐私审计、遗失演示
  cli.py         # python -m labbench ...
tests/
  test_labbench.py
demo.py
run_demo.sh
```

### 常用 Python API

```python
from pathlib import Path
from labbench.lab import Lab
from labbench.scenarios import run_core_scenario

# 直接跑带断言的完整场景
run_core_scenario(Path("runtime/demo/holder"), mode="holder", log=print)

# 或手工装配
lab = Lab.create("runtime/manual", mode="holder", reset=True)
cast = lab.seed_named_cast()

nonce = lab.bench_a.create_challenge("open")
bundle = cast["protagonist"].issue_bundle(
    [cast["a_card"]["card_id"], cast["b_card"]["card_id"]],
    bench_id="bench_a",
    nonce=nonce,
    oracle=lab.oracle,
)
seat, token = lab.bench_a.open_seat(
    bundle,
    resolve_card=lab.resolve_card,
    verify_identity=lab.verify_identity,
    receipt_book=lab.receipt_book,
)
lab.bench_a.start_operation(token)
lab.bench_a.operate_next(token)
lab.bench_a.interrupt_operation(token)
handoff_code, receipt = lab.bench_a.create_handoff_receipt(token, "bench_b")
```

---

## 5. 安全边界与后续生产化

当前存储中的 `*_secret`、席位能力串和交接码是本地模拟能力凭证；运行目录权限设为 `0600`，但不应把 `runtime/` 公开或提交。

若要变成真实系统，应替换：

- 模拟 proof ID → 持卡器私钥对 `bench_id`、nonce、卡集合、本次到场时间的签名；
- 本地 JSON 学校记录 → 学校签发的可验证数字证书/凭证；
- 不可猜测交接码 → 旧台签名的检查点收据和双方认证通道；
- 本地到场确认 → 实验台现场读卡器/短时效动态二维码/近场证明；
- 明文运行目录 → 操作系统密钥库或加密存储。

即便生产化，本模拟中确定的边界仍应保留：**姓名不判同、证明受众绑定、到场依据每台重取、公开回单不可关联、学校导出按最小权限分离。**
