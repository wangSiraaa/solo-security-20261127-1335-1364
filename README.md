# 离线推箱子：成绩核验 + 示范轨发布

纯标准库 Python，无第三方依赖。Ed25519 签名（RFC 8032）在 `puzzle/ed25519.py`
中就地实现并对 RFC 测试向量校验。小地图、单局 ≤ 20 步、两个本地目录即可完整跑通。

## 一句话流程

1. **赛前签发起点**：裁判对「地图号 + 地图 + 初始局面」签名（`issue_start`）。
2. **核验成绩**：把玩家交来的动作从签名起点重演一遍（`certify`）——判可达、
   记录推了哪些箱（按初始格识别）、实际到过哪些格、在哪里停住、算可信分。
   自报分数原样留存但与可信分分离。
3. **按房间发示范轨**：示范站按玩家**实际已开启的房间**裁切（`build_demo`）。
   未开启房间的地图/动作/停留时长都不进文件；播放到进入受限房间**前**即止。
4. **中途交接**：受信本地裁判实测并签名一段中段局面（`attest_midpoint`），
   后续段在它之上接跑（`continue_from`），**不补造先前动作**。
5. **验收**：另开新局、下载、在玩家自己的受限地图上逐步播放，比对**实际字节**与最后一格
   （`validate_demo_bytes`）。

## 跑起来

```bash
python3 -m puzzle          # 临时两目录，打印全过程 JSON
python3 -m puzzle /tmp/out # 保留 referee/ 与 demo-station/ 两个目录
python3 -m unittest discover -s tests   # 59 个测试
```

## 关键不变量

| 需求 | 实现 | 测试 |
|---|---|---|
| 动作可达 / 推箱 / 到过哪些格 / 停在哪 | `replay.replay` | `test_replay.py` |
| 起点赛前签发，链回同一 `start_commit` | `referee.certify/verify_receipt` | `test_referee.py::TestReceipt` |
| 自报分不可信，评分用可信分 | `Receipt.body['verified_score']` | `test_narrative.py` |
| 未开启房间内容不进文件（几何/箱/目标/门/动作/停留） | `Map.restricted_map` + demo 裁切 | `test_demo.py::TestNoLeak` |
| 示范在进入受限房间前停 | `_first_locked_entry` 裁在入口帧**前** | `test_demo.py::TestPerPlayerCut` |
| 撤资格：评分不动、下载即刻裁短 | `build_demo` 从收据重算 | `test_demo.py::TestRevocation` |
| 中段交接、不补造前段、伪造中段被拒 | `attest_midpoint/continue_from` | `test_referee.py::TestMidpoint` |
| 改一道墙→新局、旧轨不可复用、指出真实停车格 | `Map.moved_wall` | `test_replay.py::TestAlteredMap` |
| 分岔识别 | `detect_fork` | `test_referee.py::TestFork` |
| 验收：新局+下载+逐步播放+比对字节与末格 | `validate_demo_bytes` + `TwoDirStore` | `test_narrative.py` |

## 目录

```
puzzle/
  canon.py     # 规范 JSON + 哈希（字节可复现）
  ed25519.py   # RFC 8032 纯 Python 实现
  signing.py   # 签名信封 + 内容承诺
  model.py     # 地图/局面、房间、会关的门、restricted_map
  replay.py    # 唯一的重演真源
  referee.py   # 起点 / 收据 / 中段 / 续跑 / 分岔
  demo.py      # 按房间裁切、播放、字节校验
  store.py     # 两个本地目录
  fixtures.py  # 三房一图、15 步、<0 20 步固定赛局
  scenario.py  # 全流程编排
tests/         # 59 个 unittest
```

地图就一张：A—门—B—门—C。玩家 1 开 A，玩家 2 开 A+B，共用同一赛局。改图只挪
A 房内墙柱 `(4,1)→(8,2)`，B 房推箱路被堵；同一串动作在改图上第 7 步停于 `(6,2)`。
