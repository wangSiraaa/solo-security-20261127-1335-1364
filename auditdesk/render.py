"""证书与事实的中文呈现。"""
from __future__ import annotations

TYPE_TITLE = {
    "LOCAL_RECEIPT": "单站本地收件证明",
    "CO_WITNESS": "双站共同见证书",
    "PROVISIONAL_CO": "共同见证待补临证书",
    "DELIVERY_ONLY": "仅送达未验页证明",
}


def render_cert(cert: dict) -> str:
    t = TYPE_TITLE.get(cert["type"], cert["type"])
    out = [
        "=" * 68,
        f"【{t}】 {cert['cert_id']}",
        f"  用途：{cert.get('purpose', '—')}",
        "  —— 本证承认的事实 ——",
    ]
    out += [f"    ✓ {x}" for x in cert["admitted"]]
    out.append("  —— 本证不主张的事项 ——")
    out += [f"    · {x}" for x in cert["not_claimed"]]
    if cert.get("needs_other_station"):
        out.append("  —— 仍须另一站作证的部分 ——")
        out += [f"    ⚠ {x}" for x in cert["needs_other_station"]]
    out.append("=" * 68)
    return "\n".join(out)


def render_facts(title: str, facts: list[tuple[str, bool, str]]) -> str:
    """facts: (事实, 是否可证, 依据/缺口)"""
    out = ["-" * 68, f"【{title}】"]
    for name, provable, basis in facts:
        mark = "可证" if provable else "不可证"
        out.append(f"  [{mark}] {name} —— {basis}")
    return "\n".join(out)
