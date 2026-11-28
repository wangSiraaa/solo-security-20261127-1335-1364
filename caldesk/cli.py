"""命令行入口：

* ``python -m caldesk.cli cost``                 —— 向用户公开的代价说明
* ``python -m caldesk.cli demo <输出目录>``      —— 跑通题面完整情节并落盘 ICS
"""

from __future__ import annotations

import sys
from pathlib import Path

from .policy import describe_costs


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv or argv[0] == "cost":
        print(describe_costs())
        return 0
    if argv[0] == "demo":
        out = Path(argv[1]) if len(argv) > 1 else Path("sample_calendars")
        # 延迟导入，避免纯查价时加载整套依赖
        from .scenario import run_scenario
        result = run_scenario(out)
        print(result)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
