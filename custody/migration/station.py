"""受托站：迁移台编排器。

设计要点：
- 单一事实来源是一条哈希链事件日志（journal），内存状态全部可由事件重放重建；
- 旧解释器在 retire 时并行求值一次后封存证据，之后冻结，仅在客户追认时被引用；
- 库房离线时只消费已签出许可，站上任何新动作都不会被自动批准；
- 重放结果用根哈希与完整报告双重比对，保证本地可重放、结论一致。
"""
from __future__ import annotations

import json
from datetime import date
from typing import Optional

from ..hashing import canon, digest, station_seal, verify_seal
from ..model import (Contract, CustodyOrder, CustomerProposal, EvidenceBundle,
                     Handover, PermitOperation, PortablePermit, RatificationCase,
                     StoreLogEntry)
from ..permits import LEGACY, sign_out
from .stores import ReceivingStore

_GENESIS = "0" * 64


class Station:
    def __init__(self, station_key: str,
                 store_horizons: dict[str, Optional[date]]):
        self.station_key = station_key
        self.store_horizons = dict(store_horizons)
        # ---- 可变状态（全部可由 journal 重放）----
        self.journal: list[dict] = []
        self.contracts: dict[tuple[str, int], Contract] = {}
        self.handovers: dict[str, list[Handover]] = {}
        self.orders: dict[str, CustodyOrder] = {}
        self.permits: dict[str, PortablePermit] = {}
        self.evidence: dict[str, EvidenceBundle] = {}
        self.cases: dict[str, RatificationCase] = {}
        self.proposals: dict[str, CustomerProposal] = {}
        self.stores: dict[str, ReceivingStore] = {
            sid: ReceivingStore(sid, horizon) for sid, horizon in store_horizons.items()
        }
        self._permit_seq = 0
        self._case_seq = 0
        self._proposal_seq = 0

    # ====================================================== 哈希链事件
    def _emit(self, etype: str, on: date, payload: dict) -> dict:
        prev = self.journal[-1]["entry_hash"] if self.journal else _GENESIS
        seq = len(self.journal) + 1
        body = {"seq": seq, "type": etype, "on": on.isoformat(),
                "payload": payload, "prev_hash": prev}
        event = dict(body, entry_hash=digest(body))
        self.journal.append(event)
        return event

    @property
    def root_hash(self) -> str:
        return self.journal[-1]["entry_hash"] if self.journal else _GENESIS

    # ====================================================== 1. 登记合同/交接
    def register_contract(self, contract: Contract):
        self._emit("contract-registered", contract.effective_on, {
            "contract_id": contract.contract_id, "version": contract.version,
            "effective_on": contract.effective_on.isoformat(),
            "default_term_days": contract.default_term_days,
        })
        self.contracts[(contract.contract_id, contract.version)] = contract

    def register_handovers(self, order_id: str, handovers: list[Handover], on: date):
        self._emit("handovers-registered", on, {
            "order_id": order_id,
            "handovers": [
                {"handover_id": h.handover_id, "on": h.on.isoformat(), "kind": h.kind,
                 "from_party": h.from_party, "to_party": h.to_party}
                for h in handovers],
        })
        self.handovers[order_id] = list(handovers)

    # ====================================================== 2. 旧解释器退场：签出许可
    def retire_interpreter(self, orders: list[CustodyOrder], as_of: date) -> list[PortablePermit]:
        out: list[PortablePermit] = []
        for order in orders:
            self.orders[order.order_id] = order
            self._emit("interpreter-retired", as_of, {
                "order_id": order.order_id, "as_of": as_of.isoformat()})
            self._permit_seq += 1
            permit, evidence = sign_out(
                self.station_key, order, self.contracts, self.handovers,
                as_of, self._permit_seq)
            self.permits[order.order_id] = permit
            self.evidence[evidence.evidence_id] = evidence
            if permit.ratification_required:
                self._case_seq += 1
                case = RatificationCase(
                    f"RC-{self._case_seq:04d}", order.order_id, as_of,
                    "双解释器分歧/条件无法表达", evidence.evidence_id, "open", None)
                self.cases[case.case_id] = case
                self._emit("ratification-opened", as_of, {
                    "case_id": case.case_id, "order_id": order.order_id,
                    "evidence_id": evidence.evidence_id, "trigger": case.trigger})
            out.append(permit)
        return out

    # ====================================================== 3. 分批送往只读库
    def dispatch(self, order_id: str, store_id: str, on: date) -> StoreLogEntry | Refusal:
        order = self.orders[order_id]
        permit = self.permits[order_id]
        self._emit("dispatch-requested", on,
                   {"order_id": order_id, "store_id": store_id})
        result = self.stores[store_id].receive(on, order_id, order.seal,
                                               permit, self.station_key)
        if isinstance(result, StoreLogEntry):
            return result
        # 库拒绝（存期不够等）：原义务仍成立，客户只能拿换库/改约单
        self._proposal_seq += 1
        proposal = CustomerProposal(
            f"PP-{self._proposal_seq:04d}", order_id, result.proposal_kind,
            result.reason,
            ("更换至存期足够的接收库", "与客户改约重定期限"), "issued")
        self.proposals[proposal.proposal_id] = proposal
        self._emit("proposal-issued", on, {
            "proposal_id": proposal.proposal_id, "order_id": order_id,
            "kind": result.proposal_kind, "reason": result.reason})
        return result

    # ====================================================== 4. 演练未预料转交
    def simulate_transfer(self, order_id: str, store_id: str, on: date,
                          from_party: str, to_party: str) -> StoreLogEntry | Refusal:
        # 先把转交事实记入已发生交接（演练注入）
        handover = Handover(f"HO-{order_id}-T{on:%m%d}", on, "transfer",
                            from_party, to_party)
        self.handovers.setdefault(order_id, []).append(handover)
        self._emit("transfer-injected", on, {
            "order_id": order_id, "store_id": store_id,
            "handover": {"handover_id": handover.handover_id, "on": on.isoformat(),
                         "kind": "transfer", "from_party": from_party,
                         "to_party": to_party}})
        order = self.orders[order_id]
        permit = self.permits[order_id]
        result = self.stores[store_id].transfer_in(
            on, order_id, order.seal, permit, self.station_key)
        if not isinstance(result, StoreLogEntry):
            # 冻结该动作并形成追认案
            self._case_seq += 1
            case = RatificationCase(
                f"RC-{self._case_seq:04d}", order_id, on,
                f"未预料转交被冻结：{result.reason}", permit.evidence_id, "open", None)
            self.cases[case.case_id] = case
            self._emit("ratification-opened", on, {
                "case_id": case.case_id, "order_id": order_id,
                "evidence_id": permit.evidence_id, "trigger": case.trigger})
        return result

    # ====================================================== 5. 离线处置
    def offline_dispose(self, order_id: str, store_id: str,
                        on: date) -> StoreLogEntry | Refusal:
        order = self.orders[order_id]
        permit = self.permits[order_id]
        self._emit("offline-dispose-attempted", on,
                   {"order_id": order_id, "store_id": store_id})
        return self.stores[store_id].dispose(on, order_id, order.seal,
                                             permit, self.station_key)

    # ====================================================== 6. 客户追认
    def ratify(self, case_id: str, on: date, decision: str) -> Optional[PortablePermit]:
        case = self.cases[case_id]
        assert decision in ("approved", "denied")
        new_permit: Optional[PortablePermit] = None
        if decision == "approved":
            # 追认不是让退役字典事后补算：用封存证据里的规则 + 截至当前已发生的
            # 交接事实，由站上重新求值一次，把结论落成绝对日期写进新许可。
            order = self.orders[case.order_id]
            contract = self.contracts[(order.contract_id, order.contract_version)]
            current = LEGACY.evaluate(
                order, contract, self.handovers.get(case.order_id, []), on)
            if current.status != "determined" or current.term_end is None:
                raise ValueError(
                    f"追认 {case.case_id} 无法得出确定到期日（status={current.status}），"
                    "仍须返回复核，不得批准")
            legacy_end = current.term_end.isoformat()
            evidence = next(e for e in self.evidence.values()
                            if e.evidence_id == case.evidence_id)
            self._permit_seq += 1
            payload = {
                "permit_id": f"PM-{self._permit_seq:04d}",
                "order_id": case.order_id,
                "order_seal": self.orders[case.order_id].seal,
                "evidence_id": case.evidence_id,
                "issued_on": on.isoformat(),
                "operations": [
                    {"op": "retain-until", "arg": legacy_end,
                     "conditions": ["客户追认；按原绑定解释版本恢复的期限"]},
                    {"op": "dispose-after", "arg": legacy_end,
                     "conditions": ["客户追认批准", f"不早于 {legacy_end}"]},
                ],
                "ratification_required": False,
                "dropped_automation": [],
                "scope_note": "追认后许可，范围仍以原批准处理为限",
            }
            new_permit = PortablePermit(
                payload["permit_id"], case.order_id,
                self.orders[case.order_id].seal, case.evidence_id, on,
                (PermitOperation("retain-until", legacy_end,
                                 ("客户追认；按原绑定解释版本恢复的期限",)),
                 PermitOperation("dispose-after", legacy_end,
                                 ("客户追认批准", f"不早于 {legacy_end}"))),
                False, (), payload["scope_note"],
                station_seal(self.station_key, payload))
            self.permits[case.order_id] = new_permit
        resolved = self.cases[case_id]
        self.cases[case_id] = RatificationCase(
            resolved.case_id, resolved.order_id, resolved.opened_on,
            resolved.trigger, resolved.evidence_id, decision,
            f"{decision} on {on.isoformat()}")
        self._emit("ratification-decided", on,
                   {"case_id": case_id, "decision": decision,
                    "new_permit": new_permit.permit_id if new_permit else None})
        return new_permit

    # ====================================================== 7. 普通入口续约（带生效日）
    def intake_renewal(self, renewal: CustodyOrder, as_of: date) -> PortablePermit:
        """普通入口受理续约单：新版字典、显式生效日/到期日，走正常签出。"""
        self.orders[renewal.order_id] = renewal
        self._emit("renewal-intaken", as_of, {"order_id": renewal.order_id})
        self._permit_seq += 1
        permit, evidence = sign_out(
            self.station_key, renewal, self.contracts,
            self.handovers, as_of, self._permit_seq)
        self.permits[renewal.order_id] = permit
        self.evidence[evidence.evidence_id] = evidence
        return permit

    # ====================================================== 审计与报告
    def audit_stores(self) -> dict:
        """查每一库真正执行过什么（只认哈希链日志，不认内存意图）。"""
        out = {}
        for sid, store in sorted(self.stores.items()):
            out[sid] = {
                "chain_valid": store.verify_chain(),
                "horizon": store.guaranteed_horizon.isoformat()
                if store.guaranteed_horizon else None,
                "entries": [
                    {"seq": e.seq, "on": e.on.isoformat(), "action": e.action,
                     "order_id": e.order_id, "detail": e.detail}
                    for e in store.log],
            }
        return out

    def report(self) -> dict:
        return {
            "root_hash": self.root_hash,
            "permits": {
                oid: {
                    "permit_id": p.permit_id,
                    "operations": [
                        {"op": o.op, "arg": o.arg, "conditions": list(o.conditions)}
                        for o in p.operations],
                    "ratification_required": p.ratification_required,
                    "dropped_automation": list(p.dropped_automation),
                }
                for oid, p in sorted(self.permits.items())},
            "ratification_queue": [
                {"case_id": c.case_id, "order_id": c.order_id, "status": c.status,
                 "trigger": c.trigger, "resolution": c.resolution}
                for c in sorted(self.cases.values(), key=lambda c: c.case_id)],
            "proposals": [
                {"proposal_id": p.proposal_id, "order_id": p.order_id, "kind": p.kind,
                 "reason": p.reason, "status": p.status}
                for p in sorted(self.proposals.values(), key=lambda p: p.proposal_id)],
            "evidence": sorted(self.evidence.keys()),
            "stores": self.audit_stores(),
        }

    # ====================================================== 持久化与重放
    def save_journal(self, path: str):
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"station_key": self.station_key,
                       "store_horizons": {k: (v.isoformat() if v else None)
                                          for k, v in self.store_horizons.items()},
                       "journal": self.journal}, f, ensure_ascii=False, indent=2)

    @classmethod
    def replay(cls, path: str,
               order_factory, contract_factory) -> "Station":
        """从事件日志重建全站。两个 factory 负责按 id 还原不可变单据/合同。"""
        with open(path, encoding="utf-8") as f:
            saved = json.load(f)
        horizons = {k: (date.fromisoformat(v) if v else None)
                    for k, v in saved["store_horizons"].items()}
        s = cls(saved["station_key"], horizons)
        for ev in saved["journal"]:
            etype, on, payload = ev["type"], date.fromisoformat(ev["on"]), ev["payload"]
            if etype == "contract-registered":
                s.register_contract(contract_factory(payload))
            elif etype == "handovers-registered":
                hs = [Handover(h["handover_id"], date.fromisoformat(h["on"]), h["kind"],
                               h["from_party"], h["to_party"])
                      for h in payload["handovers"]]
                s.register_handovers(payload["order_id"], hs, on)
            elif etype == "interpreter-retired":
                order = order_factory(payload["order_id"])
                s.retire_interpreter([order], date.fromisoformat(payload["as_of"]))
            elif etype == "dispatch-requested":
                s.dispatch(payload["order_id"], payload["store_id"], on)
            elif etype == "transfer-injected":
                h = payload["handover"]
                s.simulate_transfer(
                    payload["order_id"], payload["store_id"], on,
                    h["from_party"], h["to_party"])
            elif etype == "offline-dispose-attempted":
                s.offline_dispose(payload["order_id"], payload["store_id"], on)
            elif etype == "ratification-decided":
                s.ratify(payload["case_id"], on, payload["decision"])
            elif etype == "renewal-intaken":
                s.intake_renewal(order_factory(payload["order_id"]), as_of=on)
            elif etype == "proposal-issued" or etype == "ratification-opened":
                continue  # 派生事件，已由上游处理重建
            else:
                raise ValueError(f"未知事件类型：{etype}")
        return s
