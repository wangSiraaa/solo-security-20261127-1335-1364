"""Handoff planning, legal sign-out, delivery receipts and retrospective audit."""
from __future__ import annotations

from typing import Any

from .core import Desk, DeskError, acks_for, compute_coverage, virtual_time


def _open_contexts(st: dict[str, Any], at_beat: int, from_shift: str | None,
                   from_desk: str | None) -> list[dict[str, Any]]:
    closed = {"closed", "fulfilled", "expired", "uncarried", "outside_window"}
    out = []
    for ctx in st["contexts"].values():
        if ctx.get("status") in closed:
            continue
        if ctx.get("custody") == "not_carried":
            continue
        if from_shift and ctx.get("shift") and ctx.get("shift") != from_shift:
            continue
        if from_desk and ctx.get("desk") and ctx.get("desk") != from_desk:
            continue
        if int(ctx.get("last_beat", ctx.get("started_beat", 0))) <= at_beat:
            out.append(ctx)
    return sorted(out, key=lambda c: c["id"])


def plan_handoff(d: Desk, *, at_beat: int, slots: int, by: str,
                 from_shift: str | None = None, from_desk: str | None = None,
                 to_shift: str | None = None, to_desk: str | None = None,
                 to_owner: str | None = None, emergency: bool = False,
                 policy: str = "risk-first", select: list[str] | None = None) -> dict[str, Any]:
    st = d.state()
    contexts = _open_contexts(st, at_beat, from_shift, from_desk)
    if not contexts:
        raise DeskError("没有可交接的未结观察上下文")

    ranked = []
    for ctx in contexts:
        rule = st["rule_versions"].get(f"{ctx['rule_id']}@{ctx['version']}", {})
        deadline = ctx.get("deadline_beat")
        if deadline is None and ctx["kind"] == "continuous_span":
            deadline = int(ctx["started_beat"]) + int(rule.get("beats", 3)) - 1
        progress = len(ctx.get("acked_beats") or [])
        required = int(rule.get("beats", 3)) if ctx["kind"] == "continuous_span" else 2
        priority = float(rule.get("handoff_priority", 80 if ctx["kind"] == "continuous_span" else 100))
        urgency = 50 if deadline is not None and int(deadline) - at_beat <= 1 else 10
        irreversibility = 40 if ctx["kind"] == "pair_within_beats" else 18
        continuity_value = 25 * min(1.0, progress / max(1, required)) if ctx["kind"] == "continuous_span" else 0
        score = priority + urgency + irreversibility + continuity_value
        if policy == "coverage-first" and ctx["kind"] == "continuous_span":
            score += 100
        if policy == "coverage-first" and ctx["kind"] == "pair_within_beats":
            score -= 20
        ranked.append({
            "context_id": ctx["id"],
            "rule_id": ctx["rule_id"],
            "kind": ctx["kind"],
            "progress_beats": ctx.get("acked_beats", []),
            "pending_first": ctx.get("pending_first"),
            "deadline_beat": deadline,
            "score": round(score, 2),
            "reasons": [
                f"规则交接优先级={priority:g}",
                f"距临界/结束拍={None if deadline is None else int(deadline) - at_beat}",
                "短规则未携带则无法补造现场提醒" if ctx["kind"] == "pair_within_beats" else "持续规则有已签前缀，但断上下文后不能主张同一连续全程",
            ],
        })
    ranked.sort(key=lambda x: x["score"], reverse=True)

    selected = list(select or [])
    if selected:
        valid = {x["context_id"] for x in ranked}
        bad = [x for x in selected if x not in valid]
        if bad:
            raise DeskError(f"选择了不存在或已关闭的上下文: {', '.join(bad)}")
    elif len(contexts) <= slots:
        selected = [x["context_id"] for x in ranked]
    elif emergency and slots == 1:
        selected = [ranked[0]["context_id"]]
    elif emergency:
        selected = [x["context_id"] for x in ranked[:slots]]

    if len(contexts) <= slots:
        decision = "carry_all"
        headline = f"{slots} 个携带位足够，全部 {len(contexts)} 个未结上下文可在有人值守时完整交接"
    elif not emergency:
        decision = "request_early_overlap"
        headline = ("携带位不足；程序要求在仍有人值守时提前安排重叠交接/第二名携带人，"
                    "紧急兜底选择为 " + ranked[0]["context_id"])
        if not selected:
            selected = [ranked[0]["context_id"]]
    else:
        decision = "emergency_single_carry" if slots == 1 else "emergency_limited_carry"
        headline = f"紧急离岗只有 {slots} 次携带，程序按 {policy} 选择: {', '.join(selected)}"

    plan = {
        "id": f"HO-{len(st['handoffs']) + 1:03d}",
        "created_beat": at_beat,
        "by": by,
        "from_shift": from_shift,
        "from_desk": from_desk,
        "to_shift": to_shift,
        "to_desk": to_desk,
        "to_owner": to_owner,
        "slots": slots,
        "emergency": emergency,
        "policy": policy,
        "decision": decision,
        "headline": headline,
        "ranked_options": ranked,
        "selected_context_ids": selected,
        "status": "planned",
    }
    d.append("handoff.plan", {"plan": plan}, beat=at_beat)
    st["handoffs"].append(plan)
    return plan


