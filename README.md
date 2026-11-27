# 受限字模导入器与本地排字幕机

这是一个**不读取系统字体、不执行包内代码**的声明式方格字模系统。虚拟画布默认
`16 × 6 = 96` 格；所有消耗都用整数记录。

## 发行与承诺边界

本实现选择 **“内容寻址的声明整包 + 新机沙箱排版”** 作为主发行方式，并在导入时额外
裁出旧机译字库：

- **导入/安装时兑现**
  - JSON、字段、ID、嵌套深度、包大小和操作数配额；
  - 所有引用都在包内，不访问系统字体或外部资源；
  - 依赖图、共享图元、循环/相互引用分析；
  - 每个字的静态展开 `work`、格数和包围盒上界；
  - 旧机整字集合 `classic_glyphs`，以及 `cut-classic` 裁出的自足包；
  - 内容哈希与准入令牌；包一改，旧令牌对当前修订立即失效。
- **排版/锁定时才可承诺**
  - 本场 `font_size`、`line_width` 和机器消耗切片；
  - 同一锁定版面中的相同条件只计算一次，结论写入 lock；
  - 新机在私有 stage 中按实际绘制消耗执行；
  - 字没画完、越界或递归，一律丢弃私有 stage，整字换成获准替代字；
  - 只有锁定结果中的 `promised_whole=true` 和顶层 `promise` 是对该场版面的承诺。

默认计量：

- 每次声明派发：`1 work`；
- 每画一个格：`1 work` 和 `1 cell_write`；
- 新机一次绘制切片：`work <= 32`、`cell_writes <= 12`；
- 旧机静态核算：`work <= 64`、`cell_writes <= 24`、墨迹不超过 `3 × 5`。

## 30 秒验收

```bash
cd /workspace

# 内置验收：导入两套包、排完整“启明学校”、自引用失效、恢复后收窄行宽
python3 -m subtitle_studio selftest

# 持久化 CLI 流程
STORE=/tmp/subtitle-acceptance
python3 -m subtitle_studio --store "$STORE" import packages/fangyuan.json
python3 -m subtitle_studio --store "$STORE" import packages/gewu.json

python3 -m subtitle_studio --store "$STORE" lock "启明学校" \
  --package fangyuan --line-width 16

LOCK_ID=lock_...   # 复制上一步输出的 lock id
python3 -m subtitle_studio --store "$STORE" canvas "$LOCK_ID"
```

`lock --format json` 会保存完整锁定结果；`canvas <lock_id> --format json`
可读回整数矩阵。锁定后的画布是快照，之后改包不会改写旧 lock。

## 验收人自己的两套包：Python API

```python
from subtitle_studio import FontStudio

studio = FontStudio(store_dir="/tmp/my-studio")
a = studio.import_path("packages/fangyuan.json")
b = studio.import_path("packages/gewu.json")

lock = studio.lock_layout(
    "启明学校",
    package_id=a["package_id"],
    font_size=5,
    line_width=16,
    mode="live",
)
canvas = studio.read_canvas(lock["lock_id"])
print(canvas.unicode_art)
print(canvas.matrix)
```

也可以直接导入自己的 dict：`studio.import_dict(my_package)`。
依赖、环和静态展开上界可用 `studio.analyze("fangyuan")` 或
`python3 -m subtitle_studio analyze fangyuan --format json` 查看。

### 自引用使旧准入失效

```python
import copy

old_admission = receipt["admission_id"]
bad = copy.deepcopy(raw_package)
bad["primitives"]["yong"].append({"op": "ref", "ref": "yong"})
new_receipt = studio.import_dict(bad)

assert "永" in new_receipt["rejected"]
try:
    studio.verify_admission(old_admission)
except Exception as exc:
    print("旧准入对当前包失效：", exc)
```

恢复原 dict 后重新导入，原内容哈希和准入结论可重新验证。

### 收窄行宽后重新作整字/替代决定

方圆包中 `永` 的条件是：

- `line_width >= 16`：7 个格的疏朗分支；
- `line_width <= 15`：13 次格写入的茂密窄行分支。

