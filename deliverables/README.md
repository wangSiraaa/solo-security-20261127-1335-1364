# 公开交付物与独立核验指南

这个目录可以整体交给陌生读者、异馆或另一个本地复核进程。它不包含见证私钥，也不要求读取 `../cabinet/private`。

## 先看结论

- 交割选择、期限、谁持有什么：[`DECISION.md`](DECISION.md)
- 实际交付 ZIP 的跨进程复核结果：[`reviews/EXTERNAL_REVIEW.md`](reviews/EXTERNAL_REVIEW.md)
- “先验证 → 移晚续存 → 恢复日期 → 换第二版”的连续操作报告：[`reviews/MUTATION_SEQUENCE.md`](reviews/MUTATION_SEQUENCE.md)
- 公钥指纹：[`trust/FINGERPRINTS.txt`](trust/FINGERPRINTS.txt)

## 目录

- `verify_independent.py`：零 Python 第三方依赖的独立验证器；调用系统 `openssl` 验 Ed25519 签名。
- `policy.json`：签名制度策略，规定各见证员有效期和替代见证生效日。
- `trust/`：公开钥匙、钥匙映射和指纹固定文件。
- `bundles/*.zip`：可直接拿走的证据包。
- `reader-originals/`：模拟“读者/复核进程自己带来的原件”，包括只差一字的 v1/v2。它们**不在** `dengying-registry-timely.zip` 轻量证据包内；放在这里只是让本题跨进程复核可重复，真实流程中应由持件读者自行提供。
- `reviews/`：另一个进程从临时目录解压 ZIP 后跑出的 JSON/Markdown 报告。

## 核验自包含跨馆证据包

```bash
tmp="$(mktemp -d)"
cp /workspace/deliverables/bundles/qixia-crosslibrary.zip "$tmp/"
cd "$tmp"
unzip -q qixia-crosslibrary.zip
python3 qixia-crosslibrary/verify_independent.py qixia-crosslibrary
```

退出码：

- `0`：请求的证明成立；
- `2`：证据可读且程序完成，但证明不成立或链断裂；
- `3`：命令或环境错误。

加 `--json` 可得到机器报告，加 `--now 2026-11-27T09:00:00Z` 可固定核验时钟。

## 核验轻量续存包并绑定读者原件

轻量包不含正文。读者必须提供自己带来的原稿：

```bash
python3 verify_independent.py \
  bundles/dengying-registry-timely \
  --original reader-originals/DENG-YING-v1-tui.txt
```

或从 ZIP 解压后运行包内验证器。

## 两个指定破坏实验

### 1. 最后一次续存被移到截止日之后

```bash
python3 verify_independent.py \
  bundles/dengying-late-renewal-after-deadline \
  --original reader-originals/DENG-YING-v1-tui.txt
```

预期：退出码 `2`，`break_at.artifact_id = "renewal-4"`，错误 ID 为 `deadline-anchor-late`。

### 2. 日期恢复，但读者带来第二版

使用未改动的 `dengying-registry-timely` 包，只把 `--original` 换成：

```bash
python3 verify_independent.py \
  bundles/dengying-registry-timely \
  --original reader-originals/DENG-YING-v2-qiao.txt
```

预期：退出码 `2`，断点为 `reader-original`，随后每个绑定摘要的历史凭据都报 `digest-mismatch`。

## 只拿登记处今日说明

```bash
python3 verify_independent.py bundles/registry-statement-only
```

预期退出码 `2`。当前说明即使有登记处签名，也不能替代历次原时点回条。

## 签名与摘要协议

每个 JSON 凭据都是 envelope：

```json
{"schema":"archive-coop-envelope/1","algorithm":"Ed25519","key_id":"...","data":{...},"signature_b64":"..."}
```

- 整件原稿使用 SHA-256；所有日期为 RFC 3339/ISO-8601 UTC。
- Ed25519 签名对象是 `envelope.data` 的规范化字节：Python 等价于  
  `json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")`。
- `signature_b64` 是 64 字节 Ed25519 签名的标准 Base64。
- 凭据间引用使用整个 envelope 文件的 SHA-256，而不是只哈希内部字段。
- 验证器调用 `openssl pkeyutl -verify -pubin -rawin`，无第三方 Python 包依赖。

## 信任锚说明

包内公钥能验证签名，但“该信哪把公钥”仍需真实部署中的线下/带外指纹渠道。这里的 `trust/anchors.json` 固定了包内公钥指纹，`trust/FINGERPRINTS.txt` 给出可读指纹；真实跨馆场景应通过馆际交换或治理公告另行确认该指纹。

## 一键重建并复核

在仓库根目录：

```bash
./run_demo.sh
```

它会重建所有原稿、钥匙、回条、ZIP 和变异样本，启动本地 HTTP 见证进程办理在线正常稿，然后运行 `scripts/external_review.py`。复核进程只把 ZIP 和读者原件复制到系统临时目录，并用 ZIP 内的验证器核验。
