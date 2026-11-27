# 旧字幕席保密联播本地模拟

这里没有真实网络或音视频；所有“消息”都是本地 `SIMSG/1` 自定义文本文件，
能力声明、同意记录、回单和证明都是小 JSON 文件。所有签名使用 Python 标准库
实现的 Ed25519，无需安装依赖。

## 场记能向谁证明？

在 `direct-common-text/v1` 约定下，场记可以把

- `access/proofs/MSG-....same-text.proof.json`，以及
- `trusted/keyring.json` 中的公钥副本

交给导播、联播审计或任何持有可信公钥的核对者。第三方甚至不需要运行根中的私钥，
可用 `--keyring /path/to/keyring.json` 单独验证证明。`verify-proof` 会核对：

1. 新席确实显示并签名回报了指定版本；
2. 旧席确实显示并签名“已收”了同一正文摘要；
3. 两份回单属于同一场次、同一会话、同一套会话约定；
4. 会话固定的能力声明、两端接受书和场记同意记录没有被偷换；
5. 旧席回单明确是 `note_endorsed=false`，不能被解释成背书它看不到的备注。

因此证明的不是“旧席同意备注”，而是“两席核过同一句观众可见正文”。

## 组件

- `bin/simulcast-access`：联播接入程序，负责登录记录、会话约定协商、送稿、
  同文确认、证明签发/验证。
- `bin/new-viewer`：新席收看器；显示观众稿和当班场记备注，回报实际看过的版本。
- `bin/old-viewer`：旧席收看器；只显示正文并产生“已收”回单，不显示/背书备注。
  换机时可传 `--seat old-2`。
- `bin/relay-turntable`（或 `bin/转台`）：受托转台；只有它读取
  `private/turntable/dialect-A-17.tsv`，把 `{DY-01}` 等受托代码转成可显示汉字，
  并签发只能由转台作出的逐段转换证明。
- `bin/联播接入程序`、`bin/新席收看器`、`bin/旧席收看器` 是对应英文脚本的中文别名。
- `subtitle_sim/`：上述程序的 Python 实现。
- `director_inputs/revision-SUB-002-v2.txt`：导播亲自写的改词。
- `demo.sh`：一条命令跑完正向、反向、换机和转台场景。

## 一键演示

```bash
./demo.sh
```

演示会生成两个互相独立的运行根：

- `runtime/direct/`：两端直接协商共同文字；
- `runtime/relay/`：转台分别终结两段通道并转换旧式播稿。

## 手动关键流程

以下用直接约定为例：

```bash
ROOT=runtime/direct

# 初始化小文件、钥匙、能力声明、场记同意和方言读音表
bin/simulcast-access init --root "$ROOT" --force

# 登录成功只写登录回单；此时 send/confirm 仍会被拒绝
bin/simulcast-access login --root "$ROOT" --seat new-1

# 核对并建立本期会话约定
bin/simulcast-access negotiate --root "$ROOT" \
  --consent "$ROOT/trusted/consents/CONS-A-17-DIRECT-OLD1.consent.json"

# 普通字幕：两席分别查看
bin/simulcast-access send --root "$ROOT" \
  --sequence SUB-001 --version 1 --text '今晚联播准时开始。'
bin/new-viewer  view MSG-SUB-001-v1 --root "$ROOT"
bin/old-viewer  view MSG-SUB-001-v1 --root "$ROOT"

# 同文确认和第三方验证
bin/simulcast-access confirm-same-text --root "$ROOT" \
  --sequence SUB-001 --version 1
bin/simulcast-access verify-proof --root "$ROOT" \
  "$ROOT/access/proofs/MSG-SUB-001-v1.same-text.proof.json"
```

导播亲写改词：

```bash
bin/simulcast-access send --root "$ROOT" \
  --sequence SUB-002 --version 2 \
  --body-file director_inputs/revision-SUB-002-v2.txt \
  --kind director-revision \
  --note '场记备注：仅新席可见。'

bin/new-viewer view MSG-SUB-002-v2 --root "$ROOT"
bin/old-viewer view MSG-SUB-002-v2 --root "$ROOT"
```

也可用 `watch --once` 轮询本地信箱并自动显示/回单；不带 `--once` 时持续轮询。

显示稿在：

```text
$ROOT/seats/new-1/screens/MSG-SUB-002-v2.screen.txt
$ROOT/seats/old-1/screens/MSG-SUB-002-v2.screen.txt
```

两端联播/收看回单在：

```text
$ROOT/seats/new-1/receipts/MSG-SUB-002-v2.receipt.json
$ROOT/seats/old-1/receipts/MSG-SUB-002-v2.receipt.json
```

## 必须重建会话的情况

