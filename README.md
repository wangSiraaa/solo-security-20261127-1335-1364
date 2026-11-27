# 本地协作字幕编辑器

一套无第三方依赖的本地双目录协作编辑器。馆内目录同时保存公开台词与私有旁注；合作社目录和交换包只包含经过白名单导出的公开数据。

## 选定的撤销权方案

采用题目中的第二种方案：

> **交付时给合作社分出一条自主的公开编辑史。**

具体权限是：

- 合作社端可以在断线时编辑、`undo`、`redo` **本人的公开操作**，这些操作有独立的 `PUB-C-####` 编号。
- 已交付公开分支的撤回权归合作社分支：它可用 `withdraw-delivered` 追加一笔新的合作社公开撤回；历史不会被馆内远程抹掉。
- 馆内对**尚未交付**的混合笔可以整笔撤销，公开字句和私有旁注在一次本地裁定中一起回退。
- 混合笔的公开半笔一旦交付并确认，馆内不能再把整笔事务从共同历史中抹除。馆内撤混合笔时：
  - 私有半笔在馆内撤销；
  - 同时生成一笔**新的、独立编号的公开订正提案**；
  - 该提案回到合作社端，按最后确认的共同公开底稿做三方合并。
- 合并始终只看公开锚点和公开字符区间，不读取、不导出私有旁注或其全文偏移。

这保留了合作社离线撤销/重做的确定性；代价是馆内已交付混合笔不再有跨边界的原子整笔撤销，只能做公开订正。

## 一键验证

```bash
bash ./demo.sh
```

它会运行：

1. 11 个单元测试；
2. 37 项内置端到端断言，生成在 `demo-run/`；
3. 独立进程的 CLI 场景，生成在 `cli-run/`。

只运行内置场景：

```bash
python3 -m collab_editor demo --root ./demo-run
cat ./demo-run/DEMO-REPORT.md
```

只运行两条同文句的真实 CLI 流程：

```bash
bash examples/scenario.sh ./cli-run
```

## 目录与信任边界

```text
museum/
  collab.json
  doc.json                 # 公开锚点和公开文本
  history.json             # 公开史
  public.txt               # 可发出的公开稿
  outbox/                  # 白名单公开包
  reports/                 # 公开合并答复
  conflicts/               # 公开冲突单
  private/                 # 0700
    private.json           # 旁注、全文位置、内部 MX/PV 序号和混合事务映射

coop/
  collab.json
  doc.json
  history.json             # 交付后自主的公开史
  stacks.json              # 本人 undo/redo 栈，只含公开 span
  undo-policy.json
  outbox/ reports/ conflicts/

exchange/
  *.json                   # 双方实际交换的公开包/答复
```

真正不能给对方读的是 `museum/private/`。合作社目录、`exchange/`、馆内 `outbox/`、公开报告和冲突单都可接受隐私扫描：

```bash
python3 -m collab_editor audit \
  --external ./cli-run/coop \
  --external ./cli-run/exchange \
  --external ./cli-run/museum/outbox \
  --canary '你的私有旁注特征串'
```

## 两条同文句的操作方式

初始化时给两句相同文字分配不同的稳定公开锚点，例如 `A1`、`A2`。因此“改第二句”不会因为字符串相同而误中第一句；私有旁注挂在 `A1` 上，也不会用会漂移的绝对全文偏移去改写公开稿。

```bash
./collab init --root /tmp/page/museum --role museum --author museum \
  --anchor 'A1=潮水退回桥下。' \
  --anchor 'A2=潮水退回桥下。'

./collab deliver --from /tmp/page/museum --to /tmp/page/coop \
  --packet /tmp/exchange/delivery.json

./collab edit --root /tmp/page/coop --anchor A2 \
  --old '潮水退回桥下。' --text '潮水漫过桥下。'
./collab undo --root /tmp/page/coop
./collab redo --root /tmp/page/coop
./collab push --root /tmp/page/coop --out /tmp/exchange/coop.json

./collab note --root /tmp/page/museum --anchor A1 \
  --text '私有双关考证，不能出门'
./collab merge --root /tmp/page/museum \
  --packet /tmp/exchange/coop.json \
  --answer-out /tmp/exchange/answer.json
```

合并答复中的 `affected_locations` 只包含公开锚点和锚点内字符位置，例如：

```json
{
  "anchor": "A2",
  "base_start": 2,
  "base_end": 4,
  "before": "退回",
  "after": "漫过"
}
```

随后馆内可在旁注所属的同一公开锚点 `A1` 改一个字：

```bash
./collab edit --root /tmp/page/museum --anchor A1 \
  --old '潮水退回桥下。' --text '潮光退回桥下。'
./collab push --root /tmp/page/museum --peer coop \
  --out /tmp/exchange/museum.json
./collab merge --root /tmp/page/coop --packet /tmp/exchange/museum.json
```

该答复只会指出 `A1` 的一处公开 hunk；私有注不会变成第二个位置，也不会制造公共冲突。

## 同一行被双方各自切开

初始公开锚点 `L1 = 一二三四五六七八`，双方离线分别在第 2、5 个字符处插入换行：

```text
共同底稿：一二三四五六七八
馆内切点：一二 / 三四五六七八
合作切点：一二三四五 / 六七八
合并结果：一二
          三四五
          六七八
```

合并器以最后确认的共同公开底稿为 base 做三方 span 合并。不同偏移的切点会同时保留；完全相同的切点和相同插入会幂等合并；同一点插入不同文本或重叠替换会生成一张公开冲突单，指出唯一的锚点和字符区间。

冲突单位于 `conflicts/CF-####.json` 和 `.txt`，可选择：

```bash
./collab resolve --root <workspace> --conflict CF-0001 --choice ours
./collab resolve --root <workspace> --conflict CF-0001 --choice theirs
```

## 混合修改

```python
from collab_editor import Workspace

museum = Workspace("./museum")
result = museum.mixed_edit(
    "A1",
    "新的公开句子",
    "新的私有旁注或对原旁注的修订",
    old="旧的公开句子",
    note_id="NT-0001",       # 可选：修订既有旁注
)

# 未交付：整笔撤销。
museum.undo_mixed(result["internal_id"])
```

若公开半笔已经交付并确认，再次调用 `undo_mixed` 会返回：

```json
{
  "policy": "public-correction-proposed",
  "public_correction": {
    "kind": "correction",
    "corrects": "PUB-M-0001"
  }
}
```

外部包经过 `privacy.public_op_view()` 白名单构造，不会复制内部对象，因此不会带出：

- `MX-####` / `PV-####` 内部编号；
- 旁注正文或 `annotations` 字段；
- `full_text_start` / `full_text_end`；
- `internal_seq`、纯私有操作次数或私有计数；
- 馆内混合事务到公开操作的映射。

公开编号只数公开变化；纯私有注不占用、不断开公开编号。

## 主要模块

- `collab_editor/document.py`：稳定锚点公开文档和 span 编辑。
- `collab_editor/merge.py`：只依赖公开 base/local/remote 的三方合并。
- `collab_editor/workspace.py`：目录存储、undo/redo、交付、包、确认、冲突和混合事务策略。
- `collab_editor/privacy.py`：白名单导出与泄漏扫描。
- `collab_editor/demo.py`：题目场景的可执行断言。
- `tests/test_collab_editor.py`：回归测试。

## 非目标

这是本地文件协作原型，不提供实时 CRDT 协同、服务端鉴权或恶意同机用户隔离。若要把目录边界变成操作系统级保密边界，应以另一个低权限用户运行合作社端，只通过权限受限的 `exchange/` 交换包，并且不要共享整个馆内目录。
