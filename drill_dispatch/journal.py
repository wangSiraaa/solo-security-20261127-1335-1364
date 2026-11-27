"""作业因果日志。

每个外部交接严格按顺序产生两类条目：
  INTENT      —— 发起调用之前 fsync 落盘（带幂等键、意图摘要）；
  OBSERVATION —— 拿到回条/错误之后落盘（带回条号或错误类型）。
半途崩溃时，最后一条 INTENT 可能没有配对的 OBSERVATION，
恢复流程据此重查/重试，而不是盲目再发一次。
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from .codec import append_jsonl, atomic_write, digest, now_ts, read_json, read_jsonl

INTENT = "INTENT"
OBSERVATION = "OBSERVATION"
LIFECYCLE = "LIFECYCLE"


class JournalError(Exception):
    pass


@dataclass
class PendingIntent:
    entry: dict


class JobJournal:
    def __init__(self, jobs_dir: str, job_id: str):
        self.job_id = job_id
        self.dir = os.path.join(jobs_dir, job_id)
        self.log_path = os.path.join(self.dir, "journal.jsonl")
        self.state_path = os.path.join(self.dir, "state.json")
        self.slip_path = os.path.join(self.dir, "slip.json")
        os.makedirs(self.dir, exist_ok=True)

    # ---- 生命周期 ---------------------------------------------------
    def create(self, order: dict, domain: str) -> None:
        if os.path.exists(self.state_path):
            raise JournalError(f"作业 {self.job_id} 已存在，禁止覆盖")
        atomic_write(
            self.state_path,
            {
                "job_id": self.job_id,
                "domain": domain,
                "order_id": order["order_id"],
                "order_digest": digest(order),
                "status": "CREATED",
                "attempt": 0,
                "created_at": now_ts(),
            },
        )
        self._lifecycle("created", {"order_id": order["order_id"]})

    def set_status(self, status: str, **extra) -> None:
        state = read_json(self.state_path)
        state["status"] = status
        state["updated_at"] = now_ts()
        state.update(extra)
        atomic_write(self.state_path, state)
        self._lifecycle("status", {"status": status, **extra})

    def get_state(self) -> dict | None:
        if not os.path.exists(self.state_path):
            return None
        return read_json(self.state_path)

    def _lifecycle(self, event: str, detail: dict) -> None:
        append_jsonl(
            self.log_path,
            {
                "type": LIFECYCLE,
                "ts": now_ts(),
                "event": event,
                "detail": detail,
            },
        )

    # ---- 外部交接的因果记录 -----------------------------------------
    def write_intent(self, step: str, service: str, request: dict) -> dict:
        entry = {
            "type": INTENT,
            "ts": now_ts(),
            "step": step,
            "service": service,
            "idempotency_key": request["idempotency_key"],
            "intent_digest": request["intent_digest"],
            "request": request,
        }
        append_jsonl(self.log_path, entry)
        return entry

    def write_observation(self, step: str, idem_key: str, outcome: dict) -> None:
        append_jsonl(
            self.log_path,
            {
                "type": OBSERVATION,
                "ts": now_ts(),
                "step": step,
                "idempotency_key": idem_key,
                "outcome": outcome,
            },
        )

    def entries(self) -> list[dict]:
        return read_jsonl(self.log_path)

    def pending_intents(self) -> list[dict]:
        """已发出意图但尚无配对观察（崩溃/超时留下的悬置交接）。"""
        pending: dict[str, dict] = {}
        for e in self.entries():
            if e["type"] == INTENT:
                pending[e["idempotency_key"]] = e
            elif e["type"] == OBSERVATION:
                pending.pop(e["idempotency_key"], None)
        return list(pending.values())

    def observations(self) -> list[dict]:
        return [e for e in self.entries() if e["type"] == OBSERVATION]

    def receipt_ids(self) -> list[str]:
        ids = []
        for e in self.observations():
            rid = e["outcome"].get("receipt_id")
            if rid:
                ids.append(rid)
        return ids

    # ---- 领物条 -----------------------------------------------------
    def write_slip(self, slip: dict) -> None:
        atomic_write(self.slip_path, slip)
        self._lifecycle("slip_signed", {"slip_id": slip["slip_id"]})

    def slip(self) -> dict | None:
        if os.path.exists(self.slip_path):
            return read_json(self.slip_path)
        return None
