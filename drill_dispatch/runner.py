"""值班作业执行器：取件 -> 复检 -> 签出领物条。

安全要点：
1. 每次外部调用前先持久化 INTENT（含确定性幂等键与意图摘要），
   再经路由器调用；回条/错误落 OBSERVATION。
2. 重启先 reconcile 悬置 INTENT：用原请求重放，由服务端幂等去重，
   绝不凭客户端记忆“再发一次”。
3. 重试有界；连续超时耗尽后置 UNKNOWN 并停手，
   绝不把未知结果当成失败而重发。
4. 领物条只引用本作业自己拿到的回条，不能引用历史回条。
"""
from __future__ import annotations

from .codec import digest, now_ts
from .journal import JobJournal, INTENT, OBSERVATION
from .router import DomainGuardError, ProcessCrash, Router, TimeoutError_

MAX_ATTEMPTS_PER_STEP = 3


class JobRejected(Exception):
    """服务端明确拒绝（如不是合法接收者、件已不在柜）。"""


class JobUnknown(Exception):
    """重试耗尽仍结果未知，挂起待人工核查。"""


class Runner:
    STEPS = ("take", "check", "slip")

    def __init__(self, data_root: str, jobs_dir: str, job_id: str,
                 order: dict, domain: str, router: Router):
        self.data_root = data_root
        self.router = router
        self.job_id = job_id
        self.order = order
        self.domain = domain
        self.journal = JobJournal(jobs_dir, job_id)
        if not self.journal.get_state():
            self.journal.create(order, domain)

    # ---- 确定性请求构造 ---------------------------------------------
    def _ticket(self) -> dict:
        return {
            "order_id": self.order["order_id"],
            "item_id": self.order["item_id"],
            "receiver": self.order["receiver"],
            "purpose": self.order.get("purpose", ""),
        }

    def _intent(self, step: str, service: str, payload: dict) -> dict:
        body = {
            "job_id": self.job_id,
            "step": step,
            "service": service,
            "domain": self.domain,
            "payload": payload,
        }
        return {
            "intent_digest": digest(body),
            "idempotency_key": f"{self.job_id}:{step}:{digest(body)[:16]}",
            **body,
        }

    def _request_for(self, step: str) -> tuple[str, str, dict]:
        cabinet = self.order["cabinet"]
        station = self.order["station"]
        if step == "take":
            intent = self._intent("take", cabinet, {"ticket": self._ticket()})
            req = {
                "idempotency_key": intent["idempotency_key"],
                "intent_digest": intent["intent_digest"],
                "ticket": self._ticket(),
            }
            return cabinet, "take", req
        if step == "check":
            payload = {
                "order_id": self.order["order_id"],
                "item_id": self.order["item_id"],
            }
            intent = self._intent("check", station, payload)
            req = {
                "idempotency_key": intent["idempotency_key"],
                "intent_digest": intent["intent_digest"],
                **payload,
            }
            return station, "check", req
        raise ValueError(step)

    # ---- 观察查询 ---------------------------------------------------
    def _last_observation(self, idem_key: str) -> dict | None:
        for e in reversed(self.journal.entries()):
            if e["type"] == OBSERVATION and e["idempotency_key"] == idem_key:
                return e
        return None

    def _receipt_of(self, step: str) -> dict | None:
        for e in reversed(self.journal.entries()):
            if e["type"] == INTENT and e["step"] == step:
                obs = self._last_observation(e["idempotency_key"])
                if obs and obs["outcome"].get("ok"):
                    return obs["outcome"]["receipt"]
        return None

    # ---- 单步执行（带重放恢复） -------------------------------------
    def _run_external_step(self, step: str) -> dict:
        service, method, request = self._request_for(step)
        idem = request["idempotency_key"]

        # 1) 已有成功观察：直接复用，不再出门。
        prior = self._last_observation(idem)
        if prior and prior["outcome"].get("ok"):
            return prior["outcome"]["receipt"]
        if prior and prior["outcome"].get("error_type") == "ServiceError":
            raise JobRejected(prior["outcome"]["error"])

        # 2) 没有 INTENT（正常路径）或有悬置 INTENT（崩溃恢复）：都先补登。
        pending = [p for p in self.journal.pending_intents() if p["step"] == step]
        if pending:
            intent_entry = pending[0]
            request = intent_entry["request"]  # 恢复时严格沿用原请求
        else:
            self.journal.write_intent(step, service, request)

        attempts = 0
        while True:
            attempts += 1
            self.journal.set_status("RUNNING", step=step, attempt=attempts)
            try:
                outcome = self.router.call(
                    self.domain, self.job_id, step, service, method, request
                )
            except ProcessCrash as crash:
                # 模拟进程当场死亡：把异常抛给演示脚本，由“重启”接管。
                raise
            except TimeoutError_:
                self.journal.write_observation(
                    step, idem,
                    {"ok": False, "error_type": "Timeout",
                     "error": "传输超时，结果未知", "attempt": attempts},
                )
                if attempts >= MAX_ATTEMPTS_PER_STEP:
                    self.journal.set_status("UNKNOWN", step=step)
                    raise JobUnknown(
                        f"{step} 连续 {attempts} 次超时，已停手待核查；"
                        "悬置 INTENT 保留，恢复时按幂等键重查"
                    )
                continue
            except DomainGuardError:
                self.journal.write_observation(
                    step, idem,
                    {"ok": False, "error_type": "DomainGuard",
                     "error": "跨域访问被拒绝"},
                )
                self.journal.set_status("FAILED", step=step)
                raise

            self.journal.write_observation(step, idem, outcome)
            if not outcome.get("ok"):
                self.journal.set_status("FAILED", step=step)
                raise JobRejected(outcome.get("error", "服务端拒绝"))
            return outcome["receipt"]

    # ---- 领物条（本域内部签发，仍走因果日志） -----------------------
    def _sign_slip(self) -> dict:
        step = "slip"
        existing = self.journal.slip()
        if existing:
            return existing
        take_receipt = self._receipt_of("take")
        check_receipt = self._receipt_of("check")
        if not take_receipt or not check_receipt:
            raise JobRejected("缺少本作业的取件/复检回条，不能签领物条")
        chain_basis = [take_receipt["receipt_id"], check_receipt["receipt_id"]]
        slip = {
            "slip_id": f"{self.domain}-slip-{self.job_id}",
            "domain": self.domain,
            "job_id": self.job_id,
            "order_id": self.order["order_id"],
            "item_id": self.order["item_id"],
            "receiver": self.order["receiver"],
            "issued_at": now_ts(),
            "based_on_receipts": chain_basis,
            "causal_digest": digest(chain_basis),
            "note": "领物条仅由本作业同域回条派生；历史回条不得作为依据",
        }
        intent = self._intent("slip", "internal-journal", {"slip_id": slip["slip_id"]})
        self.journal.write_intent("slip", "internal-journal", intent)
        self.journal.write_observation(
            "slip", intent["idempotency_key"],
            {"ok": True, "slip_id": slip["slip_id"]},
        )
        self.journal.write_slip(slip)
        self.journal.set_status("COMPLETED")
        return slip

    # ---- 对外主入口 -------------------------------------------------
    def run(self) -> dict:
        """跑完整作业；崩溃恢复时从中断点继续。"""
        state = self.journal.get_state()
        if state["status"] == "COMPLETED":
            return {"status": "COMPLETED", "slip": self.journal.slip()}

        try:
            take_receipt = self._run_external_step("take")
            check_receipt = self._run_external_step("check")
            slip = self._sign_slip()
        except ProcessCrash:
            raise
        return {
            "status": "COMPLETED",
            "take_receipt_id": take_receipt["receipt_id"],
            "check_receipt_id": check_receipt["receipt_id"],
            "slip_id": slip["slip_id"],
        }

    def resume_after_crash(self) -> dict:
        """崩溃重启入口：先报告悬置交接，再继续跑。"""
        pending = self.journal.pending_intents()
        result = self.run()
        result["resumed_pending"] = [
            {"step": p["step"], "idempotency_key": p["idempotency_key"]}
            for p in pending
        ]
        return result
