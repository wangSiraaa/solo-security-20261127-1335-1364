# 档案合作社：长期保存与独立核验程序

这是一个可运行的模拟实现，回答的问题是：

> 旧电子签章/旧见证制度停用后，档主还能不能向陌生读者证明“哪一个逐字节版本”在当年已经存在？

答案是：**只能证明摘要被当年有效回条绑定、且在旧制度可采信期内完成了不断链的新制度移存或连续续存的那个版本。** 过期后补盖今日章、登记处今日新说明、或读者拿来的另一版原稿，都不能获得旧证明背书。

## 快速运行

```bash
./run_demo.sh
```

该命令：

1. 生成 Ed25519 模拟见证钥匙、签名策略、虚构书稿、旧回条、当年核对材料、移存见证和续存链；
2. 启动本机 HTTP 见证服务，实际办理一份“在线正常、材料完整”的原稿；
3. 生成公开 ZIP 交付包和指定负面变异；
4. 由独立 Python 进程把 ZIP/读者原件复制到临时目录，仅使用解压出的验证器复核十一类场景（含未重签的日期篡改）。

最终应看到全部预期结果通过（当前为 `11/11`），并生成：

- `deliverables/reviews/EXTERNAL_REVIEW.md`
- `deliverables/reviews/external-review-summary.json`

## 常用命令

```bash
# 只重建证据
./coop build

# 直接验证自包含跨馆包
./verify_independent.py deliverables/bundles/qixia-crosslibrary

# 验证轻量续存包与读者原件
./verify_independent.py deliverables/bundles/dengying-registry-timely \
  --original deliverables/reader-originals/DENG-YING-v1-tui.txt

# 机器可读报告
./verify_independent.py <bundle-dir> --original <file> --json

# 启动本地模拟见证/登记服务（127.0.0.1；端口自选或固定）
./coop serve --port 8765
curl http://127.0.0.1:8765/health
```

服务还提供 `POST /v1/witness/<key-id>/sign` 与 `GET /v1/registry/<manuscript-id>`；
`scripts/online_flow.py` 会自动启动一个临时端口的服务并走完整 HTTP 办理流程。

也可以直接核验 ZIP：

```bash
tmp=$(mktemp -d)
unzip -q deliverables/bundles/qixia-crosslibrary.zip -d "$tmp"
python3 "$tmp/qixia-crosslibrary/verify_independent.py" "$tmp/qixia-crosslibrary"
```

## 目录边界

```text
cooplib/                 合作社/见证服务/场景生成代码（承办方）
cabinet/private/         模拟私钥和私有工作副本，权限 700；外部核验不读取
deliverables/            可交给陌生读者/异馆的实际交付物
  verify_independent.py  独立验证器，仅标准库 + openssl
  bundles/*.zip          自包含包、轻量链、负面变异
  reader-originals/      读者带来的两版差一字原稿
  reviews/               另一本地进程的实际复核报告
scripts/external_review.py
                         从 ZIP 暂存并启动新进程复核
scripts/online_flow.py   启动 HTTP 见证进程并办理在线正常稿
```

## 已实现的关键安全性质

- 每份回条/见证/策略均为 Ed25519 签名 JSON envelope。
- 原稿使用整件 SHA-256 绑定；改一个字就会在所有历史凭据上摘要不匹配。
- 引用的旧回条/核对材料使用整个 envelope 文件的 SHA-256 固定，替换旧文件会断链。
- 新见证移存必须发生在策略规定的旧见证 `trust_not_after` 之前；晚一天或今天补章都会被定位到具体 `migration` artifact。
- 续存链检查序号、前件 ID、前件哈希、时间顺序和有效期间隙。
- 截止后重签的“最后一次续存”会被精确定位为 `renewal-4` / `deadline-anchor-late`。
- 只有登记处今日说明、没有历次原始凭据时，声明类型 `registry-statement-only` 验证失败。
- 登记处闭馆后，完整历史签名包仍可离线验证，但报告在线核对不可用。

具体交割选择和谁持有材料见 [`deliverables/DECISION.md`](deliverables/DECISION.md)；陌生读者操作说明见 [`deliverables/README.md`](deliverables/README.md)。
