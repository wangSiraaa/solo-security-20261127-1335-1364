"""外部柜与复检站：都当作“本地目录模拟的小程序”。

共同约束（题面要求）：
1. 每个服务有独立序号 seq，只增不减；
2. 保管柜另有“已发件计数 dispatched”，同样不可倒退；
3. 每次交接给回条 receipt，回条带域标记，且按幂等键去重——
   同一个幂等键重放，只返回原回条，绝不二次发件；
4. 所有动作进哈希链台账 ledger.jsonl，可逐条验链。
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from .codec import (
    append_jsonl,
    atomic_write,
    chained_hash,
    digest,
    ensure_dir,
    now_ts,
    read_json,
    read_jsonl,
)


class ServiceError(Exception):
    """服务端明确拒绝（区别于传输层的超时/离线）。"""


@dataclass
class Receipt:
    data: dict

    @property
    def id(self) -> str:
        return self.data["receipt_id"]

    @property
    def kind(self) -> str:
        return self.data["kind"]

    @property
    def domain(self) -> str:
        return self.data["domain"]


class Service:
    """带序号、台账与回条存档的持久小程序基类。"""

    kind = "service"

    def __init__(self, root: str, domain: str, name: str, genesis: str = ""):
        self.root = root
        self.domain = domain
        self.name = name
        self.dir = os.path.join(root, domain, name)
        self.state_path = os.path.join(self.dir, "state.json")
        self.ledger_path = os.path.join(self.dir, "ledger.jsonl")
        self.receipt_dir = ensure_dir(os.path.join(self.dir, "receipts"))
        self.genesis = genesis
        if os.path.exists(self.state_path):
            self.state = read_json(self.state_path)
        else:
            self.state = self._initial_state()
            self._persist()

    def _initial_state(self) -> dict:
        return {
            "domain": self.domain,
            "name": self.name,
            "kind": self.kind,
            "seq": 0,
            "genesis": self.genesis,
        }

    def _persist(self) -> None:
        atomic_write(self.state_path, self.state)

    def _ledger(self) -> list[dict]:
        return read_jsonl(self.ledger_path)

    def _find_idem(self, idem_key: str) -> dict | None:
        """在台账里查幂等键是否已办过（崩溃恢复的关键）。"""
        for entry in self._ledger():
            if entry.get("idem_key") == idem_key:
                return entry
        return None

    def _replay_receipt(self, idem_key: str) -> Receipt | None:
        """幂等命中：原封不动取回历史回条，序号/计数/状态一律不动。"""
        prior = self._find_idem(idem_key)
        if prior is None:
            return None
        receipt = read_json(os.path.join(self.receipt_dir, prior["receipt_file"]))
        receipt = dict(receipt)
        receipt["duplicate"] = True
        return Receipt(receipt)

    def _issue(self, kind: str, idem_key: str, content: dict) -> Receipt:
        replayed = self._replay_receipt(idem_key)
        if replayed is not None:
            return replayed

        self.state["seq"] += 1
        seq = self.state["seq"]
        receipt_id = f"{self.domain}-{self.name}-{kind}-{seq:06d}"
        receipt = {
            "receipt_id": receipt_id,
            "domain": self.domain,
            "service": self.name,
            "kind": kind,
            "seq": seq,
            "idem_key": idem_key,
            "ts": now_ts(),
            **content,
        }
        receipt_file = f"{seq:06d}-{kind}.json"
        prev_hash = self._ledger()[-1]["hash"] if self._ledger() else self.genesis
        entry = {
            "seq": seq,
            "kind": kind,
            "idem_key": idem_key,
            "receipt_id": receipt_id,
            "receipt_file": receipt_file,
            "ts": receipt["ts"],
            "content_hash": digest(content),
        }
        entry["hash"] = chained_hash(prev_hash, entry)
        append_jsonl(self.ledger_path, entry)
        atomic_write(os.path.join(self.receipt_dir, receipt_file), receipt)
        self._after_issue(kind, receipt)
        self._persist()
        return Receipt(receipt)

    def _after_issue(self, kind: str, receipt: dict) -> None:
        """子类钩子：更新各自的业务计数。"""

    def verify(self) -> list[str]:
        return []

    def actions(self) -> list[dict]:
        """本服务实际办理过的动作（带序号与回条号）。"""
        return [
            {
                "seq": e["seq"],
                "kind": e["kind"],
                "idem_key": e["idem_key"],
                "receipt_id": e["receipt_id"],
                "ts": e["ts"],
            }
            for e in self._ledger()
        ]


class Cabinet(Service):
    """保管柜（正式柜 / 演练替身柜同一实现，不同目录与域）。"""

    kind = "cabinet"

    def _initial_state(self) -> dict:
        state = super()._initial_state()
        state.update(
            items={},
            # 已发件计数：只随成功取件增长，任何重放/回退都不动它。
            dispatched=0,
        )
        return state

    # ---- 装柜与快照 -------------------------------------------------
    def load_items(self, items: dict[str, dict]) -> None:
        for item_id, meta in items.items():
            self.state["items"][item_id] = {
                "item_id": item_id,
                "status": "IN",
                "allowed_receiver": meta["allowed_receiver"],
            }
        self._persist()

    def replace_receiver(self, item_id: str, new_receiver: str) -> None:
        """演练柜专用：更换合法接收组（不产生发件计数）。"""
        if self.domain != "drill":
            raise ServiceError("正式柜不允许演练式改配接收者")
        item = self.state["items"].get(item_id)
        if item is None:
            raise ServiceError(f"柜内无此件 {item_id}")
        item["allowed_receiver"] = new_receiver
        self._persist()

    def snapshot(self) -> dict:
        return {
            "snapshot_id": digest(self.state),
            "source_domain": self.domain,
            "source_name": self.name,
            "source_seq": self.state["seq"],
            "source_dispatched": self.state["dispatched"],
            "items": self.state["items"],
            "ts": now_ts(),
        }

    @classmethod
    def from_snapshot(cls, root: str, new_name: str, snap: dict) -> "Cabinet":
        """从正式柜快照长出演练替身柜：序号与发件计数从 0 重新起算。"""
        cabinet = cls(root, "drill", new_name, genesis=snap["snapshot_id"])
        cabinet.state["provenance"] = {
            "snapshot_id": snap["snapshot_id"],
            "source_domain": snap["source_domain"],
            "source_name": snap["source_name"],
            "source_dispatched": snap["source_dispatched"],
            "snapshot_ts": snap["ts"],
        }
        for item_id, item in snap["items"].items():
            cabinet.state["items"][item_id] = {
                "item_id": item_id,
                "status": item["status"],
                "allowed_receiver": item["allowed_receiver"],
            }
        cabinet._persist()
        return cabinet

    # ---- 交接动作 ---------------------------------------------------
    def take(self, req: dict) -> Receipt:
        idem_key = req["idempotency_key"]
        # 幂等查询先于一切业务校验：崩溃重放时件已是 OUT，
        # 但同一键必须原样拿回原回条，而不是再走一遍取件逻辑。
        replayed = self._replay_receipt(idem_key)
        if replayed is not None:
            return replayed
        ticket = req["ticket"]
        item = self.state["items"].get(ticket["item_id"])
        if item is None:
            raise ServiceError(f"柜内无此件 {ticket['item_id']}")
        if item["status"] != "IN":
            raise ServiceError(
                f"件 {ticket['item_id']} 当前状态 {item['status']}，不可再取"
            )
        if ticket["receiver"] != item["allowed_receiver"]:
            raise ServiceError(
                f"领用组 {ticket['receiver']} 不是该件的合法接收者"
                f"（合法：{item['allowed_receiver']}）"
            )
        receipt = self._issue(
            "take",
            idem_key,
            {
                "order_id": ticket["order_id"],
                "item_id": ticket["item_id"],
                "receiver": ticket["receiver"],
                "purpose": ticket.get("purpose", ""),
                "intent_digest": req["intent_digest"],
            },
        )
        item["status"] = "OUT"
        item["taken_by"] = ticket["receiver"]
        self._persist()
        return receipt

    def give_back(self, req: dict) -> Receipt:
        idem_key = req["idempotency_key"]
        replayed = self._replay_receipt(idem_key)
        if replayed is not None:
            return replayed
        ticket = req["ticket"]
        item = self.state["items"].get(ticket["item_id"])
        if item is None:
            raise ServiceError(f"柜内无此件 {ticket['item_id']}")
        receipt = self._issue(
            "return",
            idem_key,
            {
                "order_id": ticket["order_id"],
                "item_id": ticket["item_id"],
                "receiver": ticket["receiver"],
                "intent_digest": req["intent_digest"],
            },
        )
        item["status"] = "IN"
        item.pop("taken_by", None)
        self._persist()
        return receipt

    def _after_issue(self, kind: str, receipt: dict) -> None:
        if kind == "take":
            # 不可倒退的已发件计数：只有真正的新取件才 +1。
            self.state["dispatched"] += 1

    def verify(self) -> list[str]:
        problems = []
        entries = self._ledger()
        takes = sum(1 for e in entries if e["kind"] == "take")
        if takes != self.state["dispatched"]:
            problems.append(
                f"发件计数 {self.state['dispatched']} 与台账取件条数 {takes} 不一致"
            )
        for i, e in enumerate(entries):
            prev = entries[i - 1]["hash"] if i else self.genesis
            if "hash" not in e or chained_hash(prev, e) != e["hash"]:
                problems.append(f"台账第 {i + 1} 条哈希链断裂")
                break
        return problems

    def item_view(self) -> dict:
        return self.state["items"]


class ReinspectStation(Service):
    """复检站：只登记复检动作并出回条，同样有独立序号。"""

    kind = "station"

    def check(self, req: dict) -> Receipt:
        replayed = self._replay_receipt(req["idempotency_key"])
        if replayed is not None:
            return replayed
        return self._issue(
            "check",
            req["idempotency_key"],
            {
                "order_id": req["order_id"],
                "item_id": req["item_id"],
                "result": "OK",
                "intent_digest": req["intent_digest"],
            },
        )

    def verify(self) -> list[str]:
        entries = self._ledger()
        for i, e in enumerate(entries):
            prev = entries[i - 1]["hash"] if i else self.genesis
            if "hash" not in e or chained_hash(prev, e) != e["hash"]:
                return [f"台账第 {i + 1} 条哈希链断裂"]
        return []
