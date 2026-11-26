"""Command operations that append evidence to the ledger."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .core import (
    Desk, DeskError, EVENT_LABELS, active_enablement, acks_for,
    canonical_event_type, compute_coverage, latest_uncarried_context,
    live_context,
)


def init_desk(root: str | Path, start: str, beat_seconds: int = 60, force: bool = False) -> dict[str, Any]:
    d = Desk(root)
    if d.ledger.exists() and d.read_records() and not force:
        raise DeskError("该值班台已初始化；如需重建请使用新目录或显式 force")
    rec = d.append("clock.init", {
        "start": start,
        "start_beat": 0,
        "beat_seconds": beat_seconds,
    }, beat=0, ts=start)
    return rec


def tick(d: Desk, beats: int = 1) -> dict[str, Any]:
    st = d.state()
    if not st["clock"]:
        raise DeskError("尚未初始化虚拟时钟")
    new_beat = int(st["clock"]["beat"]) + beats
    return d.append("clock.tick", {"beats": beats, "beat": new_beat}, beat=new_beat)


def register_rule_file(d: Desk, filename: str, declared_version: str | None = None) -> dict[str, Any]:
    path = Path(filename)
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if isinstance(payload, list):
        payload = {"version": declared_version, "rules": payload}
    version = declared_version or payload.get("version")
    rules = payload.get("rules") or []
    if not version:
        raise DeskError("规则文件缺少 version")
    if not rules:
        raise DeskError("规则文件没有 rules")
    for rule in rules:
        if not rule.get("id") or not rule.get("kind"):
            raise DeskError("每条规则必须包含 id 和 kind")
        if rule["kind"] not in {"continuous_span", "pair_within_beats"}:
            raise DeskError(f"未知规则类型: {rule['kind']}")
    data = {
        "file_id": payload.get("file_id") or path.stem,
        "source_path": str(path),
        "version": version,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "rules": rules,
    }
    return d.append("rule.file_registered", data, beat=d.state()["clock"]["beat"] if d.state()["clock"] else 0)


def enable_rule(d: Desk, rule_id: str, version: str, by: str, receipt_id: str,
                from_beat: int, desk_name: str | None = None, shift: str | None = None) -> dict[str, Any]:
    st = d.state()
    key = f"{rule_id}@{version}"
    if key not in st["rule_versions"]:
        raise DeskError(f"规则版本 {key} 尚未登记")
    if active_enablement(st, rule_id, from_beat):
        raise DeskError(f"{rule_id} 在第 {from_beat} 拍已有启用版本；换版前请先停止旧版")
    en = {
        "id": receipt_id,
        "rule_id": rule_id,
        "version": version,
        "from_beat": from_beat,
        "by": by,
        "desk": desk_name,
        "shift": shift,
        "start_receipt_id": receipt_id,
    }
    return d.append("rule.enabled", en, beat=from_beat)


def stop_rule(d: Desk, rule_id: str, through_beat: int, by: str, receipt_id: str,
              version: str | None = None) -> dict[str, Any]:
    st = d.state()
    en = active_enablement(st, rule_id, through_beat, version)
    if not en:
        raise DeskError(f"{rule_id} 在第 {through_beat} 拍没有启用回执")
    return d.append("rule.stopped", {
        "enablement_id": en["id"], "rule_id": rule_id, "version": en["version"],
        "through_beat": through_beat, "by": by, "receipt_id": receipt_id,
    }, beat=through_beat)


def heartbeat(d: Desk, by: str, receipt_id: str, beat: int,
              desk_name: str | None = None, shift: str | None = None,
              component: str = "observer-console") -> dict[str, Any]:
    st = d.state()
    if st["clock"] is None:
        raise DeskError("请先 init")
    return d.append("heartbeat.received", {
        "by": by, "desk": desk_name, "shift": shift,
        "component": component, "receipt_id": receipt_id,
    }, beat=beat)


def load_events(d: Desk, filename: str) -> list[dict[str, Any]]:
    text = Path(filename).read_text(encoding="utf-8") if filename != "-" else __import__("sys").stdin.read()
    if filename.endswith(".json"):
        raw_items = json.loads(text)
    else:
        raw_items = [json.loads(line) for line in text.splitlines() if line.strip()]
    st = d.state()
    known_seqs = {e["seq"] for e in st["events"]}
    max_seq = max(known_seqs or [0])
    out = []
    for item in raw_items:
        typ = canonical_event_type(str(item.get("type") or item.get("event")))
        if "seq" in item and item["seq"] is not None:
            seq = int(item["seq"])
            if seq in known_seqs:
                raise DeskError(f"事件序号 {seq} 已存在，不能重复装载")
        else:
            max_seq += 1
            seq = max_seq
        known_seqs.add(seq)
        max_seq = max(max_seq, seq)
        if item.get("beat") is None:
            raise DeskError(f"事件 seq={seq} 缺少 beat")
        ev = {
            "seq": seq,
            "beat": int(item["beat"]),
            "type": typ,
            "label": EVENT_LABELS[typ],
            "device": item.get("device", "seal-A"),
            "payload": item.get("payload") or {},
        }
        d.append("event.ingested", {"event": ev}, beat=ev["beat"])
        out.append(ev)
    return out


def _save_context(d: Desk, ctx: dict[str, Any], beat: int) -> None:
    d.append("context.saved", {"context": ctx}, beat=beat)


def _new_context(rule: dict[str, Any], beat: int, by: str, device: str | None,
                 context_id: str, shift: str | None, desk_name: str | None,
                 reestablished_from: str | None = None) -> dict[str, Any]:
    return {
        "id": context_id,
        "rule_id": rule["id"],
        "version": rule["version"],
        "kind": rule["kind"],
        "device": device,
        "started_beat": beat,
        "last_beat": beat,
        "acked_beats": [beat],
        "current_owner": by,
        "owner": by,
        "shift": shift,
        "desk": desk_name,
        "custody": "reestablished" if reestablished_from else "owned",
        "status": "active",
        "reestablished_from": reestablished_from,
        "lineage": [reestablished_from] if reestablished_from else [context_id],
        "pending_first": None,
        "result": None,
    }


def _add_gap(d: Desk, st: dict[str, Any], *, kind: str, rule_id: str, beat: int,
             title: str, detail: str, severity: str = "coverage") -> dict[str, Any]:
    gap = {
        "id": f"GAP-{len(st['gaps']) + 1:03d}",
        "kind": kind,
        "rule_id": rule_id,
        "beat": beat,
        "severity": severity,
        "title": title,
        "detail": detail,
    }
    rec = d.append("gap.receipt", {"gap": gap}, beat=beat)
    st["gaps"].append(gap)
    return gap


def _make_alert(d: Desk, st: dict[str, Any], *, rule: dict[str, Any], ctx: dict[str, Any],
                first: dict[str, Any], second: dict[str, Any], timely: bool,
                beat: int) -> dict[str, Any]:
    aid = f"ALR-{rule['id']}-{second['seq']}"
    alert = {
        "id": aid,
        "rule_id": rule["id"],
        "version": rule["version"],
        "kind": rule["kind"],
        "status": "timely" if timely else "outside_window",
        "created_beat": beat,
        "context_id": ctx["id"],
        "first_seq": first["seq"],
        "second_seq": second["seq"],
        "first_beat": first["beat"],
        "second_beat": second["beat"],
        "message": f"相邻 {EVENT_LABELS[first['type']]}→{EVENT_LABELS[second['type']]} 命中 {rule['id']}",
        "formally_notified_groups": [],
        "formal_delivery_complete": False,
    }
    d.append("alert.generated", {"alert": alert}, beat=beat)
    st["alerts"][aid] = alert
    return alert


def acknowledge(d: Desk, *, by: str, receipt_id: str, beat: int | None = None,
                seq: int | None = None, through_seq: int | None = None,
                rule_ids: list[str] | None = None, shift: str | None = None,
                desk_name: str | None = None, note: str | None = None) -> dict[str, Any]:
    st = d.state()
    if st["clock"] is None:
        raise DeskError("请先 init")
    event = None
    if seq is not None:
        event = next((e for e in st["events"] if int(e["seq"]) == int(seq)), None)
        if event is None:
            raise DeskError(f"未装载事件序号 seq={seq}")
        if beat is None:
            beat = int(event["beat"])
        elif int(beat) != int(event["beat"]):
            raise DeskError(f"回执拍 {beat} 与事件 seq={seq} 的拍 {event['beat']} 不一致")
        if through_seq is None:
            through_seq = int(seq)
    if beat is None:
        raise DeskError("必须提供 --beat，或提供可推断拍号的 --seq")
    beat = int(beat)
    if through_seq is None:
        raise DeskError("没有新事件的逐拍轮询回执必须给出 --through-seq（实际处理到的事件序号）")

    if rule_ids is None:
        rule_ids = sorted({e["rule_id"] for e in st["enablements"]
                           if int(e["from_beat"]) <= beat and
                           (e.get("through_beat") is None or beat <= int(e["through_beat"]))})
    rule_ids = list(dict.fromkeys(rule_ids))
    if not rule_ids:
        raise DeskError("本拍没有启用规则；请用 --rule 显式指定或先启用规则")
    if any(a.get("receipt_id") == receipt_id for a in st["acks"]):
        raise DeskError(f"处理回执号 {receipt_id} 已使用")

    targets = []
    for rid in rule_ids:
        en = active_enablement(st, rid, beat)
        if not en:
            raise DeskError(f"规则 {rid} 在第 {beat} 拍没有启用/启停回执")
        rule = st["rule_versions"].get(f"{rid}@{en['version']}")
        if not rule:
            raise DeskError(f"找不到已登记规则版本 {rid}@{en['version']}")
        if acks_for(st, rid, beat):
            raise DeskError(f"规则 {rid} 第 {beat} 拍已有处理回执；如系更正请使用新回执/冲正记录")
        targets.append((rid, rule))

    ack = {
        "receipt_id": receipt_id,
        "seq": seq,
        "through_seq": int(through_seq),
        "beat": beat,
        "rule_ids": rule_ids,
        "by": by,
        "shift": shift,
        "desk": desk_name,
        "note": note,
    }
    d.append("processing.ack", ack, beat=beat)
    st["acks"].append(ack)

    results = []
    device = event.get("device") if event else None
    for rid, rule in targets:
        results.append(_evaluate_rule_after_ack(d, st, rule, beat, by, event, device, shift, desk_name))
    return {"ack": ack, "results": results}


def _evaluate_rule_after_ack(d: Desk, st: dict[str, Any], rule: dict[str, Any], beat: int,
                             by: str, event: dict[str, Any] | None, device: str | None,
                             shift: str | None, desk_name: str | None) -> dict[str, Any]:
    rid = rule["id"]
    kind = rule["kind"]
    ctx = live_context(st, rid, by, device)

    if kind == "continuous_span":
        start_type = rule.get("start_event", "seal_start")
        required = int(rule.get("beats", 3))
        if ctx is None and event and event["type"] == start_type:
            ctx = _new_context(rule, beat, by, device, f"ctx-{rid}-{event['seq']}", shift, desk_name)
        elif ctx is None:
            prior = latest_uncarried_context(st, rid, device)
            suffix = sum(1 for c in st["contexts"].values() if c["rule_id"] == rid and c.get("custody") == "reestablished") + 1
            ctx = _new_context(rule, beat, by, device, f"ctx-{rid}-rebuild-{suffix}",
                               shift, desk_name, prior["id"] if prior else None)
        if beat not in ctx["acked_beats"]:
            ctx["acked_beats"].append(beat)
        ctx["acked_beats"].sort()
        ctx["last_beat"] = max(int(ctx.get("last_beat", beat)), beat)
        ctx["current_owner"] = by
        needed = list(range(int(ctx["started_beat"]), int(ctx["started_beat"]) + required))
        if all(b in ctx["acked_beats"] for b in needed):
            ctx["status"] = "fulfilled"
            ctx["result"] = f"已由同一上下文连续守满 {required} 拍"
        _save_context(d, ctx, beat)
        st["contexts"][ctx["id"]] = ctx
        return {"rule_id": rid, "context_id": ctx["id"], "status": ctx["status"]}

    if kind == "pair_within_beats":
        first_type = rule.get("first_event", "loose")
        second_type = rule.get("second_event", "reset")
        window = int(rule.get("pair_window_beats", 2))
        adjacent = bool(rule.get("adjacent", True))

        if event and event["type"] == first_type:
            if ctx is None:
                ctx = _new_context(rule, beat, by, device, f"ctx-{rid}-{event['seq']}", shift, desk_name)
            ctx["pending_first"] = {"seq": event["seq"], "beat": event["beat"], "type": event["type"]}
            ctx["deadline_beat"] = int(event["beat"]) + window - 1
            ctx["status"] = "pending_first"
            if beat not in ctx["acked_beats"]:
                ctx["acked_beats"].append(beat)
            ctx["last_beat"] = beat
            _save_context(d, ctx, beat)
            st["contexts"][ctx["id"]] = ctx
            return {"rule_id": rid, "context_id": ctx["id"], "status": "pending_first"}

        if event and event["type"] == second_type:
            first = (ctx or {}).get("pending_first") if ctx else None
            if not first:
                prior = latest_uncarried_context(st, rid, device)
                suffix = sum(1 for c in st["contexts"].values() if c["rule_id"] == rid and c.get("custody") == "reestablished") + 1
                rebuild = _new_context(rule, beat, by, device, f"ctx-{rid}-rebuild-{suffix}",
                                       shift, desk_name, prior["id"] if prior else None)
                rebuild["status"] = "reestablished_without_live_first"
                rebuild["observed_second_only"] = {"seq": event["seq"], "beat": event["beat"]}
                _save_context(d, rebuild, beat)
                st["contexts"][rebuild["id"]] = rebuild
                return {"rule_id": rid, "context_id": rebuild["id"],
                        "status": "no_live_pair_context",
                        "explanation": "未携带第一件的未结上下文，不能生成当时的现场提醒；只能事后核对"}

            span = int(event["beat"]) - int(first["beat"])
            timely = span < window and (not adjacent or span == 1)
            if beat not in ctx["acked_beats"]:
                ctx["acked_beats"].append(beat)
            ctx["last_beat"] = beat
            first_event = next(e for e in st["events"] if int(e["seq"]) == int(first["seq"]))
            alert = _make_alert(d, st, rule=rule, ctx=ctx, first=first_event,
                                second=event, timely=timely, beat=beat)
            if timely:
                ctx["status"] = "fulfilled"
                ctx["result"] = "critical_window_hit"
            else:
                ctx["status"] = "outside_window"
                ctx["result"] = "reset_outside_critical_beat"
                _add_gap(d, st, kind="timeliness_gap", rule_id=rid, beat=beat,
                         title="短规则超过临界拍",
                         detail=f"松动在第 {first['beat']} 拍，复位在第 {event['beat']} 拍，"
                                f"窗口为 {window} 拍/相邻={adjacent}；不发及时提醒，仅留缺口回单。",
                         severity="timeliness")
            ctx["pending_first"] = None
            ctx["last_beat"] = beat
            _save_context(d, ctx, beat)
            st["contexts"][ctx["id"]] = ctx
            return {"rule_id": rid, "context_id": ctx["id"], "status": alert["status"], "alert_id": alert["id"]}

        if ctx is not None:
            if beat not in ctx["acked_beats"]:
                ctx["acked_beats"].append(beat)
            ctx["last_beat"] = beat
            _save_context(d, ctx, beat)
            st["contexts"][ctx["id"]] = ctx
        return {"rule_id": rid, "context_id": (ctx or {}).get("id"), "status": "observed"}

    return {"rule_id": rid, "status": "ignored"}
