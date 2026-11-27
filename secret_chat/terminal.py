# -*- coding: utf-8 -*-
"""
terminal.py —— 本地密谈终端。

四件分开的事（各自有独立方法与记录）：
  send      发信：棘轮前推，产出密封信套
  receive   收信：为乱序来信保留"尚缺的开件位置"（窗口内按号缓存 mk）
  mark_read 已读：只出回条（mk 指纹证明），回条不含正文
  archive   归档：初收逐件另封，交受托档案柜；归档范围建会话时定死

遗忘承诺（tombstone）：某段缺号补齐/放弃后，对应 mk 与本地明文一起销毁，
终端用自己的签章私钥签出承诺交柜；此后本机、服务台、拿到账户材料的人都
无法再开这段信。
"""
from __future__ import annotations

import os
from typing import Optional

from . import codec, crypto
from .desk import ForgottenError, ServiceDesk

WINDOW_DEFAULT = 16  # 乱序窗口：最多为尚缺来信保留多少个开件位置


class TerminalError(Exception):
    pass


class Terminal:
    def __init__(self, term_id: str, home_dir: str, desk: ServiceDesk,
                 window: int = WINDOW_DEFAULT):
        self.id = term_id
        self.home = home_dir
        self.desk = desk
        self.window = window
        os.makedirs(self.home, exist_ok=True)
        self._sk_path = os.path.join(self.home, "signing.key")
        self._sess_path = os.path.join(self.home, "sessions.json")
        self._inbox_path = os.path.join(self.home, "inbox.json")
        self._outbox_path = os.path.join(self.home, "outbox.json")
        self._forgotten_path = os.path.join(self.home, "forgotten.json")

        if os.path.exists(self._sk_path):
            raw = open(self._sk_path, "rb").read()
            self.sk, self.vk = codec.b64d(raw[:44]), codec.b64d(raw[44:])
        else:
            self.sk, self.vk = crypto.new_signing_keypair()
            with open(self._sk_path, "wb") as f:
                f.write(codec.b64e(self.sk).encode() + codec.b64e(self.vk).encode())
            self.desk.register_terminal(term_id, self.vk)

        self.sessions = self._load(self._sess_path, {})
        self.inbox = self._load(self._inbox_path, {})     # sid -> {seq: 明文}
        self.outbox = self._load(self._outbox_path, {})   # sid -> {seq: 明文}
        self.forgotten = self._load(self._forgotten_path, {})  # sid -> [[lo,hi],...]

    # ---------- 本地持久化 ----------

    def _load(self, path: str, default):
        if os.path.exists(path):
            return codec.from_json(open(path, "rb").read())
        return default

    def _save(self, path: str, obj) -> None:
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(codec.to_json(obj))
        os.replace(tmp, path)

    def _persist(self) -> None:
        self._save(self._sess_path, self.sessions)
        self._save(self._inbox_path, self.inbox)
        self._save(self._outbox_path, self.outbox)
        self._save(self._forgotten_path, self.forgotten)

    def session(self, sid: str) -> dict:
        if sid not in self.sessions:
            raise TerminalError("本机没有会话 %s，先建会话或办换机导入" % sid)
        return self.sessions[sid]

    # ---------- 建会话（成对初始化，两条方向链各自独立） ----------

    def create_session(self, other: "Terminal", sid: str, policy: str,
                       sheets_dir: str) -> None:
        assert policy in ("EPHEMERAL", "ARCHIVE_ALL")
        # 两条方向链：谁发信谁沿自己的链前推，对端持有对应的接收链
        ck_ab = crypto.random_bytes(32)
        ck_ba = crypto.random_bytes(32)
        ark = crypto.random_bytes(32)  # 归档恢复密钥（恢复手续，不进账户目录）

        for term, own_ck, peer_ck in ((self, ck_ab, ck_ba), (other, ck_ba, ck_ab)):
            state = {
                "sid": sid,
                "policy": policy,
                "peers": sorted([self.id, other.id]),
                "send_ck": codec.b64e(own_ck),
                "send_seq": 0,
                "recv": {
                    "next_ck": codec.b64e(peer_ck),
                    "next_seq": 0,
                    "mks": {},        # seq -> b64(mk)，尚缺/待开的开件位置
                    "tomb_counter": 0,
                },
                "ark_wrap": codec.b64e(crypto.kdf(ark, b"ark-wrap-%s" % sid.encode())),
            }
            term.sessions[sid] = state
            term.inbox[sid] = {}
            term.outbox[sid] = {}
            term.forgotten[sid] = []
            term._persist()
            term.desk.register_session(sid, [self.id, other.id], policy)

            # 服务台事先承诺归档的东西：只有 ARK 能开的初始状态封件
            ark_share = crypto.kdf(ark, b"ark-share-%s" % sid.encode())
            inner = codec.to_json(state)
            sealed = crypto.aead_seal(
                crypto.kdf(ark_share, b"setup"), inner,
                aad=codec.to_json({"sid": sid, "target": term.id, "kind": "setup"}))
            parcel = {
                "parcel_id": "setup:%s:%s" % (sid, term.id),
                "sid": sid, "target": term.id, "kind": "setup",
                "policy": policy, "sealed": codec.b64e(sealed),
            }
            term.desk.deposit_parcel(parcel)

            # 恢复手续单：ARK 分片，写在设备目录之外（题内程序自己生成的材料）
            os.makedirs(sheets_dir, exist_ok=True)
            sheet = os.path.join(sheets_dir, "recovery_%s_%s.txt" % (sid, term.id))
            with open(sheet, "w", encoding="utf-8") as f:
                f.write("# 换机恢复手续单（会话 %s / 终端 %s）\n" % (sid, term.id))
                f.write("# 归档策略：%s；凭此单可开柜内 setup 包裹\n" % policy)
                f.write("ARK_SHARE=%s\n" % codec.b64e(ark_share))
        other._persist()

    # ---------- 发信 ----------

    def send(self, sid: str, body: str) -> dict:
        st = self.session(sid)
        ck = codec.b64d(st["send_ck"])
        mk, nxt = crypto.derive_chain_keys(ck)
        seq = st["send_seq"]
        ct = crypto.aead_seal(mk, body.encode("utf-8"),
                              aad=codec.to_json({"sid": sid, "seq": seq, "from": self.id}))
        st["send_ck"] = codec.b64e(nxt)
        st["send_seq"] = seq + 1
        envelope = {"sid": sid, "seq": seq, "from": self.id, "ct": codec.b64e(ct)}

        self.outbox.setdefault(sid, {})[str(seq)] = body
        self._persist()

        # 归档范围 ARCHIVE_ALL：发送时逐件另封一份交柜（发件人持有的合法开件材料）
        if st["policy"] == "ARCHIVE_ALL":
            ark_wrap = codec.b64d(st["ark_wrap"])
            sealed = crypto.aead_seal(
                ark_wrap, codec.to_json({"seq": seq, "body": body, "from": self.id}),
                aad=codec.to_json({"sid": sid, "seq": seq, "kind": "sender-archive"}))
            self.desk.deposit_letter(sid, seq, sealed, "sender-archive")
        return envelope

    # ---------- 收信（乱序窗口在 recv.mks 里） ----------

    def receive(self, envelope: dict) -> Optional[str]:
        sid, seq, sender = envelope["sid"], envelope["seq"], envelope["from"]
        st = self.session(sid)
        if self._seq_forgotten(sid, seq):
            raise ForgottenError("序号 %d 已签遗忘承诺，本机拒绝再处理" % seq)
        recv = st["recv"]

        # 重放：已经开过的信不重复开
        if str(seq) in self.inbox.get(sid, {}):
            return self.inbox[sid][str(seq)]

        # 已越过链前沿：mk 此前已派生，在窗口缓存里就还能开，否则状态已销毁
        if seq < recv["next_seq"]:
            if str(seq) not in recv["mks"]:
                raise ForgottenError("序号 %d 的开件状态已不存在" % seq)
        else:
            # 乱序窗口：本号相对链前沿的跳号距离，不得超过窗口
            gap = seq - recv["next_seq"]
            if gap > self.window:
                raise TerminalError(
                    "来信 %d 超出乱序窗口（链前沿 %d，窗口 %d）；先补齐缺号或扩大窗口"
                    % (seq, recv["next_seq"], self.window))
            # 沿接收链把缺号的开件位置逐个保留在窗口里（尚缺位置只存 mk）
            ck = codec.b64d(recv["next_ck"])
            for i in range(recv["next_seq"], seq + 1):
                mk, ck = crypto.derive_chain_keys(ck)
                recv["mks"][str(i)] = codec.b64e(mk)
            recv["next_ck"] = codec.b64e(ck)
            recv["next_seq"] = seq + 1  # 链派生前沿；不等于"连续已开"

        mk = codec.b64d(recv["mks"].pop(str(seq)))
        try:
            plain = crypto.aead_open(
                mk, codec.b64d(envelope["ct"]),
                aad=codec.to_json({"sid": sid, "seq": seq, "from": sender})
            ).decode("utf-8")
        except ValueError as e:
            recv["mks"][str(seq)] = codec.b64e(mk)  # 开启失败，状态原样保留
            raise TerminalError(str(e))

        self.inbox.setdefault(sid, {})[str(seq)] = plain
        # 回条证明 = H(mk, "read-proof")：只证明该状态曾开启此号信，
        # 由证明反推不出 mk，更反推不出正文
        recv.setdefault("read_proofs", {})[str(seq)] = codec.b64e(
            crypto.h(mk, b"read-proof", sid.encode(), str(seq).encode()))
        self._persist()

        # 初收逐件另封归档（归档范围内，与已读完全无关）
        if st["policy"] == "ARCHIVE_ALL" and not self.desk.has_letter(sid, seq):
            ark_wrap = codec.b64d(st["ark_wrap"])
            sealed = crypto.aead_seal(
                ark_wrap, codec.to_json({"seq": seq, "body": plain, "from": sender}),
                aad=codec.to_json({"sid": sid, "seq": seq, "kind": "recv-archive"}))
            self.desk.deposit_letter(sid, seq, sealed, "recv-archive")
        return plain

    # ---------- 已读：只出回条，回条不含正文 ----------

    def mark_read(self, sid: str, seq: int) -> dict:
        if str(seq) not in self.inbox.get(sid, {}):
            raise TerminalError("序号 %d 尚未在本机开出，不能签已读" % seq)
        st = self.session(sid)
        proofs = st["recv"].get("read_proofs", {})
        if str(seq) not in proofs:
            raise TerminalError("缺少该号信的开件证明，无法签已读（不能凭回条补正文）")
        proof = codec.b64d(proofs[str(seq)])
        receipt = {"sid": sid, "seq": seq, "reader": self.id,
                  "proof": codec.b64e(proof)}
        self.desk.register_read_receipt(sid, seq, self.id, proof)
        return receipt

    # ---------- 遗忘承诺 ----------

    def _seq_forgotten(self, sid: str, seq: int) -> bool:
        return any(lo <= seq <= hi for lo, hi in self.forgotten.get(sid, []))

    def forget_range(self, sid: str, lo: int, hi: int) -> dict:
        """把 [lo, hi] 段的开件状态与本地明文销毁，并向柜方签出遗忘承诺。"""
        st = self.session(sid)
        recv = st["recv"]
        done = set()
        for r0, r1 in self.forgotten.get(sid, []):
            done.update(range(r0, r1 + 1))
        for i in range(lo, hi + 1):
            if i in done:
                raise TerminalError("序号 %d 此前已签遗忘承诺，不可重复签出" % i)
        ranges = [[lo, hi]]

        # 1) 销毁本机持有的开件状态与回条证明
        for i in range(lo, hi + 1):
            recv["mks"].pop(str(i), None)
            recv.get("read_proofs", {}).pop(str(i), None)
            self.inbox.get(sid, {}).pop(str(i), None)
        # 2) 若放弃段包含链前沿，链根一并销毁，这段永远无法再派生
        if recv["next_seq"] <= hi:
            recv["next_ck"] = codec.b64e(crypto.random_bytes(32))
            recv["next_seq"] = hi + 1
        self.forgotten.setdefault(sid, []).extend(ranges)

        # 3) 签章承诺交柜，柜方据此拒开、拒存
        recv["tomb_counter"] += 1
        body = codec.to_json({"sid": sid, "term": self.id,
                              "ranges": ranges, "counter": recv["tomb_counter"]})
        tomb = {"sid": sid, "term": self.id, "ranges": ranges,
                "counter": recv["tomb_counter"],
                "sig": codec.b64e(crypto.sign(self.sk, body))}
        self.desk.deposit_tombstone(tomb)
        self._persist()
        return tomb

    def forget_prefix(self, sid: str, hi: int) -> dict:
        """从头起销毁 [0, hi]（自动跳过已承诺段）。"""
        start = (max((r[1] for r in self.forgotten.get(sid, [])), default=-1) + 1)
        if hi < start:
            raise TerminalError("该段此前已签遗忘承诺，不可重复签出")
        return self.forget_range(sid, start, hi)

    def pending_gaps(self, sid: str) -> list[int]:
        """窗口里还留着开件位置、却尚未收到的号。"""
        st = self.session(sid)
        recv = st["recv"]
        return sorted(int(k) for k in recv["mks"].keys())

    # ---------- 换机：导出/清机/导入 ----------

    def export_continuation(self, sid: str) -> tuple[str, str]:
        """导出接续包裹（migration key 一次性带外交给新机）。返回 (parcel_id, key)。

        先清机场景下窗口里仍缺的号：
          ARCHIVE_ALL —— 先签遗忘承诺（柜方同步拒开拒存），再导出；
          EPHEMERAL   —— 本地逐格派过并销毁 mk，无柜内封件可补，旧信自然读不回。
        接收链根保留，对端按原链发的新号信仍能对上。
        """
        st = self.session(sid)
        recv = st["recv"]
        gaps = sorted(int(k) for k in recv["mks"].keys())
        if gaps:
            if st["policy"] == "ARCHIVE_ALL":
                # 归档会话：缺号在柜内可能有合法封件；主动放弃即签遗忘承诺，
                # 柜方同步把该段标为拒开拒存
                self.forget_prefix(sid, max(gaps))
            else:
                # 临时会话：窗口里的缺号 mk 直接销毁，没有任何柜内封件可补
                recv["mks"] = {}
        parcel_id = "cont:%s:%s:%d" % (sid, self.id, recv["tomb_counter"])
        mkey = crypto.random_bytes(32)
        # 接续包裹只搬密钥状态，不夹任何明文：
        #   EPHEMERAL 清机后旧话彻底读不回；
        #   ARCHIVE_ALL 的旧话只在柜内，凭恢复手续逐件开。
        bundle = {"state": st, "sk": codec.b64e(self.sk),
                  "inbox": {}, "outbox": {},
                  "forgotten": self.forgotten.get(sid, [])}
        sealed = crypto.aead_seal(
            mkey, codec.to_json(bundle),
            aad=codec.to_json({"sid": sid, "target": self.id, "kind": "continuation"}))
        self.desk.deposit_parcel({
            "parcel_id": parcel_id, "sid": sid, "target": self.id,
            "kind": "continuation", "policy": st["policy"],
            "sealed": codec.b64e(sealed)})
        self._persist()
        return parcel_id, codec.b64e(mkey)

    def export_rehandshake(self, sid: str, peer: "Terminal") -> tuple[str, str]:
        """setup 归档恢复专用：双方在场，换一对全新方向链。

        新链材料用各自 ark_wrap 封成包裹交柜，只有会话成员能开；
        旧链与旧信状态不动，新通信从 0 号重新起算。
        """
        st = self.session(sid)
        new_ck_me = crypto.random_bytes(32)   # 我今后的发送链
        new_ck_peer = crypto.random_bytes(32)  # 对端今后的发送链
        ids = []
        for term, my_send, peer_send in ((self, new_ck_me, new_ck_peer),
                                         (peer, new_ck_peer, new_ck_me)):
            tst = term.session(sid)
            payload = {"send_ck": codec.b64e(my_send), "send_seq": 0,
                       "recv_next_ck": codec.b64e(peer_send), "recv_next_seq": 0}
            sealed = crypto.aead_seal(
                codec.b64d(tst["ark_wrap"]), codec.to_json(payload),
                aad=codec.to_json({"sid": sid, "target": term.id,
                                   "kind": "rehandshake"}))
            pid = "rehs:%s:%s" % (sid, term.id)
            term.desk.deposit_parcel({"parcel_id": pid, "sid": sid,
                                      "target": term.id, "kind": "rehandshake",
                                      "policy": tst["policy"],
                                      "sealed": codec.b64e(sealed)})
            ids.append(pid)
        return ids[0], ids[1]

    def accept_rehandshake(self, sid: str) -> None:
        """从柜面取回再握手包裹，装入全新方向链。"""
        st = self.session(sid)
        parcel, _ = self.desk.checkout_parcel("rehs:%s:%s" % (sid, self.id), self.id)
        payload = codec.from_json(crypto.aead_open(
            codec.b64d(st["ark_wrap"]), codec.b64d(parcel["sealed"]),
            aad=codec.to_json({"sid": sid, "target": self.id,
                               "kind": "rehandshake"})))
        st["send_ck"] = payload["send_ck"]
        st["send_seq"] = payload["send_seq"]
        st["recv"] = {"next_ck": payload["recv_next_ck"], "next_seq": 0,
                      "mks": {}, "tomb_counter": st["recv"]["tomb_counter"],
                      "read_proofs": {}}
        self.inbox[sid] = {}
        self.outbox[sid] = {}
        self._persist()

    def wipe(self) -> None:
        """旧机交还清空：全部本地密钥与明文不可恢复地删除。"""
        for p in (self._sk_path, self._sess_path, self._inbox_path,
                  self._outbox_path, self._forgotten_path):
            if os.path.exists(p):
                os.remove(p)
        self.sessions, self.inbox, self.outbox, self.forgotten = {}, {}, {}, {}

    def import_from_desk(self, sid: str, *, migration_key: Optional[str] = None,
                         ark_share: Optional[str] = None,
                         continuation_id: Optional[str] = None) -> dict:
        """新机从服务台取包裹恢复。两条手续：
           A) continuation + migration_key：接续当前活状态（新通信不断）
           B) setup + ark_share：凭恢复手续单还原归档会话
           已签遗忘承诺的段，任何手续都还原不出明文。
        """
        if sid in self.sessions:
            raise TerminalError("新机已有会话 %s，拒绝覆盖导入" % sid)

        bundle = None
        if continuation_id is not None or migration_key is not None:
            if not continuation_id or not migration_key:
                raise TerminalError("接续恢复需要 continuation_id 与 migration_key 成对提供")
            parcel, receipt = self.desk.checkout_parcel(continuation_id, self.id)
            if parcel["target"] != self.id or parcel["sid"] != sid:
                raise TerminalError("包裹归属不符，拒收")
            try:
                raw = crypto.aead_open(
                    codec.b64d(migration_key), codec.b64d(parcel["sealed"]),
                    aad=codec.to_json({"sid": sid, "target": self.id,
                                       "kind": "continuation"}))
            except ValueError:
                raise TerminalError("migration key 打不开接续包裹（AAD/密钥不符）")
            bundle = codec.from_json(raw)
        else:
            if ark_share is None:
                raise TerminalError("缺少恢复手续：需要 migration_key 或 ark_share")
            parcel, receipt = self.desk.checkout_parcel(
                "setup:%s:%s" % (sid, self.id), self.id)
            raw = crypto.aead_open(
                crypto.kdf(codec.b64d(ark_share), b"setup"),
                codec.b64d(parcel["sealed"]),
                aad=codec.to_json({"sid": sid, "target": self.id, "kind": "setup"}))
            state0 = codec.from_json(raw)
            bundle = {"state": state0, "sk": None, "inbox": {}, "outbox": {},
                      "forgotten": []}

        st = bundle["state"]
        if st["sid"] != sid:
            raise TerminalError("封件属于另一段会话，不能补入当段")
        self.sessions[sid] = st
        self.inbox[sid] = bundle.get("inbox", {})
        self.outbox[sid] = bundle.get("outbox", {})
        self.forgotten[sid] = bundle.get("forgotten", [])
        if bundle.get("sk"):
            self.sk = codec.b64d(bundle["sk"])
            self.vk = crypto.h(self.sk, b"verify-key")  # 恢复出一致的验证公钥
            with open(self._sk_path, "wb") as f:
                f.write(codec.b64e(self.sk).encode() + codec.b64e(self.vk).encode())
            self.desk.register_terminal(self.id, self.vk)
        # 柜方已登记的遗忘范围同步成本机硬边界
        for tomb in self.desk.state["tombstones"].get(sid, []):
            if tomb["term"] == self.id:
                for rng in tomb["ranges"]:
                    if rng not in self.forgotten[sid]:
                        self.forgotten[sid].append(rng)
        self._persist()
        return {"sid": sid, "policy": st["policy"],
                "forgotten": self.forgotten[sid],
                "inbox_seqs": sorted(int(k) for k in self.inbox[sid].keys())}

    # ---------- 柜面开件：取回真正能打开的归档原信 ----------

    def open_archived(self, sid: str, seq: int) -> tuple[dict, dict]:
        st = self.session(sid)
        blob, receipt = self.desk.open_letter(sid, seq, self.id)
        try:
            letter = codec.from_json(crypto.aead_open(
                codec.b64d(st["ark_wrap"]), blob,
                aad=codec.to_json({"sid": sid, "seq": seq,
                                   "kind": receipt["kind"]})))
        except ValueError:
            raise TerminalError("归档封件的会话/号与 AAD 不符，疑似被换成别段同号信")
        return letter, receipt

    def restore_archived(self, sid: str, seq: int, body: str, sender: str) -> None:
        """恢复手续：用本机仍合法持有的开件材料，重封正确原件并交柜恢复入柜。"""
        st = self.session(sid)
        kind = "recv-archive"
        sealed = crypto.aead_seal(
            codec.b64d(st["ark_wrap"]),
            codec.to_json({"seq": seq, "body": body, "from": sender}),
            aad=codec.to_json({"sid": sid, "seq": seq, "kind": kind}))
        self.desk.restore_letter(sid, seq, sealed, kind, self.id)

    # ---------- 观察接口 ----------

    def view_inbox(self, sid: str) -> dict:
        return dict(sorted(self.inbox.get(sid, {}).items(), key=lambda kv: int(kv[0])))
