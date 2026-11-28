# 双人协作谜盘记分器（Mode A：公开过程 + 得分承诺）

路径片带方向、四端口两两配对；落片后若干支路可能合成一圈。本工具把对局、
成圈分类、封存支路的承诺/揭密、退场带走件、只读核验和赛后记分做成一套
**零依赖、可离线运行**的 Python 3.11 命令行程序。

## 为什么选 Mode A

题面给了两种玩法。本实现选第一种：**在可公开的交界处按回合公布连通和
得分承诺**。理由：

- 本地单机/命令行形态没有常驻在线的可信裁判；哈希承诺让选手自己就能核验，
  不需要信任某个一直在线的进程。
- 退场人可以用带走件**自行重算所有已公开成圈**；未揭的暗圈只持有承诺、
  交界端口见证与解封条件，金额不泄露，等封盘揭密后再结。
- 第二种（裁判逐笔核暗区）需要裁判长期在线守边界，与本地形态不匹配；
  其"已签边界不可改"的精神在本实现里由 `boundary_witness.json` + 清单哈希承担。

## 三种成圈事件（`game._classify`）

| 事件 | 判定 | 计分 |
|---|---|---|
| `first_close` | 本手出现的新圈，且没有已登记的被拆旧圈与它共格 | 按格计分+目标 |
| `reconnect` | 新圈与 `broken_loops` 里登记的被拆旧圈共格，认领亲缘后销账 | 按格计分+目标 |
| `cosmetic` | 换片前后圈的边集合完全不变（只是改外观/同构替换） | 0 分 |

被拆旧圈登记在 `GameState.broken_loops`，**跨回合保留**，所以"先拆断、
若干手后再接回"仍能正确判成 reconnect，而不是误判 first_close。

## 三种可见性（`game._visibility`）

| 可见性 | 构成 | 谁能重算 | 结算时机 |
|---|---|---|---|
| `public` | 全公开片 | 任何人 | 当场 |
| `self` | 含本人封存片、无对手封存片 | 本人（持实物+承诺） | 当场，对手只见承诺 |
| `dark` | 含对手封存片 | 无人可独立重算 | 挂起，封盘揭密后凭承诺结 |

暗圈在落片时把 `{边集合, 格, 金额, 封存片实物}` 做 SHA-256 承诺，公开过程
只出现承诺哈希、交界端口和解封条件，**金额不进公开摘录**。

## 退场人能带走什么、不能带走什么

`build_exit_package` 产出一个目录，`manifest.json` 对每个文件做 SHA-256：

- **能带走**：规则、公开过程、本人落片（含自己封存片实物与承诺）、
  本人账本（暗圈只给条件条目）、已领目标、封存边界见证、本人比分。
- **不能带走**（见 `exclusions.json`）：对手备件、本人未出备件（仅登记
  数量后撤回）、未领目标细节（只给剩余计数）、未落片空格。

## 两台席位

- **只读退场证明台**（`boardgame/desk.py`，命令 `audit`/`takeaway`）：
  只读取带走件，重放网格图、逐圈重算、核承诺与清单，绝不写包内文件。
- **赛后个人记分台**（`boardgame/station.py`，命令 `recompute`/`correct`/
  `compare`）：此时才收到团体/个人占比、团体池、退场前已抵奖励、并列规则，
  只用审计通过的带走件重算，办出一笔补差/追回与晋级判定。

## 快速开始

```bash
# 全幕剧本（11 幕，覆盖题面所有场景），产物在 data/demo_run
python3 -m boardgame.cli demo

# 单元测试（55 项断言，不依赖 pytest）
python3 -m tests.test_core

# 可继续走棋的本地对局
python3 -m boardgame.cli init 我的对局 --size 5 --hand 8 --seed 1 \
    --objective G1:1,1:15 --save game.json
python3 -m boardgame.cli play --save game.json --player 0
#   play 内指令：place r c 类型/旋转/外观 [seal] / unseal r c / hand /
#                log / seal / exit <目录> / quit（quit 自动存档）

# 退场与只读核验
python3 -m boardgame.cli exit-package --save game.json --player 0 --out pkg0
python3 -m boardgame.cli audit pkg0
python3 -m boardgame.cli takeaway pkg0

# 赛后记分（占比/已抵/并列此时才给）
python3 -m boardgame.cli recompute pkg0 --team-pool 200 --team-share 0.4 \
    --prior 18 --tie 60

# 见证不变、仅改一次已兑奖励
python3 -m boardgame.cli correct pkg0 --loop <loop_id> --score 35 \
    --reason "扣回误发奖励" --out pkg0_fixed

# 两套不同暗局的带走件等价对比
python3 -m boardgame.cli compare pkg_a pkg_b
```

路径片格式：`类型/旋转/外观`，类型 ∈ `straight|curve|cross`，旋转 0..3，
外观 ∈ `木纹|青石|苔痕|铜锈`。外观相同的两片配对可以不同——这正是
"外观相同但连向不同暗区"的两片，承诺哈希会让掉包现形。

## 目录

```
boardgame/
  tile.py      路径片（配对、旋转、外观）
  commit.py    哈希承诺原语
  board.py     网格图与成圈检测（度≤2 分量）
  game.py      状态机：落片/换片/揭片/结算/退场打包
  desk.py      只读退场证明台
  station.py   赛后个人记分台
  cli.py       命令行入口（含 play REPL）
  demo.py      题面全幕剧本
tests/test_core.py
data/demo_run/  demo 产物（存档、带走件、改正包、篡改反面对照）
```

## 安全边界与已知限制

- 承诺用 SHA-256 截断（16 位 hex）作为落片承诺，完整文件清单用全长
  SHA-256；本地对抗模型下足够，若要上生产应换全长哈希并加签名。
- 封存片的保密依赖"退场包按人分发"：本人包里有自己封存片实物，
  对手包里没有；公开视图对 `conditional.sealed_tiles` 做了脱敏。
- Mode A 下含对手封存片的暗圈在揭密前确实无法结算，这是模式本身的取舍，
  不是实现缺陷；封盘 `seal_game` 强制无封存片、无挂起圈。