def execute_handoff(d: Desk, *, plan_id: str, receipt_id: str, by: str,
                    at_beat: int | None = None, carry: list[str] | None = None,
                    to_owner: str | None = None, to_desk: str | None = None,
                    to_shift: str | None = None, auto_sign_prefix: bool = True) -> dict[str, Any]:
    st = d.state()
    plan = next((p for p in st["handoffs"] if p["id"] == plan_id), None)
    if not plan:
        raise DeskError(f"找不到交接计划 {plan_id}")
    if plan.get("status") == "executed":
        raise DeskError("该交接计划已经执行")
    beat = int(at_beat if at_beat is not None else plan["created_beat"])
    carry_ids = list(carry or plan.get("selected_context_ids") or [])
    all_open = _open_contexts(st, beat, plan.get("from_shift"), plan.get("from_desk"))
    open_ids = {c["id"] for c in all_open}
    bad = [x for x in carry_ids if x not in open_ids]
    if bad:
        raise DeskError(f"不能携带已关闭/不属于该班次的上下文: {', '.join(bad)}")
    if len(carry_ids) > int(plan["slots"]):
        raise DeskError(f"只有 {plan['slots']} 个携带位，却试图携带 {len(carry_ids)} 个")

    to_owner = to_owner or plan.get("to_owner")
    to_desk = to_desk or plan.get("to_desk")
    to_shift = to_shift or plan.get("to_shift")
    if not to_owner:
        raise DeskError("必须指定接手人 --to-owner")

    carried, uncarried, certificates, gaps = [], [], [], []
    for ctx in all_open:
        if ctx["id"] in carry_ids:
            old_owner = ctx.get("current_owner")
            ctx["current_owner"] = to_owner
            ctx["owner"] = to_owner
            ctx["desk"] = to_desk or ctx.get("desk")
            ctx["shift"] = to_shift or ctx.get("shift")
            ctx["custody"] = "carried"
            ctx.setdefault("carries", []).append({
                "plan_id": plan_id, "receipt_id": receipt_id, "by": by,
                "from_owner": old_owner, "to_owner": to_owner, "beat": beat,
            })
            d.append("context.saved", {"context": ctx}, beat=beat)
            st["contexts"][ctx["id"]] = ctx
            carried.append(ctx["id"])
        else:
            ctx["custody"] = "not_carried"
            ctx["status"] = "uncarried"
            ctx["uncarried_beat"] = beat
            d.append("context.saved", {"context": ctx}, beat=beat)
            st["contexts"][ctx["id"]] = ctx
            uncarried.append(ctx["id"])
            gap = {
                "id": f"GAP-{len(st['gaps']) + 1:03d}",
                "kind": "custody_lineage_break",
                "rule_id": ctx["rule_id"],
                "beat": beat,
                "severity": "handoff",
                "title": "未结上下文未随人携带",
                "detail": f"{ctx['id']} 只能由接手人从现场之后的实际回报重建；"
                          "台账中的旧事件可供事后核对，但不能恢复为当时已在新席生效的现场提醒上下文。",
            }
            d.append("gap.receipt", {"gap": gap}, beat=beat)
            st["gaps"].append(gap)
            gaps.append(gap["id"])

            if auto_sign_prefix and ctx.get("acked_beats"):
                end = max(int(b) for b in ctx["acked_beats"] if int(b) <= beat)
                cert = signout_coverage(
                    d, rule_id=ctx["rule_id"], start_beat=int(ctx["started_beat"]), end_beat=end,
                    shift=plan.get("from_shift") or ctx.get("shift") or "outgoing-shift",
                    by=ctx.get("current_owner") or by,
                    receipt_id=f"CERT-PREFIX-{ctx['id']}", desk_name=plan.get("from_desk"),
                    allow_partial=True, append_gaps=False,
                    title="未携带上下文的已守前缀必须签出",
                )
                certificates.extend(cert["certificate_ids"])

    execution = {
        "id": f"EXE-{len(st['handoff_executions']) + 1:03d}",
        "plan_id": plan_id,
        "receipt_id": receipt_id,
        "by": by,
        "beat": beat,
        "carried_context_ids": carried,
        "uncarried_context_ids": uncarried,
        "auto_certificate_ids": certificates,
        "lineage_gap_ids": gaps,
    }
    d.append("handoff.executed", {"execution": execution}, beat=beat)
    st["handoff_executions"].append(execution)
    plan["status"] = "executed"
    return execution


