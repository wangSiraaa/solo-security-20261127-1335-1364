"""外地实验室：收到履历后独立核源，并实际调出模拟色样。

外地实验室不接受“柜子合格”这类汇总结论；它重算全部封存哈希链、核对
来源绑定，再按样管最不利热暴露推强度保留率。复验单写明已签配方剂量，
实际调样时每支分液器只能按自身可量取步长取整；独立比色台另持目标色卡，
只按 CIEDE2000 验收。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import color
from .sealing import verify_envelope

DELTA_E_LIMIT = 1.5
BASE_VOLUME_ML = 50.0
BASE_LOT_VOLUME_ML = 250.0
# 虚构强度衰减常数：5°C 等效小时 -> 染料强度保留率
STRENGTH_TAU_EQH = 1200.0


def packet_for(world: dict, tube_id: str) -> dict:
    """库方按管打包的完整履历（不含任何“柜子合格”汇总章）。"""
    return {
        "tube": tube_id,
        "chains": {
            "probes": world["probes"].export(),
            "bath": world["bath"].export(),
            "bulletins": world["bulletins"].export(),
            "events": world["events"].export(),
            "loggers": world["loggers"].export(),
        },
        "readings": {f"{k[0]}|{k[1]}": v for k, v in world["readings"].items()},
        "tube_record": world["tubes"][tube_id],
    }


def independently_verify(packet: dict) -> dict:
    """收件人独立重算每条封存链的哈希，并核对来源绑定字段。"""
    problems: list[str] = []
    for name, envelopes in packet["chains"].items():
        tip = "GENESIS"
        for env in envelopes:
            good, why = verify_envelope(env, tip)
            if not good:
                problems.append(f"{name}：{why}")
                break
            tip = env["hash"]

    tube_id = packet["tube"]
    receipt_id = packet["tube_record"].get("receipt_id")
    for env in packet["chains"]["loggers"]:
        rec = env["body"]["record"]
        if rec["id"] != receipt_id:
            continue
        if rec["bound_tube"] != tube_id:
            problems.append(
                f"回单 {rec['id']} 绑定 {rec['bound_tube']}，不是本管 {tube_id}")
        transport = [s for s in packet["tube_record"]["timeline"]
                     if s[2] == "transport"]
        if transport and tuple(rec["window"]) != (transport[0][0], transport[0][1]):
            problems.append(f"回单 {rec['id']} 时段与交接段不一致")
    return {"ok": not problems, "problems": problems}


def strength_retention(eq_hours: float) -> float:
    return math.exp(-eq_hours / STRENGTH_TAU_EQH)


@dataclass
class Dispenser:
    steps_ml: dict[str, float]

    def quantize(self, colorant: str, dose: float) -> float:
        step = self.steps_ml[colorant]
        return round(round(dose / step) * step, 6)


@dataclass
class RecheckOrder:
    order_id: str
    formula: str
    colorants: list[str]
    signed_doses_ml: dict[str, float]
    target_lab: tuple[float, float, float]
    base_lot: str
    status: str = "open"
    swatches: list[dict] = field(default_factory=list)
    doses_used_ml: dict[str, float] = field(default_factory=dict)
    base_used_ml: float = 0.0
    verdict: str = ""

    def consume(self, doses: dict[str, float],
                tube_stock: dict[str, float]) -> dict[str, float]:
        for c, v in doses.items():
            if tube_stock[c] + 1e-9 < v:
                raise ValueError(f"{self.order_id}：{c} 剩余体积不足")
            tube_stock[c] -= v
            self.doses_used_ml[c] = self.doses_used_ml.get(c, 0.0) + v
        return tube_stock


class ExternalLab:
    def __init__(self, name: str, steps_ml: dict[str, float]):
        self.name = name
        self.dispenser = Dispenser(steps_ml)
        # 独立比色台自己持有的目标色卡（库方无法修改）
        self.target_cards: dict[str, tuple[float, float, float]] = {}
        # 按染料名记账的余量，以及染料 -> 实际来源样管
        self.tube_stock: dict[str, float] = {}
        self.source_tube: dict[str, str] = {}
        self.tube_colorant: dict[str, str] = {}
        self.tube_eqh: dict[str, float] = {}
        # 两个复验单须共享的基液批次；耗尽后才启用 BL-08
        self.base_lots = {"BL-07": BASE_LOT_VOLUME_ML, "BL-08": BASE_LOT_VOLUME_ML}
        self.base_ledger: list[dict] = []

    def _draw_base(self, order: RecheckOrder) -> str:
        """每张色样先取共享批次 BL-07，余量不足才换 BL-08，逐笔记账。"""
        if self.base_lots["BL-07"] + 1e-9 >= BASE_VOLUME_ML:
            lot = "BL-07"
        else:
            lot = "BL-08"
        if self.base_lots[lot] + 1e-9 < BASE_VOLUME_ML:
            raise ValueError(f"{order.order_id}：基液 {lot} 余量不足")
        self.base_lots[lot] -= BASE_VOLUME_ML
        order.base_used_ml += BASE_VOLUME_ML
        self.base_ledger.append(
            {"order": order.order_id, "base_lot": lot, "ml": BASE_VOLUME_ML})
        return lot

    def receive(self, packets: dict[str, dict], eq_hours: dict[str, float],
                target_cards: dict[str, tuple[float, float, float]]) -> dict:
        report = {}
        for tube_id, packet in packets.items():
            verdict = independently_verify(packet)
            report[tube_id] = verdict
            if verdict["ok"]:
                rec = packet["tube_record"]
                colorant = rec["colorant"]
                self.tube_stock[colorant] = rec["remaining_ml"]
                self.source_tube[colorant] = tube_id
                self.tube_colorant[tube_id] = colorant
                self.tube_eqh[tube_id] = eq_hours[tube_id]
        self.target_cards.update(target_cards)
        return report

    def _strengths(self, tubes_by_colorant: dict[str, str], worst: bool) -> dict[str, float]:
        return {
            colorant: (strength_retention(self.tube_eqh[tube_id]) if worst else 1.0)
            for colorant, tube_id in tubes_by_colorant.items()
        }

    def run_order(self, order: RecheckOrder,
                  tubes_by_colorant: dict[str, str]) -> RecheckOrder:
        for c, tube_id in tubes_by_colorant.items():
            if c not in self.tube_stock or self.source_tube.get(c) != tube_id:
                order.status = "rejected"
                order.verdict = f"{c} 无经独立核源的样管，拒绝凭汇总结论复验"
                return order

        # 已签配方剂量，经本台分液器步长量化后实际取液
        doses = {
            c: self.dispenser.quantize(c, order.signed_doses_ml[c])
            for c in order.colorants
        }
        nominal = self._strengths(tubes_by_colorant, worst=False)
        worst = self._strengths(tubes_by_colorant, worst=True)
        for strengths, tag in ((nominal, "nominal"), (worst, "worst-case")):
            self.tube_stock = order.consume(doses, self.tube_stock)
            lot = self._draw_base(order)
            lab = color.lab_of(doses, strengths)
            de = color.ciede2000(lab, order.target_lab)
            order.swatches.append({
                "tag": tag, "doses_ml": dict(doses), "base_lot": lot,
                "strengths": dict(strengths),
                "lab": tuple(round(v, 3) for v in lab),
                "delta_e00": round(de, 4),
                "bench": "独立比色台（自持目标色卡）",
                "pass": de <= DELTA_E_LIMIT,
            })
        order.status = "closed"
        if all(s["pass"] for s in order.swatches):
            order.verdict = (
                f"办结通过：名义与最坏允许变化两张色样 ΔE00 均 ≤ {DELTA_E_LIMIT}，"
                f"按实际混色与耗量通过")
        else:
            failed = [s["tag"] for s in order.swatches if not s["pass"]]
            order.verdict = (
                f"办结拒收：{','.join(failed)} 色样超出已签色差限 "
                f"ΔE00 {DELTA_E_LIMIT}，按实际混色与耗量拒收")
        return order