```python
wide = studio.lock_layout("永", package_id="fangyuan", line_width=16)
narrow = studio.lock_layout("永", package_id="fangyuan", line_width=15)

# 默认新机 cell_budget=12：第 13 次写入前停下，私有半个字被丢弃，
# 画布上只有完整的获准 '?'。
assert narrow["glyph_results"][0]["outcome"] == "substituted"

# 若该机切片放宽到 cell_budget=16/work_budget=40，同一窄行分支可整字提交。
whole = studio.lock_layout(
    "永", package_id="fangyuan", line_width=15,
    work_budget=40, cell_budget=16,
)
assert whole["glyph_results"][0]["outcome"] == "committed"
```

这说明排版端没有复用宽行的成功结论；每次锁定都按真实版面和实际消耗重算。

## 哪些字可承诺

```python
# 旧机：安装时静态保证所有分支的最坏上界
studio.promised_chars("fangyuan", mode="classic")

# 新机：针对本场 profile 实际沙箱试跑后承诺
studio.promised_chars(
    "fangyuan", mode="live",
    font_size=5, line_width=16,
)
```

示例包中的 `梦` 有一个仅在 `font_size >= 999` 选择的、引用十次 `bar3`
的重分支。它的静态最坏上界超过旧机政策，因此不在 classic cut；但在本场
`font_size=5` 下实际执行的是省分支，所以新机可以在排版时承诺整字。

裁出旧机译字库：

```bash
python3 -m subtitle_studio --store /tmp/sub cut-classic fangyuan /tmp/fangyuan-classic.json
python3 -m subtitle_studio --store /tmp/sub import /tmp/fangyuan-classic.json
```

## 声明式包格式

包是 UTF-8 JSON，没有 `eval`、脚本字段、插件路径或系统字体名。完整示例见
[`packages/fangyuan.json`](packages/fangyuan.json) 和
[`packages/gewu.json`](packages/gewu.json)。

```json
{
  "id": "my-pack",
  "fallback": "?",
  "glyphs": {
    "永": {"primitive": "yong", "advance": 4},
    "?": "ask"
  },
  "primitives": {
    "dot": [
      {"op": "square", "x": 1, "y": 0}
    ],
    "yong": [
      {
        "op": "branch",
        "cases": [
          {
            "when": {"line_width_lte": 15},
            "ops": [{"op": "ref", "ref": "yong_narrow"}]
          },
          {
            "otherwise": true,
            "ops": [{"op": "ref", "ref": "yong_wide"}]
          }
        ]
      }
    ]
  }
}
```

允许的图元操作只有三类：

1. **方块**
   - `{"op":"square","x":0,"y":0}`：一个格；
   - `{"op":"square","x":0,"y":0,"size":2}`：`2×2` 方块；
   - 也接受 `block/rect` 与 `"w"/"h"`。
2. **引用图元**
   - `{"op":"ref","ref":"dot","x":1,"y":2}`；
   - 同一图元被引用 N 次就会计量/绘制 N 次。
3. **条件分支**
   - 谓词可用 `font_size_gte`、`font_size_lte`、`line_width_gte`、
     `line_width_lte`；
   - 别名包括 `min_font_size`、`max_font_size`、`max_line_width`、`width` 等；
   - 条件对象是纯数据。相同规范条件在一次 lock 中只评估一次。

## 本地 JSON API（可选）

```bash
python3 -m subtitle_studio --store /tmp/sub serve --host 127.0.0.1 --port 8765
```

```bash
curl -X POST http://127.0.0.1:8765/api/packages/import \
  -H 'Content-Type: application/json' \
  --data-binary @packages/fangyuan.json

curl -X POST http://127.0.0.1:8765/api/layouts/lock \
  -H 'Content-Type: application/json' \
  -d '{"package_id":"fangyuan","text":"启明学校","line_width":16}'

curl http://127.0.0.1:8765/api/locks/<lock_id>/canvas

# 旧准入令牌是否仍对当前包修订有效；失效时返回 409
curl http://127.0.0.1:8765/api/admissions/<admission_id>/verify
```

## 测试

```bash
python3 -m unittest discover -s tests -v
```

所有测试均使用仓库内 JSON 包和内置声明式替代字；不会读取 `/usr/share/fonts`、
fontconfig 或任何系统字体。