def signout_coverage(d: Desk, *, rule_id: str, start_beat: int, end_beat: int,
                     shift: str, by: str, receipt_id: str,
                     desk_name: str | None = None, allow_partial: bool = True,
                     append_gaps: bool = True, title: str | None = None) -> dict[str, Any]:
    st = d.state()
    cov = compute_coverage(st, rule_id, start_beat, end_beat)
    if not allow_partial and not cov["fully_covered"]:
        raise DeskError("请求区间缺少启用或逐拍处理证据，不能按完整区间签出")

    joint_shift = shift in {"joint", "all", "auto"} or "-to-" in shift or "→" in shift
    eligible: list[dict[str, Any]] = []
    for row in cov["beats"]:
        if not row["covered"]:
            continue
        acks = acks_for(st, rule_id, row["beat"])
        ack_shift = (acks[0].get("shift") if acks else None) or "unassigned"
        row["_ack_shift"] = ack_shift
        if shift == "auto" or joint_shift or ack_shift == shift or ack_shift == "unassigned":
            eligible.append(row)

    raw_segments: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    for row in eligible:
        if not cur or int(row["beat"]) == int(cur[-1]["beat"]) + 1:
            cur.append(row)
        else:
            raw_segments.append(cur)
            cur = [row]
    if cur:
        raw_segments.append(cur)

    segments: list[tuple[int, int, list[dict[str, Any]], str]] = []
    for rows in raw_segments:
        split_by_shift = shift == "auto" or (
            cov.get("rule_kind") == "continuous_span"
            and cov.get("continuous_context_intact") is False
            and joint_shift
        )
        if not split_by_shift:
            segments.append((rows[0]["beat"], rows[-1]["beat"], rows, shift))
            continue
        group: list[dict[str, Any]] = []
        group_shift = rows[0]["_ack_shift"]
        for row in rows:
            if group and (row["_ack_shift"] != group_shift or int(row["beat"]) != int(group[-1]["beat"]) + 1):
                segments.append((group[0]["beat"], group[-1]["beat"], group, group_shift))
                group = []
            group_shift = row["_ack_shift"]
            group.append(row)
        if group:
            segments.append((group[0]["beat"], group[-1]["beat"], group, group_shift))

    if not segments and not allow_partial:
        raise DeskError("没有任何由启用回执和逐拍处理回执共同支持的合法覆盖段，不能签出")

    cert_ids: list[str] = []
    signed_intervals: list[list[int]] = []
    for lo, hi, rows, cert_shift in segments:
        if len(segments) == 1:
            cid = receipt_id
        else:
            slug = "".join(ch if ch.isalnum() else "-" for ch in str(cert_shift)).strip("-").lower()
            cid = f"{receipt_id}-{slug}-{lo}-{hi}"
        signed_intervals.append([lo, hi])
        chain_ok = cov.get("rule_kind") != "continuous_span" or cov.get("continuous_context_intact") is True
        is_chain_cert = bool(chain_ok and len(segments) == 1 and cov.get("rule_kind") == "continuous_span")
        certificate = {
            "id": cid,
            "kind": "continuous_context_chain" if is_chain_cert else "coverage_interval",
            "rule_id": rule_id,
            "shift": cert_shift,
            "requested_shift": shift,
            "desk": desk_name,
            "signed_by": by,
            "from_beat": lo,
            "to_beat": hi,
            "title": title or "合法覆盖区间签出",
            "context_id": cov.get("intact_context_id") if chain_ok else None,
            "evidence": {
                "enablement_receipt_ids": sorted({b["enablement_receipt_id"] for b in rows if b["enablement_receipt_id"]}),
                "processing_receipt_ids": [rid for b in rows for rid in b["processing_receipt_ids"]],
                "heartbeat_count": sum(b["heartbeat_count"] for b in rows),
            },
            "claim": (
                f"{cert_shift} 携带同一未结上下文，规则 {rule_id} 从第 {lo} 拍连续守到第 {hi} 拍"
                if is_chain_cert
                else f"{cert_shift} 对规则 {rule_id} 的可证明覆盖仅限第 {lo}–{hi} 拍"
            ),
        }
        d.append("coverage.certificate", {"certificate": certificate}, beat=hi)
        st["certificates"].append(certificate)
        cert_ids.append(cid)

    gap_ids: list[str] = []
    if append_gaps:
        for beat in cov["gap_beats"]:
            row = next(b for b in cov["beats"] if b["beat"] == beat)
            gap = {
                "id": f"GAP-{len(st['gaps']) + 1:03d}",
                "kind": "coverage_gap",
                "rule_id": rule_id,
                "beat": beat,
                "severity": "coverage",
                "title": "缺少中间拍处理回执，不能保证持续覆盖",
                "detail": f"第 {beat} 拍原因: {', '.join(row['reasons'])}。"
                          "心跳或规则登记不能替代实际处理回执。",
            }
            d.append("gap.receipt", {"gap": gap}, beat=beat)
            st["gaps"].append(gap)
            gap_ids.append(gap["id"])

    return {
        "rule_id": rule_id,
        "certificate_ids": cert_ids,
        "gap_ids": gap_ids,
        "intervals": signed_intervals,
        "gap_beats": cov["gap_beats"],
        "fully_covered": cov["fully_covered"],
        "continuous_context_intact": cov.get("continuous_context_intact"),
    }


