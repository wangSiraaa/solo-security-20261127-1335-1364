# 维护分支修正移植与复核台

这是一个完全虚构、离线运行的校词软件演练场。它用真实 Git 分支、可解压交付包、虚构教室、本地模拟接收柜和签名背书，回答一个问题：

> 主线已审过的修正，维护分支能沿用哪一张发布背书？

答案不是“同名补丁即可沿用”，而是：

1. **只有背书明确覆盖、且实际调用入口的模块/词表/契约哈希完全一致的对象，才能沿用原背书。**
2. 原生重写即使本地输出相同，也不能照签主线背书；必须按该分支的真实入口、依赖和观察行为重验，并签发只覆盖该分支/提交的声明。
3. 原样封存主线组件和词表时，原背书只覆盖哈希一致的原样部分；旧词号兼容层、打包组合和教室行为必须单独复核。兼容层或词表一改版，组合背书须重审。
4. 来源真实只证明包的来历，不证明目标教室所需词表仍在、离线零外传或承诺覆盖完整。

## 一条命令重建全部演练

```sh
python3 -m reviewdesk.cli demo
```

该命令会：

- 从 `main` 创建两个真实维护分支及同名修正/合并记录：
  - `maint/qianzi-native`：千字社，原生实现 + 四位旧词号；
  - `maint/bihe-sealed-component`：碧荷社，原样封存主线 resolver/词表 + 旧词号边界；
  - `trial/swapped-called-impl`：反向演练分支，补丁/合并名相同，但实际入口是未背书的 `mojiao.other_native:resolve`。
- 记录实际入口、函数/模块哈希、全部 `mojiao/**/*.py` 哈希、词表 `list_id/version/hash`、调用方契约和观察行为到哈希链账本 `review-desk/ledger.jsonl`。
- 签发 Ed25519 虚构发布背书到 `review-desk/endorsements/`。
- 构建并签名学校包 `dist/qianzi-school-1.0.0.tar.gz`，安装到 `classrooms/xian-ge-room-01/`。
- 独立把新版词表 `school-promised-modern-2.2.0.json` 加入已安装教室。
- 启动又断开模拟接收柜，完成旧词号离线验收。
- 演练三个反向案例：偷换实际入口、恢复实现后撤走目标词表、本地答案正确但兼容层外传待查词。
- 输出机器报告 `review-desk/reports/final-report.json` 和人读报告 `review-desk/reports/final-report.md`。

## 复核者怎么用发到手里的包

```sh
tar -xzf dist/qianzi-school-1.0.0.tar.gz -C /tmp/review
cd /tmp/review/qianzi-school-1.0.0

python3 verify.py --human
./ww lookup 1001 --human
./ww lookup 1002 --human
```

`verify.py` 会校验：

- `MANIFEST.json` 的 Ed25519 签名；
- 清单内全部文件哈希；
- 包内背书签名。

独立词表更新后，在已安装教室可看到：

```sh
cd classrooms/xian-ge-room-01
./ww wordlists
./ww lookup 1001 --human       # 旧承诺词号仍离线可用
./ww lookup 1004 --human       # 2.2.0 新增且带旧词号
./ww lookup W-NEW-017 --mode old-id
# status=contract_error：新版专用号不在旧词号调用契约内
```

## 查询模拟接收柜

```sh
# 查询登记在内的所有柜
python3 -m reviewdesk.cli cabinet query --all

# 只查某个词是否曾被任何柜收到；有命中时配合 --exit-code-on-hit 返回 1
python3 -m reviewdesk.cli cabinet query --all --word 1002 --exit-code-on-hit

# 直接查询单个柜文件
python3 -m reviewdesk.cli cabinet query --state review-desk/cabinets/leaky-drill.jsonl

# 手动启动一个 HTTP 接收柜
python3 -m reviewdesk.cli cabinet serve \
  --state review-desk/cabinets/manual.jsonl \
  --cabinet-id manual \
  --port 8765
```

## 关键背书

- `END-MAIN-1396`：主线已审 resolver + 指定 2.1.0 词表/契约。
- `CARRY-BIHE-VERBATIM-1396`：仅确认碧荷分支中哈希一致的原样组件/词表可继承主线背书。
- `END-BIHE-COMPOSITION-1396`：只覆盖碧荷分支指定提交上的旧词号边界与组合；`compat_oldid.py`、契约或词表哈希变化即须重审。
- `END-QIANZI-BRANCH-1396`：只覆盖千字社原生分支指定提交、旧词号契约和记录的依赖树；不覆盖其他分支，也不自动覆盖未来词表内容。
- `DELIVERY-XIANGE-1396`：弦歌小学教室的离线覆盖/零接收柜事件验收收据。

## 目录

```text
mojiao/                     主线新版运行时与已审 resolver
wordlists/                  初始本地词表（旧表 + 新版 2.1.0）
updates/                    交付后才独立发布的新版词表 2.2.0
contracts/                  调用方约定
lab/overlays/               两个维护分支和反向演练的覆盖件
scripts/bootstrap_branches.py
reviewdesk/                 复核台、账本、签名、探针、模拟柜和编排
bundle-assets/              随包发布的 ww 与 verify.py
classrooms/                 demo 后生成的虚构教室安装
dist/                       demo 后生成的交付包
review-desk/                账本、背书、报告、柜状态和公钥
```

私钥 `review-desk/publisher-ed25519.key.pem` 仅为本地演练生成，已被 `.gitignore` 忽略；这不是真实发布 CA。
