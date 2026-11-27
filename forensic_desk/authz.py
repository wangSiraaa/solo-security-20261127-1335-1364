"""Authorization grants and scope enforcement."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set

from .hashing import expand_page_ranges


# Physical evidence and verification.
VERIFY_PHYSICAL = "verify_physical"
OBSERVE_RAW_BLOCK = "observe_raw_block"
READ_RAW_CONTENT = "read_raw_content"
EXPORT_RAW_BLOCKS = "export_raw_blocks"

# Derived examination.
REPLAY_JOURNAL = "replay_journal"
RUN_LEGACY_WRITEBACK_COW = "run_legacy_writeback_cow"
EXPORT_LOGICAL_RESULT = "export_logical_result"

# Mutating private work product.
WRITE_WORK_COPY = "write_work_copy"

ALL_PERMISSIONS = {
    VERIFY_PHYSICAL,
    OBSERVE_RAW_BLOCK,
    READ_RAW_CONTENT,
    EXPORT_RAW_BLOCKS,
    REPLAY_JOURNAL,
    RUN_LEGACY_WRITEBACK_COW,
    EXPORT_LOGICAL_RESULT,
    WRITE_WORK_COPY,
}

STATION_MODE = "write_protected_station"
COW_MODE = "custodian_cow"

STATION_PERMISSIONS = {
    VERIFY_PHYSICAL,
    OBSERVE_RAW_BLOCK,
    READ_RAW_CONTENT,
    EXPORT_RAW_BLOCKS,
    REPLAY_JOURNAL,
    EXPORT_LOGICAL_RESULT,
}

COW_PERMISSIONS = {
    VERIFY_PHYSICAL,
    OBSERVE_RAW_BLOCK,
    REPLAY_JOURNAL,
    RUN_LEGACY_WRITEBACK_COW,
    EXPORT_LOGICAL_RESULT,
}


@dataclass
class EffectiveGrant:
    case_id: str
    source: str
    generation: str
    handoff_record_id: str
    collection_mode: str
    investigator_id: str
    readable_blocks: Set[str] = field(default_factory=set)
    all_blocks: bool = False
    page_ranges: Optional[List[str]] = None
    permissions: Set[str] = field(default_factory=set)
    offsite_physical_verification: bool = False
    raw_blocks_released: bool = False
    amendment_ids: List[str] = field(default_factory=list)

    def can(self, permission: str) -> bool:
        return permission in self.permissions

    def block_allowed(self, block_id: str) -> bool:
        return self.all_blocks or block_id in self.readable_blocks

    def page_allowed(self, page_id: str) -> bool:
        if self.page_ranges is None:
            return True
        return page_id in set(expand_page_ranges(self.page_ranges))

    def page_set(self) -> Optional[Set[str]]:
        if self.page_ranges is None:
            return None
        return set(expand_page_ranges(self.page_ranges))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "source": self.source,
            "generation": self.generation,
            "handoff_record_id": self.handoff_record_id,
            "collection_mode": self.collection_mode,
            "investigator_id": self.investigator_id,
            "readable_blocks": sorted(self.readable_blocks),
            "all_blocks": self.all_blocks,
            "page_ranges": self.page_ranges,
            "page_scope_expanded": sorted(self.page_set() or []),
            "permissions": sorted(self.permissions),
            "offsite_physical_verification": self.offsite_physical_verification,
            "raw_blocks_released": self.raw_blocks_released,
            "amendment_ids": self.amendment_ids,
        }


def grant_from_handoff(payload: Dict[str, Any], investigator_id: str) -> EffectiveGrant:
    handoff = payload["handoff"]
    grant = payload["grant"]
    if grant.get("investigator_id", "*") not in {"*", investigator_id}:
        # A named grant for someone else is no grant at all.  The desk still
        # returns a restricted object so callers can emit an auth decision.
        permissions: Set[str] = set()
        blocks: Set[str] = set()
        all_blocks = False
    else:
        permissions = set(grant.get("permissions", []))
        blocks = set(grant.get("readable_blocks", []))
        all_blocks = bool(grant.get("all_blocks", False))

    mode = handoff["collection_mode"]
    return EffectiveGrant(
        case_id=handoff["case_id"],
        source=handoff["source"],
        generation=handoff["generation"],
        handoff_record_id=payload["record_id"],
        collection_mode=mode,
        investigator_id=investigator_id,
        readable_blocks=blocks,
        all_blocks=all_blocks,
        page_ranges=grant.get("page_ranges"),
        permissions=permissions,
        offsite_physical_verification=(mode == STATION_MODE),
        raw_blocks_released=bool(grant.get("raw_blocks_released", mode == STATION_MODE)),
    )


def apply_amendments(
    grant: EffectiveGrant, records: Iterable[Dict[str, Any]], investigator_id: str
) -> EffectiveGrant:
    for record in records:
        if record.get("type") != "AUTHORIZATION_AMENDMENT":
            continue
        payload = record.get("payload", {})
        target = payload.get("target_investigator", "*")
        if target not in {"*", investigator_id}:
            continue
        if payload.get("replace_page_ranges") is not None:
            grant.page_ranges = list(payload["replace_page_ranges"])
        if payload.get("readable_blocks_add"):
            grant.readable_blocks.update(payload["readable_blocks_add"])
        if payload.get("all_blocks") is True:
            grant.all_blocks = True
        for perm in payload.get("add_permissions", []):
            grant.permissions.add(perm)
        for perm in payload.get("revoke_permissions", []):
            grant.permissions.discard(perm)
        if "raw_blocks_released" in payload:
            grant.raw_blocks_released = bool(payload["raw_blocks_released"])
        grant.amendment_ids.append(record["id"])
    return grant
