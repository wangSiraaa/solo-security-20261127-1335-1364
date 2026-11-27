"""柜外恢复工作台。

职责边界：
- 值班员只能指定“到场保管人/实有封套”，工作台从账本读取这些柜里的真实封套，
  不能凭空造份，也没有任何测试原钥/备用钥入口。
- 合份动作委托可信开封柜；工作台拿到卷钥后只用于逐页 HMAC 核验，不落盘、不转吐。
- 页面必须从实际卷副本取回，并同时通过：
  1) 摘要清单（sha256 内容摘要，防串卷/换页）
  2) 当前代页标签（HMAC，防旧代页顶替）
  两者全过才算“核准原页”；任一不过，该页只能记失败，不能签“全部恢复”。
- 每次恢复都生成恢复单存档，重开程序后可凭单号查回具体交给档主的页。
"""

import base64
import json
import os
import uuid
from datetime import datetime

from .crypto import page_digest, verify_page_tag
from .policy import POLICIES
from .storage import Storage


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class RecoveryError(Exception):
    pass


class Workbench:
    def __init__(self, storage: Storage, cabinet):
        self.storage = storage
        self.cabinet = cabinet
        self.ledger = storage.load_ledger()

    def _refresh(self):
        self.ledger = self.storage.load_ledger()

    def present_envelopes(self, volume_id: str, custodians: list[str]) -> list[dict]:
        """列出场保管人柜中、本卷当前代的有效封套（在途/旧代自动排除）。"""
        self._refresh()
        if volume_id not in self.ledger["volumes"]:
            raise KeyError(f"账本中没有卷 {volume_id}")
        out = []
        seen = set()
        for name in custodians:
            for env in self.cabinet.envelopes_held_by(name):
                if env["volume_id"] != volume_id:
                    continue
                if env["index"] in seen:
                    # 同名份位再次出现（例如交接异常导致两人各执同份位）：
                    # 工作台只认一只盒子，第二只不产生新材料
                    continue
                seen.add(env["index"])
                out.append(env)
        return out

    def recover(
        self, volume_id: str, custodians: list[str], force_envelope_ids: list[str] | None = None
    ) -> dict:
        """以到场材料恢复指定卷，返回并归档恢复单。"""
        self._refresh()
        vol = self.ledger["volumes"][volume_id]
        threshold = vol["threshold"]

        if force_envelope_ids is not None:
            # 演练用：显式指定提交哪些封套（仍须是账本里实有的封套）
            env_ids = list(force_envelope_ids)
        else:
            env_ids = [e["envelope_id"] for e in self.present_envelopes(volume_id, custodians)]

        # 委托开封柜柜内合份；份位不足/旧代/在途/串卷都会在此被拒，抛错即开封失败
        try:
            volume_key = self.cabinet.combine_for_recovery(volume_id, env_ids)
        except ValueError as exc:
            raise RecoveryError(f"开封失败：{exc}") from exc

        manifest = self.storage.read_manifest(volume_id)
        if manifest["generation"] != vol["generation"]:
            raise RecoveryError("清单代际与卷记录不符，中止恢复")

        delivered, failed = [], []
        for entry in manifest["pages"]:
            pn = entry["page_no"]
            page_result = self._recover_one_page(volume_id, vol["generation"], entry, volume_key)
            if page_result["verified"]:
                delivered.append(page_result)
            else:
                failed.append(page_result)

        order_id = f"ORD_{uuid.uuid4().hex[:12]}"
        order = {
            "order_id": order_id,
            "volume_id": volume_id,
            "generation": vol["generation"],
            "created_at": _now(),
            "threshold": threshold,
            "submitted_envelopes": env_ids,
            "present_custodians": custodians,
            "delivered_pages": delivered,
            "failed_pages": failed,
            "all_pages_recovered": len(failed) == 0 and len(delivered) == len(manifest["pages"]),
            "signed_statement": (
                f"卷 {volume_id} 全部 {len(manifest['pages'])} 页均已恢复并核对通过"
                if len(failed) == 0 and len(delivered) == len(manifest["pages"])
                else f"卷 {volume_id} 仅 {len(delivered)} 页核准，{len(failed)} 页未通过核对，不能签“两页均已恢复”"
            ),
        }
        self.ledger["recovery_orders"][order_id] = order
        # 与开封柜共用文件锁：锁内重读再增量写入恢复单，
        # 不会用本进程启动时的陈旧账本整体覆盖保障单/交接/其它恢复单。
        with self.storage.ledger_lock():
            latest = self.storage.load_ledger()
            latest["recovery_orders"][order_id] = order
            self.storage.save_ledger(latest)
        self.ledger = latest
        return order

    def _recover_one_page(self, volume_id: str, generation: int, entry: dict, key: bytes) -> dict:
        pn = entry["page_no"]
        replicas = self.storage.list_page_replicas(volume_id, pn)
        attempts = []
        for replica in replicas:
            bundle = self.storage.read_page_bundle(volume_id, pn, replica)
            content = bundle["content"]
            digest_ok = page_digest(volume_id, pn, content) == entry["digest"]
            # 封存页自带的标签必须能被当前代卷钥验证；旧代页标签在此必然失败
            tag_ok = verify_page_tag(key, volume_id, pn, content, bundle["tag"])
            gen_ok = bundle["generation"] == generation
            attempts.append({
                "replica": replica,
                "digest_ok": digest_ok,
                "tag_ok": tag_ok,
                "generation_ok": gen_ok,
                "bundle_generation": bundle["generation"],
            })
            if digest_ok and tag_ok and gen_ok:
                return {
                    "page_no": pn,
                    "verified": True,
                    "replica_used": replica,
                    "digest": entry["digest"],
                    "text": content.decode("utf-8"),
                    "checks": attempts,
                }
        return {
            "page_no": pn,
            "verified": False,
            "digest": entry["digest"],
            "text": None,
            "checks": attempts,
            "reason": "所有副本均未同时通过摘要清单与当前代页标签核对（疑为旧页/串卷/缺页）",
        }

    def swap_page_with_old_generation(self, volume_id: str, page_no: int) -> str:
        """演练专用：把该页的全部现存副本换成上一代封存页（旧页）。

        旧页正文可能一字未改，但封存页里的标签是旧卷钥所出、代际也是旧代，
        在当前代卷钥与清单下必然核对失败。
        """
        self._refresh()
        vol = self.ledger["volumes"][volume_id]
        old_snapshots = vol.get("old_generation_blobs", [])
        if not old_snapshots:
            raise RecoveryError("该卷没有旧代快照，先做一次补发换代才能演练旧页")
        snapshot_dir = old_snapshots[-1]
        old_path = os.path.join(snapshot_dir, f"page_{page_no}.json")
        if not os.path.exists(old_path):
            raise RecoveryError(f"旧代快照中没有第 {page_no} 页")
        with open(old_path, "r", encoding="utf-8") as f:
            old_bundle = json.load(f)
        for replica in self.storage.list_page_replicas(volume_id, page_no):
            self.storage.overwrite_page_bundle(volume_id, page_no, replica, old_bundle)
        return old_path

    def get_recovery_order(self, order_id: str) -> dict:
        """重开程序后凭恢复单查回交给档主的具体页面。"""
        self._refresh()
        if order_id not in self.ledger["recovery_orders"]:
            raise KeyError(f"恢复单 {order_id} 不存在")
        return self.ledger["recovery_orders"][order_id]

    # ---------- 态势查询（灾备演练用） ----------
    def scenario_west_offline(self, volume_id: str) -> dict:
        """西库全失联：只有东库两人到场。"""
        return self._evaluate(volume_id, present=["东甲", "东乙"], scenario="西库全失联")

    def scenario_east_escrow(self, volume_id: str) -> dict:
        """东库两只柜整体交管：代管方手里是东库全部材料。"""
        return self._evaluate(volume_id, present=["东甲", "东乙"], scenario="东库整批临时交管（代管方视角）")

    def _evaluate(self, volume_id: str, present: list[str], scenario: str) -> dict:
        policy = POLICIES[self.ledger["volumes"][volume_id]["policy"]]
        envs = self.present_envelopes(volume_id, present)
        indices = sorted(e["index"] for e in envs)
        enough = policy.can_combine(set(indices))
        return {
            "scenario": scenario,
            "volume_id": volume_id,
            "present_indices": indices,
            "threshold": policy.threshold,
            "can_open": enough,
            "conclusion": "材料足额，可恢复" if enough else f"仅 {len(indices)} 份 < 门限 {policy.threshold}，不能开卷",
        }