def dispatch_alert(d: Desk, *, alert_id: str, channel: str, by: str,
                   notification_id: str | None = None, beat: int | None = None) -> dict[str, Any]:
    st = d.state()
    alert = st["alerts"].get(alert_id)
    if not alert:
        raise DeskError(f"找不到提醒命中 {alert_id}")
    nid = notification_id or f"MSG-{alert_id}"
    rec = d.append("notification.dispatched", {
        "notification_id": nid, "alert_id": alert_id, "channel": channel, "by": by,
    }, beat=beat if beat is not None else alert["created_beat"])
    st["notifications"].append({"notification_id": nid, "alert_id": alert_id, "channel": channel})
    alert.setdefault("notification_ids", []).append(nid)
    return rec


def sign_delivery(d: Desk, *, alert_id: str, group: str, received_by: str,
                  receipt_id: str, beat: int | None = None, channel: str = "duty-board") -> dict[str, Any]:
    st = d.state()
    alert = st["alerts"].get(alert_id)
    if not alert:
        raise DeskError(f"找不到提醒命中 {alert_id}")
    if alert.get("status") != "timely":
        raise DeskError(
            f"{alert_id} 状态为 {alert.get('status')}，不是临界窗口内的及时提醒；"
            "只能做事后核对/缺口回单，不能补签为该组当时已收到提醒"
        )
    sid = receipt_id or f"DLV-{alert_id}-{group}"
    if sid in st["signoffs"]:
        raise DeskError(f"送达签收号 {sid} 已存在")
    signoff = {
        "id": sid,
        "alert_id": alert_id,
        "group": group,
        "received_by": received_by,
        "channel": channel,
        "receipt_id": receipt_id,
        "beat": beat if beat is not None else st["clock"]["beat"],
    }
    d.append("delivery.signed", {"signoff": signoff}, beat=signoff["beat"])
    st["signoffs"][sid] = signoff
    if group not in alert["formally_notified_groups"]:
        alert["formally_notified_groups"].append(group)
    active = [so for so in st["signoffs"].values()
              if so["alert_id"] == alert_id and not so.get("revoked")]
    alert["formal_delivery_complete"] = bool(active)
    d.append("alert.generated", {"alert": alert}, beat=signoff["beat"])
    return signoff


