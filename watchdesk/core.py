"""Evidence-aware monitoring handoff desk.

The ledger is append-only JSONL.  Query/projection state is always rebuilt by
replaying it; there is no mutable "green board" that can outrank evidence.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

EVENT_ALIASES = {
    "seal_start": "seal_start",
    "封存开始": "seal_start",
    "start": "seal_start",
    "loose": "loose",
    "封条松动": "loose",
    "松动": "loose",
    "reset": "reset",
    "复位": "reset",
}
EVENT_LABELS = {
    "seal_start": "封存开始",
    "loose": "封条松动",
    "reset": "复位",
}


class DeskError(RuntimeError):
    """A command cannot be justified from the recorded evidence."""


class Desk:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.ledger = self.root / "ledger.jsonl"

    def read_records(self) -> list[dict[str, Any]]:
        if not self.ledger.exists():
            return []
        records: list[dict[str, Any]] = []
        for line_no, line in enumerate(self.ledger.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise DeskError(f"ledger 第 {line_no} 行损坏: {exc}") from exc
        return records

    def append(self, typ: str, data: dict[str, Any] | None = None,
               beat: int | None = None, ts: str | None = None) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        st = build_state(self.read_records())
        if beat is None:
            beat = int(st["clock"]["beat"]) if st["clock"] else 0
        if ts is None and st["clock"]:
            ts = virtual_time(st["clock"], beat)
        rec = {
            "ledger_seq": len(self.read_records()) + 1,
            "ts": ts,
            "beat": beat,
            "type": typ,
            "data": data or {},
        }
        with self.ledger.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
        return rec

    def state(self) -> dict[str, Any]:
        return build_state(self.read_records())


def virtual_time(clock: dict[str, Any], beat: int) -> str:
    start = datetime.fromisoformat(clock["start"])
    base = int(clock.get("start_beat", 0))
    return (start + timedelta(seconds=(beat - base) * int(clock.get("beat_seconds", 60)))).isoformat()


def _next_id(items: Iterable[dict[str, Any]], prefix: str, key: str = "id") -> str:
    mx = 0
    for item in items:
        val = str(item.get(key, ""))
        if val.startswith(prefix + "-"):
            try:
                mx = max(mx, int(val.rsplit("-", 1)[1]))
            except (ValueError, IndexError):
                pass
    return f"{prefix}-{mx + 1:03d}"


def build_state(records: list[dict[str, Any]]) -> dict[str, Any]:
    st: dict[str, Any] = {
        "clock": None,
        "rule_files": [],
        "rule_versions": {},
        "enablements": [],
        "heartbeats": [],
        "events": [],
        "acks": [],
        "contexts": {},
        "alerts": {},
        "notifications": [],
        "signoffs": {},
        "handoffs": [],
        "handoff_executions": [],
        "certificates": [],
        "gaps": [],
        "audits": [],
    }

    for rec in records:
        typ = rec.get("type")
        d = rec.get("data") or {}
        beat = rec.get("beat")

        if typ == "clock.init":
            st["clock"] = {
                "start": d["start"],
                "start_beat": int(d.get("start_beat", 0)),
                "beat_seconds": int(d.get("beat_seconds", 60)),
                "beat": int(d.get("start_beat", 0)),
            }
        elif typ == "clock.tick" and st["clock"]:
            st["clock"]["beat"] = int(d.get("beat", st["clock"]["beat"] + int(d.get("beats", 1))))

        elif typ == "rule.file_registered":
            st["rule_files"].append(d)
            version = d.get("version")
            for rule in d.get("rules", []):
                rv = rule.get("version", version)
                rule = {**rule, "version": rv, "file_id": d.get("file_id"), "sha256": d.get("sha256")}
                st["rule_versions"][f"{rule['id']}@{rv}"] = rule

        elif typ == "rule.enabled":
            st["enablements"].append(d)

        elif typ == "rule.stopped":
            for en in reversed(st["enablements"]):
                if en.get("id") == d.get("enablement_id"):
                    en["through_beat"] = d.get("through_beat")
                    en["stop_receipt_id"] = d.get("receipt_id")
                    en["stopped_by"] = d.get("by")
                    break

        elif typ == "heartbeat.received":
            st["heartbeats"].append({**d, "beat": beat, "ts": rec.get("ts")})

        elif typ == "event.ingested":
            ev = dict(d["event"])
            ev.setdefault("beat", beat)
            st["events"].append(ev)

        elif typ == "processing.ack":
            st["acks"].append({**d, "beat": d.get("beat", beat), "ts": rec.get("ts")})

        elif typ == "context.saved":
            ctx = d["context"]
            st["contexts"][ctx["id"]] = ctx

        elif typ == "alert.generated":
            alert = d["alert"]
            st["alerts"][alert["id"]] = alert

        elif typ == "notification.dispatched":
            st["notifications"].append({**d, "beat": beat})
            alert = st["alerts"].get(d["alert_id"])
            if alert:
                alert.setdefault("notification_ids", []).append(d.get("notification_id"))

        elif typ == "delivery.signed":
            signoff = d["signoff"]
            signoff["revoked"] = False
            st["signoffs"][signoff["id"]] = signoff

        elif typ == "delivery_signoff.revoked":
            so = st["signoffs"].get(d["signoff_id"])
            if so:
                so["revoked"] = True
                so["revoke_reason"] = d.get("reason")
                so["revoked_beat"] = beat
                so["revoke_receipt_id"] = d.get("receipt_id")

        elif typ == "handoff.plan":
            st["handoffs"].append(d["plan"])

        elif typ == "handoff.executed":
            st["handoff_executions"].append(d["execution"])
            for plan in st["handoffs"]:
                if plan.get("id") == d["execution"].get("plan_id"):
                    plan["status"] = "executed"
                    plan["execution_id"] = d["execution"].get("id")

        elif typ == "coverage.certificate":
            st["certificates"].append(d["certificate"])

        elif typ == "gap.receipt":
            st["gaps"].append(d["gap"])

        elif typ == "audit.finding":
            st["audits"].append(d["finding"])

    st["events"].sort(key=lambda e: int(e.get("seq", 0)))
    return st


def canonical_event_type(value: str) -> str:
    if value not in EVENT_ALIASES:
        allowed = "、".join(EVENT_LABELS)
        raise DeskError(f"设备只允许三种状态事件，无法识别 {value!r}；允许：{allowed}")
    return EVENT_ALIASES[value]


def active_enablement(st: dict[str, Any], rule_id: str, beat: int,
                      version: str | None = None) -> dict[str, Any] | None:
    found = None
    for en in st["enablements"]:
        if en.get("rule_id") != rule_id:
            continue
        if version and en.get("version") != version:
            continue
        if int(en["from_beat"]) <= int(beat):
            through = en.get("through_beat")
            if through is None or int(beat) <= int(through):
                found = en
    return found


def rule_for_beat(st: dict[str, Any], rule_id: str, beat: int) -> dict[str, Any] | None:
    en = active_enablement(st, rule_id, beat)
    if not en:
        return None
    return st["rule_versions"].get(f"{rule_id}@{en['version']}")


def acks_for(st: dict[str, Any], rule_id: str, beat: int) -> list[dict[str, Any]]:
    return [a for a in st["acks"] if int(a.get("beat", -1)) == int(beat)
            and rule_id in (a.get("rule_ids") or [])]


def latest_uncarried_context(st: dict[str, Any], rule_id: str, device: str | None = None) -> dict[str, Any] | None:
    candidates = []
    for ctx in st["contexts"].values():
        if ctx.get("rule_id") != rule_id or ctx.get("custody") != "not_carried":
            continue
        if device and ctx.get("device") not in (None, "*", device):
            continue
        candidates.append(ctx)
    return sorted(candidates, key=lambda c: int(c.get("last_beat", c.get("started_beat", 0))))[-1] if candidates else None


def live_context(st: dict[str, Any], rule_id: str, by: str,
                 device: str | None = None) -> dict[str, Any] | None:
    candidates = []
    closed = {"closed", "fulfilled", "expired", "uncarried", "outside_window"}
    for ctx in st["contexts"].values():
        if ctx.get("rule_id") != rule_id or ctx.get("status") in closed:
            continue
        if ctx.get("custody") in {"not_carried"}:
            continue
        if device and ctx.get("device") not in (None, "*", device):
            continue
        owner = ctx.get("current_owner") or ctx.get("owner")
        if by and owner and owner != by:
            continue
        candidates.append(ctx)
    if not candidates:
        return None
    return sorted(candidates, key=lambda c: int(c.get("last_beat", c.get("started_beat", 0))))[-1]


def compute_coverage(st: dict[str, Any], rule_id: str, start_beat: int, end_beat: int) -> dict[str, Any]:
    if end_beat < start_beat:
        raise DeskError("结束拍不能早于开始拍")
    beats = []
    for beat in range(start_beat, end_beat + 1):
        en = active_enablement(st, rule_id, beat)
        rule = rule_for_beat(st, rule_id, beat)
        acks = acks_for(st, rule_id, beat)
        hbs = [h for h in st["heartbeats"] if int(h.get("beat", -1)) == beat]
        reasons = []
        if rule is None:
            reasons.append("rule_version_not_registered_or_enabled")
        elif en is None:
            reasons.append("rule_not_enabled")
        if not acks:
            reasons.append("missing_processing_receipt")
        beats.append({
            "beat": beat,
            "covered": bool(en and acks),
            "enablement_receipt_id": en.get("start_receipt_id") if en else None,
            "version": en.get("version") if en else None,
            "processing_receipt_ids": [a.get("receipt_id") for a in acks],
            "processed_through_seq": max([a.get("through_seq") or 0 for a in acks] or [None]),
            "heartbeat_receipt_ids": [h.get("receipt_id") for h in hbs],
            "heartbeat_count": len(hbs),
            "reasons": reasons,
        })

    intervals: list[list[int]] = []
    gaps: list[int] = []
    cur: list[int] = []
    for row in beats:
        if row["covered"]:
            cur.append(row["beat"])
        else:
            gaps.append(row["beat"])
            if cur:
                intervals.append([cur[0], cur[-1]])
                cur = []
    if cur:
        intervals.append([cur[0], cur[-1]])

    rule_kind = None
    rule = rule_for_beat(st, rule_id, start_beat)
    if rule is None:
        for rv in st["rule_versions"].values():
            if rv.get("id") == rule_id:
                rule = rv
                break
    if rule:
        rule_kind = rule.get("kind")

    intact_context = None
    if rule_kind == "continuous_span":
        wanted = set(range(start_beat, end_beat + 1))
        for ctx in st["contexts"].values():
            if ctx.get("rule_id") != rule_id or ctx.get("custody") == "not_carried":
                continue
            if wanted.issubset(set(ctx.get("acked_beats") or [])):
                intact_context = ctx
                break

    warnings = []
    fully_covered = not gaps and intervals == [[start_beat, end_beat]]
    continuous_context_intact = None
    if rule_kind == "continuous_span":
        continuous_context_intact = intact_context is not None
        if fully_covered and not intact_context:
            warnings.append(
                "逐拍处理回执连续，但不存在覆盖全程且被携带/继承的同一观察上下文；"
                "只能按交班点或重建点分段签出，不能主张同一观察从开始连续守到结束。"
            )
        elif intact_context is not None:
            warnings.append(f"同一未结上下文链完整：{intact_context['id']}（状态 {intact_context.get('status')}）")

    return {
        "rule_id": rule_id,
        "rule_kind": rule_kind,
        "from_beat": start_beat,
        "to_beat": end_beat,
        "beats": beats,
        "intervals": intervals,
        "gap_beats": gaps,
        "fully_covered": fully_covered,
        "continuous_context_intact": continuous_context_intact,
        "intact_context_id": (intact_context or {}).get("id"),
        "claim_warnings": warnings,
    }


def processed_progress(st: dict[str, Any]) -> list[dict[str, Any]]:
    by_rule: dict[str, list[dict[str, Any]]] = {}
    for ack in st["acks"]:
        for rid in ack.get("rule_ids") or []:
            by_rule.setdefault(rid, []).append(ack)
    out = []
    for rid, acks in sorted(by_rule.items()):
        beats = sorted({int(a["beat"]) for a in acks})
        through = max(int(a.get("through_seq") or a.get("seq") or 0) for a in acks)
        receipt = sorted(acks, key=lambda a: int(a["beat"]))[-1].get("receipt_id")
        out.append({
            "rule_id": rid,
            "acked_beats": beats,
            "last_receipt_id": receipt,
            "last_claimed_through_seq": through,
            "contiguous_beat_hwm": _contiguous_hwm(beats),
        })
    return out


def _contiguous_hwm(beats: list[int]) -> int | None:
    if not beats:
        return None
    hwm = beats[0]
    for b in beats[1:]:
        if b == hwm + 1:
            hwm = b
        else:
            break
    return hwm
