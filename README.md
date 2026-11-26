# 电子档案封存柜（seal-cabinet）

一个只依赖 Python 标准库的命令行封存柜，用来回答：

> 一个副本到底能证明哪一个提交时刻？哪些块确实在同一个具名代次中共存？

题内介质固定为 `A/B/C/D` 四个编号块（也接受 `1/2/3/4`）。`A` 是目录，`B` 是签收页，`C` 是交接说明，`D` 是介质底账。

## 1. 一键演示

```bash
./demo.sh /tmp/sealcab-demo
```

脚本会在独立临时目录中断言并演示：

1. 开机已固化第 7 代完整旧卷；
2. 收 `A8/B8` 到两个 WORM 新位置，`C7/D7` 由连续见证接回，封出**第八代完整时点副本**；
3. 交付独立受托人、按封条号复核、重启后取回原副本；
4. 第九代 `B9/C9` 到来后不覆盖 `A8/B8`，原 G8 封条仍有效；
5. 派生独立补卷介质，另出锚定 G8 封条的 G9 **限定范围片段**；
6. 另一个案例演示把两个空位留给 `B9/C9`，直接出具第九代窄范围补卷；
7. 无第十代证据时出具**缺证待补**回执；
8. `A7 + B8` 跨代混卷或只替换一条跨代见证时，整卷验证失效；
9. 还原来源后只委托 `B7`，复核员能取回签收页但包内没有目录 `A`；
10. 再次重启并按新封条号取回原副本。

也可以直接运行自动化测试：

```bash
python3 -m unittest -v
```

## 2. 核心结论类型

| JSON `status` | 中文回执 | 退出码 | 含义 |
|---|---|---:|---|
| `complete` | 完整时点副本 | 0 | A/B/C/D 四块均与具名代次的重放状态一致 |
| `partial` | 限定范围片段 | 0 | 仅承诺的子集（如 `B,C`）成对共存并可取回；不证明全卷 |
| `incomplete` | 缺证待补 | 2 | 缺块、缺目标代次见证或见证前缀缺失 |
| `invalid` | 验证失败 | 3 | 封条号不符、哈希被改、见证链断裂或跨代矛盾 |
| `SLOT_FULL_WORM` | 位置已满 | 4 | 两个新块位置已 WORM 占用，拒绝覆盖 |

## 3. 三分钟手工流程

所有状态都在 `SEALCAB_HOME`。一个 home 就是一个本案封存柜。

```bash
export SEALCAB_HOME=/tmp/my-archive-case
./seal-cabinet init
S7=$(python3 -c 'import json;print(json.load(open("'$SEALCAB_HOME'/config.json"))["old_seal_id"])')

# 第八代：源端写入 A8/B8
./seal-cabinet scenario gen8

# 提交完整卷新单；只实际接收两个变更块
O8=$(./seal-cabinet order submit \
  --generation 8 --all --code DA-G8-001 --json \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["order_id"])')

./seal-cabinet copy receive "$O8" A --from-source
./seal-cabinet copy receive "$O8" B --from-source

# C7/D7 自动由 G7 旧封条和连续见证接回
S8=$(./seal-cabinet seal "$O8" --json \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["seal_id"])')

# 交给独立受托人
./seal-cabinet deliver "$S8" --to 独立复核人 \
  --bundle-out /tmp/g8-delivery/bundle

# 独立复核：只需要 bundle/ 中的副本、seal.json、witness.json，以及可信封条号
./seal-cabinet verify \
  --bundle /tmp/g8-delivery/bundle \
  "$S8" \
  --receipt /tmp/g8-delivery/delivery-receipt.json \
  --retrieve-to /tmp/g8-retrieved
```

重启后，所有状态均从 `SEALCAB_HOME` 磁盘恢复：

```bash
./seal-cabinet restart
./seal-cabinet fetch "$S8" --out /tmp/g8-original-copy
```

## 4. 第九代成对片段

如果选择的策略是“先交旧卷，把两个空位留给下一次变动”，则不要在 G8 消耗两个位置：

```bash
export SEALCAB_HOME=/tmp/case9
./seal-cabinet init
./seal-cabinet scenario gen9

O9=$(./seal-cabinet order submit \
  --generation 9 --blocks B,C --code DA-G9-PAIR --json \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["order_id"])')

./seal-cabinet copy receive "$O9" B --from-source
./seal-cabinet copy receive "$O9" C --from-source
S9=$(./seal-cabinet seal "$O9" --json \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["seal_id"])')

./seal-cabinet export-bundle "$S9" --out /tmp/g9-bundle
./seal-cabinet verify --bundle /tmp/g9-bundle "$S9" \
  --retrieve-to /tmp/g9-retrieved
```

结论是 `partial / 限定范围片段`，只取回 `B9` 和 `C9`，不会导出 `A.bin`。

如果原柜已经用 `A8/B8` 占满两个 WORM 位置，同一柜会拒绝用 `B9/C9` 覆盖。第九代应“另出补件”：

```bash
SUP=/tmp/g9-supplement-home
./seal-cabinet supplement-open \
  --home "$SUP" \
  --from-home "$SEALCAB_HOME" \
  --predecessor "$S8"

export SEALCAB_HOME="$SUP"
# 然后提交 --generation 9 --blocks B,C --predecessor "$S8" 的新单
```