def revoke_delivery_signoff(d: Desk, *, signoff_id: str, by: str, reason: str,
                            receipt_id: str, beat: int | None = None) -> dict[str, Any]:
    st = d.state()
    so = st["signoffs"].get(signoff_id)
    if not so:
        raise DeskError(f"找不到正式签收 {signoff_id}")
    used_beat = int(beat if beat is not None else st["clock"]["beat"])
    d.append("delivery_signoff.revoked", {
        "signoff_id": signoff_id, "by": by, "reason": reason, "receipt_id": receipt_id,
    }, beat=used_beat)
    so["revoked"] = True
    so["revoke_reason"] = reason
    alert = st["alerts"].get(so["alert_id"])
    if alert:
        alert["formally_notified_groups"] = sorted({
            x["group"] for x in st["signoffs"].values()
            if x["alert_id"] == alert["id"] and not x.get("revoked")
        })
        alert["formal_delivery_complete"] = bool(alert["formally_notified_groups"])
        d.append("alert.generated", {"alert": alert}, beat=used_beat)
    return {"revoked": signoff_id, "receipt_id": receipt_id}


def audit_pairs(d: Desk, rule_id: str = "pair2", save: bool = True) -> dict[str, Any]:
    st = d.state()
    rule = next(iter([r for r in st["rule_versions"].values() if r["id"] == rule_id]), None)
    if not rule:
        raise DeskError(f"规则 {rule_id} 尚未登记")
    first_type = rule.get("first_event", "loose")
    second_type = rule.get("second_event", "reset")
    window = int(rule.get("pair_window_beats", 2))
    adjacent = bool(rule.get("adjacent", True))
    events = st["events"]
    findings = []
    pending = None
    for ev in events:
        if ev["type"] == first_type:
            pending = ev
        elif ev["type"] == second_type and pending:
            span = int(ev["beat"]) - int(pending["beat"])
            timely = span < window and (not adjacent or span == 1)
            live_alert = next((a for a in st["alerts"].values()
                               if a.get("first_seq") == pending["seq"] and a.get("second_seq") == ev["seq"]
                               and a.get("status") == "timely"), None)
            signed_groups = []
            if live_alert:
                signed_groups = [so["group"] for so in st["signoffs"].values()
                                 if so["alert_id"] == live_alert["id"] and not so.get("revoked")]
            finding = {
                "id": f"AUD-{len(st['audits']) + len(findings) + 1:03d}",
                "rule_id": rule_id,
                "first_seq": pending["seq"],
                "second_seq": ev["seq"],
                "first_beat": pending["beat"],
                "second_beat": ev["beat"],
                "span_beats": span,
                "within_window": timely,
                "live_alert_id": (live_alert or {}).get("id"),
                "formally_signed_groups": signed_groups,
                "classification": (
                    "及时命中且有正式送达签收" if timely and signed_groups else
                    "及时命中但无有效正式签收" if timely and live_alert else
                    "事后核对：事件对成立且在窗口内，但无现场提醒/送达证据" if timely else
                    "事后核对：事件对存在，但在临界窗口外；只有缺口回单/记录可查"
                ),
                "notice_claim_allowed": bool(timely and signed_groups),
            }
            findings.append(finding)
            pending = None
    result = {"rule_id": rule_id, "findings": findings}
    if save:
        for f in findings:
            d.append("audit.finding", {"finding": f}, beat=f["second_beat"])
            st["audits"].append(f)
    return result
