# -*- coding: utf-8 -*-
"""
desk.py —— 换机服务台（受托档案柜）。

柜面规则（对应题意）：
  1. 柜外只能"申请开件"，得到封件 + 回执；柜台不持有任何明文密钥，读不出正文。
  2. 已签出的遗忘承诺（tombstone，由终端签章）覆盖的序号，一律拒开，
     且承诺成立后再往该段存入封件也拒收——承诺不可撤回。
  3. 开件/领件全部入流水账；同一申请第二次来，只查回原交接记录，不重复出库。
  4. 已读回条不产生任何正文档案：柜台只登记回执存在，绝不据此补写信件。
  5. 归档范围在会话建立时登记（EPHEMERAL / ARCHIVE_ALL），事后不可改。
"""
from __future__ import annotations

import os
import time

from . import codec, crypto


class DeskError(Exception):
    """柜面业务异常基类。"""


class UnknownItemError(DeskError):
    """柜内无此封件/包裹。"""


class ForgottenError(DeskError):
    """该段状态已由合法遗忘承诺销毁，任何手续都不能再开。"""


class ServiceDesk:
    def __init__(self, desk_dir: str):
        self.dir = desk_dir
        os.makedirs(desk_dir, exist_ok=True)
        self.state_path = os.path.join(desk_dir, "desk.json")
        self.log_path = os.path.join(desk_dir, "events.log")
        if os.path.exists(self.state_path):
            self.state = codec.from_json(open(self.state_path, "rb").read())
        else:
            self.state = {
                "terminals": {},      # term_id -> {vk, sessions:[...]}
                "sessions": {},       # sid -> {peers, policy, created_at}
                "letters": {},        # "sid/seq" -> {blob, kind}
                "tombstones": {},     # sid -> [ {term, ranges, counter, sig} ]
                "parcels": {},        # parcel_id -> dict
                "ledger": {},         # request_key -> 交接记录
                "receipts": [],       # 已读回条登记（仅登记，不含正文）
            }
            self._flush()

    # ---------- 持久化与审计日志 ----------

    def _flush(self) -> None:
        tmp = self.state_path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(codec.to_json(self.state))
        os.replace(tmp, self.state_path)

    def _audit(self, event: str, **fields) -> None:
        line = "%s | %s | %s" % (
            time.strftime("%Y-%m-%d %H:%M:%S"), event,
            " ".join("%s=%s" % (k, v) for k, v in sorted(fields.items())))
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    # ---------- 开户/会话登记 ----------

    def register_terminal(self, term_id: str, verify_key: bytes) -> None:
        if term_id in self.state["terminals"]:
            old = self.state["terminals"][term_id]["vk"]
            if old != codec.b64e(verify_key):
                # 新机重开同名身份：更新验证公钥并留痕，旧签章仍可凭审计留档核验
                self.state["terminals"][term_id]["vk"] = codec.b64e(verify_key)
                self._audit("TERMINAL_KEY_ROTATED", term=term_id, old_vk=old[:12])
                self._flush()
            return
        self.state["terminals"][term_id] = {"vk": codec.b64e(verify_key), "sessions": []}
        self._audit("REGISTER_TERMINAL", term=term_id)
        self._flush()

    def register_session(self, sid: str, peers: list[str], policy: str) -> None:
        if sid in self.state["sessions"]:
            return
        assert policy in ("EPHEMERAL", "ARCHIVE_ALL")
        self.state["sessions"][sid] = {"peers": sorted(peers), "policy": policy,
                                       "created_at": time.time()}
        for p in peers:
            self.state["terminals"][p]["sessions"].append(sid)
        self._audit("REGISTER_SESSION", sid=sid, peers=",".join(sorted(peers)), policy=policy)
        self._flush()

    def _require_member(self, sid: str, term_id: str) -> None:
        sess = self.state["sessions"].get(sid)
        if sess is None:
            raise UnknownItemError("柜内无此会话：%s" % sid)
        if term_id not in sess["peers"]:
            raise DeskError("终端 %s 不是会话 %s 的成员，无权申请开件" % (term_id, sid))

    # ---------- 封件存取 ----------

    @staticmethod
    def _lkey(sid: str, seq: int) -> str:
        return "%s/%d" % (sid, seq)

    def _is_forgotten(self, sid: str, seq: int) -> bool:
        for tomb in self.state["tombstones"].get(sid, []):
            for lo, hi in tomb["ranges"]:
                if lo <= seq <= hi:
                    return True
        return False

    def deposit_letter(self, sid: str, seq: int, blob: bytes, kind: str) -> None:
        """存入一封密封信（发送端归档件 / 初收另封归档件）。

        遗忘承诺成立后，该段拒绝再存——不能事后把已销毁状态对应的封件塞回柜里。
        """
        if sid not in self.state["sessions"]:
            raise UnknownItemError("柜内无此会话：%s" % sid)
        if self._is_forgotten(sid, seq):
            raise ForgottenError("序号 %d 已在遗忘承诺范围内，柜方拒收新封件" % seq)
        key = self._lkey(sid, seq)
        if key in self.state["letters"]:
            # 同号重复投递：幂等忽略，不覆盖原件
            self._audit("DEPOSIT_DUP_IGNORED", sid=sid, seq=seq, kind=kind)
            return
        self.state["letters"][key] = {"blob": codec.b64e(blob), "kind": kind}
        self._audit("DEPOSIT_LETTER", sid=sid, seq=seq, kind=kind)
        self._flush()

    def open_letter(self, sid: str, seq: int, requester: str) -> tuple[bytes, dict]:
        """申请开件：返回 (封件, 回执)。重复申请只查回原交接。"""
        self._require_member(sid, requester)
        rkey = "letter:%s:%d:%s" % (sid, seq, requester)
        if rkey in self.state["ledger"]:
            rec = self.state["ledger"][rkey]
            self._audit("OPEN_DUPLICATE_HANDOVER", sid=sid, seq=seq, requester=requester,
                        receipt=rec["receipt_id"])
            return codec.b64d(self.state["letters"][self._lkey(sid, seq)]["blob"]), rec
        if self._is_forgotten(sid, seq):
            self._audit("OPEN_DENIED_FORGOTTEN", sid=sid, seq=seq, requester=requester)
            raise ForgottenError("序号 %d 的状态已销毁，缺号范围不可再开" % seq)
        rec = self.state["letters"].get(self._lkey(sid, seq))
        if rec is None:
            self._audit("OPEN_DENIED_MISSING", sid=sid, seq=seq, requester=requester)
            raise UnknownItemError("柜内没有会话 %s 的 %d 号封件" % (sid, seq))
        receipt = {
            "receipt_id": crypto.random_bytes(8).hex(),
            "sid": sid, "seq": seq, "requester": requester,
            "kind": rec["kind"], "at": time.time(),
        }
        self.state["ledger"][rkey] = receipt
        self._audit("OPEN_LETTER", sid=sid, seq=seq, requester=requester,
                    kind=rec["kind"], receipt=receipt["receipt_id"])
        self._flush()
        return codec.b64d(rec["blob"]), receipt

    # ---------- 遗忘承诺（已签出的销毁承诺） ----------

    def deposit_tombstone(self, tomb: dict) -> None:
        """核验终端签章后登记遗忘承诺。签章不符一律拒收。"""
        sid = tomb["sid"]
        signer = tomb["term"]
        vk = codec.b64d(self.state["terminals"][signer]["vk"])
        body = codec.to_json({
            "sid": sid, "term": signer, "ranges": tomb["ranges"],
            "counter": tomb["counter"],
        })
        if not crypto.verify(vk, body, codec.b64d(tomb["sig"])):
            raise DeskError("遗忘承诺签章不符，柜方拒绝登记")
        self.state["tombstones"].setdefault(sid, []).append(tomb)
        self._audit("TOMBSTONE_ACCEPTED", sid=sid, term=signer,
                    ranges=tomb["ranges"], counter=tomb["counter"])
        self._flush()

    def tombstone_ranges(self, sid: str) -> list[list[int]]:
        out = []
        for tomb in self.state["tombstones"].get(sid, []):
            out.extend(tomb["ranges"])
        return out

    # ---------- 已读回条：只登记，不产生正文 ----------

    def register_read_receipt(self, sid: str, seq: int, reader: str, proof: bytes) -> None:
        self.state["receipts"].append(
            {"sid": sid, "seq": seq, "reader": reader,
             "proof_fp": crypto.fingerprint(proof), "at": time.time()})
        self._audit("READ_RECEIPT_LOGGED", sid=sid, seq=seq, reader=reader)
        self._flush()

    # ---------- 换机包裹（setup 归档 / continuation 接续） ----------

    def deposit_parcel(self, parcel: dict) -> None:
        pid = parcel["parcel_id"]
        if pid in self.state["parcels"]:
            return
        self.state["parcels"][pid] = parcel
        self._audit("DEPOSIT_PARCEL", parcel=pid, sid=parcel["sid"],
                    kind=parcel["kind"], target=parcel["target"])
        self._flush()

    def checkout_parcel(self, parcel_id: str, requester: str) -> tuple[dict, dict]:
        parcel = self.state["parcels"].get(parcel_id)
        if parcel is None:
            raise UnknownItemError("柜内无包裹：%s" % parcel_id)
        self._require_member(parcel["sid"], requester)
        rkey = "parcel:%s:%s" % (parcel_id, requester)
        if rkey in self.state["ledger"]:
            rec = self.state["ledger"][rkey]
            self._audit("PARCEL_DUPLICATE_HANDOVER", parcel=parcel_id,
                        requester=requester, receipt=rec["receipt_id"])
            return parcel, rec
        receipt = {"receipt_id": crypto.random_bytes(8).hex(),
                   "parcel_id": parcel_id, "requester": requester, "at": time.time()}
        self.state["ledger"][rkey] = receipt
        self._audit("CHECKOUT_PARCEL", parcel=parcel_id, requester=requester,
                    receipt=receipt["receipt_id"])
        self._flush()
        return parcel, receipt

    def has_letter(self, sid: str, seq: int) -> bool:
        return self._lkey(sid, seq) in self.state["letters"]

    def restore_letter(self, sid: str, seq: int, blob: bytes, kind: str,
                       requester: str) -> None:
        """双机在场恢复手续：用合法开件材料重封的正确原件替换被调包封件。

        旧交接流水作废（同一申请下次重新出库、重新给回执），全程留痕；
        遗忘承诺范围内同样拒绝恢复。
        """
        self._require_member(sid, requester)
        if self._is_forgotten(sid, seq):
            raise ForgottenError("序号 %d 已在遗忘承诺范围内，不能恢复入柜" % seq)
        self.state["letters"][self._lkey(sid, seq)] = {
            "blob": codec.b64e(blob), "kind": kind}
        rkey = "letter:%s:%d:%s" % (sid, seq, requester)
        self.state["ledger"].pop(rkey, None)
        self._audit("RESTORE_LETTER", sid=sid, seq=seq, requester=requester, kind=kind)
        self._flush()
