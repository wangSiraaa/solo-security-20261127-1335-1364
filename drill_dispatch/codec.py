"""规范化编码、哈希链与原子落盘原语。

所有持久化记录都走这里：
- canonical/digest 保证同一条目的哈希在任何进程里算出来都一样；
- atomic_write 保证状态文件不会因崩溃写出半截 JSON；
- append_jsonl 保证日志条目顺序落盘（fsync 后才返回）。
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from typing import Any, Iterable


def canonical(obj: Any) -> str:
    """排序键、紧凑分隔的确定性 JSON 文本。"""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(obj: Any) -> str:
    return hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()


def now_ts() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def atomic_write(path: str, obj: Any) -> None:
    directory = ensure_dir(os.path.dirname(path) or ".")
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def read_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def append_jsonl(path: str, obj: Any) -> None:
    directory = ensure_dir(os.path.dirname(path) or ".")
    line = canonical(obj) + "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


def read_jsonl(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def chained_hash(prev_hash: str, entry: dict) -> str:
    """哈希链：本条哈希 = sha256(上一条哈希 || 本条规范化内容)。"""
    payload = dict(entry)
    payload.pop("hash", None)
    return hashlib.sha256((prev_hash + canonical(payload)).encode("utf-8")).hexdigest()


def verify_chain(entries: Iterable[dict], genesis: str = "") -> list[str]:
    """重算哈希链，返回所有断链说明；空列表即链完整。"""
    problems: list[str] = []
    prev = genesis
    for i, entry in enumerate(entries):
        expect = chained_hash(prev, entry)
        if entry.get("hash") != expect:
            problems.append(f"第 {i} 条记录哈希不符（期望 {expect[:12]}…）")
        prev = entry.get("hash", expect)
    return problems
