# -*- coding: utf-8 -*-
"""独立托管席（escrow seat）：持有跨行对应与多方合同，按最不利的同时兑付核定覆盖。

关键规则
--------
1. 多家寄存行可能为同一件可拆套器材盖章。托管席维护私有 ``physical_map``：
   ``sid -> 物理实体标识``，把重复盖章折叠成同一件实物；公开数据只出现 sid。
2. “四家都盖了足额章”只证明每张收据自身足额，不等于两套保函能同时兑现。
   必须通过 **最不利同时兑付测试**：在同一履约日，用互不相同的物理实体，
   为全部有效保函一次性分配成功，覆盖才算成立。
3. 零件可重组，但同一件物理实体不能在同一日同时履约——由物理实体去重 +
   当日 nullifier 双重保证。
4. 别名攻击（把某机构第二张收据改成同一实物的别名）会让 distinct 实物数下降，
   托管席重新核定覆盖组并改判；恢复收据后重新核定。
5. 柜台离线时只能按托管席已签发的预留交接范围（scope bundle）办，超出范围拒办。
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from datetime import date
from itertools import combinations
from typing import Optional

from crypto import Identity, ed25519_verify, sealed_box_send, sha256
from model import (BODY, RING, BankReceipt, PairedOrder, SyntheticItem,
                   canonical, digest, verify_receipt)

# 覆盖组状态
COVERED = "covered"          # 通过最不利同时兑付核定
UNDER_COVERED = "under"      # 核定失败（distinct 实物不足或公差不匹配）
SUSPENDED = "suspended"      # 收据变动后等待重新核定


def _today_iso() -> str:
    return date.today().isoformat()


def _parse(iso: str):
    return date.fromisoformat(iso)


# ---------------------------------------------------------------------------
# 匹配引擎：最不利同时兑付
# ---------------------------------------------------------------------------

def compatible(body: SyntheticItem, ring: SyntheticItem,
               order: PairedOrder) -> bool:
    """一套可用器材的装配规则（独立配套图的同一套规则，客户端可自行复核）。"""
    if body.kind != BODY or ring.kind != RING:
        return False
    if body.tolerance_um > order.max_tolerance_um:
        return False
    # 配对转接环同样要满足订单公差上限（订单约束镜身与环两者）
    if ring.tolerance_um > order.max_tolerance_um:
        return False
    if ring.mount not in order.allowed_mounts:
        return False
    # 镜座类别必须与转接环一致，且转接环公差不能比镜身更松超过 1um
    if body.mount != ring.mount:
        return False
    if ring.tolerance_um > body.tolerance_um + 1.0:
        return False
    return True


def _bipartite_can_pair(bodies, rings, order):
    """判断给定镜身集合与转接环集合，能否按装配规则一一配对。"""
    n = len(bodies)
    if len(rings) < n:
        return False
    used = [False] * len(rings)

    def backtrack(i):
        if i == n:
            return True
        b = bodies[i]
        for j, r in enumerate(rings):
            if not used[j] and compatible(b, r, order):
                used[j] = True
                if backtrack(i + 1):
                    return True
                used[j] = False
        return False

    return backtrack(0)


def worst_case_feasible(orders, items_by_sid, physical_map, as_of=None):
    """最不利同时兑付核定。

    :param orders: 当前全部有效保函对应的订单
    :param items_by_sid: sid -> SyntheticItem（已折叠跨行重复盖章）
    :param physical_map: sid -> 物理实体标识
    :return: (是否可行, 分配方案)。分配方案把每个 order_id 映射到
             {"bodies": [sid...], "rings": [sid...], "pairs": [(b_sid,r_sid)...]}
    """
    as_of = as_of or _today_iso()
    # 先按物理实体去重：同一物理实体只保留一个 sid 候选
    seen_physical = set()
    distinct_items = []
    for sid, item in items_by_sid.items():
        phys = physical_map.get(sid, sid)
        if phys in seen_physical:
            continue
        seen_physical.add(phys)
        if _parse(item.expires_on) >= _parse(as_of):
            distinct_items.append(item)

    bodies = [i for i in distinct_items if i.kind == BODY]
    rings = [i for i in distinct_items if i.kind == RING]

    allocation = {}
    used_phys = set()

    def assign(order_idx):
        if order_idx == len(orders):
            return True
        order = orders[order_idx]
        # 该单需要 qty_assemblies 套；普通单为 1
        need = order.qty_assemblies
        free_bodies = [b for b in bodies
                       if physical_map.get(b.sid, b.sid) not in used_phys
                       and b.tolerance_um <= order.max_tolerance_um]
        free_rings = [r for r in rings
                      if physical_map.get(r.sid, r.sid) not in used_phys
                      and r.mount in order.allowed_mounts]
        # 枚举 need 只镜身组合（数据规模小，回溯足够；规模大可换最大流）
        for body_combo in combinations(free_bodies, need):
            body_phys = {physical_map.get(b.sid, b.sid) for b in body_combo}
            candidate_rings = [r for r in free_rings
                               if physical_map.get(r.sid, r.sid) not in body_phys]
            # 在这些镜身上尝试一一配对
            if not _bipartite_can_pair(list(body_combo), candidate_rings, order):
                continue
            # 取出一份具体配对
            pairs = _concrete_pairs(list(body_combo), candidate_rings, order)
            ring_phys = {physical_map.get(r.sid, r.sid) for _, r in pairs}
            if body_phys & ring_phys:
                continue
            used_phys.update(body_phys)
            used_phys.update(ring_phys)
            allocation[order.order_id] = {
                "bodies": [b.sid for b in body_combo],
                "rings": [r.sid for _, r in pairs],
                "pairs": [(b.sid, r.sid) for b, r in pairs],
            }
            if assign(order_idx + 1):
                return True
            for p in body_phys | ring_phys:
                used_phys.discard(p)
            allocation.pop(order.order_id, None)
        return False

    ok = assign(0)
    return ok, (allocation if ok else {})


def _concrete_pairs(bodies, rings, order):
    used = [False] * len(rings)
    pairs = []

    def bt(i):
        if i == len(bodies):
            return True
        b = bodies[i]
        for j, r in enumerate(rings):
            if not used[j] and compatible(b, r, order):
                used[j] = True
                pairs.append((b, r))
                if bt(i + 1):
                    return True
                pairs.pop()
                used[j] = False
        return False

    bt(0)
    return pairs


# ---------------------------------------------------------------------------
# 公开公告板：只放承诺锚点与已花费 nullifier，不放名册
# ---------------------------------------------------------------------------

@dataclass
class BulletinEntry:
    label: str                 # 每保函一次性的盲化标签
    anchor: str                # 密封保函内容的哈希锚点
    status: str                # covered / under / suspended / spent
    nullifier: str             # 兑付时公开的一次性 nullifier
    group_root: str            # 所属覆盖组的根哈希（不含成员名册）


class Bulletin:
    def __init__(self):
        self.entries: dict[str, BulletinEntry] = {}
        self.spent_nullifiers: set[str] = set()

    def public_view(self) -> dict:
        # 客户视角：只能看到标签、锚点、状态、nullifier、组根；无身份、无 sid
        return {
            "entries": [asdict(e) for e in
                        sorted(self.entries.values(), key=lambda e: e.label)],
            "spent_count": len(self.spent_nullifiers),
        }

    def root(self) -> str:
        return digest(self.public_view())


# ---------------------------------------------------------------------------
# 托管席
# ---------------------------------------------------------------------------

class EscrowSeat:
    def __init__(self, seat_identity: Identity):
        self.seat = seat_identity
        self.banks: dict[str, Identity] = {}
        # 私有跨行台账：sid -> 物理实体标识（绝不进入公开数据）
        self.physical_map: dict[str, str] = {}
        self.items: dict[str, SyntheticItem] = {}
        self.receipts: dict[str, BankReceipt] = {}     # sid -> 有效收据
        self.orders: dict[str, PairedOrder] = {}
        self.guarantees: dict[str, dict] = {}          # label -> 保函文档
        self.groups: dict[str, dict] = {}              # group_id -> 核定结果
        self.current_group_id: Optional[str] = None
        self.bulletin = Bulletin()
        # 当日已交走的物理实体（同日不可重复履约）
        self.spent_today: dict[str, set] = {}
        # 已兑付保函标签：退出后续最不利核定，避免状态被重判覆盖
        self.spent_labels: set[str] = set()
        # 已签发的离线预留范围
        self.offline_scopes: dict[str, dict] = {}

    # -- 寄存行注册与收据 -------------------------------------------------
    def register_bank(self, bank: Identity):
        self.banks[bank.label] = bank

    def deposit_receipt(self, receipt: BankReceipt, physical_id: str):
        """接收寄存行足额章；physical_id 仅进入私有跨行台账。"""
        if receipt.bank not in self.banks:
            raise ValueError("未注册的寄存行")
        if not verify_receipt(receipt):
            raise ValueError(f"收据签名无效: {receipt.sid}")
        if receipt.sid in self.receipts:
            # 同一 sid 的跨行重复盖章：折叠到同一物理实体
            existing_phys = self.physical_map.get(receipt.sid, receipt.sid)
            if existing_phys != physical_id:
                raise ValueError("同一 sid 出现物理实体冲突，疑似别名攻击")
        self.receipts[receipt.sid] = receipt
        self.physical_map[receipt.sid] = physical_id
        self.items[receipt.sid] = SyntheticItem(
            sid=receipt.sid, kind=receipt.kind, mount=receipt.mount,
            tolerance_um=receipt.tolerance_um, expires_on=receipt.expires_on,
            bank=receipt.bank)
        self._reassess()

    # -- 别名攻击模拟：把某 sid 的物理对应改成另一实物的别名 --------------
    def alias_receipt_to(self, sid: str, alias_physical_id: str):
        if sid not in self.receipts:
            raise KeyError(sid)
        self.physical_map[sid] = alias_physical_id
        self._reassess()

    def restore_receipt(self, sid: str, physical_id: str):
        if sid not in self.receipts:
            raise KeyError(sid)
        self.physical_map[sid] = physical_id
        self._reassess()

    # -- 保函签发 ---------------------------------------------------------
    def submit_order(self, order: PairedOrder) -> str:
        self.orders[order.order_id] = order
        label = "lbl-" + sha256(os.urandom(32) + order.order_id.encode()).hex()[:24]
        nullifier = "nul-" + sha256(os.urandom(32) + label.encode()).hex()[:24]
        self.guarantees[label] = {
            "label": label,
            "order_id": order.order_id,
            "nullifier": nullifier,
            "status": SUSPENDED,
        }
        self._reassess()
        return label

    def _active_orders(self):
        spent_order_ids = {self.guarantees[l]["order_id"]
                           for l in self.spent_labels if l in self.guarantees}
        return [o for o in self.orders.values()
                if o.order_id not in spent_order_ids]

    def _reassess(self):
        """覆盖组重新核定：任何收据/订单/别名变化都触发。

        组 ID 只由“物理映射 + 订单集合”决定：别名改变映射会换组，恢复映射后
        回到原组并就地覆盖其核定结果，因此别名/恢复往返不会留下陈旧状态。
        """
        orders = self._active_orders()
        root_material = (canonical(sorted(self.physical_map.items()))
                         + canonical(sorted(o.order_id for o in orders)))
        group_id = "grp-" + sha256(root_material).hex()[:16]
        group_root = group_id
        ok, allocation = worst_case_feasible(
            orders, self.items, self.physical_map) if orders else (True, {})
        status = COVERED if ok else UNDER_COVERED
        self.current_group_id = group_id
        self.groups[group_id] = {
            "group_id": group_id,
            "group_root": group_root,
            "status": status,
            "distinct_bodies": len({self.physical_map.get(s.sid, s.sid)
                                    for s in self.items.values() if s.kind == BODY}),
            "distinct_rings": len({self.physical_map.get(s.sid, s.sid)
                                   for s in self.items.values() if s.kind == RING}),
            "allocation": allocation,
        }
        # 更新每张保函状态与公告板锚点（公开侧不附分配明细/名册）
        for label, g in self.guarantees.items():
            if label in self.spent_labels:
                # 已兑付保函不再参与重判，保持 spent
                g["status"] = "spent"
                continue
            g["status"] = status
            g["group_id"] = group_id
            order = self.orders[g["order_id"]]
            # 客户私有内容：包含允许替代范围与必须公开的兑付依据
            private_doc = {
                "label": label,
                "order_id": g["order_id"],
                "allowed_mounts": order.allowed_mounts,
                "max_tolerance_um": order.max_tolerance_um,
                "need_before": order.need_before,
                "group_id": group_id,
                "cover_status": status,
                "nullifier": g["nullifier"],
                "allocation_if_covered": allocation.get(order.order_id, {}),
            }
            g["private_doc"] = private_doc
            anchor = digest(private_doc)
            g["anchor"] = anchor
            entry = self.bulletin.entries.get(label)
            if entry is None:
                entry = BulletinEntry(label=label, anchor=anchor, status=status,
                                  nullifier=g["nullifier"],
                                  group_root=group_root)
                self.bulletin.entries[label] = entry
            else:
                entry.status = status
                entry.anchor = anchor
                entry.group_root = group_root

    def issue_sealed_guarantee(self, label: str, client_x_pub: str) -> dict:
        """把保函私有用密封信封发给客户；公告板只留锚点。"""
        g = self.guarantees[label]
        plaintext = canonical(g["private_doc"])
        box = sealed_box_send(bytes.fromhex(client_x_pub), plaintext)
        return {"label": label, "box": box,
                "seat_sig": self.seat.sign(canonical(
                    {"label": label, "anchor": g["anchor"]})).hex(),
                "anchor": g["anchor"]}

    # -- 兑付日：根据当日已交走部件 + 实际替代镜座组装 --------------------
    def redeem(self, label: str, use_day: str, chosen_alt_mount: str,
               client_identity: Identity) -> dict:
        g = self.guarantees.get(label)
        if g is None:
            raise ValueError("未知保函标签")
        if g["status"] != COVERED:
            raise ValueError(f"保函未通过足额核定，当前状态 {g['status']}")
        order = self.orders[g["order_id"]]
        if chosen_alt_mount not in order.allowed_mounts:
            raise ValueError("替代镜座不在客户接受范围")
        if _parse(use_day) > _parse(order.need_before):
            raise ValueError("已超过承诺窗口")
        nullifier = g["nullifier"]
        if nullifier in self.bulletin.spent_nullifiers:
            raise ValueError("该保函已兑付，nullifier 已公开花费")
        spent = self.spent_today.setdefault(use_day, set())

        # 在扣除当日已交走实物后，重新做一次可行性匹配（同日同一实物不可重复）
        available = {sid: it for sid, it in self.items.items()
                     if self.physical_map.get(sid, sid) not in spent}
        # 优先使用客户声明的替代镜座
        order_for_day = PairedOrder(
            order_id=order.order_id, client_pub=order.client_pub,
            client_ed_pub=order.client_ed_pub,
            preferred_mount=chosen_alt_mount,
            allowed_mounts=[chosen_alt_mount] +
                           [m for m in order.allowed_mounts if m != chosen_alt_mount],
            max_tolerance_um=order.max_tolerance_um,
            need_before=order.need_before, qty_assemblies=1)
        ok, alloc = worst_case_feasible(
            [order_for_day], available, self.physical_map, as_of=use_day)
        if not ok:
            raise ValueError("当日剩余实物无法在公差内组装，拒付并保留保函")
        plan = alloc[order.order_id]
        body_sid, ring_sid = plan["pairs"][0]
        # 标记实物已花费 + 公开 nullifier
        for sid in (body_sid, ring_sid):
            spent.add(self.physical_map.get(sid, sid))
        self.bulletin.spent_nullifiers.add(nullifier)
        self.bulletin.entries[label].status = "spent"
        self.spent_labels.add(label)
        g["status"] = "spent"

        manifest = self._build_manifest(
            label, use_day, body_sid, ring_sid, order, nullifier)
        # 客户取件签认（证明本人领走）
        take_receipt = client_identity.sign(canonical(
            {"label": label, "use_day": use_day,
             "body": body_sid, "ring": ring_sid,
             "nullifier": nullifier})).hex()
        return {"manifest": manifest, "client_take_receipt": take_receipt}

    def _build_manifest(self, label, use_day, body_sid, ring_sid,
                        order, nullifier) -> dict:
        body = self.items[body_sid]
        ring = self.items[ring_sid]
        receipts = []
        for sid in (body_sid, ring_sid):
            r = self.receipts[sid]
            receipts.append({"receipt": asdict(r),
                             "valid": verify_receipt(r)})
        diagram = {
            "rule": "body.mount == ring.mount; ring in allowed_mounts; "
                    "body.tolerance <= max AND ring.tolerance <= max; "
                    "ring.tolerance <= body.tolerance + 1um",
            "body": body.public_descriptor(),
            "ring": ring.public_descriptor(),
            "edge_check": compatible(body, ring, order),
        }
        doc = {
            "label": label,
            "use_day": use_day,
            "nullifier": nullifier,
            "body_sid": body_sid,
            "ring_sid": ring_sid,
            "receipts": receipts,
            "assembly_diagram": diagram,
            "allowed_mounts": order.allowed_mounts,
            "max_tolerance_um": order.max_tolerance_um,
        }
        doc["seat_signature"] = self.seat.sign(canonical(
            {k: v for k, v in doc.items() if k != "seat_signature"})).hex()
        return doc

    # -- 离线柜台：仅按已预留交接范围办 ----------------------------------
    def issue_offline_scope(self, label: str, use_day: str) -> dict:
        """为离线柜台签发预留交接范围（只含允许交付的 sid 与核验依据）。

        scope 自带允许部件的公开描述符，离线柜台无需访问全库即可本地复核
        配套图；签名覆盖全部字段，任何篡改都会在验签时暴露。
        """
        g = self.guarantees[label]
        if g["status"] != COVERED:
            raise ValueError("只有 covered 保函能预留离线交接范围")
        order = self.orders[g["order_id"]]
        group = self.groups[g["group_id"]]
        plan = group["allocation"].get(order.order_id, {})
        scope = {
            "label": label,
            "use_day": use_day,
            "allowed_body_sids": plan.get("bodies", []),
            "allowed_ring_sids": plan.get("rings", []),
            "allowed_mounts": order.allowed_mounts,
            "max_tolerance_um": order.max_tolerance_um,
            "nullifier": g["nullifier"],
            # 仅包含允许交付部件的公开描述符（不含押物人/全库名册）
            "descriptors": [
                self.items[sid].public_descriptor()
                for sid in plan.get("bodies", []) + plan.get("rings", [])],
        }
        scope["seat_signature"] = self.seat.sign(canonical(
            {k: v for k, v in scope.items() if k != "seat_signature"})).hex()
        self.offline_scopes[label] = scope
        return scope


class OfflineCounter:
    """断网柜台：只持托管席公钥、签名 scope 副本与本地 nullifier 花费集。

    它不连接托管席、读不到全库；能否交付完全取决于 scope 签名与本地核验。
    回连后把本地花费的 nullifier 批量同步回托管席公告板。
    """

    def __init__(self, seat_pub: bytes):
        self.seat_pub = seat_pub
        self.local_spent: set[str] = set()

    @staticmethod
    def _scope_base(scope: dict) -> dict:
        return {k: v for k, v in scope.items() if k != "seat_signature"}

    def redeem(self, scope: dict, use_day: str,
               body_sid: str, ring_sid: str) -> dict:
        # 1. 验签：篡改任一字段即失败
        if not ed25519_verify(self.seat_pub, canonical(self._scope_base(scope)),
                              bytes.fromhex(scope["seat_signature"])):
            raise PermissionError("离线范围签名无效，拒办（疑似篡改）")
        # 2. 使用日、交付范围
        if use_day != scope["use_day"]:
            raise PermissionError("使用日与预留范围不符，拒办")
        if body_sid not in scope["allowed_body_sids"]:
            raise PermissionError("镜身不在预留交接范围，拒办")
        if ring_sid not in scope["allowed_ring_sids"]:
            raise PermissionError("转接环不在预留交接范围，拒办")
        # 3. 防双花：本地 nullifier 花费集
        if scope["nullifier"] in self.local_spent:
            raise PermissionError("nullifier 已花费，拒付以防双花")
        # 4. 仅凭 scope 内公开描述符做配套图本地复核
        desc = {d["sid"]: d for d in scope["descriptors"]}
        body = SyntheticItem(**{**desc[body_sid], "bank": "offline"})
        ring = SyntheticItem(**{**desc[ring_sid], "bank": "offline"})
        probe = PairedOrder(
            order_id=scope["label"], client_pub="", client_ed_pub="",
            preferred_mount=scope["allowed_mounts"][0],
            allowed_mounts=scope["allowed_mounts"],
            max_tolerance_um=scope["max_tolerance_um"],
            need_before=scope["use_day"])
        if not compatible(body, ring, probe):
            raise PermissionError("装配公差/镜座不满足配套图，拒办")
        self.local_spent.add(scope["nullifier"])
        return {"offline": True, "label": scope["label"],
                "body_sid": body_sid, "ring_sid": ring_sid,
                "nullifier": scope["nullifier"]}

    def sync_back(self, seat: EscrowSeat):
        """回连后把离线花费的 nullifier 同步到托管席公告板。"""
        nul_to_label = {g["nullifier"]: lbl
                        for lbl, g in seat.guarantees.items()}
        for nul in self.local_spent:
            seat.bulletin.spent_nullifiers.add(nul)
            lbl = nul_to_label.get(nul)
            if lbl is not None:
                seat.spent_labels.add(lbl)
                seat.guarantees[lbl]["status"] = "spent"
                seat.bulletin.entries[lbl].status = "spent"
        if self.local_spent:
            seat._reassess()
        return sorted(self.local_spent)
