"""路由与故障传输层。

域守卫（切域点就在这一层）：
- 作业携带 domain；路由器只在同域目录下解析服务，
  演练作业物理上拿不到 prod/ 下的任何句柄；
- 演练域解析未登记的服务名 -> fail-closed（缓存里残留的正式柜名一律拦截）；
- 正式域只读期被调用 -> fail-closed。

故障注入（题面“调整模拟返回”）：
- TIMEOUT_ONCE / TIMEOUT_ALWAYS：回条丢失式超时，服务端可能已落账，
  重放靠幂等键拿回原回条；
- CRASH_ONCE：服务端已处理、客户端未写 OBSERVATION 即“进程死亡”，
  重启后由恢复流程捡起悬置 INTENT。
故障触发记录持久化，ONCE 类故障在重放/重启后自动转正常。
"""
from __future__ import annotations

import os

from .codec import atomic_write, ensure_dir, now_ts, read_json
from .external import Cabinet, ReinspectStation, Service, ServiceError


class TimeoutError_(Exception):
    """模拟传输超时：结果未知，服务端或许已经办了。"""


class ProcessCrash(Exception):
    """模拟作业进程崩溃：意图已发、服务端可能已办、观察未记录。"""


class DomainGuardError(Exception):
    """跨域访问被拒绝。"""


NORMAL = "NORMAL"
TIMEOUT_ONCE = "TIMEOUT_ONCE"
TIMEOUT_ALWAYS = "TIMEOUT_ALWAYS"
CRASH_ONCE = "CRASH_ONCE"


class Router:
    def __init__(self, data_root: str, domain_aliases: dict[str, str] | None = None):
        self.data_root = data_root
        # 演练作业里缓存的服务名 -> 演练替身名（None/缺失即 fail-closed）
        self.domain_aliases = domain_aliases or {}
        self.fault_path = os.path.join(data_root, "fault_policy.json")
        self.fired_path = os.path.join(data_root, "fault_fired.json")
        ensure_dir(data_root)
        if not os.path.exists(self.fault_path):
            atomic_write(self.fault_path, {})
        if not os.path.exists(self.fired_path):
            atomic_write(self.fired_path, {"fired": []})

    # ---- 故障策略 ---------------------------------------------------
    def set_fault(self, job_id: str, step: str, mode: str) -> None:
        policy = read_json(self.fault_path)
        policy.setdefault(job_id, {})[step] = mode
        atomic_write(self.fault_path, policy)

    def get_fault(self, job_id: str, step: str) -> str:
        return read_json(self.fault_path).get(job_id, {}).get(step, NORMAL)

    def clear_faults(self, job_id: str | None = None) -> None:
        policy = read_json(self.fault_path)
        if job_id is None:
            policy = {}
        else:
            policy.pop(job_id, None)
        atomic_write(self.fault_path, policy)
        fired = read_json(self.fired_path)
        fired["fired"] = [
            f for f in fired["fired"] if job_id is None or f["job_id"] != job_id
        ]
        atomic_write(self.fired_path, fired)

    def _consume_fault(self, job_id: str, step: str) -> str | None:
        mode = self.get_fault(job_id, step)
        if mode == NORMAL:
            return None
        fired = read_json(self.fired_path)
        key = {"job_id": job_id, "step": step, "mode": mode}
        already = any(
            f["job_id"] == job_id and f["step"] == step for f in fired["fired"]
        )
        if mode in (TIMEOUT_ONCE, CRASH_ONCE):
            if already:
                return None  # 重放/重启后故障不再复现
            fired["fired"].append({**key, "ts": now_ts()})
            atomic_write(self.fired_path, fired)
            return mode
        if mode == TIMEOUT_ALWAYS:
            fired["fired"].append({**key, "ts": now_ts()})
            atomic_write(self.fired_path, fired)
            return mode
        return None

    # ---- 域守卫与服务解析 -------------------------------------------
    def _resolve(self, job_domain: str, service_name: str) -> Service:
        if job_domain == "drill":
            mapped = self.domain_aliases.get(service_name)
            if mapped is None:
                raise DomainGuardError(
                    f"演练作业引用未登记的服务 {service_name!r}（疑似缓存的正式柜名），"
                    "按 fail-closed 拒绝"
                )
            service_name = mapped
            base = os.path.join(self.data_root, "drill")
        else:
            base = os.path.join(self.data_root, "prod")
            seal = os.path.join(base, "SEALED")
            if os.path.exists(seal):
                raise DomainGuardError(
                    "正式域已封存（演练进行中），正式出口已切断"
                )
        svc_dir = os.path.join(base, service_name)
        meta_path = os.path.join(svc_dir, "state.json")
        if not os.path.exists(meta_path):
            raise DomainGuardError(
                f"{job_domain} 域内不存在服务 {service_name!r}"
            )
        meta = read_json(meta_path)
        if meta["kind"] == "cabinet":
            return Cabinet(self.data_root, meta["domain"], service_name)
        return ReinspectStation(self.data_root, meta["domain"], service_name)

    def call(self, job_domain: str, job_id: str, step: str, service_name: str,
             method: str, request: dict) -> dict:
        """统一外部调用入口：先过故障闸门，再解析同域服务。"""
        fault = self._consume_fault(job_id, step)
        service = self._resolve(job_domain, service_name)
        try:
            result = getattr(service, method)(request)
        except ServiceError as e:
            # 服务端明确拒绝：不属于传输故障，直接把拒绝带回作业。
            return {"ok": False, "error_type": "ServiceError", "error": str(e)}

        if fault == CRASH_ONCE:
            # 服务端已落账并返回，但客户端此刻“死亡”：不产生 OBSERVATION。
            raise ProcessCrash(
                f"{step} 已在 {service_name} 落账，作业进程崩溃，回条未登记"
            )
        if fault in (TIMEOUT_ONCE, TIMEOUT_ALWAYS):
            raise TimeoutError_(
                f"{step} 调用 {service_name} 超时（服务端可能已处理）"
            )
        return {"ok": True, "receipt": result.data}
