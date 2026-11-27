"""演练主控：把一次业务记录带进演练室，但不带走正式出口。

职责：
1. 封存正式域（SEALED 标志 + 只读证据目录）；
2. 从正式柜作业前快照长出演练替身柜，序号/已发件计数独立起算；
3. 历史回条复制进 evidence/ 并出哈希清单，标记为“历史见证、不可复用”；
4. 受理演练工单（含改过的工单），但初态声明必须与快照、历史回条
   的凭据逐项匹配，防止旧回条/复制物冒充新单初态；
5. 所有演练作业落在 drill/ 域，正式计数物理上无法被改动。
"""
from __future__ import annotations

import os
import shutil

from .codec import (
    atomic_write,
    ensure_dir,
    now_ts,
    read_json,
    sha256_file,
)
from .external import Cabinet
from .router import Router
from .runner import Runner


class ClaimError(Exception):
    """演练工单的初态声明不成立。"""


class DrillMaster:
    def __init__(self, data_root: str):
        self.data_root = data_root
        self.prod_dir = os.path.join(data_root, "prod")
        self.drill_dir = os.path.join(data_root, "drill")
        self.evidence_dir = os.path.join(data_root, "evidence")
        self.manifest_path = os.path.join(self.evidence_dir, "manifest.json")
        self.baseline_path = os.path.join(self.prod_dir, "baseline_snapshot.json")

    # ---- 正式域封存 -------------------------------------------------
    def seal_production(self) -> str:
        seal = os.path.join(self.prod_dir, "SEALED")
        atomic_write(
            seal,
            {"sealed_at": now_ts(), "reason": "演练期间切断正式出口"},
        )
        return seal

    def unseal_production(self) -> None:
        seal = os.path.join(self.prod_dir, "SEALED")
        if os.path.exists(seal):
            os.unlink(seal)

    # ---- 快照与替身柜 -----------------------------------------------
    def take_baseline(self, prod_cabinet: Cabinet) -> dict:
        snap = prod_cabinet.snapshot()
        atomic_write(self.baseline_path, snap)
        return snap

    def build_shadow(self, shadow_name: str,
                     cabinet_snapshot: dict | None = None) -> Cabinet:
        snap = cabinet_snapshot or read_json(self.baseline_path)
        return Cabinet.from_snapshot(self.data_root, shadow_name, snap)

    # ---- 证据封存 ---------------------------------------------------
    def import_evidence(self, source_jobs_dir: str, job_id: str) -> dict:
        """把正式作业回条复制为只读历史证据，并出哈希清单。"""
        src_job = os.path.join(source_jobs_dir, job_id)
        dst_job = ensure_dir(os.path.join(self.evidence_dir, job_id))
        manifest = self._load_manifest()
        entries = manifest.setdefault("entries", [])
        for root_dir, _dirs, files in os.walk(src_job):
            for fname in files:
                src = os.path.join(root_dir, fname)
                rel = os.path.relpath(src, src_job)
                dst = os.path.join(dst_job, rel)
                ensure_dir(os.path.dirname(dst))
                shutil.copy2(src, dst)
                os.chmod(dst, 0o444)
                entries.append(
                    {
                        "source_job": job_id,
                        "path": os.path.join(job_id, rel),
                        "sha256": sha256_file(dst),
                        "role": "历史见证（只读），不得作为任何新作业的初态或回条",
                        "imported_at": now_ts(),
                    }
                )
        manifest["updated_at"] = now_ts()
        # 清单本身保持可写以容纳后续批次证据；清单内登记的证据文件一律 0444。
        atomic_write(self.manifest_path, manifest)
        return manifest

    def _load_manifest(self) -> dict:
        if os.path.exists(self.manifest_path):
            return read_json(self.manifest_path)
        return {"created_at": now_ts(), "entries": []}

    def verify_evidence(self) -> list[str]:
        """重算证据清单里的文件哈希，发现任何复制物被改动即报警。"""
        problems = []
        manifest = self._load_manifest()
        for e in manifest.get("entries", []):
            path = os.path.join(self.evidence_dir, e["path"])
            if not os.path.exists(path):
                problems.append(f"证据缺失：{e['path']}")
                continue
            if sha256_file(path) != e["sha256"]:
                problems.append(f"证据哈希不符（疑似被改写）：{e['path']}")
        return problems

    # ---- 初态声明校验（防止旧回条冒充本单初态） ---------------------
    def check_claim(self, order: dict, source_receipt: dict | None,
                    snapshot: dict | None = None) -> None:
        snap = snapshot or read_json(self.baseline_path)

        # 声明一：来源凭据必须与本工单同单同件；移到另一正式单号即不成立。
        if source_receipt is not None:
            if source_receipt.get("order_id") != order["order_id"]:
                raise ClaimError(
                    f"来源回条属于工单 {source_receipt.get('order_id')}，"
                    f"不能充当新单 {order['order_id']} 的初态凭据"
                )
            if source_receipt.get("item_id") != order["item_id"]:
                raise ClaimError("来源回条物件与本工单不一致")
            if source_receipt.get("domain") != "prod":
                raise ClaimError("只有正式域历史回条可作历史见证")

        # 声明二：快照里该件必须在柜；已被取走的件不能凭复制回条“复原”为在柜。
        item = snap["items"].get(order["item_id"])
        if item is None:
            raise ClaimError(f"快照中不存在物件 {order['item_id']}")
        if item["status"] != "IN":
            raise ClaimError(
                f"快照显示物件 {order['item_id']} 状态为 {item['status']}，"
                "历史回条不能把它复原成在柜初态"
            )

        # 声明三：合法接收者以替身柜当前配属为准；换组后原组声明即不成立。
        shadow_item = self._shadow_item(order["cabinet_shadow"], order["item_id"])
        if shadow_item is not None and \
                shadow_item["allowed_receiver"] != order["receiver"]:
            raise ClaimError(
                f"工单声明接收者 {order['receiver']} 与演练柜配属的"
                f" {shadow_item['allowed_receiver']} 不一致"
            )

    def _shadow_item(self, shadow_name: str, item_id: str) -> dict | None:
        state_path = os.path.join(self.drill_dir, shadow_name, "state.json")
        if not os.path.exists(state_path):
            return None
        return read_json(state_path)["items"].get(item_id)

    # ---- 演练作业 ---------------------------------------------------
    def start_drill_job(self, drill_jobs_dir: str, job_id: str, order: dict,
                        router: Router,
                        source_receipt: dict | None = None) -> Runner:
        self.check_claim(order, source_receipt)
        return Runner(
            self.data_root, drill_jobs_dir, job_id, order, "drill", router
        )

    def counter_snapshot(self) -> dict:
        """正式柜 vs 演练柜的序号与已发件计数对照。"""
        out = {"prod": {}, "drill": {}}
        for domain in ("prod", "drill"):
            base = os.path.join(self.data_root, domain)
            if not os.path.isdir(base):
                continue
            for name in sorted(os.listdir(base)):
                state_path = os.path.join(base, name, "state.json")
                if not os.path.exists(state_path):
                    continue
                state = read_json(state_path)
                if state.get("kind") != "cabinet":
                    continue
                out[domain][name] = {
                    "seq": state["seq"],
                    "dispatched": state["dispatched"],
                }
        return out
