"""证书签发：把“可证事实”和“仍须另一站作证的部分”分开写清楚。"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict

from . import crypto
from .chain import Chain, ChainError
from .models import RECEIPT_DELIVERED, RECEIPT_VERIFIED, dumps_canonical
from .store import CaseStore

CERT_LOCAL = "LOCAL_RECEIPT"          # 单站可证：某站确曾收过某页
CERT_CO = "CO_WITNESS"                # 双站共同见证
CERT_PROVISIONAL = "PROVISIONAL_CO"   # 共同见证待补
CERT_DELIVERY = "DELIVERY_ONLY"       # 仅送达未验页


class CertError(RuntimeError):
    pass


class Certifier:
    def __init__(self, store: CaseStore, chain: Chain):
        self.store = store
        self.chain = chain
        self.desk = store.desk_keypair()

    # ---------- 凭证复核 ----------
    def verify_receipt(self, r) -> None:
        pub_pem = self.store.pubkeys.get(r.key_id)
        if pub_pem is None:
            raise CertError(f"缺少 {r.station_id} 的登记公钥 {r.key_id}")
        if not crypto.verify(pub_pem.encode(), r.signed_message(),
                             bytes.fromhex(r.signature)):
            raise CertError(f"签收 {r.receipt_id} 签名无效")

    def _verified_receipt(self, station_id: str, page_digest: str):
        r = self.chain.find_receipt_for_page(
            station_id, page_digest, RECEIPT_VERIFIED)
        if r is None:
            return None
        self.verify_receipt(r)
        return r

    def _sign_cert(self, body: dict) -> dict:
        cert_id = "C-" + uuid.uuid4().hex[:12]
        body = dict(body)
        body["cert_id"] = cert_id
        body["desk_key_id"] = self.desk.key_id
        body["desk_signature"] = crypto.sign(
            self.desk.priv_pem, dumps_canonical(body)).hex()
        self.store.save_cert(cert_id, body)
        return body

    # ---------- 1. 单站本地收件证明 ----------
    def issue_local_receipt(self, station_id: str, page_digest: str,
                            purpose: str) -> dict:
        r = self._verified_receipt(station_id, page_digest)
        if r is None:
            raise CertError(
                f"{station_id} 没有该页的 VERIFIED 签收，不能出具本地收件证明")
        page = self.store.get_page(page_digest)
        try:
            self.chain.verify_page_integrity(page)
        except ChainError as e:
            raise CertError(f"本地收件证明拒绝签发：{e}") from e
        return self._sign_cert({
            "type": CERT_LOCAL,
            "purpose": purpose,
            "admitted": [
                f"{station_id} 以钥匙 {r.key_id} 对第{page.page_no}页"
                f"（摘要 {page.page_digest[:16]}…）出具过 VERIFIED 签收 {r.receipt_id}",
                "该页页内条目根与页摘要复核一致",
            ],
            "not_claimed": [
                f"本证书不证明另一见证站见过第{page.page_no}页",
                "不证明该页与其他站后续页之间存在任何全局先后顺序",
                "不排除同一前页上存在另一支分叉",
            ],
            "needs_other_station": [
                f"如需共同见证，仍须另一站对同一摘要 {page.page_digest[:16]}… "
                "出具 VERIFIED 签收",
            ],
            "facts": {
                "station_id": station_id,
                "receipt_id": r.receipt_id,
                "page_no": page.page_no,
                "page_digest": page.page_digest,
            },
        })

    # ---------- 2. 双站共同见证 ----------
    def issue_co_witness(self, station_a: str, station_b: str,
                         page_digest: str) -> dict:
        ra = self._verified_receipt(station_a, page_digest)
        rb = self._verified_receipt(station_b, page_digest)
        missing = []
        if ra is None:
            missing.append(station_a)
        if rb is None:
            missing.append(station_b)
        if missing:
            raise CertError(
                f"共同见证不能提前签发：{', '.join(missing)} "
                "尚无该页 VERIFIED 签收（DELIVERED 不算验页）")
        try:
            pages = self.chain.verify_prefix(page_digest)
        except ChainError as e:
            raise CertError(f"共同见证拒绝签发：{e}") from e
        page = pages[-1]
        return self._sign_cert({
            "type": CERT_CO,
            "admitted": [
                f"第1至第{page.page_no}页前缀完整，逐页摘要、条目根、链接关系复核通过",
                f"{station_a} 与 {station_b} 分别对同一摘要 "
                f"{page.page_digest[:16]}… 留有 VERIFIED 签收",
                f"两站在第{page.page_no}页及其之前的历史一致",
            ],
            "not_claimed": [
                f"本证书只覆盖到第{page.page_no}页；不证明其后任何页的顺序",
                "不排除该页之后出现分叉",
            ],
            "needs_other_station": [],
            "facts": {
                "page_no": page.page_no,
                "page_digest": page.page_digest,
                "receipts": {station_a: ra.receipt_id, station_b: rb.receipt_id},
                "prefix_digests": [p.page_digest for p in pages],
            },
        })

    # ---------- 3. 共同见证待补（临证） ----------
    def issue_provisional(self, station_a: str, station_b: str,
                          page_digest: str) -> dict:
        ra = self._verified_receipt(station_a, page_digest)
        rb = self._verified_receipt(station_b, page_digest)
        have, lack = [], []
        (have if ra else lack).append(station_a)
        (have if rb else lack).append(station_b)
        if not have:
            raise CertError("两站均无 VERIFIED 签收，无任何可承认事实")
        if not lack:
            raise CertError("双站签收齐备，应直接出具 CO_WITNESS")
        try:
            pages = self.chain.verify_prefix(page_digest)
            prefix_ok = f"前缀第1至第{pages[-1].page_no}页结构复核通过"
        except ChainError as e:
            prefix_ok = f"前缀尚不能完整复核：{e}"
        return self._sign_cert({
            "type": CERT_PROVISIONAL,
            "admitted": [
                f"{'、'.join(have)} 对该页留有 VERIFIED 签收（签名复核通过）",
                prefix_ok,
            ],
            "not_claimed": [
                "本证书不是共同见证，不能当作双站一致的结论使用",
                "不证明两站历史一致，也不证明全局顺序",
            ],
            "needs_other_station": [
                f"仍须 {', '.join(lack)} 对同一页摘要出具 VERIFIED 签收，"
                "方可换发 CO_WITNESS",
            ],
            "facts": {
                "page_digest": page_digest,
                "have": have,
                "lack": lack,
            },
        })

    # ---------- 4. 仅送达证明 ----------
    def issue_delivery_only(self, station_id: str, receipt_id: str) -> dict:
        r = self.store.get_receipt(receipt_id)
        if r is None or r.station_id != station_id:
            raise CertError("查无该站送达签收")
        self.verify_receipt(r)
        if r.kind != RECEIPT_DELIVERED:
            raise CertError("该签收不是 DELIVERED 类型")
        return self._sign_cert({
            "type": CERT_DELIVERY,
            "admitted": [
                f"{station_id} 在 {r.ts} 签收了包裹（签收 {r.receipt_id}，签名有效）",
                f"原始备注：{r.note}",
            ],
            "not_claimed": [
                "不证明包裹内任何页的摘要、条目根或链接关系",
                "不能用于双站共同见证",
            ],
            "needs_other_station": [
                "须待该站拆包验页并出具 VERIFIED 签收后，事实才能升级",
            ],
            "facts": {"receipt_id": r.receipt_id, "page_no": r.page_no},
        })

    # ---------- 证书复核（客户凭实际寄存材料复查） ----------
    @staticmethod
    def verify_cert(store: CaseStore, cert: dict) -> dict:
        body = {k: v for k, v in cert.items() if k != "desk_signature"}
        sig = bytes.fromhex(cert["desk_signature"])
        ok = crypto.verify(store.pubkeys[cert["desk_key_id"]].encode(),
                           dumps_canonical(body), sig)
        return {"cert_id": cert["cert_id"], "valid": ok, "type": cert["type"]}
