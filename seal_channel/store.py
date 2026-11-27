"""JSON 文件持久化。

每次写入都使用同目录临时文件 + ``os.replace``，避免重启时读到写了一半的
封条消耗记录或收端账册。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


_MISSING = object()


class JsonStore:
    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root).expanduser().resolve()

    def path(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    def ensure_dir(self, *parts: str) -> Path:
        p = self.path(*parts)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def exists(self, *parts: str) -> bool:
        return self.path(*parts).exists()

    def read_json(self, *parts: str, default: Any = _MISSING) -> Any:
        p = self.path(*parts)
        try:
            with p.open("r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            if default is _MISSING:
                raise
            return default

    def write_json(self, *parts: str, value: Any) -> Path:
        if not parts:
            raise ValueError("JSON 路径不能为空")
        p = self.path(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)

        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{p.name}.",
            suffix=".tmp",
            dir=str(p.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(value, f, ensure_ascii=False, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, p)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
            raise
        return p

    def append_jsonl(self, *parts: str, value: Any) -> Path:
        p = self.path(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        return p
