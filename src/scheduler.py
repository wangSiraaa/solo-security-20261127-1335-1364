"""半双工时隙调度器（离线可执行）。

两站：ARCHIVE（档案室，接收器 R1）与 VISITOR（访客，接收器 R2）。
任一时隙至多一个方向发帧；某站连续发送后需要 recovery_slots 个空闲时隙
才能切回接收（接收恢复时间），慢接收器通过更大的 recovery 体现。

两种方案：
  conservative：按巡测最差损耗预留固定功率档与守卫间隔，全程离线，损失访客容量。
  adaptive（本教具选定并签发）：先保档案室保底，接收窗用短训练脉冲收窄整体范围，
                再在不触碰先前承诺的前提下借出边缘时隙；回波仍有歧义时，
                边缘借出策略要求观测归属签名与签发时一致，否则强制收回。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Dict, List, Optional

from plant import TX_LEVELS_DBM

F = Fraction

# ---- 时间窗（时隙号，截止语义见下方各 DEADLINE 常量）----
# 档案室约定短窗 0..13、访客买下基础窗 9..17（边界以各 DEADLINE 为准）
VISITOR_BASE_WINDOW = range(9, 18)   # 访客买下的基础窗口
ARCHIVE_CATALOG_DEADLINE = 13        # 两份目录页数据帧须在此时隙前发完
ARCHIVE_ACK_DEADLINE = 14            # 目录页回执截止
VISITOR_SAMPLE_DEADLINE = 18         # 访客样件数据帧截止
VISITOR_ACK_DEADLINE = 21            # 访客回执截止
TRAINING_SLOT = 8                    # 自适应短训练脉冲占用的时隙


@dataclass
class SlotOp:
    slot: int
    op: str                  # TX / RX / IDLE / GUARD / TRAIN
    station: Optional[str] = None
    stream: Optional[str] = None
    frame_seq: Optional[int] = None
    level_dbm: Optional[Fraction] = None
    note: str = ""


@dataclass
class ReceiverSpec:
    name: str
    sensitivity_dbm: Fraction
    recovery_slots: int


@dataclass
class Schedule:
    scheme: str
    ops: List[SlotOp] = field(default_factory=list)
    rejected: List[str] = field(default_factory=list)
    reclaimed: List[str] = field(default_factory=list)
    level_table: Dict[str, Fraction] = field(default_factory=dict)
    visitor_capacity_slots: int = 0
    notes: List[str] = field(default_factory=list)

    def op_at(self, slot: int) -> Optional[SlotOp]:
        return next((o for o in self.ops if o.slot == slot), None)


def _guard_needed(last_tx_slot: Optional[int], slot: int, recovery: int) -> bool:
    if last_tx_slot is None:
        return False
    return slot - last_tx_slot <= recovery


def _pick_level(path_loss_ub: Fraction, receiver_sens: Fraction) -> Optional[Fraction]:
    """选满足 tx - pathloss >= sensitivity 的最小有限档；无可用档返回 None。"""
    need = receiver_sens + path_loss_ub
    for lvl in TX_LEVELS_DBM:
        if lvl >= need:
            return lvl
    return None


def build_conservative(
    path_ubs: Dict[str, Fraction],
    receivers: Dict[str, ReceiverSpec],
    recovery_archive: int,
    recovery_visitor: int,
) -> Schedule:
    """方案一：全程按巡测最差情形预留固定档 + 固定守卫，离线执行。"""
    sch = Schedule(scheme="conservative")
    lvl_r1 = _pick_level(path_ubs["R1"], receivers["R1"].sensitivity_dbm)
    lvl_r2 = _pick_level(path_ubs["R2"], receivers["R2"].sensitivity_dbm)
    assert lvl_r1 is not None and lvl_r2 is not None, "有限发射档无法覆盖最差损耗"
    sch.level_table = {"catalog": lvl_r1, "visitor": lvl_r2}

    # 档案室保底：两页数据 + 两回执，守卫间隔固定按各自恢复时间预留
    plan = [
        (0, "TX", "ARCHIVE", "catalog", 0, lvl_r1),
        (1, "TX", "ARCHIVE", "catalog", 1, lvl_r1),
        (2, "GUARD", "ARCHIVE", None, None, None),
        (3, "RX", "ARCHIVE", "catalog_ack", 0, None),
        (4, "RX", "ARCHIVE", "catalog_ack", 1, None),
    ]
    for slot, op, st, stream, seq, lvl in plan:
        sch.ops.append(SlotOp(slot, op, st, stream, seq, lvl,
                              "固定守卫/功率档（巡测最差情形）"))
    # 访客基础窗：保守档全程用 R2 最差档，边缘时隙不借出。
    # 数据帧 TX@13，恢复后 ACK RX@15（与自适应同一物理时序，便于对照容量差异）。
    last_v_tx = None
    for slot in range(VISITOR_BASE_WINDOW.start, VISITOR_BASE_WINDOW.stop + 1):
        existing = sch.op_at(slot)
        if existing:
            continue
        if slot == 13:
            sch.ops.append(SlotOp(slot, "TX", "VISITOR", "visitor", 0, lvl_r2,
                                  "保守固定档"))
            last_v_tx = slot
        elif slot == 15:
            if _guard_needed(last_v_tx, slot, recovery_visitor):
                sch.ops.append(SlotOp(slot, "GUARD", "VISITOR", note="接收恢复守卫"))
            else:
                sch.ops.append(SlotOp(slot, "RX", "VISITOR", "visitor_ack", 0))
        else:
            sch.ops.append(SlotOp(slot, "IDLE", "VISITOR", note="未借出的边缘时隙"))
    sch.visitor_capacity_slots = 1
    sch.notes.append("保守方案：边缘时隙不借出，访客可用容量 = 基础窗内 1 个发送时隙")
    return sch


def build_adaptive(
    patrol_ubs: Dict[str, Fraction],
    trained_ubs: Optional[Dict[str, Fraction]],
    receivers: Dict[str, ReceiverSpec],
    recovery_archive: int,
    recovery_visitor: int,
    echo_signature_match: bool = True,
    stress_request: bool = False,
) -> Schedule:
    """方案二（选定）：保底优先 + 训练脉冲收束 + 有条件借出边缘时隙。"""
    sch = Schedule(scheme="adaptive")

    # 档案室保底档仍按巡测最差域选，训练不改其承诺
    lvl_r1 = _pick_level(patrol_ubs["R1"], receivers["R1"].sensitivity_dbm)
    assert lvl_r1 is not None
    sch.level_table["catalog"] = lvl_r1

    # 训练脉冲（接收窗内短脉冲），占用 TRAINING_SLOT
    sch.ops.append(SlotOp(TRAINING_SLOT, "TRAIN", "ARCHIVE",
                          note="短训练脉冲：取得新的整体损耗范围"))

    # 档案室承诺（与保守方案相同的时隙，绝不被借出）
    committed = [
        (0, "TX", "ARCHIVE", "catalog", 0, lvl_r1),
        (1, "TX", "ARCHIVE", "catalog", 1, lvl_r1),
        (2, "GUARD", "ARCHIVE", None, None, None),
        (3, "RX", "ARCHIVE", "catalog_ack", 0, None),
        (4, "RX", "ARCHIVE", "catalog_ack", 1, None),
    ]
    assert TRAINING_SLOT not in {p[0] for p in committed}, "训练脉冲不得占用档案室承诺时隙"
    for slot, op, st, stream, seq, lvl in committed:
        sch.ops.append(SlotOp(slot, op, st, stream, seq, lvl, "档案室先前承诺"))

    # 训练后访客档：按收窄的整体范围上界选最小档
    ubs = trained_ubs or patrol_ubs
    lvl_r2 = _pick_level(ubs["R2"], receivers["R2"].sensitivity_dbm)
    assert lvl_r2 is not None, "训练后仍无可用档：回退保守档并拒绝边缘借出"
    sch.level_table["visitor"] = lvl_r2

    # ---- 边缘借出策略 ----
    # 仅当 (a) 回波归属签名与维护依据签发时一致；(b) 收窄后最差损耗仍被所选档覆盖，
    # 才把基础窗外的边缘时隙借给访客。任一不满足 => 收回先前可借段，保住承诺。
    edge_slots = [10, 11]
    lending_allowed = echo_signature_match
    if not lending_allowed:
        sch.reclaimed.append("edge-10/11：回波分支归属变更，边缘时隙收回")
        for s in edge_slots:
            sch.ops.append(SlotOp(s, "IDLE", "VISITOR",
                                  note="回波歧义未消，保留先前承诺，边缘不借出"))
    else:
        for s in edge_slots:
            sch.ops.append(SlotOp(s, "TX", "VISITOR", "visitor", None, lvl_r2,
                                  "训练收束后借出的边缘时隙"))
        sch.notes.append(f"边缘时隙 {edge_slots} 已借出（签名一致 + 档位覆盖）")

    # 基础窗内的访客收发。布局：数据帧 TX@13，恢复够 r 后 ACK RX@15；
    # r=2 的慢接收器会令 15 不满足恢复间隔，该 ACK 窗必须留空（GUARD），
    # 回执被推迟到截止期之后登记为逾窗 —— 但档案室目录页走独立光路，仍按时交到。
    visitor_data_slot = 13
    visitor_ack_slot = 15
    last_v_tx = max(edge_slots) if lending_allowed else None
    for slot in range(VISITOR_BASE_WINDOW.start, VISITOR_BASE_WINDOW.stop + 1):
        if sch.op_at(slot):
            continue
        if slot == visitor_data_slot:
            sch.ops.append(SlotOp(slot, "TX", "VISITOR", "visitor", 0, lvl_r2,
                                  "访客样件数据帧"))
            last_v_tx = slot
        elif slot == visitor_ack_slot:
            if _guard_needed(last_v_tx, slot, recovery_visitor):
                sch.ops.append(SlotOp(
                    slot, "GUARD", "VISITOR",
                    note=f"慢接收恢复：距上次发送 {slot - last_v_tx} 格 <= r={recovery_visitor}，"
                         "ACK 窗必须留空"))
            else:
                sch.ops.append(SlotOp(slot, "RX", "VISITOR", "visitor_ack", 0,
                                      "访客回执（恢复后）"))
        else:
            sch.ops.append(SlotOp(slot, "IDLE", "VISITOR"))

    sch.visitor_capacity_slots = (
        len(edge_slots) + 1 if lending_allowed else 1
    )

    # 压力请求：在容量之外再要一个发送时隙。能在截止前安排则接受，否则拒绝并记录。
    if stress_request:
        candidate = next(
            (s for s in range(VISITOR_BASE_WINDOW.start, VISITOR_SAMPLE_DEADLINE)
             if sch.op_at(s) is None and not _guard_needed(last_v_tx, s, recovery_visitor)),
            None,
        )
        if candidate is not None and lending_allowed:
            sch.ops.append(SlotOp(candidate, "TX", "VISITOR", "visitor_extra", 99, lvl_r2,
                                  "压力请求：额外访客帧"))
            sch.notes.append(f"压力请求在时隙 {candidate} 接受")
        else:
            sch.rejected.append("stress-extra：无满足恢复时间与截止期的空闲时隙，拒绝")
    sch.ops.sort(key=lambda o: o.slot)
    return sch


def check_deadlines(sch: Schedule) -> List[str]:
    """独立核验：各流数据帧/回执是否落在对应截止期前。

    数据 TX 时隙须 < ARCHIVE_CATALOG_DEADLINE / VISITOR_SAMPLE_DEADLINE；
    回执 RX 时隙须 <= ARCHIVE_ACK_DEADLINE / VISITOR_ACK_DEADLINE。
    """
    data_deadline = {
        "catalog": ARCHIVE_CATALOG_DEADLINE,
        "visitor": VISITOR_SAMPLE_DEADLINE,
    }
    ack_deadline = {
        "catalog_ack": ARCHIVE_ACK_DEADLINE,
        "visitor_ack": VISITOR_ACK_DEADLINE,
    }
    violations = []
    for op in sorted(sch.ops, key=lambda o: o.slot):
        if op.op == "TX" and op.stream in data_deadline:
            if op.slot >= data_deadline[op.stream]:
                violations.append(
                    f"时隙 {op.slot} {op.stream} 数据帧晚于截止 {data_deadline[op.stream]}"
                )
        if op.op == "RX" and op.stream in ack_deadline:
            if op.slot > ack_deadline[op.stream]:
                violations.append(
                    f"时隙 {op.slot} {op.stream} 回执晚于截止 {ack_deadline[op.stream]}"
                )
    return violations


def check_half_duplex(sch: Schedule, recovery_archive: int, recovery_visitor: int) -> List[str]:
    """独立核验：任一接收时隙与本站最近一次发送之间必须隔够恢复时隙。"""
    violations = []
    last_tx = {"ARCHIVE": None, "VISITOR": None}
    recovery = {"ARCHIVE": recovery_archive, "VISITOR": recovery_visitor}
    for op in sorted(sch.ops, key=lambda o: o.slot):
        if op.station is None:
            continue
        if op.op == "TX":
            last_tx[op.station] = op.slot
        elif op.op == "RX":
            lt = last_tx[op.station]
            if lt is not None and op.slot - lt <= recovery[op.station]:
                violations.append(
                    f"时隙 {op.slot} {op.station} 接收距上次发送仅 {op.slot - lt} 格，"
                    f"需 {recovery[op.station] + 1} 格"
                )
    return violations
