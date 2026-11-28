"""离线染料留样库的虚构世界与履历判定。

温度全部为模拟值，配方与批号均为虚构。判定规则：
1. 每条封存链独立重算哈希；记录体的来源绑定（样管/时段/柜体）必须一致，
   真实回单若贴给另一管或另一时段，该管履历直接判否。
2. 探头缺段/失准段不得用单点结论替代，须由同层参考点与允许温变速率
   夹出逐小时上下限；借用邻架时叠加排架图给出的跨架不确定度。
3. 对该管取最不利暴露（逐小时上限）折算为 5°C 等效小时，超过虚构预算
   即不可领出。
"""
from __future__ import annotations

import math

from .sealing import Chain

HOURS = 240
T_REF = 5.0
BETA = 0.08            # 虚构 Arrhenius 系数 (1/°C)
BUDGET_EQH = 295.0     # 虚构累计 5°C 等效小时预算
RATE_LIMIT = 0.35      # 允许温变界限 (°C/h)
CROSS_SHELF_U = 0.60   # 借邻架重建时的跨架关联不确定度 (°C)
EVENT_U = 0.30         # 开门/过秤当小时的局部扰动余量 (°C)

LAYERS_C1 = {"L1": 0.4, "L2": 1.2, "L3": 2.3, "L4": 3.4}
LAYER_PROBE_C1 = {"L1": "P1", "L2": "P2", "L3": "P3", "L4": "P4"}
LAYERS_C2 = {"L2": 1.9}
PROBE_PHASE = {"P1": 0.2, "P2": 1.1, "P3": 2.3, "P4": 3.7, "P5": 4.4}

GAP = (96, 126)        # P3 已封原读数缺失的半开区间（30h）
OOT = (150, 190)       # P4 失准公告区间（原读数封存但不采信）
WIDE_WINDOW = (90, 132)  # 参考浴临时宽校正窗（覆盖 P3 缺口）
WIDE_HALFWIDTH = 0.80
NARROW_HALFWIDTH = 0.10


def _diurnal(h: int) -> float:
    return 0.35 * math.sin(2.0 * math.pi * h / 24.0)


def _noise(probe: str, h: int) -> float:
    return 0.05 * math.sin(h * 1.7 + PROBE_PHASE[probe])


