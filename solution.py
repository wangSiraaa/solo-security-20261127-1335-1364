#!/usr/bin/env python3
"""Task-specific entry point: run the three-block/two-pattern acceptance demo."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from handoff_service.scenario import run_demo

if __name__ == "__main__":
    result = run_demo()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result.get("all_assertions_passed") else 1)
