#!/usr/bin/env python3
"""根目录便捷入口：等价于 python -m labbench demo --mode both。"""

from labbench.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["demo", "--mode", "both", "--dir", "runtime/demo"]))
