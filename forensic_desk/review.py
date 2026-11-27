"""Review desk: scope-checked requests, evidence classes and conclusions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import authz
from .authz import EffectiveGrant, apply_amendments, grant_from_handoff
from .cow import CowLayer
from .disk import DIRECTORY_NAME, JOURNAL_NAME, DiskImage, parse_directory, serialize_directory
from .hashing import object_hash, sha256_bytes, sha256_text
from .ledger import Ledger
from .legacy import LegacyViewer, LegacyWriteBackBlocked
from .replay import ReplayResult

ResponseTuple = Tuple[str, Dict[str, Any], List[str], Optional[Dict[str, Any]]]


class ReviewDesk:
    def __init__(self, case_dir: str | Path, investigator_id: str):
        self.case_dir = Path(case_dir)
        self.investigator_id = investigator_id
        self.ledger = Ledger(self.case_dir)
        self.intake_record = self.ledger.latest("INTAKE")
        if self.intake_record is None:
            raise RuntimeError("case has no INTAKE/handoff record")
        self.vault = self.case_dir / "vault"
        self.disk = DiskImage(self.vault)
        self.cow = CowLayer(self.case_dir, investigator_id, self.vault)
        self.grant = self._refresh_grant()

    # ------------------------------------------------------------------
    # Grant / record helpers
    # ------------------------------------------------------------------

    def _refresh_grant(self) -> EffectiveGrant:
        enriched = {
            **self.intake_record["payload"],
            "record_id": self.intake_record["id"],
        }
        grant = grant_from_handoff(enriched, self.investigator_id)
        return apply_amendments(grant, self.ledger.records(), self.investigator_id)

    def get_record(self, record_id: str) -> Dict[str, Any]:
        return self.ledger.require(record_id)

    def verify_ledger(self) -> Dict[str, Any]:
        return self.ledger.verify_chain()

    def _manifest_entry(self, rel_path: str) -> Optional[Dict[str, Any]]:
        for entry in self.intake_record["payload"]["manifest"]:
            if entry["path"] == rel_path:
                return entry
        return None

    def _artifact_rel(self, block_id: str) -> str:
        specials = {
            "DIRECTORY": DIRECTORY_NAME,
            "JOURNAL": JOURNAL_NAME,
            "LAYOUT": "layout.json",
        }
        return specials.get(block_id, f"blocks/{block_id}.txt")

    def _replay_result(self) -> ReplayResult:
        return LegacyViewer.replay_disk(self.disk)

    def _replay_evidence_record(self) -> Tuple[Dict[str, Any], ReplayResult]:
        result = self._replay_result()
        for record in self.ledger.find("DERIVED_EVIDENCE"):
            if record["payload"].get("proof", {}).get("evidence_id") == result.evidence_id:
                return record, result
        record = self.ledger.append(
            "DERIVED_EVIDENCE",
            {
                "evidence_class": "DERIVED_REPLAY",
                "proof": result.to_proof(),
                "source_refs": [
                    self.intake_record["id"],
                    f"urn:forensic:raw:directory:{self.disk.directory_sha256}",
                    f"urn:forensic:raw:journal:{self.disk.journal_sha256}",
                ],
            },
            actor=self.investigator_id,
        )
        return record, result

    def _deny(
        self,
        reason: str,
        *,
        required_permission: Optional[str] = None,
        required_scope: Optional[Dict[str, Any]] = None,
    ) -> ResponseTuple:
        result = {
            "access": "denied",
            "reason": reason,
            "required_permission": required_permission,
            "required_authorization": required_scope or {},
            "handoff_record_id": self.intake_record["id"],
        }
        return "AUTHORIZATION_REQUIRED", result, [self.intake_record["id"]], result

    # ------------------------------------------------------------------
    # Request envelope
    # ------------------------------------------------------------------

    def submit(self, request: Dict[str, Any]) -> Dict[str, Any]:
        self.grant = self._refresh_grant()
        kind = request.get("kind")
        request_record = self.ledger.append(
            "REQUEST", {"request": request}, actor=self.investigator_id
        )
        handler = getattr(self, f"_handle_{kind}", None)
        if handler is None:
            status = "UNKNOWN_REQUEST"
            stored: Dict[str, Any] = {"supported_kinds": self.supported_kinds()}
            refs: List[str] = []
            disclosure = stored
        else:
            status, stored, refs, disclosure = handler(request, request_record)

        conclusion_record = self.ledger.append(
            "CONCLUSION",
            {
                "request_record_id": request_record["id"],
                "request": request,
                "status": status,
                "result": stored,
                "evidence_refs": refs,
                "grant_snapshot": self.grant.to_dict(),
            },
            actor=self.investigator_id,
        )
        return {
            "request_record": request_record,
            "conclusion": conclusion_record,
            "disclosure": disclosure if disclosure is not None else stored,
        }

    @staticmethod
    def supported_kinds() -> List[str]:
        return [
            "verify_physical",
            "raw_block",
            "page",
            "replay",
            "entry",
            "absence",
            "open_legacy",
            "rename",
            "write_line",
            "confirm_investigator_first_write",
        ]

    # Convenience wrappers ------------------------------------------------

    def request_raw_block(self, block_id: str) -> Dict[str, Any]:
        return self.submit({"kind": "raw_block", "block_id": block_id})

    def request_page(self, page_id: str) -> Dict[str, Any]:
        return self.submit({"kind": "page", "page_id": page_id})

    def request_entry(self, *, name: Optional[str] = None, entry_id: Optional[str] = None) -> Dict[str, Any]:
        req: Dict[str, Any] = {"kind": "entry"}
        if name is not None:
            req["name"] = name
        if entry_id is not None:
            req["entry_id"] = entry_id
        return self.submit(req)

    def request_absence(self, name: str) -> Dict[str, Any]:
        return self.submit({"kind": "absence", "name": name})

    def open_legacy(self, target: str = "cow") -> Dict[str, Any]:
        return self.submit({"kind": "open_legacy", "target": target})

    def rename_entry(self, entry_id: str, new_name: str) -> Dict[str, Any]:
        return self.submit({"kind": "rename", "entry_id": entry_id, "new_name": new_name})

    def write_line(
        self, entry_id: str, name: str, block_id: str = "WORK", pages: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        return self.submit(
            {
                "kind": "write_line",
                "entry_id": entry_id,
                "name": name,
                "block_id": block_id,
                "pages": pages or [],
            }
        )

    def confirm_investigator_first_write(self, entry_id: str) -> Dict[str, Any]:
        return self.submit({"kind": "confirm_investigator_first_write", "entry_id": entry_id})

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _handle_verify_physical(self, request: Dict[str, Any], req_rec: Dict[str, Any]) -> ResponseTuple:
        if not self.grant.can(authz.VERIFY_PHYSICAL):
            return self._deny("缺少物理核验权限", required_permission=authz.VERIFY_PHYSICAL)

        checked = []
        ok = True
        for expected in self.intake_record["payload"]["manifest"]:
            path = self.vault / expected["path"]
            data = path.read_bytes()
            actual = {
                "path": expected["path"],
                "size": len(data),
                "sha256": sha256_bytes(data),
            }
            match = actual["sha256"] == expected["sha256"] and actual["size"] == expected["size"]
            ok = ok and match
            checked.append({**actual, "matches_intake": match})

        station_dir = self.case_dir / "write_protected_station"
        result = {
            "verified": ok,
            "artifact_count": len(checked),
            "artifacts": checked,
            "intake_record_id": self.intake_record["id"],
            "offsite_physical_reverification": self.grant.offsite_physical_verification,
            "physical_disclosure": (
                "raw_blocks_were_released_to_write_protected_station"
                if self.grant.offsite_physical_verification
                else "volume_remains_with_custodian;_only_custodian_side_hash_verification"
            ),
            "station_bundle_present": station_dir.exists(),
        }
        return "PHYSICAL_HASHES_VERIFIED" if ok else "PHYSICAL_HASH_MISMATCH", result, [self.intake_record["id"]], result

    def _handle_raw_block(self, request: Dict[str, Any], req_rec: Dict[str, Any]) -> ResponseTuple:
        block_id = request.get("block_id", "")
        if not self.grant.can(authz.OBSERVE_RAW_BLOCK):
            return self._deny("缺少原始块观察权限", required_permission=authz.OBSERVE_RAW_BLOCK)
        if not self.grant.block_allowed(block_id):
            return self._deny(
                f"原始块 {block_id} 不在接盘授权范围内",
                required_scope={"readable_blocks_add": [block_id]},
            )

        rel = self._artifact_rel(block_id)
        path = self.vault / rel
        if not path.is_file():
            return "RAW_BLOCK_NOT_FOUND", {"block_id": block_id, "path": rel}, [], None

        data = path.read_bytes()
        digest = sha256_bytes(data)
        expected = self._manifest_entry(rel)
        hash_match = bool(expected and expected["sha256"] == digest)
        base = {
            "evidence_class": "PHYSICAL_RAW_OBSERVATION",
            "block_id": block_id,
            "path": rel,
            "size": len(data),
            "sha256": digest,
            "matches_intake_manifest": hash_match,
            "evidence_urn": f"urn:forensic:raw:{self.grant.case_id}:{block_id}:{digest}",
        }

        if not self.grant.can(authz.READ_RAW_CONTENT):
            stored = {**base, "content_released": False}
            denial = {
                **stored,
                "access": "authorization_required",
                "reason": "本通道只允许逻辑结果/见证，未准许带走或显示原始块内容",
                "required_permission": authz.READ_RAW_CONTENT,
            }
            return "AUTHORIZATION_REQUIRED", denial, [self.intake_record["id"]], denial

        # Page-scoped disclosure: never return a whole physical block if it
        # contains a page outside the current grant.
        if block_id not in {"DIRECTORY", "JOURNAL", "LAYOUT"}:
            mapped_pages = self.disk.block_pages(block_id)
            allowed = self.grant.page_set()
            if allowed is not None and mapped_pages:
                outside = [p for p in mapped_pages if p not in allowed]
                if outside:
                    stored = {
                        **base,
                        "content_released": False,
                        "redacted": True,
                        "mapped_page_count": len(mapped_pages),
                        "authorized_pages": [p for p in mapped_pages if p in allowed],
                    }
                    disclosure = {
                        **stored,
                        "text": None,
                        "redaction_reason": "block contains pages outside current page scope",
                        "request_page_instead": [p for p in mapped_pages if p in allowed],
                    }
                    return "RAW_BLOCK_PAGE_SCOPE_REDACTED", stored, [self.intake_record["id"]], disclosure

        text = data.decode("utf-8")
        evidence = self.ledger.append(
            "RAW_OBSERVATION",
            {**base, "request_record_id": req_rec["id"], "text_persisted_to_ledger": False},
            actor=self.investigator_id,
        )
        stored = {**base, "content_released": True, "raw_observation_record": evidence["id"]}
        disclosure = {**stored, "text": text}
        return "RAW_BLOCK_OBSERVED", stored, [self.intake_record["id"], evidence["id"]], disclosure

    def _handle_page(self, request: Dict[str, Any], req_rec: Dict[str, Any]) -> ResponseTuple:
        page_id = request.get("page_id", "")
        if not self.grant.can(authz.READ_RAW_CONTENT):
            return self._deny("缺少原始页内容读取权限", required_permission=authz.READ_RAW_CONTENT)
        if not self.grant.page_allowed(page_id):
            return self._deny(
                f"页 {page_id} 不在当前准许调阅范围内",
                required_scope={"replace_page_ranges": [page_id]},
            )
        try:
            page = self.disk.read_page(page_id)
        except KeyError:
            return "PAGE_NOT_FOUND", {"page_id": page_id}, [], None

        if not self.grant.block_allowed(page["block_id"]):
            return self._deny(
                f"页所在块 {page['block_id']} 未获授权",
                required_scope={"readable_blocks_add": [page["block_id"]]},
            )

        evidence_urn = f"urn:forensic:page:{self.grant.case_id}:{page_id}:{page['sha256'][:16]}"
        stored = {
            "evidence_class": "PHYSICAL_RAW_PAGE",
            "page_id": page_id,
            "block_id": page["block_id"],
            "sha256": page["sha256"],
            "block_sha256": page["block_sha256"],
            "size": len(page["text"].encode("utf-8")),
            "evidence_urn": evidence_urn,
            "text_persisted_to_ledger": False,
        }
        evidence = self.ledger.append(
            "RAW_OBSERVATION",
            {**stored, "request_record_id": req_rec["id"]},
            actor=self.investigator_id,
        )
        stored["raw_observation_record"] = evidence["id"]
        disclosure = {**stored, "text": page["text"]}
        return "RAW_PAGE_OBSERVED", stored, [self.intake_record["id"], evidence["id"]], disclosure

    def _handle_replay(self, request: Dict[str, Any], req_rec: Dict[str, Any]) -> ResponseTuple:
        if not self.grant.can(authz.REPLAY_JOURNAL):
            return self._deny("缺少重放日志的派生权限", required_permission=authz.REPLAY_JOURNAL)
        evidence_rec, result = self._replay_evidence_record()
        can_export = self.grant.can(authz.EXPORT_LOGICAL_RESULT)
        stored: Dict[str, Any] = {
            "evidence_class": "DERIVED_REPLAY",
            **result.to_proof(),
            "evidence_record_id": evidence_rec["id"],
        }
        disclosure = dict(stored)
        if can_export:
            stored["rows"] = [r.to_dict() for r in result.rows]
            disclosure["rows"] = stored["rows"]
            disclosure["output_text"] = result.output_text
        else:
            stored["rows_exported"] = False
        return "REPLAYED_ENTRIES", stored, [self.intake_record["id"], evidence_rec["id"]], disclosure

    def _handle_entry(self, request: Dict[str, Any], req_rec: Dict[str, Any]) -> ResponseTuple:
        if not self.grant.can(authz.EXPORT_LOGICAL_RESULT):
            return self._deny("缺少导出逻辑条目的派生权限", required_permission=authz.EXPORT_LOGICAL_RESULT)

        name = request.get("name")
        entry_id = request.get("entry_id")
        if not name and not entry_id:
            return "INVALID_REQUEST", {"reason": "name or entry_id required"}, [], None

        physical = self.disk.find_rows(name=name, entry_id=entry_id)
        can_replay = self.grant.can(authz.REPLAY_JOURNAL)
        replay_rec = None
        replay_result: Optional[ReplayResult] = None
        replayed = []
        if can_replay:
            replay_rec, replay_result = self._replay_evidence_record()
            replayed = replay_result.find(name=name, entry_id=entry_id)

        overlay_rows: List[Dict[str, Any]] = []
        overlay_active = self.cow.using_upper(DIRECTORY_NAME)
        if overlay_active:
            overlay_rows = [r.to_dict() for r in parse_directory(self.cow.read_text(DIRECTORY_NAME))]
        overlay_matches = [
            r
            for r in overlay_rows
            if (entry_id is None or r["entry_id"] == entry_id)
            and (name is None or r["name"] == name)
        ]

        refs = [self.intake_record["id"]]
        if replay_rec is not None:
            refs.append(replay_rec["id"])
        result: Dict[str, Any] = {
            "query": {"name": name, "entry_id": entry_id},
            "physical_matches": [r.to_dict() for r in physical],
            "replay_authorized": can_replay,
            "replayed_matches": [r.to_dict() for r in replayed],
            "work_overlay_matches": overlay_matches,
            "overlay_active": overlay_active,
            "physical_directory_sha256": self.disk.directory_sha256,
            "journal_sha256": self.disk.journal_sha256,
        }
        if not can_replay:
            result["replay_authorization_required"] = authz.REPLAY_JOURNAL

        target_name = name or (physical[0].name if physical else None) or (replayed[0].name if replayed else None)
        if physical:
            status = "PHYSICAL_PRESENT_AT_INTAKE"
            row = physical[0]
            result["classification"] = "PHYSICAL_ORIGINAL"
            result["original_existence_proof"] = {
                "intake_record_id": self.intake_record["id"],
                "directory_sha256": self.disk.directory_sha256,
                "line_no": row.line_no,
                "raw_line": row.raw_line,
            }
        elif not can_replay:
            return self._deny(
                "该名称不在物理目录中；缺少重放权限，无法区分日志派生条目与工作层写入",
                required_permission=authz.REPLAY_JOURNAL,
            )
        elif replayed:
            if replay_result is None:  # defensive; can_replay guarantees a result
                raise RuntimeError("replay result missing")
            status = "REPLAYED_PRESENT"
            row = replayed[0]
            witnesses = [
                w
                for w in replay_result.applied
                if w.get("entry_id") == row.entry_id
                or (w.get("after") or {}).get("entry_id") == row.entry_id
            ]
            result["classification"] = "DERIVED_FROM_PHYSICAL_JOURNAL_REPLAY"
            result["replay_witnesses"] = witnesses
            result["warning"] = "该条目由日志重放产生，不证明同字节行曾存在于接盘目录。"
        elif overlay_matches:
            status = "INVESTIGATOR_WORK_PRODUCT"
            row_dict = overlay_matches[0]
            work_records = [
                r
                for r in self.ledger.find("WORK_WRITE")
                if r["payload"].get("entry_id") == row_dict["entry_id"]
                and r.get("actor") == self.investigator_id
            ]
            provenance = [
                r
                for r in self.ledger.find("PROVENANCE")
                if r["payload"].get("entry_id") == row_dict["entry_id"]
            ]
            result["classification"] = "PRIVATE_COW_WORK_PRODUCT"
            result["work_write_records"] = [r["id"] for r in work_records]
            result["provenance_records"] = [r["id"] for r in provenance]
            result["warning"] = "调查时可以在私有工作层看见该行，但不得沿用接盘原始存在证明。"
            if provenance:
                status = "INVESTIGATOR_FIRST_WRITE_PROVENANCED"
                refs.extend(r["id"] for r in provenance)
        else:
            if not self.grant.can(authz.REPLAY_JOURNAL):
                return self._deny(
                    "无法在未授权重放的情况下出具完整缺证结论",
                    required_permission=authz.REPLAY_JOURNAL,
                )
            status = "ABSENT_IN_AUTHORIZED_SCOPE"
            result["classification"] = "NEGATIVE_CONCLUSION_WITHIN_GRANT"
            result["negative_proof"] = {
                "searched_physical_directory_sha256": self.disk.directory_sha256,
                "searched_journal_sha256": self.disk.journal_sha256,
                "replay_evidence_id": replay_result.evidence_id,
                "overlay_active": overlay_active,
                "page_scope": self.grant.page_ranges,
                "block_scope": sorted(self.grant.readable_blocks),
                "limit": "仅在当前授权范围及可重放日志内缺证；范围外可能存在。",
            }

        if target_name:
            result["requested_name"] = target_name
            block_id = None
            if physical:
                block_id = physical[0].block_id
            elif replayed:
                block_id = replayed[0].block_id
            if block_id:
                result["raw_content_authorization"] = {
                    "block_id": block_id,
                    "block_allowed": self.grant.block_allowed(block_id),
                    "read_raw_content": self.grant.can(authz.READ_RAW_CONTENT),
                }
        return status, result, refs, result

    def _handle_absence(self, request: Dict[str, Any], req_rec: Dict[str, Any]) -> ResponseTuple:
        # Entry handler already emits a scoped negative proof when absent.
        return self._handle_entry({**request, "kind": "entry"}, req_rec)

    def _handle_open_legacy(self, request: Dict[str, Any], req_rec: Dict[str, Any]) -> ResponseTuple:
        target = request.get("target", "cow")
        if target == "write_protected_station":
            station = self.case_dir / "write_protected_station"
            try:
                LegacyViewer.assert_direct_open_allowed(station)
            except FileNotFoundError:
                result = {
                    "opened": False,
                    "station_path": str(station),
                    "reason": "本案例没有可直接核验的写保护阅盘台副本",
                }
                return "STATION_BUNDLE_NOT_FOUND", result, [self.intake_record["id"]], result
            except LegacyWriteBackBlocked as exc:
                result = {
                    "opened": False,
                    "status_detail": str(exc),
                    "station_path": str(station),
                    "third_party_verification": "use verify_station_bundle() / CLI verify-station",
                }
                return "LEGACY_DIRECT_OPEN_BLOCKED_WRITE_PROTECTED", result, [self.intake_record["id"]], result
            return "UNEXPECTEDLY_WRITABLE", {"station_path": str(station)}, [], None

        if target != "cow":
            return "INVALID_REQUEST", {"reason": "target must be cow or write_protected_station"}, [], None
        if self.grant.collection_mode != authz.COW_MODE:
            return self._deny(
                "当前交接方式为写保护阅盘台；受管写回须另补 COW 授权",
                required_scope={"collection_mode": authz.COW_MODE},
            )
        if not self.grant.can(authz.RUN_LEGACY_WRITEBACK_COW):
            return self._deny(
                "缺少在一次性 COW 层运行老阅卷器的派生授权",
                required_permission=authz.RUN_LEGACY_WRITEBACK_COW,
            )

        witness = LegacyViewer.run_in_cow(self.cow, self.disk)
        can_export_logical = self.grant.can(authz.EXPORT_LOGICAL_RESULT)
        work_payload: Dict[str, Any] = {
            "kind": "legacy_replay_writeback",
            "investigator_id": self.investigator_id,
            "viewer": witness["viewer"],
            "mode": witness["mode"],
            "replay": witness["replay"],
            "write_witness": witness["write_witness"],
            "conflict_count": len(witness.get("conflicts", [])),
        }
        if can_export_logical:
            work_payload["applied_witnesses"] = witness["applied_witnesses"]
            work_payload["logical_rows"] = witness["logical_rows"]
        else:
            work_payload["applied_witness_count"] = len(witness.get("applied_witnesses", []))
        record = self.ledger.append("WORK_WRITE", work_payload, actor=self.investigator_id)
        stored = {
            "opened": True,
            "cow_root": str(self.cow.root),
            "work_write_record": record["id"],
            "replay": witness["replay"],
            "write_witness": witness["write_witness"],
            "applied_witness_count": len(witness.get("applied_witnesses", [])),
        }
        if can_export_logical:
            stored["applied_witnesses"] = witness["applied_witnesses"]
            stored["logical_rows"] = witness["logical_rows"]
        return "LEGACY_REPLAY_WRITTEN_TO_PRIVATE_COW", stored, [self.intake_record["id"], record["id"]], stored

    def _require_work_write_permission(self) -> Optional[ResponseTuple]:
        if self.grant.collection_mode != authz.COW_MODE:
            return self._deny(
                "调查员写入只能在委托方保管卷的一次性 COW 层进行；请补授权/改用 COW 受理卷",
                required_scope={"collection_mode": authz.COW_MODE},
            )
        if not self.grant.can(authz.WRITE_WORK_COPY):
            return self._deny(
                "调查员新增/再次改名属于新的写入派生操作，须补 write_work_copy 授权",
                required_permission=authz.WRITE_WORK_COPY,
            )
        return None

    def _handle_rename(self, request: Dict[str, Any], req_rec: Dict[str, Any]) -> ResponseTuple:
        denied = self._require_work_write_permission()
        if denied:
            return denied
        entry_id = request.get("entry_id")
        new_name = request.get("new_name")
        if not entry_id or not new_name:
            return "INVALID_REQUEST", {"reason": "entry_id and new_name required"}, [], None
        if not self.cow.using_upper(DIRECTORY_NAME):
            return (
                "LEGACY_LAYER_NOT_OPENED",
                {"reason": "先由老阅卷器在 COW 层重放并写回目录，再做调查员改名"},
                [],
                None,
            )

        rows = parse_directory(self.cow.read_text(DIRECTORY_NAME))
        row = next((r for r in rows if r.entry_id == entry_id), None)
        if row is None:
            return "ENTRY_NOT_FOUND_IN_WORK_LAYER", {"entry_id": entry_id}, [], None
        before = row.to_dict()
        old_name = row.name
        row.name = new_name
        witness = self.cow.write_text(DIRECTORY_NAME, serialize_directory(rows))
        record = self.ledger.append(
            "WORK_WRITE",
            {
                "kind": "investigator_rename",
                "investigator_id": self.investigator_id,
                "entry_id": entry_id,
                "old_name": old_name,
                "new_name": new_name,
                "before": before,
                "after": row.to_dict(),
                "write_witness": witness,
            },
            actor=self.investigator_id,
        )
        result = {
            "classification": "INVESTIGATOR_RENAME_IN_PRIVATE_COW",
            "entry_id": entry_id,
            "before": before,
            "after": row.to_dict(),
            "work_write_record": record["id"],
            "original_volume_unchanged_sha256": self.disk.directory_sha256,
        }
        return "INVESTIGATOR_RENAME_WRITTEN_TO_PRIVATE_COW", result, [self.intake_record["id"], record["id"]], result

    def _handle_write_line(self, request: Dict[str, Any], req_rec: Dict[str, Any]) -> ResponseTuple:
        denied = self._require_work_write_permission()
        if denied:
            return denied
        entry_id = request.get("entry_id")
        name = request.get("name")
        if not entry_id or not name:
            return "INVALID_REQUEST", {"reason": "entry_id and name required"}, [], None

        if self.cow.using_upper(DIRECTORY_NAME):
            text = self.cow.read_text(DIRECTORY_NAME)
        else:
            text = self.disk.directory_text
        rows = parse_directory(text)
        if any(r.entry_id == entry_id for r in rows):
            return (
                "ENTRY_ALREADY_EXISTS_REFUSING_FIRST_WRITE",
                {"entry_id": entry_id},
                [],
                None,
            )

        from .disk import DirectoryRow

        row = DirectoryRow(
            entry_id=entry_id,
            name=name,
            block_id=request.get("block_id", "WORK"),
            pages=list(request.get("pages", [])),
        )
        rows.append(row)
        witness = self.cow.write_text(DIRECTORY_NAME, serialize_directory(rows))
        line_text = row.canonical_line()
        record = self.ledger.append(
            "WORK_WRITE",
            {
                "kind": "investigator_first_write",
                "investigator_id": self.investigator_id,
                "entry_id": entry_id,
                "name": name,
                "line": line_text,
                "line_sha256": sha256_text(line_text),
                "after": row.to_dict(),
                "write_witness": witness,
            },
            actor=self.investigator_id,
        )
        result = {
            "classification": "INVESTIGATOR_WRITE_IN_PRIVATE_COW",
            "entry_id": entry_id,
            "line": line_text,
            "line_sha256": sha256_text(line_text),
            "work_write_record": record["id"],
            "note": "已记录写入见证；尚需 confirm_investigator_first_write 补真实来源结论。",
        }
        return "INVESTIGATOR_LINE_WRITTEN", result, [self.intake_record["id"], record["id"]], result

    def _handle_confirm_investigator_first_write(
        self, request: Dict[str, Any], req_rec: Dict[str, Any]
    ) -> ResponseTuple:
        entry_id = request.get("entry_id")
        existing_provenance = [
            r
            for r in self.ledger.find("PROVENANCE")
            if r["payload"].get("entry_id") == entry_id
            and r["payload"].get("classification") == "INVESTIGATOR_FIRST_WRITE"
        ]
        if existing_provenance:
            rec = existing_provenance[0]
            return (
                "INVESTIGATOR_FIRST_WRITE_PROVENANCE_ALREADY_EXISTS",
                {"provenance_record_id": rec["id"], "immutable": True},
                [rec["id"]],
                rec["payload"],
            )

        write_records = [
            r
            for r in self.ledger.find("WORK_WRITE")
            if r["payload"].get("entry_id") == entry_id
            and r["payload"].get("kind") == "investigator_first_write"
        ]
        if not write_records:
            return "FIRST_WRITE_WITNESS_NOT_FOUND", {"entry_id": entry_id}, [], None
        write_record = write_records[-1]
        line = write_record["payload"]["line"]

        physical_exists = any(r.raw_line and line in r.raw_line for r in self.disk.rows) or any(
            r.entry_id == entry_id for r in self.disk.rows
        )
        replay_result = self._replay_result()
        replay_exists = any(r.entry_id == entry_id or r.canonical_line() == line for r in replay_result.rows)
        if physical_exists or replay_exists:
            return (
                "REFUSING_FALSE_FIRST_WRITE_CLAIM",
                {
                    "entry_id": entry_id,
                    "physical_exists": physical_exists,
                    "replay_exists": replay_exists,
                },
                [],
                None,
            )

        payload = {
            "evidence_class": "PROVENANCE_RECORD",
            "classification": "INVESTIGATOR_FIRST_WRITE",
            "entry_id": entry_id,
            "line": line,
            "line_sha256": write_record["payload"]["line_sha256"],
            "source_work_write_record": write_record["id"],
            "source_witness": write_record["payload"]["write_witness"],
            "intake_record_id": self.intake_record["id"],
            "original_existence_proof_available": False,
            "permitted_report_statement": "调查员在私有 COW 工作层首次写入；调查时可见。",
            "prohibited_use": "不得将该行声称为接盘时物理目录原始行，也不得沿用原始存在证明。",
        }
        record = self.ledger.append("PROVENANCE", payload, actor=self.investigator_id)
        return (
            "INVESTIGATOR_FIRST_WRITE_PROVENANCED",
            {**payload, "provenance_record_id": record["id"]},
            [self.intake_record["id"], write_record["id"], record["id"]],
            {**payload, "provenance_record_id": record["id"]},
        )

    # ------------------------------------------------------------------
    # Provenance review (direct, no request/conclusion noise)
    # ------------------------------------------------------------------

    def review_provenance(self, ref: str) -> Dict[str, Any]:
        target = self.ledger.get(ref)
        related_ids = {ref}
        if target is None:
            # Maybe an evidence URN: find record whose payload contains it.
            for record in self.ledger.records():
                if ref in json.dumps(record.get("payload", {}), ensure_ascii=False):
                    target = record
                    related_ids.add(record["id"])
                    break
        if target is None:
            raise KeyError(f"provenance reference not found: {ref}")

        blob = json.dumps(target, ensure_ascii=False)
        for record in self.ledger.records():
            if record["id"] in blob or self.intake_record["id"] == record["id"]:
                related_ids.add(record["id"])

        chain = self.ledger.verify_chain()
        return {
            "reference": ref,
            "target": target,
            "related_records": [self.ledger.require(rid) for rid in sorted(related_ids)],
            "intake": self.intake_record,
            "effective_grant": self.grant.to_dict(),
            "ledger_chain": chain,
            "custody_rule": "结论只追加、不覆盖；当前页范围在每次取原始内容时重新校验。",
        }
