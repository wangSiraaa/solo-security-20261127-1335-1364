"""Intake desk: bind source, generation, scope and derivative permissions."""

from __future__ import annotations

import json
import os
import shutil
import stat
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .authz import STATION_MODE, STATION_PERMISSIONS
from .disk import DIRECTORY_NAME, JOURNAL_NAME, LAYOUT_NAME, DiskImage
from .hashing import expand_page_ranges, object_hash, sha256_bytes, utc_now_iso
from .ledger import Ledger


class IntakeError(RuntimeError):
    pass


class ScopeError(IntakeError):
    pass


def make_tree_writable(root: str | Path) -> None:
    """Restore write bits so tests/demos can remove read-only media copies."""
    root = Path(root)
    if not root.exists():
        return
    for path in [root, *sorted((p for p in root.rglob("*")), reverse=True)]:
        try:
            current = path.stat().st_mode
            path.chmod(current | stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
        except FileNotFoundError:
            pass


def lock_tree_readonly(root: str | Path) -> None:
    """Simulate write-protected media with owner/group/other read/execute bits."""
    root = Path(root)
    for path in root.rglob("*"):
        if path.is_file() or path.is_symlink():
            path.chmod(0o444)
        elif path.is_dir():
            path.chmod(0o555)
    root.chmod(0o555)


class IntakeDesk:
    def __init__(self, case_dir: str | Path):
        self.case_dir = Path(case_dir)
        self.case_dir.mkdir(parents=True, exist_ok=True)
        self.ledger = Ledger(self.case_dir)
        self.vault = self.case_dir / "vault"
        self.station = self.case_dir / "write_protected_station"

    def receive(
        self,
        disk_root: str | Path,
        *,
        case_id: str,
        source: str,
        generation: str,
        signed_by: str,
        collection_mode: str,
        investigator_id: str = "*",
        readable_blocks: Optional[Iterable[str]] = None,
        all_blocks: bool = False,
        page_ranges: Optional[Iterable[str]] = None,
        permissions: Optional[Iterable[str]] = None,
        notes: str = "",
        lock_vault: bool = True,
        lock_station: bool = True,
    ) -> Dict[str, Any]:
        if self.ledger.latest("INTAKE") is not None:
            raise IntakeError("this case already has an intake/handoff record")
        if collection_mode not in {STATION_MODE, "custodian_cow"}:
            raise IntakeError(f"unknown collection mode: {collection_mode}")

        disk_root = Path(disk_root)
        disk = DiskImage(disk_root)
        manifest = disk.artifact_manifest()
        manifest_sha256 = object_hash(manifest)

        pages = list(page_ranges) if page_ranges is not None else None
        # Validate ranges now; persist original tokens.
        if pages is not None:
            expand_page_ranges(pages)

        if permissions is None:
            permission_set = set(STATION_PERMISSIONS) if collection_mode == STATION_MODE else set()
            if collection_mode == "custodian_cow":
                from .authz import COW_PERMISSIONS

                permission_set = set(COW_PERMISSIONS)
        else:
            permission_set = set(permissions)

        block_list = sorted(set(readable_blocks or []))
        grant = {
            "investigator_id": investigator_id,
            "readable_blocks": block_list,
            "all_blocks": bool(all_blocks),
            # null = no page restriction; [] = explicitly no pages.
            "page_ranges": None if pages is None else list(pages),
            "permissions": sorted(permission_set),
            "raw_blocks_released": collection_mode == STATION_MODE,
        }

        # Fail before writing the append-only intake record if an raw-block
        # export would necessarily expose an out-of-scope page.
        if collection_mode == STATION_MODE:
            self._allowed_station_relative_paths({"payload": {"grant": grant}}, disk)

        if self.vault.exists():
            make_tree_writable(self.vault)
            shutil.rmtree(self.vault)
        shutil.copytree(disk_root, self.vault)
        if lock_vault:
            lock_tree_readonly(self.vault)

        handoff = {
            "case_id": case_id,
            "source": source,
            "generation": generation,
            "signed_by": signed_by,
            "signed_statement": (
                "委托人仅签署本交接书；受理端按下列来源、卷代、范围和派生权限执行。"
            ),
            "collection_mode": collection_mode,
            "received_at": utc_now_iso(),
            "notes": notes,
        }
        payload = {
            "handoff": handoff,
            "grant": grant,
            "manifest": manifest,
            "manifest_sha256": manifest_sha256,
            "vault_path": str(self.vault),
        }
        record = self.ledger.append("INTAKE", payload, actor="intake-desk")
        # Convenience copy of the single signed handoff record.
        (self.case_dir / "handoff.record.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        result: Dict[str, Any] = {"intake_record": record}
        if collection_mode == STATION_MODE:
            result["station_bundle"] = self.build_station_bundle(
                record, disk, lock=lock_station
            )
        return result

    def _allowed_station_relative_paths(
        self, record: Dict[str, Any], disk: DiskImage
    ) -> List[str]:
        grant = record["payload"]["grant"]
        block_ids = set(grant.get("readable_blocks", []))
        all_blocks = bool(grant.get("all_blocks"))
        can_verify = "verify_physical" in grant.get("permissions", [])
        paths: List[str] = []

        special = {
            "DIRECTORY": DIRECTORY_NAME,
            "JOURNAL": JOURNAL_NAME,
            "LAYOUT": LAYOUT_NAME,
        }
        for artifact_id, rel in special.items():
            if (artifact_id in block_ids or all_blocks) and (disk.root / rel).exists():
                paths.append(rel)
            elif can_verify and rel in {DIRECTORY_NAME, JOURNAL_NAME}:
                # Verification permission necessarily includes the physical
                # directory and original log named in the handoff.
                paths.append(rel)

        page_scope = grant.get("page_ranges")
        allowed_pages = None if page_scope is None else set(expand_page_ranges(page_scope))

        block_files = sorted((disk.root / "blocks").glob("*.txt")) if (disk.root / "blocks").exists() else []
        for path in block_files:
            block_id = path.stem
            if not (all_blocks or block_id in block_ids):
                continue
            mapped_pages = disk.block_pages(block_id)
            if allowed_pages is not None and mapped_pages:
                outside = [p for p in mapped_pages if p not in allowed_pages]
                if outside:
                    raise ScopeError(
                        f"cannot release raw block {block_id}: page scope excludes {outside}; "
                        "release only whole authorized blocks or issue a page-scoped observation"
                    )
            paths.append(f"blocks/{path.name}")
        return sorted(set(paths))

    def build_station_bundle(
        self, record: Dict[str, Any], disk: Optional[DiskImage] = None, *, lock: bool = True
    ) -> Dict[str, Any]:
        if disk is None:
            disk = DiskImage(self.vault)
        if self.station.exists():
            make_tree_writable(self.station)
            shutil.rmtree(self.station)
        self.station.mkdir(parents=True)

        rel_paths = self._allowed_station_relative_paths(record, disk)
        exported_manifest: List[Dict[str, Any]] = []
        for rel in rel_paths:
            src = self.vault / rel
            dst = self.station / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            data = dst.read_bytes()
            exported_manifest.append({"path": rel, "size": len(data), "sha256": sha256_bytes(data)})

        handoff_copy = self.station / "handoff.record.json"
        handoff_copy.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        bundle_manifest = {
            "handoff_record_id": record["id"],
            "case_id": record["payload"]["handoff"]["case_id"],
            "files": exported_manifest,
        }
        (self.station / "station-manifest.json").write_text(
            json.dumps(bundle_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        checksums = "".join(f"{item['sha256']}  {item['path']}\n" for item in exported_manifest)
        (self.station / "SHA256SUMS").write_text(checksums, encoding="utf-8")
        if lock:
            lock_tree_readonly(self.station)
        bundle = {
            "path": str(self.station),
            "handoff_record_id": record["id"],
            "file_count": len(exported_manifest),
        }
        self.ledger.append(
            "STATION_BUNDLE_CREATED",
            bundle,
            actor="intake-desk",
        )
        return bundle

    def amend_authorization(
        self,
        *,
        target_investigator: str,
        authorized_by: str,
        reason: str,
        replace_page_ranges: Optional[List[str]] = None,
        add_permissions: Optional[List[str]] = None,
        revoke_permissions: Optional[List[str]] = None,
        readable_blocks_add: Optional[List[str]] = None,
        all_blocks: Optional[bool] = None,
        raw_blocks_released: Optional[bool] = None,
    ) -> Dict[str, Any]:
        intake = self.ledger.latest("INTAKE")
        if intake is None:
            raise IntakeError("cannot amend scope before intake")
        if replace_page_ranges is not None:
            expand_page_ranges(replace_page_ranges)
        payload = {
            "case_id": intake["payload"]["handoff"]["case_id"],
            "target_investigator": target_investigator,
            "authorized_by": authorized_by,
            "reason": reason,
            "replace_page_ranges": replace_page_ranges,
            "add_permissions": add_permissions or [],
            "revoke_permissions": revoke_permissions or [],
            "readable_blocks_add": readable_blocks_add or [],
            "all_blocks": all_blocks,
            "raw_blocks_released": raw_blocks_released,
            "source_manifest_sha256": intake["payload"]["manifest_sha256"],
        }
        return self.ledger.append("AUTHORIZATION_AMENDMENT", payload, actor=authorized_by)
