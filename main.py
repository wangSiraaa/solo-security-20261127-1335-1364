#!/usr/bin/env python3
"""兼容常见入口名：python3 main.py ..."""

from seal_channel.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