### 一端能力声明改成另一场次

```bash
bin/simulcast-access set-scene --root "$ROOT" --seat new-1 --scene B-209

# 旧会话固定的是原能力声明摘要，下面两条都会被拒绝：
bin/simulcast-access send --root "$ROOT" --sequence X --text x
bin/simulcast-access negotiate --root "$ROOT" \
  --consent "$ROOT/trusted/consents/CONS-A-17-DIRECT-OLD1.consent.json"
```

要恢复 A-17，需要把可信能力声明改回/重新交付，由场记就当前能力摘要重新出具
同意记录，再 `negotiate` 生成新的 `SES-A-17-...` 会话。旧证明仍可独立验证，
但旧约定不能继续处理新稿。

### 一席还停留在改词前版本

若 `SUB-003` 已有 v1，旧席只看了 v1，而新席看了 v2：

```bash
bin/simulcast-access confirm-same-text --root "$ROOT" \
  --sequence SUB-003 --version 2
# 拒绝：VERSION_SKEW ... old-1 ... MSG-SUB-003-v1 ...
```

没有旧席对 v2 正文摘要的签名回单，就不能确认两端见过新词。

### 联播中途更换旧席

```bash
bin/simulcast-access suspend-session --root "$ROOT" \
  --reason 'old-1 故障，换 old-2' --replacement-seat old-2

bin/simulcast-access issue-consent --root "$ROOT" \
  --profile direct-common-text/v1 --seats new-1,old-2 \
  --consent-id CONS-A17-DIRECT-OLD2

bin/simulcast-access negotiate --root "$ROOT" \
  --consent "$ROOT/trusted/consents/CONS-A17-DIRECT-OLD2.consent.json"
```

新会话有新的 session id/generation 和新名册。`old-2` 即使持有自己的合法回单，
也不属于原 `SES-A-17-001` 名册；程序会以 `RECEIPT_FROM_UNKNOWN_SEAT` 拒绝把它
并入旧约定。

## 转台模式

场记选择 `relay-turntable-terminated/v1` 后：

```bash
R=runtime/relay
bin/simulcast-access negotiate --root "$R" \
  --consent "$R/trusted/consents/CONS-A-17-RELAY-OLD1.consent.json"

NOTE=$'@relay 方音：{DY-01}；{DY-02}。\n@duty 仅当班看见。'
bin/simulcast-access send --root "$R" \
  --sequence SUB-002 --version 2 \
  --body-file director_inputs/revision-SUB-002-v2.txt \
  --note "$NOTE"

bin/relay-turntable process-job JOB-MSG-SUB-002-v2 --root "$R"
bin/relay-turntable show-cert MSG-SUB-002-v2 --root "$R"

bin/new-viewer view MSG-SUB-002-v2 --root "$R"
bin/old-viewer view MSG-SUB-002-v2 --root "$R"
bin/simulcast-access confirm-relay --root "$R" \
  --sequence SUB-002 --version 2
```

转台证明：

```text
$R/access/conversion-certs/CERT-MSG-SUB-002-v2.cert.json
```

其中 `segments` 逐段记录转台本次打开的正文/`@relay` 文本、使用的读音表行摘要、
转换出的汉字和输出摘要。`@duty` 段在接入程序构造任务时就不交给转台，证明中
标明 `duty_only_section_seen=false`。审计 JSONL 在：

```text
$R/audit/audit.jsonl
```

转台模式不谎称两席显示同文：新席看原稿和值班备注，旧席看转台生成的旧式播稿；
两者关系由转台签名的转换证明绑定。

## 文件归宿

```text
audience/                 观众可见稿主文件
duty/                     当班场记备注
seats/<seat>/inbox/       该终端收到的 SIMSG/1 文本消息
seats/<seat>/screens/     终端实际显示稿
seats/<seat>/receipts/    终端签名回单
trusted/capabilities/     经可信文件通道交来的能力声明
trusted/consents/         场记同意记录
trusted/keyring.json      可信公钥副本
access/sessions/          本期会话约定
access/proofs/            同文证明或转台交接证明
access/conversion-certs/  只能由转台签名的逐段转换证明
private/turntable/        受托方言读音表和转台私钥模拟位置
```

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试覆盖：登录不足为凭、同文确认、导播改词、版本停留导致 `VERSION_SKEW`、
能力声明换场次、旧席中途替换、转台逐段转换和证明验证。

## 信任模型说明

这是单机协议与文件归宿模拟。私钥也以小文件保存在运行根中，用来模拟各角色
持有密钥；它不提供真实操作系统用户隔离，也不构成真实网络保密通道。它精确
演示的是协议约束、签名责任、版本/场次绑定和审计路径，而不是生产级密钥存储。
