# 离线邮件读本（阅读室）

给借阅室用的一套**离线**邮件读本：把一期通讯社来件在“到刊时”封存成本地版，
之后读者翻阅、反复切换、打印、关闭任何一封信，**都只读本地文件**，不再访问
供图站。入场指引只从“确有依据的”材料签出：随信文字方向、随信地图及说明、
以及公共图说明；单向通道、仅能爬楼的近路、以及活动方的无障碍绕行，都按规则
处理。

- 纯标准库实现（Python 3.11），无第三方依赖。
- 34 个单元/回归测试；一个可复跑的演示与证明脚本。

## 一分钟上手

```bash
# 1) 生成随信素材（wire 邮箱 JSON）与本地供图站文件
python3 -m reading_room.fixtures

# 2) 起本地供图站 + 封存第 1420 期 -> 本地版目录 ./work/edition
python3 - <<'PY'
from reading_room.supply import SupplyStation
from reading_room.wire import load_mailbox
from reading_room.edition import seal_issue
with SupplyStation("supply_station/assets", "work/requests.log") as st:
    ed = seal_issue(load_mailbox("fixtures/issue.json"), "work/edition", base_url=st.base_url)
print("sealed:", ed.edition_id)
PY

# 3) 离线翻阅
python3 -m reading_room.cli browse --edition work/edition --letter letter_a --event A-101

# 4) 打印入场指引（通用 / 轮椅）
python3 -m reading_room.cli print --edition work/edition --letter letter_a --event A-101 \
        --mode step_free --format html --output work/guide_a.html

# 5) 生成整套可拷走的离线 HTML 读本
python3 -m reading_room.browse --edition work/edition --out work/bundle
#    打开 work/bundle/index.html
```

一键演示（含两种阅读顺序对照、两张指引、修订地图场景）：

```bash
python3 tools/demo.py /tmp/demo_out
python3 -m tools.proof          # 纯文本证明报告
python3 -m unittest discover -s tests -v
```

## 选定的交付方案

题目允许两种做法。本读本采用：**到刊时统一领取合格的公共插图，并把该期待用内容
封成本地版**（“封存式”）。理由：

- 公共插图地址对所有订户**相同**，到刊时按路径去重各取一次，供图站只看到“有人
  取过 /public/issue1420/x.png”，无法据此判断是哪位读者。取回后写成本地文件，
  阅读期零请求。
- 按订户分配的图片地址（`/sub/...`）**从不请求**，因此供图站永远学不到读者身份；
  正文中以醒目标注的**替代图**呈现。
- 缺图处置：公共图在封存时取不到（如 404），同样落成**带标注的替代图**，并在
  `edition.json` 的 `substitutions` 与离线读本的“替代插图说明”里明示。

> 另一条可行路线是“完全从文字与随信素材重排阅读版”。本实现取封存式，但在
> `reader.py` 中保留了纯文字/HTML 渲染路径，二者都可离线。

## 隐私规则：按地址归属判定，而非按图片长相

`reading_room/assets.py` 只用**地址命名空间**判定归属：

| 地址 | 归属 | 封存时 |
|---|---|---|
| 信内 `inline` 字节 | 该信 | 直接落地，零请求 |
| `/public/...` | 全期共用 | 每路径取 **一次** |
| `/sub/...` | 单一订户 | **绝不请求**，替代图 |

关键点：**两张“外观相同”的图可以归属不同**。演示里 `letter_a` 的 `promo_a`
（内嵌字节）与 `letter_b` 的 `promo_b`（`/sub/reader_b/promo.png`）是**逐字节相同**
的 PNG，但一个内嵌、一个按订户分配。封存逻辑按 `owner::asset_id` 分键，阅读时
`Edition.assert_visible()` 拒绝跨信读取——长相相同不代表可互相借用。

“只能看见发给它的请求记录”：`supply.py` 的供图站只把 `method/path/status/time`
追加进日志，从不接收读者名、会话或除请求行外的头部；因此“两封外观相同、归属
不同的图”在日志里也只会表现为不同的**路径**，不表现为任何读者身份。

## 同一期刊物，阅读过程不改变供图记录

`tools/proof.py` 实测：封存时 6 条请求；两种截然不同的阅读顺序（不同的翻页、
反复切换、各打印两次、关闭不同信）之后，日志**逐条完全一致**。测试
`OfflineTests.test_two_reading_orders_identical_log` 固化此点，且阅读期根本不再
起供图站也能渲染，进一步证明零请求。

## 入场指引：只签有依据的路线

`reading_room/routing.py` 在**已交付**的通道集合上做 Dijkstra，来源有三：

