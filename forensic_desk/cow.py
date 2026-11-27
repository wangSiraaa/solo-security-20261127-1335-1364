"""Private copy-on-write work layers for legacy viewers."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from .hashing import sha256_bytes, utc_now_iso
from .disk import safe_artifact_id


class CowViolation(RuntimeError):
    pass


class CowLayer:
    """A per-investigator overlay whose lower volume is never written."""

    def __init__(self, case_dir: str | Path, investigator_id: str, lower_root: str | Path):
        safe_artifact_id(investigator_id)
        self.case_dir = Path(case_dir)
        self.investigator_id = investigator_id
        self.root = self.case_dir / "work" / investigator_id
        self.upper = self.root / "upper"
        self.lower = Path(lower_root)
        self.upper.mkdir(parents=True, exist_ok=True)
        self.layer_file = self.root / "layer.json"
        if not self.layer_file.exists():
            self.layer_file.write_text(
                '{\n  "one_shot": true,\n'
                f'  "investigator": "{investigator_id}",\n'
                f'  "created_at": "{utc_now_iso()}"\n'
                "}\n",
                encoding="utf-8",
            )

    def _validate_rel(self, rel: str) -> Path:
        path = Path(rel)
        if path.is_absolute() or ".." in path.parts:
            raise CowViolation(f"refusing unsafe overlay path: {rel}")
        # Validate each path segment except directories like blocks? IDs are
        # filenames; keep this conservative for the simulation.
        return path

    def lower_path(self, rel: str) -> Path:
        self._validate_rel(rel)
        return self.lower / rel

    def upper_path(self, rel: str) -> Path:
        self._validate_rel(rel)
        return self.upper / rel

    def exists(self, rel: str) -> bool:
        return self.upper_path(rel).exists() or self.lower_path(rel).exists()

    def using_upper(self, rel: str) -> bool:
        return self.upper_path(rel).exists()

    def read_bytes(self, rel: str) -> bytes:
        upper = self.upper_path(rel)
        if upper.exists():
            return upper.read_bytes()
        return self.lower_path(rel).read_bytes()

    def read_text(self, rel: str) -> str:
        return self.read_bytes(rel).decode("utf-8")

    def sha256(self, rel: str) -> str:
        return sha256_bytes(self.read_bytes(rel))

    def lower_sha256(self, rel: str) -> Optional[str]:
        path = self.lower_path(rel)
        return sha256_bytes(path.read_bytes()) if path.exists() else None

    def upper_sha256(self, rel: str) -> Optional[str]:
        path = self.upper_path(rel)
        return sha256_bytes(path.read_bytes()) if path.exists() else None

    def copy_up(self, rel: str) -> Dict[str, Any]:
        upper = self.upper_path(rel)
        lower = self.lower_path(rel)
        if upper.exists():
            return {"path": rel, "copied_up": False, "upper_path": str(upper)}
        if not lower.exists():
            return {"path": rel, "copied_up": False, "new_file": True, "upper_path": str(upper)}
        upper.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(lower, upper)
        # A copied read-only piece of evidence must become writable only in
        # this investigator's private upper layer.
        upper.chmod(0o644)
        return {
            "path": rel,
            "copied_up": True,
            "lower_sha256": sha256_bytes(lower.read_bytes()),
            "upper_sha256_before_write": sha256_bytes(upper.read_bytes()),
            "upper_path": str(upper),
        }

    def write_text(self, rel: str, text: str) -> Dict[str, Any]:
        return self.write_bytes(rel, text.encode("utf-8"))

    def write_bytes(self, rel: str, data: bytes) -> Dict[str, Any]:
        copy_witness = self.copy_up(rel)
        upper = self.upper_path(rel)
        before_hash = sha256_bytes(upper.read_bytes()) if upper.exists() else None
        upper.parent.mkdir(parents=True, exist_ok=True)
        # Atomic replacement inside the private upper directory.
        tmp = upper.with_name(upper.name + ".tmp")
        tmp.write_bytes(data)
        tmp.chmod(0o644)
        tmp.replace(upper)
        after_hash = sha256_bytes(data)
        return {
            **copy_witness,
            "path": rel,
            "before_sha256": before_hash,
            "after_sha256": after_hash,
            "bytes": len(data),
            "written_at": utc_now_iso(),
        }
