"""验收员工具：重放核查、动作查询、台账验链、演练结论出单。"""
from __future__ import annotations

import os

from .codec import read_json
from .external import Cabinet, ReinspectStation
from .journal import INTENT, OBSERVATION, JobJournal


def query_service(data_root: str, domain: str, name: str) -> dict:
    state = read_json(os.path.join(data_root, domain, name, "state.json"))
    if state["kind"] == "cabinet":
        svc = Cabinet(data_root, domain, name)
    else:
        svc = ReinspectStation(data_root, domain, name)
    return {
        "domain": domain,
        "name": name,
        "kind": state["kind"],
        "seq": state["seq"],
        "dispatched": state.get("dispatched"),
        "chain_problems": svc.verify(),
        "actions": svc.actions(),
    }


def query_all_cabinets(data_root: str) -> dict:
    result = {}
    for domain in ("prod", "drill"):
        base = os.path.join(data_root, domain)
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            state_path = os.path.join(base, name, "state.json")
            if not os.path.exists(state_path):
                continue
            if read_json(state_path).get("kind") == "cabinet":
                result[f"{domain}/{name}"] = query_service(
                    data_root, domain, name
                )
    return result


def query_job(jobs_dir: str, job_id: str) -> dict:
    j = JobJournal(jobs_dir, job_id)
    state = j.get_state()
    causal = []
    for e in j.entries():
        if e["type"] == INTENT:
            causal.append(
                {
                    "type": "INTENT",
                    "step": e["step"],
                    "service": e["service"],
                    "idempotency_key": e["idempotency_key"],
                    "ts": e["ts"],
                }
            )
        elif e["type"] == OBSERVATION:
            outcome = e["outcome"]
            causal.append(
                {
                    "type": "OBSERVATION",
                    "step": e["step"],
                    "ok": outcome.get("ok"),
                    "receipt_id": outcome.get("receipt", {}).get("receipt_id")
                    or outcome.get("slip_id"),
                    "error": outcome.get("error_type"),
                    "ts": e["ts"],
                }
            )
        else:
            causal.append({"type": "LIFECYCLE", "event": e["event"], "ts": e["ts"]})
    return {
        "job_id": job_id,
        "state": state,
        "slip": j.slip(),
        "pending_intents": [
            {"step": p["step"], "idempotency_key": p["idempotency_key"]}
            for p in j.pending_intents()
        ],
        "causal_order": causal,
    }


def verify_job_chain(jobs_dir: str, job_id: str) -> dict:
    """核验作业因果日志：每个 OBSERVATION 都能找到前置同键 INTENT。"""
    j = JobJournal(jobs_dir, job_id)
    intents: dict[str, dict] = {}
    problems = []
    for e in j.entries():
        if e["type"] == INTENT:
            intents[e["idempotency_key"]] = e
        elif e["type"] == OBSERVATION:
            if e["idempotency_key"] not in intents:
                problems.append(
                    f"观察 {e['step']} 缺少配对意图（因果顺序被破坏）"
                )
    return {
        "job_id": job_id,
        "problems": problems,
        "pending": [
            {"step": p["step"], "idempotency_key": p["idempotency_key"]}
            for p in j.pending_intents()
        ],
        "sound": not problems,
    }


def write_conclusion(path: str, sections: list[tuple[str, dict | list | str]]) -> None:
    """生成给操作员的 Markdown 演练结论（含可验证范围说明）。"""
    lines = ["# 值班发件安全演练结论", ""]
    for title, body in sections:
        lines.append(f"## {title}")
        lines.append("")
        if isinstance(body, str):
            lines.append(body)
        else:
            lines.append("```json")
            import json
            lines.append(json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True))
            lines.append("```")
        lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