- `letter_map` —— 随信地图的文字说明（**无障碍绕行的唯一来源**，甲信特有）
- `letter_text` —— 随信文字方向（**可靠、较长、全程平层**，甲乙丙都有）
- `common_map` —— 公共图说明（**仅公共图标出的短楼梯近路**就在这里）

规则：

1. `standard` 模式可用任何通道，故可能选到公共图上的**短楼梯近路**。
2. `step_free` 模式在构图阶段就**剔除此类楼梯边**，所以轮椅读者的指引**绝不**
   把他带上楼；哪怕那是更短的路。
3. `auto` 取“所有已交付材料里代价最低的**有依据**路线”，来源优先级只在**同代价**
   时用于决胜（随信地图 > 随信文字 > 公共图）。因此：
   - 甲信（有地图）：轮椅走地图上的**西廊坡道绕行**；
   - 乙信（仅地址+文字）：轮椅走**随信文字**的东廊平层；
   - 两者都全程无楼梯。
4. 没有依据就不签。起点无任何已交付通道时抛 `RouteError`，不臆造路线。
5. `basis=letter_text` 可强制只用随信文字签出，证明“可靠的随信路线必须可用”
   ——甲乙丙三封信在两种模式下都能据此签出。

## 题目各场景的落点

| 要求 | 实现 / 验证 |
|---|---|
| 三份合成邮件；内嵌图、公共插图、按订户图片地址 | `fixtures.py`：`letter_a/b/c`，`promo_a` inline、`a_only` 等 public、`promo_b/promo_c` subscriber |
| 文字版活动号可选场次 | `events[].event_number`（A-101/B-202/C-303），`browse --event` |
| 打印“从到达门到会场”的入场指引 | `cli print` / `render_guidance_*` |
| 单向通道 | `Edge.one_way`；`OneWayTests` |
| 仅能爬楼的近路 | `access=stairs`，只在公共图；`standard` 会选、`step_free` 剔除 |
| 无障碍绕行可能只在随信地图及说明 | 甲信 `letter_a_map_description()` 仅此一处 |
| 短通道只在公共图、随信文字写的是较长通道 | `common_map_description()` vs `letter_text_directions()` |
| 可靠的随信路线必须可用 | `basis=letter_text` 独立签出（测试） |
| 两封信填外观相同而归属不同的图 | `promo_a`(inline) 与 `promo_b`(/sub) 字节相同、归属不同 |
| 一封有随信地图，另一封只有地址和文字方向 | 甲有 `letter_map`，乙无 |
| 换两种阅读顺序，对照供图记录与两张指引 | `tools/demo.py`、`tools/proof.py` |
| 只更换随信地图及说明，把一条走廊改单向 | `issue_revised_map.json` 仅改甲 `letter_map` |
| 新指引要绕行 | 修订后西廊单向 → 轮椅改走东廊（测试断言） |
| 原信不动 | 修订版乙信逐字段相同（测试断言） |
| 轮椅路线不能仍带他爬楼 | `step_free` 剔楼梯；断言 `has_stairs=False`、无 `[楼梯]` |

## 目录结构

```
reading_room/
  assets.py   地址归属分类（public / subscriber / inline）
  wire.py     wire 邮箱模型：信件、活动、通道、地图说明；校验
  supply.py   本地供图站 + 追加式请求日志（只能看见请求）
  edition.py  封存（到刊时取公共图一次、绝不取订户图）+ 本地版读写 + 替代图
  routing.py  基于已交付通道的 Dijkstra；单向、楼梯、无障碍模式
  reader.py   离线翻阅/选择/打印（文本与 HTML）
  browse.py   生成整套离线 HTML 读本（相对链接、本地图片）
  fixtures.py 确定性素材生成（含“同貌异主”图与缺失图）
  cli.py      import / browse / print / bases 入口
tools/
  demo.py     一键实况演示（写出可检查的产物）
  proof.py    纯文本证明报告
tests/
  test_reading_room.py  34 个测试
fixtures/      生成的 wire 邮箱 JSON（含修订地图变体）
supply_station/assets/  本地供图站文件
```

## 已知取舍

- 封存式交付假定“公共图属全期共用、到刊即可取”。若某公共图只在特定时点可用，
  封存时取不到会被记为替代图并在读本中明示（而非静默缺图）。
- 指引语料来自随信/公共图的**文字说明**（题目允许“地图及其文字说明”）；本实现
  不解析位图像素，只依据图中随附的文字描述编排路线。
- 同一期一旦封存，即为该期的稳定快照；如需新版地图，按“只换随信地图”重新封存
  一份修订版（演示中的 `issue_revised_map.json`），原信不受影响。
