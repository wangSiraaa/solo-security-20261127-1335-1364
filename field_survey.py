# -*- coding: utf-8 -*-
"""
野外调查隐私覆盖仿真（security exercise 1485）
================================================

场景重述（去隐喻）：
  馆方持有未公布遗址的真实坐标（私有秘密），只对外声明每个密点所在的
  等价类（“落在这几格之一”）。外队拿不到含真坐标的底图，只能拿到公开
  地形（道路、坡面、公开片区），要靠自己实走 + 观察窗完成可复查覆盖，
  馆方凭实报记录做覆盖复核与签收。

安全目标：
  对同一等价类内两个候选世界 W0 / W1（密点互换），外队在方案允许观察
  到的全部资料（任务书、约束、路线、观察记录、受理反馈、签收单）上
  无法区分——两份转录稿必须逐字节相同。

活性目标：
  正常任务中，至少一个受保护片区（真实密点所在片区）能被完整签收。

本文件以 10x8 方格世界把题面每一条要求跑成可复查断言：
  1. 方案一（静态任务带）与方案二（交互式路线包）各跑通；
  2. 密点互换后转录稿相同，敌手后验仍等于整个等价类；
  3. 扰动一：收窄一处公开区边界 -> 公开任务与实走签收范围同时缩小；
  4. 扰动二：一条路改为仅能下坡 -> 任务带不变但实走轨迹/换路发生变化；
  5. 工头事后披露的坡向、双人会合班次、路口封闭全部纳入合法性校验；
  6. 外队领取的任何资料中不含遗址标识或真坐标归属字段。
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# 1. 公开地形：方格、道路/坡面通行图、公开片区、视线遮挡
# ---------------------------------------------------------------------------

GRID_W, GRID_H = 10, 8
Cell = tuple[int, int]
OneWayEdge = tuple[Cell, Cell]          # 有向边（允许方向）

# 节点可读名（仅用于报告输出；坐标本身是公开的，秘密是“哪一格是遗址”）
NAMES: dict[Cell, str] = {}
for _x in range(7):
    NAMES[(_x, 1)] = f"R0{_x + 1}"          # 底部道路 y=1
    NAMES[(_x, 3)] = f"R1{_x + 1}"          # 中部道路 y=3
    NAMES[(_x, 5)] = f"R2{_x + 1}"          # 顶部道路 y=5
NAMES.update({
    (1, 2): "C12", (1, 4): "C14",
    (6, 2): "C62", (6, 4): "C64",
    (4, 2): "S42", (4, 4): "S44",
    (2, 2): "S22", (2, 4): "S24",
    (3, 2): "V32", (3, 4): "V34",
    (5, 2): "V52", (5, 4): "V54",
})


def _hline(y: int) -> list[tuple[Cell, Cell]]:
    return [((x, y), (x + 1, y)) for x in range(6)]


def _vline(x: int, y1: int, y2: int) -> list[tuple[Cell, Cell]]:
    return [((x, y), (x, y + 1)) for y in range(y1, y2)]


BASE_EDGES: set[frozenset[Cell]] = set()
for _e in _hline(1) + _hline(3) + _hline(5):
    BASE_EDGES.add(frozenset(_e))
for _x, _y1, _y2 in ((1, 1, 5), (6, 1, 5), (4, 1, 5), (2, 1, 5),
                     (3, 1, 3), (3, 3, 5), (5, 1, 3), (5, 3, 5)):
    for _e in _vline(_x, _y1, _y2):
        BASE_EDGES.add(frozenset(_e))
# 坡面横向联络线 y=2 / y=4
for _e in [((2, 2), (3, 2)), ((3, 2), (4, 2)), ((4, 2), (5, 2)),
           ((2, 4), (3, 4)), ((3, 4), (4, 4)), ((4, 4), (5, 4)),
           ((5, 4), (6, 4))]:
    BASE_EDGES.add(frozenset(_e))

# 高脊线：视线遮挡物（公开地形资料的一部分）
RIDGE: set[Cell] = {(x, 6) for x in range(GRID_W)}


def manhattan(a: Cell, b: Cell) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def visible_from(v: Cell, c: Cell) -> bool:
    """观察窗：曼哈顿距离 <= 2，且最短视线上不跨脊线格（终点不算）。"""
    if manhattan(v, c) > 2:
        return False
    # 规范最短视线：先水平后竖直
    x, y = v
    steps: list[Cell] = []
    dx = 1 if c[0] > x else -1
    for _ in range(abs(c[0] - x)):
        x += dx
        steps.append((x, y))
    dy = 1 if c[1] > y else -1
    for _ in range(abs(c[1] - y)):
        y += dy
        steps.append((x, y))
    return not any(p in RIDGE for p in steps[:-1])


# 公开片区（合同规定可公开）与基础任务带
PARCELS: dict[str, set[Cell]] = {
    "P1": {(2, 2), (3, 2), (2, 3), (3, 3)},
    "P2": {(4, 2), (5, 2), (4, 3), (5, 3)},
    "P3": {(2, 4), (3, 4), (2, 5), (3, 5)},
    "P4": {(4, 4), (5, 4), (4, 5), (5, 5)},
    "P5": {(6, 4), (7, 4), (6, 5), (7, 5)},   # 不在任务带内的公开片区
}
BELT: set[Cell] = set().union(*(PARCELS[p] for p in ("P1", "P2", "P3", "P4")))

# ---------------------------------------------------------------------------
# 2. 私有世界：等价类与真实密点（只有馆方可见）
# ---------------------------------------------------------------------------

EQUIV_CLASS: list[Cell] = [(3, 2), (3, 4)]      # 馆方声明：密点必居其一


@dataclass(frozen=True)
class World:
    site: Cell                       # 真实遗址坐标（私有）
    guard_distance: int = 2          # 保护距离（私有参数，不进外队资料）

    def __post_init__(self):
        assert self.site in EQUIV_CLASS


W0 = World(EQUIV_CLASS[0])
W1 = World(EQUIV_CLASS[1])
WORLDS = [W0, W1]

# ---------------------------------------------------------------------------
# 3. 工头在任务下发后才披露的公开动态约束
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ForemanNotice:
    rendezvous: Cell = (6, 5)        # 双人设备必须就近会合的节点
    rendezvous_t: int = 10           # 会合班次
    closed_node: Cell = (5, 3)       # 随后封闭的路口（R16）
    closed_from_t: int = 12          # 自该班次起禁止进入
    # 坡向策略：只允许 a -> b 单向通行的有向边（默认空=全部双向）
    one_way: tuple[OneWayEdge, ...] = ()


# ---------------------------------------------------------------------------
# 4. 独立记录器：只记公开资料（班次、班组、节点、可见地块）
#    结构上不接收任何 World/遗址字段
# ---------------------------------------------------------------------------

@dataclass
class Recorder:
    squad: str
    log: list[tuple[int, str, Cell, tuple[Cell, ...]]] = field(default_factory=list)

    def record(self, t: int, node: Cell, horizon: set[Cell]):
        self.log.append((t, self.squad, node,
                         tuple(sorted(horizon))))


@dataclass
class Transcript:
    """外队在整个方案中能拿到的全部资料（敌手可见物）。"""
    task_id: str
    belt: tuple[Cell, ...]
    parcels: dict[str, tuple[Cell, ...]]
    notice: ForemanNotice
    routes: dict[str, tuple[Cell, ...]]
    records: tuple[tuple[int, str, Cell, tuple[Cell, ...]], ...]
    feedback: tuple[tuple[str, str, bool], ...]   # (分区, 受理项, 是否通过)
    receipts: tuple[str, ...]                      # 完整签收的公开分区

    def to_public_blob(self) -> bytes:
        # 模拟“外队可取到的全部资料”的序列化，便于做逐字节不可区分比对
        return repr(sorted(self.__dict__.items(), key=lambda kv: kv[0])).encode()


# ---------------------------------------------------------------------------
# 5. 路线（只用公开信息生成；签名里没有 World）
# ---------------------------------------------------------------------------

def _squad_a_prefix() -> list[Cell]:
    # R01 沿底部道路到 R07，再沿 x=6 上坡，恰好 t=10 到会合点
    return [(x, 1) for x in range(7)] + [(6, 2), (6, 3), (6, 4), (6, 5)]


def _squad_b_prefix(meet_t: int) -> list[Cell]:
    seq = [(x, 5) for x in range(7)]              # t=0..6 到达 R27
    seq += [(6, 5)] * (meet_t - len(seq) + 1)     # 原地等待至会合班次
    return seq


def _joint_sweep_baseline() -> list[Cell]:
    """任务带全扫（基线方案）。t=10 从会合点 R27 出发。"""
    return [
        (6, 5), (6, 4), (6, 3), (6, 2), (6, 1),          # 10..14 下到 R07
        (5, 1), (5, 2), (4, 2), (4, 1),                  # 15..18 收 P2
        (3, 1), (2, 1), (2, 2), (2, 3),                  # 19..22 经 e* 上坡收 P1
        (2, 4), (2, 5), (3, 5), (4, 5), (5, 5), (6, 5),  # 23..28 收 P3/P4
    ]


def _joint_sweep_downhill_only() -> list[Cell]:
    """扰动二：e*=(R03->S22) 仅能下坡后，改走 x=3 坡面上行的绕行线。"""
    return [
        (6, 5), (6, 4), (6, 3), (6, 2), (6, 1),
        (5, 1), (5, 2), (4, 2), (4, 1),
        (3, 1), (3, 2), (3, 3), (2, 3),                  # 不再踏 e*，改 V32/R14
        (2, 4), (2, 5), (3, 5), (4, 5), (5, 5), (6, 5),
    ]


def _joint_sweep_narrowed() -> list[Cell]:
    """扰动一：P1 收窄、任务带少一格后，取消西侧甩尾的短路线。"""
    return [
        (6, 5), (6, 4), (6, 3), (6, 2), (6, 1),
        (5, 1), (5, 2), (4, 2), (4, 1),
        (3, 1), (3, 2), (3, 3), (2, 3),
        (2, 4), (2, 5), (3, 5), (4, 5), (5, 5),          # 提前一班结束
    ]


def build_routes(sweep: list[Cell], notice: ForemanNotice) -> dict[str, list[Cell]]:
    """拼接前缀（含等待）与会合后的联合扫线，两班组时间轴等长。"""
    a = _squad_a_prefix()
    b = _squad_b_prefix(notice.rendezvous_t)
    assert a[notice.rendezvous_t] == notice.rendezvous
    assert b[notice.rendezvous_t] == notice.rendezvous
    assert sweep[0] == notice.rendezvous
    a += sweep[1:]
    b += sweep[1:]
    assert len(a) == len(b)
    return {"甲队": a, "乙队": b}


# ---------------------------------------------------------------------------
# 6. 馆方复核器（唯一接触私有世界的组件）
# ---------------------------------------------------------------------------

class MuseumReject(Exception):
    pass


def _check_move(u: Cell, v: Cell, t: int, notice: ForemanNotice):
    if u == v:  # 等待
        return
    if frozenset((u, v)) not in BASE_EDGES:
        raise MuseumReject(f"t={t} 边 {u}->{v} 不在公开通行图上")
    for a, b in notice.one_way:       # 该无向边只允许 a -> b，逆向禁止
        if frozenset((u, v)) == frozenset((a, b)) and (u, v) != (a, b):
            raise MuseumReject(f"t={t} {u}->{v} 违反仅能下坡的坡向管制 {a}->{b}")
    if v == notice.closed_node and t >= notice.closed_from_t:
        raise MuseumReject(f"t={t} 进入已封闭路口 {v}")


def museum_review(world: World,
                  routes: dict[str, list[Cell]],
                  notice: ForemanNotice,
                  belt: set[Cell],
                  parcels: dict[str, set[Cell]],
                  task_id: str) -> Transcript:
    recorders = {"甲队": Recorder("甲队"), "乙队": Recorder("乙队")}
    horizon: dict[Cell, set[Cell]] = {}
    T = len(next(iter(routes.values())))

    for t in range(T):
        positions = {s: seq[t] for s, seq in routes.items()}
        # 会合班次校验
        if t == notice.rendezvous_t:
            if len(set(positions.values())) != 1 or \
                    next(iter(positions.values())) != notice.rendezvous:
                raise MuseumReject(f"t={t} 双人设备未在 {notice.rendezvous} 会合")
        for squad, node in positions.items():
            if t > 0:
                _check_move(routes[squad][t - 1], node, t, notice)
            if node not in horizon:
                horizon[node] = {c for c in
                                 (c for x in range(GRID_W) for y in range(GRID_H)
                                  for c in [(x, y)])
                                 if visible_from(node, c)}
            recorders[squad].record(t, node, horizon[node])

    observed: set[Cell] = set()
    for rec in recorders.values():
        for _, _, _, cells in rec.log:
            observed.update(cells)

    covered_belt = belt & observed
    if covered_belt != belt:
        raise MuseumReject(f"任务带未全覆盖，缺 {sorted(belt - observed)}")

    # 按公开分区出受理反馈与签收单（反馈只认分区，不认遗址）
    feedback: list[tuple[str, str, bool]] = []
    receipts: list[str] = []
    for pid, cells in parcels.items():
        ok = cells <= observed
        feedback.append((pid, "分区覆盖受理", ok))
        if ok and cells <= belt:
            receipts.append(pid)

    # 私有活性检查（结果不写进转录稿）：
    #  (a) 至少一个完整公开分区签收；
    #  (b) 真实密点格必须被某个观察窗实际看到；若它仍归属某公开分区，
    #      则该保护片区须被完整签收（基线场景下成立）。
    assert receipts, "无任何完整签收的保护片区"
    if world.site not in observed:
        raise MuseumReject("真实密点格未被任何观察窗覆盖")
    protected_parcel = next((pid for pid, cells in parcels.items()
                             if world.site in cells), None)
    if protected_parcel is not None and protected_parcel not in receipts:
        raise MuseumReject("真实密点所在保护片区未完整签收")

    all_records = tuple(sorted(
        r for rec in recorders.values() for r in rec.log))
    return Transcript(
        task_id=task_id,
        belt=tuple(sorted(belt)),
        parcels={p: tuple(sorted(c)) for p, c in parcels.items()},
        notice=notice,
        routes={s: tuple(seq) for s, seq in routes.items()},
        records=all_records,
        feedback=tuple(feedback),
        receipts=tuple(sorted(receipts)),
    )


# ---------------------------------------------------------------------------
# 7. 方案二：馆内受理等价路线包，只按公开分区给统一反馈
# ---------------------------------------------------------------------------

def _package_b_routes(notice: ForemanNotice) -> dict[str, list[Cell]]:
    """东线半包：两班组均在 R27 等待至会合，之后严格沿 x>=5 的东线扫线，
    绕开 t12 起封闭的 R16，观察窗（曼哈顿距离<=2）够不到西侧 P1/P3，
    仅完整覆盖 P2/P4。"""
    a = [(6, 5)] * 10
    b = [(6, 5)] * 10
    sweep = [
        (6, 5), (6, 4), (6, 3), (6, 2), (6, 1),
        (5, 1), (5, 2), (5, 1), (6, 1), (6, 2),
        (6, 3), (6, 4), (5, 4), (5, 5), (6, 5),
        (6, 5),
    ]
    a += sweep[1:]
    b += sweep[1:]
    assert len(a) == len(b) == 25
    assert all(x >= 5 for seq in (a, b) for (x, y) in seq)
    return {"甲队": a, "乙队": b}


def route_packages(notice: ForemanNotice) -> dict[str, dict[str, list[Cell]]]:
    """外队提出的若干等价路线包（包内不含任何遗址信息）。"""
    return {
        "包甲-全带扫线": build_routes(_joint_sweep_baseline(), notice),
        # 合法的东线替代包：东侧起线、避开封闭路口，与西侧 P1/P3 保持
        # 距离，只完整覆盖 P2/P4；受理口据此给分区级反馈，不评价密点
        "包乙-东线半包": _package_b_routes(notice),
        # 封路旧线：t=17 进入自 t=12 封闭的 R16，必须被受理口驳回
        "包丙-封路旧线": build_routes([
            (6, 5), (6, 4), (6, 3), (6, 2), (6, 1),
            (5, 1), (5, 2), (5, 3),
            (5, 4), (5, 5), (4, 5), (3, 5), (2, 5),
            (2, 4), (2, 3), (2, 2), (2, 1), (3, 1), (4, 1),
        ], notice),
    }


def oracle_review(pkg_id: str,
                  routes: dict[str, list[Cell]],
                  notice: ForemanNotice,
                  belt: set[Cell],
                  parcels: dict[str, set[Cell]]) -> tuple[str, tuple[tuple[str, bool], ...]]:
    """馆方受理口：统一按公开分区给反馈，不接受也不输出密点级信息。"""
    try:
        # 受理口只做形状/约束预检，正式签收仍走 museum_review
        T = len(next(iter(routes.values())))
        for t in range(1, T):
            for seq in routes.values():
                _check_move(seq[t - 1], seq[t], t, notice)
        observed: set[Cell] = set()
        for seq in routes.values():
            for node in seq:
                observed.update(c for x in range(GRID_W) for y in range(GRID_H)
                                for c in [(x, y)] if visible_from(node, c))
        zone_feedback = tuple((pid, cells <= observed)
                              for pid, cells in parcels.items() if cells <= belt)
        return f"{pkg_id}: 受理，路线合法", zone_feedback
    except MuseumReject as e:
        return f"{pkg_id}: 驳回（{e}）", tuple()


# ---------------------------------------------------------------------------
# 8. 场景装配与断言
# ---------------------------------------------------------------------------

def run_scenario(task_id: str, sweep, notice: ForemanNotice,
                 belt: set[Cell], parcels: dict[str, set[Cell]]):
    """同一方案在 W0/W1 两个世界各跑一遍，返回两份转录稿。"""
    routes = build_routes(sweep, notice)
    t0 = museum_review(W0, routes, notice, belt, parcels, task_id)
    t1 = museum_review(W1, routes, notice, belt, parcels, task_id)
    # 安全断言：密点互换后外队全部资料逐字节相同
    assert t0.to_public_blob() == t1.to_public_blob(), \
        f"{task_id}: 两个世界的转录稿可区分！"
    # 资料洁净断言：外队资料中不得出现遗址标识/真坐标归属
    blob = t0.to_public_blob()
    for leak in (b"site", b"World", b"guard_distance", b"EQUIV"):
        assert leak not in blob, f"{task_id}: 转录稿泄漏私有字段 {leak}"
    return t0, t1, routes


def adversary_posterior(task_id: str, sweep, notice: ForemanNotice,
                        belt: set[Cell], parcels: dict[str, set[Cell]]) -> set[Cell]:
    """敌手视角：枚举等价类内所有候选世界，保留与转录稿相容者。"""
    routes = build_routes(sweep, notice)
    observed_blob = museum_review(W0, routes, notice, belt, parcels,
                                  task_id).to_public_blob()
    posterior: set[Cell] = set()
    for cand in EQUIV_CLASS:
        w = World(cand)
        blob = museum_review(w, routes, notice, belt, parcels,
                             task_id).to_public_blob()
        if blob == observed_blob:
            posterior.add(cand)
    return posterior


def main():
    report: list[str] = []
    push = report.append

    push("=" * 72)
    push("野外调查隐私覆盖仿真 —— 结论：外队无需领取未公开遗址坐标即可完工")
    push("=" * 72)

    notice = ForemanNotice()
    push(f"\n[公开地形] 节点 {len(NAMES)} 个，通行边 {len(BASE_EDGES)} 条，"
         f"脊线遮挡格 {len(RIDGE)} 个")
    push(f"[私有信息] 等价类 E = {[NAMES.get(c, c) for c in EQUIV_CLASS]}，"
         f"真实密点 W0/W1 互换；保护距离等参数不出馆")
    push(f"[工头披露] 会合 {NAMES[notice.rendezvous]}@t{notice.rendezvous_t}，"
         f"路口 {NAMES[notice.closed_node]} 自 t{notice.closed_from_t} 封闭，"
         f"单向坡向 {len(notice.one_way)} 条")

    # --- 场景 0：基线（方案一，静态任务带） ---
    t0, t1, routes0 = run_scenario(
        "基线-方案一静态任务带", _joint_sweep_baseline(), notice, BELT, PARCELS)
    walked0 = {n for seq in routes0.values() for n in seq}
    push("\n[场景0 基线] 任务带 %d 格；两班组时间轴 %d 班；实走节点 %d 个"
         % (len(t0.belt), len(routes0["甲队"]), len(walked0)))
    push("  签收分区: %s（含真实密点所在保护片区，两世界均签收）" % list(t0.receipts))
    posterior = adversary_posterior(
        "基线-方案一静态任务带", _joint_sweep_baseline(), notice, BELT, PARCELS)
    push("  敌手后验候选: %s == 等价类 E，密点互换不可区分"
         % [NAMES[c] for c in sorted(posterior)])
    assert posterior == set(EQUIV_CLASS)
    assert notice.closed_node not in walked0, "实走轨迹闯入了封闭路口"

    # --- 场景 1：扰动一，单收窄一处公开区边界 ---
    parcels_narrow = {p: set(c) for p, c in PARCELS.items()}
    parcels_narrow["P1"] = parcels_narrow["P1"] - {(3, 2)}
    belt_narrow = set().union(*(parcels_narrow[p]
                                for p in ("P1", "P2", "P3", "P4")))
    tn0, tn1, routes_n = run_scenario(
        "扰动一-收窄公开区边界", _joint_sweep_narrowed(), notice,
        belt_narrow, parcels_narrow)
    push("\n[场景1 收窄P1边界] 任务带 %d -> %d 格；班次 %d -> %d"
         % (len(t0.belt), len(tn0.belt),
            len(routes0["甲队"]), len(routes_n["甲队"])))
    push("  收窄后分区形状: P1=%s；完整签收分区: %s"
         % (tuple(sorted(parcels_narrow["P1"])), list(tn0.receipts)))
    push("  转出公开范围、不再被任务带签收的格: %s"
         % sorted(set(t0.belt) - set(tn0.belt)))
    assert set(tn0.belt) < set(t0.belt), "收窄后任务带应严格变小"
    walked_n = {n for seq in routes_n.values() for n in seq}
    assert walked_n < walked0 or len(routes_n["甲队"]) < len(routes0["甲队"]), \
        "收窄后实走范围/班次应缩小"
    assert tn0.to_public_blob() == tn1.to_public_blob()

    # --- 场景 2：扰动二，单将一条路改为仅能下坡 ---
    e_star = ((2, 1), (2, 2))                      # R03 -> S22 为上坡方向
    notice_slope = ForemanNotice(one_way=(((2, 2), (2, 1)),))  # 仅 S22->R03（下坡）
    # 旧线在新坡向下必须被复核驳回
    try:
        museum_review(W0, build_routes(_joint_sweep_baseline(), notice_slope),
                      notice_slope, BELT, PARCELS, "旧线-坡向校验")
        raise AssertionError("坡向管制下旧线应被驳回")
    except MuseumReject as e:
        push(f"\n[场景2 仅能下坡] 旧线被驳回：{e}")
    ts0, ts1, routes_s = run_scenario(
        "扰动二-道路仅能下坡", _joint_sweep_downhill_only(),
        notice_slope, BELT, PARCELS)
    walked_s = {n for seq in routes_s.values() for n in seq}
    edges0 = {frozenset((seq[t - 1], seq[t]))
              for seq in routes0.values() for t in range(1, len(seq))
              if seq[t - 1] != seq[t]}
    edges_s = {frozenset((seq[t - 1], seq[t]))
               for seq in routes_s.values() for t in range(1, len(seq))
               if seq[t - 1] != seq[t]}
    push("  任务带仍为 %d 格（公开任务范围未变），实走边集发生变化"
         % len(ts0.belt))
    push("  旧线独有边: %s；绕行新增边: %s"
         % ([(NAMES.get(u, u), NAMES.get(v, v)) for u, v in
             sorted(tuple(e) for e in edges0 - edges_s)],
            [(NAMES.get(u, u), NAMES.get(v, v)) for u, v in
             sorted(tuple(e) for e in edges_s - edges0)]))
    assert set(ts0.belt) == set(t0.belt), "坡向变化不应改变任务带本身"
    assert frozenset(e_star) in edges0, "基线上坡边 e* 应被实走"
    assert frozenset(e_star) not in edges_s, "仅能下坡后 e* 不得再被实走"
    assert edges_s != edges0
    assert ts0.to_public_blob() == ts1.to_public_blob()
    # 两种变化分别改变不同对象
    assert set(tn0.belt) != set(ts0.belt)
    push("  对照：扰动一改变任务带与签收范围；扰动二任务带不变、只改实走轨迹")

    # --- 场景 3：方案二，等价路线包 + 统一受理反馈 ---
    push("\n[方案二 交互式路线包] 外队提交三个等价包，受理口只按公开分区应答：")
    pkgs = route_packages(notice)
    response_log: list[tuple[str, str, tuple[tuple[str, bool], ...]]] = []
    chosen: Optional[str] = None
    for pid, pkg_routes in pkgs.items():
        verdict, zones = oracle_review(
            pid, pkg_routes, notice, BELT, PARCELS)
        response_log.append((pid, verdict, zones))
        push(f"  {verdict}；分区反馈 { {z: ok for z, ok in zones} }")
        # 选中条件：受理通过且任务带四个公开分区全部覆盖
        if "受理" in verdict and zones and all(ok for _, ok in zones):
            chosen = pid
    # 封路旧线被驳回、半包覆盖不足后换包：序列中不含任何密点级信息
    assert any("驳回" in v for _, v, _ in response_log), "封路旧线应被驳回"
    assert any(zones and not all(ok for _, ok in zones)
               for _, _, zones in response_log), "半包应给出覆盖不足的分区反馈"
    assert chosen == "包甲-全带扫线"
    ta0, ta1, _ = run_scenario(
        "方案二-受理后正式签收", _joint_sweep_baseline(), notice, BELT, PARCELS)
    push(f"  封闭路口触发换路：丙 -> {chosen}；正式签收分区 {list(ta0.receipts)}")
    # 同一受理序列在 W0/W1 下完全一致（oracle 签名中没有 World）
    for pid, pkg_routes in pkgs.items():
        v_a, z_a = oracle_review(pid, pkg_routes, notice, BELT, PARCELS)
        v_b, z_b = oracle_review(pid, pkg_routes, notice, BELT, PARCELS)
        assert (v_a, z_a) == (v_b, z_b)
    push("  受理/反馈序列在两个世界逐字相同，无法据此逐个排出密点")

    # --- 场景 4：方案一容量上限（面积过大接不下） ---
    belt_big = BELT | PARCELS["P5"]                       # 16 -> 20 格
    parcels_big = dict(PARCELS)
    short_routes = _package_b_routes(notice)              # 短班数东线包
    verdict_big, zones_big = oracle_review(
        "扩带-短路线", short_routes, notice, belt_big, parcels_big)
    push(f"\n[场景4 容量上限] 任务带扩至 {len(belt_big)} 格、沿用短班数"
         f"路线（{len(short_routes['甲队'])} 班）：{verdict_big}")
    push(f"  分区反馈 { {z: ok for z, ok in zones_big} }；"
         f"覆盖不足即静态隐匿带面积超过班组容量/班次上限，接不下，"
         f"需改走方案二按包受理")
    assert not all(ok for _, ok in zones_big)

    push("\n" + "=" * 72)
    push("全部断言通过：")
    push(" 1) 密点互换转录稿逐字节相同，敌手后验=整个等价类；")
    push(" 2) 外队资料不含任何遗址标识/真坐标归属字段；")
    push(" 3) 正常任务中真实密点被观察窗实际覆盖、保护片区完整签收（活性）；")
    push(" 4) 收窄边界 -> 公开任务与实走签收范围同时严格缩小；")
    push(" 5) 仅能下坡 -> 任务带不变、旧线驳回、实走轨迹绕行变化；")
    push(" 6) 会合班次、封闭路口、坡向管制均由馆方复核器独立拦截校验。")
    push("=" * 72)
    print("\n".join(report))


if __name__ == "__main__":
    main()
