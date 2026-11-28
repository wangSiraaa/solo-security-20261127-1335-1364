# 需求对照（题面 → 实现）

| 题面要求 | 实现位置 | 验证 |
|---|---|---|
| 带方向的路径片，落片后支路合成圈 | `tile.py`、`board.find_cycles`（度≤2 连通分量） | demo 幕一 |
| 区分本回合首次闭合 | `_classify` → `first_close` | test_event_kinds |
| 区分原圈拆开重新接上 | `broken_loops` 跨回合登记亲缘 → `reconnect` | demo 幕三、测试 |
| 区分只改外观的替换 | 边集合不变 → `cosmetic`，0 分 | demo 幕二 |
| 只看过自己备件和已公开落片 | `public_view` 对封存格只给外观+承诺 | takeaway 排除项 |
| 封存支路/未领目标/未落片不随退场带走 | `exclusions.json`、未出备件仅登记数量 | test_exit_package_exclusions |
| 可继续走棋的本地/命令行入口 | `cli.play` REPL + JSON 存档 | REPL 实测 |
| 只读退场证明的赛后个人记分台 | `desk.py`（只读）、`audit`/`takeaway` | audit 实测 |
| 交界处按回合公布连通与得分承诺（选 Mode A） | 暗圈 `promise`+`junction`+`conditions` | demo 幕四 |
| 退场人自行重算已公开成圈 | `desk.verify_loops` 重放网格图 | 55 项测试 |
| 未揭部分等封盘 | `conditional` 挂起，`seal_game` 强制清结 | test_visibility_and_dark |
| 两人当场各领至少一项应得分 | 剧本安排两人各自闭合可结圈 | demo 幕一/幕四 |
| 记分台此时才收到占比/已抵奖励/并列规则 | `station.recompute` 入参，包内不含这些 | test_recompute |
| 用实际能带走的资料重算并办补差/晋级 | recompute 先 `_require_clean` 审计再算 | demo 幕九 |
| 外观相同连向不同暗区的两片临时交换 | 同外观异配对，承诺哈希不同 | demo 幕五、掉包断言 |
| 核一圈可结、一圈有条件 | self 当场结 / dark 挂起 | test_self_loop、test_visibility |
| 保持见证不变仅改一次已兑奖励 | `station.correct_reward` + correction_log | 见证逐字节比对 |
| 改奖励后旧包审计失败、改正包通过 | 清单哈希拦截篡改；改正留痕可还原重算值 | test_tamper_and_correction |
| 公开过程/可见积分/条件相同时比较两套暗局 | `station.compare_dark_games`，不看对手备件 | test_equivalent_dark_games |
| 无须公开对手备件便能各自核分 | compare 只比可带走字段，`opponent_spares_inspected=False` | demo 幕十一 |

## Mode B（裁判逐笔核暗区）的取舍说明

未选。Mode B 要求可信裁判长期在线、离席时守住已签边界，这与"本地桌面/
命令行入口、无常驻服务"的部署形态冲突。Mode A 用承诺+只读重放达到同等的
"可自行核验、暗区待封盘"效果；Mode B 中"已签边界不可改"的约束在本实现中
由 `boundary_witness.json` 与 `manifest.json` 的全长 SHA-256 共同强制：
任何对已兑奖励或边界的改动都会让清单哈希失配。
