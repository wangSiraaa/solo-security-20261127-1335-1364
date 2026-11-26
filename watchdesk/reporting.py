"""Human-readable Chinese reports."""
from __future__ import annotations

import json
from typing import Any

from .core import compute_coverage, processed_progress


def dumps(value: Any, as_json: bool = False) -> str:
    if as_json:
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    return render_report(value)


def render_report(data: Any) -> str:
    if isinstance(data, dict) and data.get("_report") == "desk":
        return render_desk(data["state"])
    if isinstance(data, dict) and "beats" in data and "intervals" in data:
        return render_coverage(data)
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)


def render_coverage(cov: dict[str, Any]) -> str:
    lines = [f"规则 {cov['rule_id']} 第 {cov['from_beat']}–{cov['to_beat']} 拍覆盖证据："]
    for b in cov["beats"]:
        mark = "✅覆盖" if b["covered"] else "❌缺口"
        lines.append(
            f"  第{b['beat']}拍 {mark} 版本={b['version'] or '无'} "
            f"启用回执={b['enablement_receipt_id'] or '无'} "
            f"处理回执={','.join(b['processing_receipt_ids']) or '无'} "
            f"心跳={b['heartbeat_count']}条(不替代处理回执)"
        )
        if b["reasons"]:
            lines.append(f"    原因: {', '.join(b['reasons'])}")
    if cov["intervals"]:
        lines.append("可签出的连续区间: " + ", ".join(f"第{a}–{z}拍" for a, z in cov["intervals"]))
    if cov["gap_beats"]:
        lines.append("缺口拍: " + ", ".join(map(str, cov["gap_beats"])))
    for warning in cov.get("claim_warnings") or []:
        lines.append("上下文链: " + warning)
    if cov["fully_covered"] and cov.get("continuous_context_intact") is False:
        conclusion = "逐拍回执虽连续，但不能保证同一持续观察上下文全程未断；只能分段承诺责任区间"
    else:
        conclusion = "请求区间完整覆盖" if cov["fully_covered"] else "不能保证请求区间的持续覆盖；只能签出上述合法区间"
    lines.append("结论: " + conclusion)
    return "\n".join(lines)


def render_desk(st: dict[str, Any]) -> str:
    lines = ["值班板证据总览（投影由 ledger.jsonl 重放生成）"]
    clock = st.get("clock")
    if clock:
        lines.append(f"虚拟时钟: 第{clock['beat']}拍，起点 {clock['start']}，每拍 {clock['beat_seconds']}s")
    lines.append(f"规则登记文件: {len(st['rule_files'])}；规则版本: {len(st['rule_versions'])}；启用回执: {len(st['enablements'])}")
    lines.append(f"心跳证据: {len(st['heartbeats'])}；事件: {len(st['events'])}；处理回执: {len(st['acks'])}")
    lines.append("处理进度（按规则重放）:")
    for p in processed_progress(st):
        lines.append(f"  {p['rule_id']}: 已回执拍={p['acked_beats']}，最后声称处理到 seq={p['last_claimed_through_seq']}，连续拍高水位={p['contiguous_beat_hwm']}，末回执={p['last_receipt_id']}")

    lines.append("未结/已结上下文与携带责任:")
    for ctx in sorted(st["contexts"].values(), key=lambda c: c["id"]):
        pending = ctx.get("pending_first")
        pending_text = f"，待配第一件={pending}" if pending else ""
        reborn = f"，重建自 {ctx['reestablished_from']}" if ctx.get("reestablished_from") else ""
        lines.append(
            f"  {ctx['id']} [{ctx['rule_id']}/{ctx['status']}/{ctx.get('custody')}] "
            f"责任={ctx.get('shift')}/{ctx.get('current_owner')} "
            f"已回执拍={ctx.get('acked_beats')}{pending_text}{reborn}"
        )

    if st["handoffs"]:
        lines.append("交班计划:")
        for p in st["handoffs"]:
            lines.append(f"  {p['id']} {p['decision']} -> {p['selected_context_ids']}；{p['headline']}")

    lines.append("覆盖签出:")
    if not st["certificates"]:
        lines.append("  无")
    for c in st["certificates"]:
        lines.append(f"  {c['id']}: {c['shift']} / {c['rule_id']} 第{c['from_beat']}–{c['to_beat']}拍，签收人 {c['signed_by']}")

    lines.append("缺口/断链回单:")
    if not st["gaps"]:
        lines.append("  无")
    for g in st["gaps"]:
        lines.append(f"  {g['id']} [{g['kind']}] {g['rule_id']} 第{g['beat']}拍: {g['title']}")

    lines.append("规则命中与送达:")
    if not st["alerts"]:
        lines.append("  无命中")
    for a in st["alerts"].values():
        active_groups = sorted({so["group"] for so in st["signoffs"].values()
                                if so.get("alert_id") == a["id"] and not so.get("revoked")})
        revoked = [so["id"] for so in st["signoffs"].values()
                   if so.get("alert_id") == a["id"] and so.get("revoked")]
        claim = "可写已正式通知 " + ",".join(active_groups) if active_groups else "不得写成已正式通知任何接收组"
        lines.append(f"  {a['id']} [{a['status']}] {a['message']}；{claim}" + (f"；已撤签收={revoked}" if revoked else ""))

    if st["audits"]:
        lines.append("事后核对:")
        for f in st["audits"]:
            groups = ",".join(f.get("formally_signed_groups") or []) or "无有效签收"
            lines.append(f"  {f['id']}: seq{f['first_seq']}→{f['second_seq']}，跨{f['span_beats']}拍；{f['classification']}；签收组={groups}")
    return "\n".join(lines)
