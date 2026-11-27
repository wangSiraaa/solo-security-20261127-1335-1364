#!/usr/bin/env python3
"""Conventional local entry point; delegates to run.py/coupon_demo.cli."""
from coupon_demo.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