def build_world() -> dict:
    probes = Chain("probes")
    bath = Chain("bath")
    bulletins = Chain("bulletins")
    events = Chain("events")
    loggers = Chain("loggers")

    # 开门/过秤带来的当小时温升（带顺序号封存）
    raw_events = [
        ("E-101", "door-open", "C1", 30, 0.8, None),
        ("E-102", "weigh", "C1", 55, 0.6, "T-02"),
        ("E-103", "door-open", "C1", 99, 0.9, None),    # 落在 P3 缺口内
        ("E-104", "move", "C1", 120, 0.0, "T-02"),      # L2 -> L1
        ("E-105", "move", "C1", 144, 0.0, "T-03"),      # L3 -> L2
        ("E-106", "door-open", "C1", 155, 0.7, None),   # 落在 P4 失准段
        ("E-107", "weigh", "C1", 200, 0.5, "T-01"),
        ("E-108", "move", "C1", 132, 0.0, "T-05"),      # L3 -> L4
        ("E-201", "cross-cabinet-release", "C1", 60, 0.0, "T-04"),
        ("E-202", "cross-cabinet-receipt", "C2", 72, 0.0, "T-04"),
    ]
    spikes: dict[tuple[str, int], float] = {}
    for seq, (eid, etype, cab, h, spike, tube) in enumerate(raw_events, start=1):
        rec = {
            "id": eid, "seq": seq, "type": etype, "cabinet": cab,
            "hour": h, "spike": spike, "tube": tube,
        }
        if eid == "E-201":
            rec.update({"from": "C1/L4", "to": "transport",
                        "receipt_id": "T-LOG-04"})  # 误贴：该回单实绑 T-07
        if eid == "E-202":
            rec.update({"from": "transport", "to": "C2/L2",
                        "receipt_id": "T-LOG-04"})
        events.add(rec)
        if spike:
            spikes[(cab, h)] = spikes.get((cab, h), 0.0) + spike

    # 运输温度记录仪：一张误贴（真实但属于 T-07/另一时段），一张 T-04 备份留存
    loggers.add({
        "id": "T-LOG-04", "bound_tube": "T-07", "window": (48, 60),
        "range_c": (4.2, 6.5), "cabinet": "transport",
        "note": "真实回单，但绑定另一管与另一时段，不能充作 T-04 履历",
    })
    loggers.add({
        "id": "T-LOG-04-R", "bound_tube": "T-04", "window": (60, 72),
        "range_c": (5.4, 10.4), "cabinet": "transport",
        "note": "库方封存的 T-04 正确随运回单（备份件）",
    })

    # 参考浴常规校正（默认 ±0.10°C）
    for h in (0, 48, 96, 144, 192, 239):
        bath.add({"id": f"B-CAL-{h:03d}", "hour": h,
                  "correction_halfwidth": 0.10})
    # 临时宽校正公告与（可选的）缩窄公告
    bulletins.add({
        "id": "BTN-01", "scope": "P3", "window": GAP,
        "kind": "missing-segment",
        "text": "P3 在该时段无已封原读数，须按同层参考与温变速率夹逼",
    })
    bulletins.add({
        "id": "BTN-02", "scope": "B-REF", "window": WIDE_WINDOW,
        "kind": "wide-correction", "halfwidth": WIDE_HALFWIDTH,
        "text": "参考浴校正临时放宽至 ±0.80°C",
    })
    bulletins.add({
        "id": "BTN-04", "scope": "P4", "window": OOT,
        "kind": "out-of-tolerance",
        "text": "P4 该段失准，原读数封存保留但不采信，按失准段重建",
    })

    def true_temp(cab: str, layer: str, h: int) -> float:
        offset = (LAYERS_C1 if cab == "C1" else LAYERS_C2)[layer]
        base = T_REF + offset + _diurnal(h) + spikes.get((cab, h), 0.0)
        return base

    readings: dict[tuple[str, str], list[float | None]] = {}
    for cab, layers in (("C1", LAYERS_C1), ("C2", LAYERS_C2)):
        for layer in layers:
            probe = LAYER_PROBE_C1[layer] if cab == "C1" else "P5"
            series: list[float | None] = []
            for h in range(HOURS):
                value = true_temp(cab, layer, h) + _noise(probe, h)
                if probe == "P3" and GAP[0] <= h < GAP[1]:
                    value = None
                if probe == "P4" and OOT[0] <= h < OOT[1]:
                    value += 1.8  # 失准漂移：读数本身仍原样封存
                series.append(value)
                probes.add({
                    "id": f"{probe}-H{h:03d}", "probe": probe,
                    "cabinet": cab, "layer": layer, "hour": h,
                    "reading_c": None if value is None else round(value, 3),
                })
            readings[(cab, layer)] = series

    tubes = {
        "T-01": {"colorant": "A", "lot": "L-A-1", "remaining_ml": 18.0,
                 "timeline": [(0, HOURS, "C1", "L1")]},
        "T-02": {"colorant": "B", "lot": "L-B-1", "remaining_ml": 16.0,
                 "timeline": [(0, 120, "C1", "L2"), (120, HOURS, "C1", "L1")]},
        "T-03": {"colorant": "C", "lot": "L-C-1", "remaining_ml": 14.0,
                 "timeline": [(0, 144, "C1", "L3"), (144, HOURS, "C1", "L2")]},
        "T-04": {"colorant": "A", "lot": "L-A-2", "remaining_ml": 12.0,
                 "timeline": [(0, 60, "C1", "L4"), (60, 72, "transport", None),
                              (72, HOURS, "C2", "L2")],
                 "receipt_id": "T-LOG-04"},
        "T-05": {"colorant": "C", "lot": "L-C-2", "remaining_ml": 10.0,
                 "timeline": [(0, 132, "C1", "L3"), (132, HOURS, "C1", "L4")]},
    }
    return {
        "probes": probes, "bath": bath, "bulletins": bulletins,
        "events": events, "loggers": loggers, "readings": readings,
        "tubes": tubes, "spikes": spikes, "narrow": False,
    }


def apply_narrow_bath(world: dict) -> None:
    """追加封存 BTN-03：把宽校正窗缩窄到 ±0.12°C（不改动任何旧信封）。"""
    world["bulletins"].add({
        "id": "BTN-03", "scope": "B-REF", "window": WIDE_WINDOW,
        "kind": "narrow-correction", "halfwidth": NARROW_HALFWIDTH,
        "supersedes": "BTN-02",
        "text": "参考浴复核完成，临时窗更正限缩窄至 ±0.10°C",
    })
    world["narrow"] = True


def restore_t04_receipt(world: dict) -> None:
    """还原 T-04 履历：以新封存事件把回单指向正确的 T-LOG-04-R。"""
    world["events"].add({
        "id": "E-203", "seq": len(world["events"].envelopes) + 1,
        "type": "receipt-correction", "cabinet": "C2", "hour": 72,
        "tube": "T-04", "receipt_id": "T-LOG-04-R",
        "supersedes_event": "E-201",
        "text": "撤回误贴的 T-07 回单，改用 T-04 随运封存回单",
    })
    world["tubes"]["T-04"]["receipt_id"] = "T-LOG-04-R"


def bath_halfwidth(world: dict, h: int) -> float:
    lo, hi = WIDE_WINDOW
    if lo <= h < hi:
        if world["narrow"]:
            return NARROW_HALFWIDTH
        return WIDE_HALFWIDTH
    return 0.10


def _valid_hours(series: list[float | None], probe: str) -> list[int]:
    out = []
    for h, v in enumerate(series):
        if v is None:
            continue
        if probe == "P4" and OOT[0] <= h < OOT[1]:
            continue
        out.append(h)
    return out


