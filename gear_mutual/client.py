# -*- coding: utf-8 -*-
"""客户端：只能读自己的承诺、允许的替代范围与必须公开的兑付依据。

客户不接触全库，也读不到别家合同；其信任锚只有：
- 托管席公钥（验保函/出库单/离线范围签名）
- 各寄存行公钥（验足额章收据）
- 自己的 X25519 私钥（打开密封保函）
- 独立配套图规则（本地复核装配，不信托管席的 edge_check 结论）
"""
from __future__ import annotations

from crypto import Identity, ed25519_verify, sealed_box_open
from model import canonical, digest
from escrow import compatible


class ClientView:
    def __init__(self, identity: Identity, seat_pub: bytes,
                 bank_pubs: dict):
        self.me = identity
        self.seat_pub = seat_pub
        self.bank_pubs = bank_pubs          # bank label -> ed pub bytes
        self.guarantee_doc = None
        self.sealed = None

    def open_guarantee(self, sealed_message: dict):
        """打开托管席密封送达的保函，并核对公告板锚点。"""
        plaintext = sealed_box_open(self.me, sealed_message["box"])
        import json
        doc = json.loads(plaintext.decode("utf-8"))
        if digest(doc) != sealed_message["anchor"]:
            raise ValueError("保函内容与公告板锚点不一致")
        if not ed25519_verify(
                self.seat_pub,
                canonical({"label": doc["label"], "anchor": sealed_message["anchor"]}),
                bytes.fromhex(sealed_message["seat_sig"])):
            raise ValueError("托管席锚点签名无效")
        self.guarantee_doc = doc
        self.sealed = sealed_message
        return doc

    def my_private_view(self) -> dict:
        """客户可见的全部信息：自己的承诺 + 替代范围 + 兑付依据指针。"""
        if self.guarantee_doc is None:
            raise RuntimeError("尚未打开保函")
        d = self.guarantee_doc
        return {
            "label": d["label"],
            "order_id": d["order_id"],
            "cover_status": d["cover_status"],
            "allowed_mounts": d["allowed_mounts"],
            "max_tolerance_um": d["max_tolerance_um"],
            "need_before": d["need_before"],
            "nullifier": d["nullifier"],
            "allocation_if_covered": d["allocation_if_covered"],
        }

    def verify_manifest(self, manifest: dict, order) -> dict:
        """独立核验出库单：签名、足额章、配套图装配、nullifier 一致性。"""
        report = {"seat_signature": False, "receipts": [],
                  "assembly_diagram": False, "nullifier_match": False,
                  "ok": False}
        sig = manifest.pop("seat_signature")
        base = {k: v for k, v in manifest.items()}
        report["seat_signature"] = ed25519_verify(
            self.seat_pub, canonical(base), bytes.fromhex(sig))
        manifest["seat_signature"] = sig

        for item in manifest["receipts"]:
            r = item["receipt"]
            bank_ok = r["bank"] in self.bank_pubs and ed25519_verify(
                self.bank_pubs[r["bank"]], canonical(
                    {k: v for k, v in r.items() if k != "signature"}),
                bytes.fromhex(r["signature"]))
            report["receipts"].append({"sid": r["sid"], "bank": r["bank"],
                                       "signature_ok": bank_ok})

        # 独立配套图：本地重新计算，不采信 manifest 给的 edge_check
        body = type("I", (), manifest["assembly_diagram"]["body"])()
        ring = type("I", (), manifest["assembly_diagram"]["ring"])()
        local_edge = compatible(body, ring, order)
        diagram = manifest["assembly_diagram"]
        report["assembly_diagram"] = (
            local_edge
            and diagram["body"]["sid"] == manifest["body_sid"]
            and diagram["ring"]["sid"] == manifest["ring_sid"]
            and diagram["edge_check"] is True)

        report["nullifier_match"] = (
            manifest["nullifier"] == self.guarantee_doc["nullifier"])
        report["ok"] = (report["seat_signature"]
                        and all(x["signature_ok"] for x in report["receipts"])
                        and report["assembly_diagram"]
                        and report["nullifier_match"])
        return report

    def verify_offline_scope(self, scope: dict) -> bool:
        sig = scope["seat_signature"]
        base = {k: v for k, v in scope.items() if k != "seat_signature"}
        return ed25519_verify(self.seat_pub, canonical(base),
                              bytes.fromhex(sig))
