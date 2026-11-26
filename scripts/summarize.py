#!/usr/bin/env python3
"""Build a concise cross-scenario summary from persisted replay state."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from museum_update.report import conclusion  # noqa: E402

SCENARIOS = [
    ("一直健康 / 两回执后烧桥", ROOT / "state" / "replay-healthy", "replay-healthy.md"),
    ("先成功后失败 / 暂不烧桥", ROOT / "state" / "replay-flaky-wait", "replay-flaky-wait.md"),
    ("先成功后失败 / 首次成功即提前烧桥", ROOT / "state" / "whatif-flaky-burn-early", "whatif-flaky-burn-early.md"),
]


def main() -> int:
    lines = [
        "# 回放结果摘要",
        "",
        "下表由 `scripts/summarize.py` 从各场景的持久 `controller-state.json` 生成；完整时间线见链接记录。",
        "",
        "| 回放 | M1 甲机 | M2 乙机 | M3 丙机 | 完整记录 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for name, state_dir, record in SCENARIOS:
        state = json.loads((state_dir / "controller-state.json").read_text(encoding="utf-8"))
        cells = []
        for mid in ("M1", "M2", "M3"):
            m = state["machines"][mid]
            cells.append(conclusion(m, state).replace("|", "\\|"))
        lines.append(f"| {name} | {cells[0]} | {cells[1]} | {cells[2]} | [{record}](./{record}) |")

    lines.extend(
        [
            "",
            "## 什么时候可以烧桥？",
            "",
            "本实现把**写槽/试启合格**和**不可逆承诺**分开：",
            "",
            "1. 实际字节必须与发布方批准的 SHA-256 摘要、代次和配套组件清单完全一致；",
            "2. 目标机必须有真实、签名可验、机器/槽位/整套摘要匹配的开机回执；",
            "3. 最低安全策略建议至少见到**第二次连续自检合格**，且最近一次不是失败；",
            "4. 满足这些条件后，维护员可执行 `commit` 把最低可启动代次从 5 提高到 6。",
            "",
            "只有第一次合格回执时，CLI 也允许维护员显式加 `--accept-early-risk` 提前烧桥并记录理由。",
            "若随后第二次自检失败，最低代次 6 会拒绝第五代，控制器不能再原地恢复，只能登记停用待修。",
            "若暂不烧桥且仍在第 3 班停用点前，失败会切回完好的第五代槽并做恢复自检，服务保留；到第 3 班后第五代同样被禁止。",
            "",
        ]
    )
    out = ROOT / "records" / "summary.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
