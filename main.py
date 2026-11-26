#!/usr/bin/env python3
"""兼容入口：python3 main.py ... 等价于 ./seal-cabinet ..."""
from seal_cabinet import main

if __name__ == "__main__":
    raise SystemExit(main())
