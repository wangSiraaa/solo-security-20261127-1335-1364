"""Fictional short-text disks used by tests and the demonstration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_case_1401(root: str | Path) -> Path:
    root = Path(root) / "case-1401"
    _write(
        root / "directory.idx",
        """# FDIDX/1
E100|未重放改名-旧名.txt|BLK100|P100
E200|稳定条目.txt|BLK200|P201,P202
E300|接盘目录原行.txt|BLK300|P301
""",
    )
    _write(
        root / "operation_log.jsonl",
        """{"tx":"TX1001","op":"rename","entry_id":"E100","old_name":"未重放改名-旧名.txt","name":"日志重放-新名.txt"}
{"tx":"TX1002","op":"add","entry_id":"E900","name":"Q-开卷即写行-来自日志重放.txt","block_id":"BLK900","pages":["P900"]}
""",
    )
    _write(root / "blocks" / "BLK100.txt", "P100 原始块观察：旧名对应的短文本块。\n")
    _write(
        root / "blocks" / "BLK200.txt",
        "P201 可授权页：稳定条目的第一页。\fP202 邻页内容：未获当前页范围授权时不得交出。\n",
    )
    _write(root / "blocks" / "BLK300.txt", "P301 原始目录已有行对应的块。\n")
    _write(root / "blocks" / "BLK900.txt", "P900 日志新增条目引用的块；初始范围未放行。\n")
    layout = {
        "pages": {
            "P100": {"block": "BLK100", "index": 0},
            "P201": {"block": "BLK200", "index": 0},
            "P202": {"block": "BLK200", "index": 1},
            "P301": {"block": "BLK300", "index": 0},
            "P900": {"block": "BLK900", "index": 0},
        }
    }
    _write(root / "layout.json", json.dumps(layout, ensure_ascii=False, indent=2) + "\n")
    return root


def write_second_volume(root: str | Path) -> Path:
    root = Path(root) / "second-volume"
    _write(
        root / "directory.idx",
        """# FDIDX/1
E001|接盘时已存在的行.txt|BLK001|P001
""",
    )
    _write(root / "operation_log.jsonl", "# no committed but unreplayed operations\n")
    _write(
        root / "blocks" / "BLK001.txt",
        "P001 接盘时已核验的原始页：这一行在接盘目录中存在。\fP002 邻页保密文本：改变页范围后绝不能随同一证据交出。\n",
    )
    layout = {
        "pages": {
            "P001": {"block": "BLK001", "index": 0},
            "P002": {"block": "BLK001", "index": 1},
        }
    }
    _write(root / "layout.json", json.dumps(layout, ensure_ascii=False, indent=2) + "\n")
    return root


def write_all_fixtures(root: str | Path) -> Dict[str, Path]:
    base = Path(root)
    return {
        "case1401": write_case_1401(base),
        "second": write_second_volume(base),
    }