这会派生独立补卷介质，复制同一条源端哈希见证，并把补件封条锚定到原 G8 封条；原柜、原封条和原收件范围均不改写。

## 5. 跨代矛盾与收窄委托

下面构造一个 `A7 + B8` 的矛盾包。每个文件自身的 SHA-256 都正确，但见证重放表明它们从未在同一代状态共存：

```bash
export SEALCAB_HOME=/tmp/fault-case
./seal-cabinet init
S7=$(python3 -c 'import json;print(json.load(open("'$SEALCAB_HOME'/config.json"))["old_seal_id"])')

./seal-cabinet export-bundle "$S7" --out /tmp/good7
./seal-cabinet source snapshot --out /tmp/src7
./seal-cabinet scenario gen8

# 方式一：只替换一条见证记录
./seal-cabinet demo-witness-swap \
  --bundle /tmp/good7 --out /tmp/bad-witness
./seal-cabinet verify --bundle /tmp/bad-witness "$S7"
# 退出码 3：witness_anchor_record_replaced / cross_generation_conflict

# 方式二：把副本中的 B7 换成真实有效的 B8，并重算攻击者封条
MIX=$(./seal-cabinet demo-mix --bundle /tmp/good7 --out /tmp/bad-mix --json)
FORGED=$(printf '%s' "$MIX" | python3 -c 'import sys,json;print(json.load(sys.stdin)["forged_seal_number"])')
./seal-cabinet verify --bundle /tmp/bad-mix "$FORGED" --json
# block_self_hashes_valid=true，但 status=invalid
```

还原来源并只委托签收页：

```bash
./seal-cabinet source restore --input /tmp/src7 --yes

OB=$(./seal-cabinet order submit \
  --generation 7 --blocks B --code DA-B-ONLY-001 --json \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["order_id"])')
./seal-cabinet copy receive "$OB" B --from-source
SB=$(./seal-cabinet seal "$OB" --json \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["seal_id"])')

./seal-cabinet export-bundle "$SB" --out /tmp/b-only-bundle
./seal-cabinet verify --bundle /tmp/b-only-bundle "$SB" \
  --retrieve-to /tmp/b-only-retrieved

./seal-cabinet restart
./seal-cabinet fetch "$SB" --out /tmp/b-only-fetch
```

此时复核员能读到 `B7` 签收页；交付包和取回目录中都没有 `A.bin` / 目录正文。

## 6. 它如何证明“同一时刻共存”

源端 `journal.json` 按操作顺序记录：

- 每个写操作的全局 `op_id`、块号、SHA-256、大小和时间；
- 每次代次提交包含 `changes`、每个块的 `prior`、父记录哈希 `parent` 和 `record_hash`；
- 复核方从创世 G7 开始重放，得到每一代的完整块状态。

封条正文 `body` 包含：

- 具名版本 `named_generation`；
- 承诺范围 `requested_blocks` / `promised_kind`；
- 每个实际块的 SHA-256；
- 源端 ID 和目标见证记录哈希；
- 可选的前置封条。

封条号为：

```text
SL- + SHA256(canonical_json(body))[:32 hex]
```

因此：

1. 改任一块字节会导致块自哈希或封条摘要不符；
2. 改任一见见证记录会导致记录哈希、父链或封条锚点不符；
3. `A7` 与 `B8` 即使各自哈希正确，也无法同时等于 G7 或 G8 的重放状态；
4. 完整四块一致才是 `complete`；只有被委托子集一致则是 `partial`；
5. 缺块或缺目标见证不会被误报为通过，而是 `incomplete`。

## 7. 存储与 WORM 策略

- `cabinet/objects/` 是内容寻址对象库，哈希即路径，写入后不提供覆盖命令；
- `cabinet/slots.json` 模拟本轮两个新的整块位置 `S1/S2`；
- 相同哈希重复接收是幂等引用；不同哈希尝试进入已占用位置会得到 `SLOT_FULL_WORM`；
- G8 完整卷只直接占 `A8/B8`，`C7/D7` 从 G7 封条和柜内对象引用；
- 一个 `SEALCAB_HOME` 对应一个本案；原案位置满后，G9 可通过 `supplement-open` 派生独立补卷介质；
- `source wipe-blobs --yes` 只删除源端块字节并保留哈希日志，用来模拟清机后不能从源端补读旧代；柜内见证快照和已封块仍可验证、取回。

## 8. 信任边界（模拟系统的明确简化）

这里的封条号和哈希链是完整性锚点，不替代现实中的数字签名、时间戳服务或见证人身份认证。生产化时应把源端见证改为源端私钥签名、把封条交给独立受托人，并对封条号进行带外确认。本练习的命令行和数据结构已经把“副本、封条、源端变更见证、收件记录”四类材料分开，便于替换为真实签名/PKI。

## 文件

- `seal_cabinet.py`：全部 CLI 和验证逻辑；
- `main.py`：`python3 main.py ...` 兼容入口；
- `seal-cabinet`：相对路径 shell 包装器；
- `demo.sh`：带断言的完整叙事演示；
- `test_seal_cabinet.py`：CLI 端到端测试。
