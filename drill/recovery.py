"""Recovery process.

Inputs are ONLY:
  * the durable store tree (journal.log + vol*/wal.log) -- ground truth
  * held receipts the teacher presents (signed by each classroom's key)
  * power.json (facility's actual report)

It never imports the engine/config, and it never reads cache.json as durable.

Guarantees the drill checks:
  1. no index points at an old/missing page (stale index flagged, not served)
  2. a confirmed receipt shows BOTH halves of its cross-volume pages, or is
     honestly downgraded to partial -- never half-presented
  3. cache-only writes are NOT counted as recovered
  4. previously returned receipt numbers are honoured on reopen
  5. two classrooms' confirmed work recovers independently by their own
     signed credentials
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from .receipts import verify_receipt
from .storage import load_durable

PageKey = Tuple[str, int]


@dataclass
class RecoveredTx:
    txid: str
    classroom: str
    assignment: str
    cabinet: str
    status: str                     # confirmed | pending | partial | phantom
    pages: Dict[PageKey, Dict[str, Any]] = field(default_factory=dict)
    issues: List[str] = field(default_factory=list)
    receipt_number: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "txid": self.txid,
            "classroom": self.classroom,
            "assignment": self.assignment,
            "cabinet": self.cabinet,
            "status": self.status,
            "receipt_number": self.receipt_number,
            "pages": [
                {"vol": v, "page_no": n, **p}
                for (v, n), p in sorted(self.pages.items())
            ],
            "issues": list(self.issues),
            "editable": self.status in ("confirmed", "pending"),
        }


class Recovery:
    def __init__(self, root: str, held_dir: str,
                 classroom_keys: Dict[str, bytes]) -> None:
        self.root = root
        self.held_dir = held_dir
        self.keys = classroom_keys
        self.durable = load_durable(root)

    # -- durable reconstruction ----------------------------------------
    def _durable_pages(self) -> Dict[str, Dict[PageKey, Dict[str, Any]]]:
        """txid -> {(vol,page): record}.  WAL only; cache is ignored."""
        out: Dict[str, Dict[PageKey, Dict[str, Any]]] = {}
        for vol in ("vol1", "vol2"):
            for rec in self.durable["wal"][vol]:
                out.setdefault(rec["txid"], {})[(vol, rec["page_no"])] = {
                    "kind": rec["kind"],
                    "content": rec["content"],
                    "flush_tick": rec["tick"],
                    "op_id": rec.get("op_id"),
                }
        return out

    def _durable_op_ids(self) -> Set[str]:
        ids: Set[str] = set()
        for vol in ("vol1", "vol2"):
            for rec in self.durable["wal"][vol]:
                if rec.get("op_id"):
                    ids.add(rec["op_id"])
        for rec in self.durable["journal"]:
            if rec.get("op_id"):
                ids.add(rec["op_id"])
        return ids

    def _commits(self) -> Dict[str, Dict[str, Any]]:
        return {r["txid"]: r for r in self.durable["journal"]}

    def _commit_valid(self, commit: Dict[str, Any], op_ids: Set[str]) -> bool:
        deps = commit.get("deps") or []
        return all(d in op_ids for d in deps)

    def _load_held(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if not os.path.isdir(self.held_dir):
            return out
        for name in sorted(os.listdir(self.held_dir)):
            if not name.endswith(".json"):
                continue
            with open(os.path.join(self.held_dir, name), "r", encoding="utf-8") as fh:
                out.append(json.load(fh))
        return out

    def _meta_for(self, txid: str) -> Tuple[str, str, str]:
        rows = list(self.durable["journal"])
        for wl in self.durable["wal"].values():
            rows.extend(wl)
        for r in rows:
            if r.get("txid") == txid:
                return (r.get("classroom", "?"), r.get("assignment", "?"),
                        r.get("cabinet", "?"))
        return ("?", "?", "?")

    # -- public ---------------------------------------------------------
    def recover(self) -> Dict[str, Any]:
        durable_pages = self._durable_pages()
        op_ids = self._durable_op_ids()
        commits = self._commits()
        held = self._load_held()

        # strongest receipt per tx (confirmed beats pending), verifying signature
        receipt_by_tx: Dict[str, Dict[str, Any]] = {}
        forged: Dict[str, RecoveredTx] = {}
        for r in held:
            p = r.get("payload", {})
            txid = p.get("txid")
            if not txid:
                continue
            classroom = p.get("classroom", "?")
            if not verify_receipt(r, self.keys.get(classroom, b"")):
                forged[txid] = RecoveredTx(
                    txid, classroom, p.get("assignment", "?"),
                    p.get("cabinet", "?"), "phantom",
                    issues=["receipt signature invalid"])
                continue
            prev = receipt_by_tx.get(txid)
            if prev is None or (prev["payload"].get("policy") != "durable"
                                and p.get("policy") == "durable"):
                receipt_by_tx[txid] = r

        tx_out: Dict[str, RecoveredTx] = {}
        for txid in sorted(set(durable_pages) | set(commits) | set(receipt_by_tx)
                           | set(forged)):
            if txid in forged:
                tx_out[txid] = forged[txid]
                continue
            pages = dict(durable_pages.get(txid, {}))
            commit = commits.get(txid)
            commit_ok = bool(commit) and self._commit_valid(commit, op_ids)
            r = receipt_by_tx.get(txid)
            cls, asg, cab = self._meta_for(txid)
            if r is not None:
                tx_out[txid] = self._from_receipt(txid, r, pages, commit_ok)
            else:
                tx_out[txid] = self._from_durable_only(
                    txid, cls, asg, cab, pages, commit_ok)
            tx_out[txid].issues.extend(self._stale_index_issues(tx_out[txid].pages))

        # cache snapshot is read ONLY to prove we discard it
        cache_tx = self._cache_only_ids()
        durable_tx = set(durable_pages) | set(commits)
        discarded_cache = sorted(cache_tx - durable_tx)
        summary = {
            "recovered": [t.to_dict() for t in tx_out.values()],
            "usable": sorted(t.txid for t in tx_out.values()
                             if t.status in ("confirmed", "pending")),
            "confirmed": sorted(t.txid for t in tx_out.values()
                                if t.status == "confirmed"),
            "phantom": sorted(t.txid for t in tx_out.values()
                              if t.status == "phantom"),
            "partial": sorted(t.txid for t in tx_out.values()
                              if t.status == "partial"),
            # txids whose ONLY copy was in volatile cache -- never resurrected
            "rejected_cache_only": discarded_cache,
            "power": self.durable["power"],
        }
        return summary

    def _cache_only_ids(self) -> Set[str]:
        """txids seen ONLY in the volatile cache snapshot."""
        wal_tx: Set[str] = set()
        for vol in ("vol1", "vol2"):
            wal_tx |={r["txid"] for r in self.durable["wal"][vol]}
        wal_tx |={r["txid"] for r in self.durable["journal"]}
        cache_tx: Set[str] = set()
        for vol in ("vol1", "vol2"):
            path = os.path.join(self.root, vol, "cache.json")
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as fh:
                    cache_tx |= {v["txid"] for v in json.load(fh).values()}
        cpath = os.path.join(self.root, "cache_commit.json")
        if os.path.exists(cpath):
            with open(cpath, "r", encoding="utf-8") as fh:
                cache_tx |= {v["txid"] for v in json.load(fh).values()}
        return cache_tx - wal_tx

    def _from_receipt(self, txid: str, receipt: Dict[str, Any],
                      durable_pages: Dict[PageKey, Dict[str, Any]],
                      commit_ok: bool) -> RecoveredTx:
        p = receipt["payload"]
        out = RecoveredTx(
            txid=txid, classroom=p["classroom"], assignment=p["assignment"],
            cabinet=p["cabinet"], status="pending",
            receipt_number=p.get("receipt_number") or txid)
        promised = {(pg["vol"], pg["page_no"]) for pg in p.get("pages", [])}
        have = set(durable_pages) & promised
        missing = promised - have
        policy = p.get("policy")
        if policy == "durable":
            if not missing and commit_ok:
                out.status = "confirmed"
            else:
                out.status = "partial"
                for vol, pno in sorted(missing):
                    out.issues.append(f"promised page missing on {vol}:{pno}")
                if not commit_ok:
                    out.issues.append("commit record not durable/incomplete")
        elif policy == "cache":
            if not missing and commit_ok:
                out.status = "confirmed"
            else:
                out.status = "phantom"
                out.issues.append("promise rested on volatile cache only")
        else:  # pending confirmation
            if not missing and commit_ok:
                out.status = "confirmed"
            elif have:
                # near-boundary pending receipt: real durable progress exists,
                # but not everything is promised.  Editable as a resume, the
                # receipt number is still honoured.
                out.status = "pending"
                for vol, pno in sorted(missing):
                    out.issues.append(f"not yet durable on {vol}:{pno}")
                if not commit_ok:
                    out.issues.append("commit not yet durable")
            else:
                # nothing durable ever landed -- the whole order lived in the
                # volatile cache; do NOT resurrect it.
                out.status = "phantom"
                out.issues.append("pending receipt had no durable pages")
        for key in have:
            out.pages[key] = durable_pages[key]
        return out

    def _from_durable_only(self, txid: str, cls: str, asg: str, cab: str,
                           pages: Dict[PageKey, Dict[str, Any]],
                           commit_ok: bool) -> RecoveredTx:
        out = RecoveredTx(txid, cls, asg, cab,
                          "confirmed" if commit_ok else "partial")
        out.pages = dict(pages)
        if not commit_ok:
            out.issues.append("no durable commit record (uncommitted write)")
        return out

    @staticmethod
    def _stale_index_issues(
            pages: Dict[PageKey, Dict[str, Any]]) -> List[str]:
        issues: List[str] = []
        idx = [(k, v) for k, v in pages.items() if v.get("kind") == "index"]
        data = [(k, v) for k, v in pages.items() if v.get("kind") == "data"]
        for (vol, pno), rec in idx:
            content = rec.get("content") or ""
            refs: List[int] = []
            if content.strip().startswith("{"):
                try:
                    refs = json.loads(content).get("points_to", [])
                except ValueError:
                    refs = []
            for ref in refs:
                if not any(k[1] == ref for k, _ in data):
                    issues.append(
                        f"stale index {vol}:{pno} -> missing data page {ref}")
        return issues


def reopen(summary: Dict[str, Any],
           receipt_numbers: Dict[str, str]) -> Dict[str, Any]:
    """Reopen editing for usable work and redeem previously returned numbers."""
    out = []
    for rec in summary["recovered"]:
        if rec["status"] not in ("confirmed", "pending"):
            continue
        txid = rec["txid"]
        out.append({
            "txid": txid,
            "classroom": rec["classroom"],
            "assignment": rec["assignment"],
            "editing": True,
            "redeemed_receipt": receipt_numbers.get(txid),
            "status": rec["status"],
        })
    return {"reopened": out}