def hourly_envelope(world: dict, tube_id: str) -> list[dict]:
    tube = world["tubes"][tube_id]
    rows: list[dict] = []
    for h0, h1, cab, layer in tube["timeline"]:
        if cab == "transport":
            rid = tube["receipt_id"]
            env = world["loggers"].find(rid)
            lo, hi = env["body"]["record"]["range_c"]
            for h in range(h0, h1):
                rows.append({"hour": h, "lo": lo, "hi": hi,
                             "kind": "transport-logger", "source": rid})
            continue
        probe = LAYER_PROBE_C1[layer] if cab == "C1" else "P5"
        series = world["readings"][(cab, layer)]
        valid = _valid_hours(series, probe)
        for h in range(h0, h1):
            cr = bath_halfwidth(world, h)
            ev_u = EVENT_U if (cab, h) in world["spikes"] else 0.0
            raw = series[h]
            missing = raw is None or (probe == "P4" and OOT[0] <= h < OOT[1])
            if not missing:
                rows.append({
                    "hour": h, "lo": raw - cr - ev_u, "hi": raw + cr + ev_u,
                    "kind": "sealed-raw", "source": f"{probe}-H{h:03d}",
                })
                continue
            prior = [x for x in valid if x < h]
            later = [x for x in valid if x > h]
            ups, lows = [], []
            if prior:
                a = prior[-1]
                anchor = series[a] + bath_halfwidth(world, a)
                ups.append(anchor + RATE_LIMIT * (h - a))
                lows.append(series[a] - bath_halfwidth(world, a)
                            - RATE_LIMIT * (h - a))
            if later:
                b = later[0]
                anchor = series[b] + bath_halfwidth(world, b)
                ups.append(anchor + RATE_LIMIT * (b - h))
                lows.append(series[b] - bath_halfwidth(world, b)
                            - RATE_LIMIT * (b - h))
            rows.append({
                "hour": h,
                "lo": max(lows) - ev_u - CROSS_SHELF_U,
                "hi": min(ups) + ev_u + CROSS_SHELF_U,
                "kind": "gap-clamp" if raw is None else "oot-clamp",
                "source": f"{probe} anchors {prior[-1] if prior else '-'}/"
                          f"{later[0] if later else '-'}",
            })
    return rows


def worst_exposure(world: dict, tube_id: str) -> dict:
    rows = hourly_envelope(world, tube_id)
    eq_hours = sum(math.exp(BETA * (r["hi"] - T_REF)) for r in rows)
    mean_hi = sum(r["hi"] for r in rows) / len(rows)
    rebuilt = sum(1 for r in rows if r["kind"] in ("gap-clamp", "oot-clamp"))
    return {"eq_hours": eq_hours, "mean_upper_c": mean_hi,
            "rebuilt_hours": rebuilt, "rows": rows}


def verify_provenance(world: dict, tube_id: str) -> dict:
    problems: list[str] = []
    for chain in (world["probes"], world["bath"], world["bulletins"],
                  world["events"], world["loggers"]):
        ok, msg, _ = chain.verify_chain()
        if not ok:
            problems.append(f"{chain.name} 链失效：{msg}")

    tube = world["tubes"][tube_id]
    # 跨柜交接回单必须绑定本管与本时段
    if "receipt_id" in tube:
        rec = world["loggers"].find(tube["receipt_id"])["body"]["record"]
        if rec["bound_tube"] != tube_id:
            problems.append(
                f"跨柜回单 {rec['id']} 实绑 {rec['bound_tube']}（窗口 "
                f"{rec['window'][0]}-{rec['window'][1]}h），与 {tube_id} 不符")
        else:
            w0, w1 = rec["window"]
            transport = [seg for seg in tube["timeline"] if seg[2] == "transport"]
            t0, t1 = transport[0][0], transport[0][1]
            if (w0, w1) != (t0, t1):
                problems.append(f"回单 {rec['id']} 时段与交接段不一致")
    # 该管相关事件的绑定与顺序
    for env in world["events"].envelopes:
        rec = env["body"]["record"]
        if rec.get("tube") == tube_id and rec["type"] != "receipt-correction":
            if "seq" not in rec or rec["seq"] < 1:
                problems.append(f"事件 {rec['id']} 顺序号缺失")
    return {"ok": not problems, "problems": problems}


def leadability(world: dict, tube_id: str) -> dict:
    prov = verify_provenance(world, tube_id)
    expo = worst_exposure(world, tube_id)
    reasons = list(prov["problems"])
    if expo["eq_hours"] > BUDGET_EQH:
        reasons.append(
            f"最不利暴露 {expo['eq_hours']:.1f} 等效小时 > 预算 {BUDGET_EQH}")
    leadable = prov["ok"] and not reasons
    return {
        "tube": tube_id, "leadable": leadable,
        "eq_hours": expo["eq_hours"], "mean_upper_c": expo["mean_upper_c"],
        "rebuilt_hours": expo["rebuilt_hours"], "reasons": reasons,
    }
