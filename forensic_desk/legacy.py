"""Emulated old阅卷器 that must replay the journal and rewrite the index."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from .cow import CowLayer
from .disk import DIRECTORY_NAME, DiskImage, parse_directory
from .replay import ReplayResult, replay_journal


class LegacyViewerError(RuntimeError):
    pass


class LegacyWriteBackBlocked(LegacyViewerError):
    pass


class LegacyViewer:
    """A small deterministic model of the legacy application."""

    name = "fictional-legacy-indexer/1.0"

    @staticmethod
    def replay_disk(disk: DiskImage) -> ReplayResult:
        return replay_journal(
            disk.rows,
            disk.journal_ops,
            directory_sha256=disk.directory_sha256,
            journal_sha256=disk.journal_sha256,
        )

    @staticmethod
    def assert_direct_open_allowed(directory_root: str | Path) -> None:
        root = Path(directory_root)
        index = root / DIRECTORY_NAME
        if not index.exists():
            raise FileNotFoundError(index)

        policy_readonly = (root / "station-manifest.json").exists()
        index_writable = os.access(index, os.W_OK)
        dir_writable = os.access(root, os.W_OK)
        if policy_readonly or not index_writable or not dir_writable:
            raise LegacyWriteBackBlocked(
                "老阅卷器要求重放日志并以写回方式重建 directory.idx；"
                "该阅盘台/原始卷为写保护，不能直接开盘。"
            )

    @classmethod
    def open_direct(cls, directory_root: str | Path) -> "LegacyViewer":
        cls.assert_direct_open_allowed(directory_root)
        # We deliberately do not provide a method that mutates an original
        # path.  If the assertion is somehow disabled, fail closed here.
        raise LegacyWriteBackBlocked("direct original write-back is not implemented by the desk")

    @classmethod
    def run_in_cow(cls, cow: CowLayer, original: DiskImage) -> Dict[str, Any]:
        """Open the viewer in a private COW layer and let it write the index."""
        base_text = cow.read_text(DIRECTORY_NAME)
        base_rows = parse_directory(base_text)
        result = replay_journal(
            base_rows,
            original.journal_ops,
            directory_sha256=original.directory_sha256,
            journal_sha256=original.journal_sha256,
        )
        write_witness = cow.write_text(DIRECTORY_NAME, result.output_text)
        return {
            "viewer": cls.name,
            "mode": "one_shot_copy_on_write",
            "replay": result.to_proof(),
            "applied_witnesses": result.applied,
            "conflicts": result.conflicts,
            "write_witness": write_witness,
            "logical_rows": [row.to_dict() for row in result.rows],
        }
