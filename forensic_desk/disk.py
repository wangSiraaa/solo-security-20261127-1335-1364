"""Short-text simulation of a disk's physical directory, journal and blocks."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .hashing import expand_page_ranges, sha256_bytes


DIRECTORY_NAME = "directory.idx"
JOURNAL_NAME = "operation_log.jsonl"
LAYOUT_NAME = "layout.json"
BLOCK_DIR = "blocks"

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")


class DiskFormatError(RuntimeError):
    pass


class UnsafeBlockId(ValueError):
    pass


def safe_artifact_id(value: str) -> str:
    if not value or not _SAFE_ID_RE.match(value) or value in {".", ".."}:
        raise UnsafeBlockId(f"unsafe artifact id: {value!r}")
    return value


@dataclass
class DirectoryRow:
    entry_id: str
    name: str
    block_id: Optional[str] = None
    pages: List[str] = field(default_factory=list)
    line_no: Optional[int] = None
    raw_line: Optional[str] = None

    def canonical_line(self) -> str:
        pages = ",".join(self.pages)
        block = self.block_id or "-"
        return f"{self.entry_id}|{self.name}|{block}|{pages}"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class JournalOp:
    tx_id: str
    op: str
    entry_id: Optional[str] = None
    old_name: Optional[str] = None
    name: Optional[str] = None
    block_id: Optional[str] = None
    pages: List[str] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any], line_no: int) -> "JournalOp":
        op = raw.get("op")
        tx_id = raw.get("tx") or f"LINE-{line_no}"
        if op not in {"rename", "add", "put_row", "write_line"}:
            raise DiskFormatError(f"unsupported journal op {op!r} on line {line_no}")
        pages = raw.get("pages") or []
        if isinstance(pages, str):
            pages = expand_page_ranges([pages])
        return cls(
            tx_id=str(tx_id),
            op=str(op),
            entry_id=raw.get("entry_id"),
            old_name=raw.get("old_name"),
            name=raw.get("name"),
            block_id=raw.get("block_id"),
            pages=list(pages),
            raw=raw,
        )


def parse_directory(text: str) -> List[DirectoryRow]:
    rows: List[DirectoryRow] = []
    for line_no, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("|")
        if len(parts) != 4:
            raise DiskFormatError(
                f"directory line {line_no} must have 4 pipe fields, got {len(parts)}"
            )
        entry_id, name, block_id, page_field = parts
        pages = expand_page_ranges([p.strip() for p in page_field.split(",") if p.strip()])
        rows.append(
            DirectoryRow(
                entry_id=entry_id.strip(),
                name=name,
                block_id=None if block_id.strip() in {"", "-"} else block_id.strip(),
                pages=pages,
                line_no=line_no,
                raw_line=raw_line,
            )
        )
    return rows


def serialize_directory(rows: List[DirectoryRow], *, header: bool = True) -> str:
    lines = ["# FDIDX/1 physical/reconstructed directory"] if header else []
    lines.extend(row.canonical_line() for row in rows)
    return "\n".join(lines) + "\n"


@dataclass
class PageLocation:
    page_id: str
    block_id: str
    index: int


class DiskImage:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.directory_path = self.root / DIRECTORY_NAME
        self.journal_path = self.root / JOURNAL_NAME
        self.layout_path = self.root / LAYOUT_NAME
        if not self.directory_path.is_file():
            raise FileNotFoundError(self.directory_path)
        if not self.journal_path.is_file():
            raise FileNotFoundError(self.journal_path)

        self.directory_bytes = self.directory_path.read_bytes()
        self.directory_text = self.directory_bytes.decode("utf-8")
        self.directory_sha256 = sha256_bytes(self.directory_bytes)
        self.rows = parse_directory(self.directory_text)

        self.journal_bytes = self.journal_path.read_bytes()
        self.journal_text = self.journal_bytes.decode("utf-8")
        self.journal_sha256 = sha256_bytes(self.journal_bytes)
        self.journal_ops = self._parse_journal()

        self.layout = self._load_layout()
        self.pages: Dict[str, PageLocation] = {
            pid: PageLocation(page_id=pid, block_id=loc["block"], index=int(loc["index"]))
            for pid, loc in self.layout.get("pages", {}).items()
        }

    def _parse_journal(self) -> List[JournalOp]:
        ops: List[JournalOp] = []
        for line_no, line in enumerate(self.journal_text.splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DiskFormatError(f"journal line {line_no} is not JSON: {exc}") from exc
            ops.append(JournalOp.from_dict(raw, line_no))
        return ops

    def _load_layout(self) -> Dict[str, Any]:
        if self.layout_path.is_file():
            return json.loads(self.layout_path.read_text(encoding="utf-8"))
        return {"pages": {}}

    def artifact_manifest(self) -> List[Dict[str, Any]]:
        """Hash every physical file that arrived on the simulated disk."""
        manifest: List[Dict[str, Any]] = []
        for path in sorted(p for p in self.root.rglob("*") if p.is_file()):
            rel = path.relative_to(self.root).as_posix()
            data = path.read_bytes()
            manifest.append(
                {
                    "path": rel,
                    "size": len(data),
                    "sha256": sha256_bytes(data),
                }
            )
        return manifest

    def block_path(self, block_id: str) -> Path:
        safe_artifact_id(block_id)
        return self.root / BLOCK_DIR / f"{block_id}.txt"

    def has_block(self, block_id: str) -> bool:
        return self.block_path(block_id).is_file()

    def read_block_bytes(self, block_id: str) -> bytes:
        path = self.block_path(block_id)
        if not path.is_file():
            raise FileNotFoundError(f"block not found: {block_id}")
        return path.read_bytes()

    def read_block_text(self, block_id: str) -> str:
        return self.read_block_bytes(block_id).decode("utf-8")

    def block_pages(self, block_id: str) -> List[str]:
        return [pid for pid, loc in self.pages.items() if loc.block_id == block_id]

    def read_page(self, page_id: str) -> Dict[str, Any]:
        location = self.pages.get(page_id)
        if location is None:
            raise KeyError(f"no such page in physical layout: {page_id}")
        block_bytes = self.read_block_bytes(location.block_id)
        parts = block_bytes.split(b"\f")
        if location.index >= len(parts):
            raise DiskFormatError(f"page index out of range for {page_id}")
        page_bytes = parts[location.index]
        return {
            "page_id": page_id,
            "block_id": location.block_id,
            "index": location.index,
            "text": page_bytes.decode("utf-8"),
            "sha256": sha256_bytes(page_bytes),
            "block_sha256": sha256_bytes(block_bytes),
        }

    def find_rows(
        self, *, name: Optional[str] = None, entry_id: Optional[str] = None
    ) -> List[DirectoryRow]:
        result = []
        for row in self.rows:
            if entry_id is not None and row.entry_id != entry_id:
                continue
            if name is not None and row.name != name:
                continue
            result.append(row)
        return result
