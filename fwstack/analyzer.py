"""静态分析：调用图展开、栈峰值、中断封闭区间、最坏响应、内存放置。

所有时间单位微秒，栈单位字节。分析给出的是安全上界：
响应上界采用 R_L = W_L + C_L + Σ(高优先级到达数 × 高优先级处理时间)。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .model import HIGH, LOW, LEVELS, ISOLATED, SHARED, Board, Function, Program

_HIGHER = {HIGH: (), LOW: (HIGH,)}


@dataclass
class OpenPoint:
    op_id: str
    function: str
    path: str
    mask: tuple[str, ...]


@dataclass
class Segment:
    t0: int
    t1: int
    mask: frozenset[str]
    depth: int


@dataclass
class ModeReport:
    name: str
    entry: str
    fg_peak: int
    fg_time: int
    max_closed: dict[str, int]
    open_points: list[OpenPoint]


@dataclass
class LevelReport:
    level: str
    handler: str
    arrivals: int
    handler_peak: int          # 处理函数调用树自身峰值
    handler_time: int          # C_L：处理函数总执行时间
    nested_peak: int           # 含高一级嵌套后的中断侧峰值（相对中断栈基址）
    max_closed: int            # W_L：本等级被持续屏蔽的最长时间（跨所有上下文）
    worst_latency: int         # 到达 -> 处理函数首条语句
    worst_response: int        # 到达 -> 处理完成
    deadline: int | None
    meets_deadline: bool | None


@dataclass
class RegionPlacement:
    name: str
    size: int
    start: int | None
    end: int | None


@dataclass
class StackPlan:
    model: str
    peak: int                              # 该接法需要的总栈量
    regions: list[RegionPlacement]
    fits: bool
    free_total: int
    gaps: list[str] = field(default_factory=list)


@dataclass
class AnalysisResult:
    program: Program
    board: Board | None
    mode_reports: dict[str, ModeReport]
    level_reports: dict[str, LevelReport]
    plans: dict[str, StackPlan]
    open_points: list[OpenPoint]
    period_load: int
    period: int | None
    meets_period: bool | None
    gaps: list[str]

    @property
    def ok(self) -> bool:
        return not self.gaps


class _Walker:
    """按语句顺序展开一个入口的完整时间线。"""

    def __init__(self, prog: Program, mode_name: str | None):
        self.prog = prog
        self.mode_name = mode_name
        self.segments: list[Segment] = []
        self.open_points: list[OpenPoint] = []
        self.peak = 0
        self.time = 0
        self.depth = 0
        self._op_seen: set[str] = set()

    def _eligible(self, fn: Function) -> bool:
        if fn.is_handler:
            return True
        if not fn.modes:
            return True
        return self.mode_name in fn.modes

    def run_entry(self, entry: str) -> None:
        self._walk(self.prog.functions[entry], (), frozenset())

    def _walk(self, fn: Function, path: tuple[int, ...], mask: frozenset[str]) -> None:
        if not self._eligible(fn):
            return
        self.depth += fn.frame
        self.peak = max(self.peak, self.depth)
        for idx, st in enumerate(fn.body):
            here = path + (idx,)
            kind = st[0]
            if kind == "call":
                callee = self.prog.functions[st[1]]
                self._walk(callee, here, mask)
            elif kind == "irq":
                action, levels = st[1], st[2]
                new_mask = set(mask)
                for lvl in levels:
                    if action == "on":
                        new_mask.add(lvl)
                    else:
                        new_mask.discard(lvl)
                if action == "on":
                    op_id = f"irq_on:{fn.name}:{'.'.join(map(str, here))}"
                    if op_id not in self._op_seen:
                        self._op_seen.add(op_id)
                        self.open_points.append(
                            OpenPoint(op_id, fn.name, ".".join(map(str, here)),
                                      tuple(l for l in LEVELS if l in new_mask))
                        )
                mask = frozenset(new_mask)
            elif kind == "region":
                region = st[1]
                rmask = frozenset(region.mask)
                op_id = f"region:{region.name}@{fn.name}:{'.'.join(map(str, here))}"
                if op_id not in self._op_seen:
                    self._op_seen.add(op_id)
                    self.open_points.append(
                        OpenPoint(op_id, fn.name, ".".join(map(str, here)), region.mask)
                    )
                self._walk_block(region.body, here, rmask)
        if fn.work:
            self.segments.append(Segment(self.time, self.time + fn.work, mask, self.depth))
            self.time += fn.work
        self.depth -= fn.frame

    def _walk_block(self, stmts: list, path: tuple[int, ...], mask: frozenset[str]) -> None:
        # 区间复用父函数帧：手工展开语句，不重复计帧
        for idx, st in enumerate(stmts):
            here = path + (idx,)
            kind = st[0]
            if kind == "call":
                self._walk(self.prog.functions[st[1]], here, mask)
            elif kind == "irq":
                action, levels = st[1], st[2]
                new_mask = set(mask)
                for lvl in levels:
                    if action == "on":
                        new_mask.add(lvl)
                    else:
                        new_mask.discard(lvl)
                mask = frozenset(new_mask)
            elif kind == "region":
                self._walk_block(st[1].body, here, frozenset(st[1].mask))


def _max_closed_window(segments: list[Segment], level: str) -> int:
    """该等级被持续屏蔽的最长连续时间（屏蔽期间更高优先级的执行由响应公式另行计入）。"""
    longest = cur = 0
    for seg in segments:
        if level in seg.mask:
            cur = 0
        else:
            cur += seg.t1 - seg.t0
            longest = max(longest, cur)
    return longest


def _handler_walk(prog: Program, handler_name: str) -> _Walker:
    walker = _Walker(prog, None)
    walker.run_entry(handler_name)
    return walker


def analyze(prog: Program, board: Board | None = None) -> AnalysisResult:
    gaps: list[str] = []

    # ---- 1. 每个互斥模式的前台时间线 -------------------------------------
    mode_reports: dict[str, ModeReport] = {}
    all_open: dict[str, OpenPoint] = {}
    for mname, mode in prog.modes.items():
        w = _Walker(prog, mname)
        w.run_entry(mode.entry)
        closed = {lvl: _max_closed_window(w.segments, lvl) for lvl in LEVELS}
        mode_reports[mname] = ModeReport(
            mname, mode.entry, w.peak, w.time, closed, list(w.open_points)
        )
        for op in w.open_points:
            all_open.setdefault(op.op_id, op)

    # ---- 2. 两级中断处理函数时间线与嵌套峰值 -----------------------------
    handler_walks: dict[str, _Walker] = {}
    level_reports: dict[str, LevelReport] = {}
    for lvl in LEVELS:
        isr = prog.isrs[lvl]
        hw = _handler_walk(prog, isr.handler)
        handler_walks[lvl] = hw

    # 低级处理函数被高级嵌套：在低级时间线上高级开放的最深点叠加高级峰值
    low_w = handler_walks[LOW]
    high_peak = handler_walks[HIGH].peak
    nested_low_peak = low_w.peak
    for seg in low_w.segments:
        if HIGH in seg.mask:
            nested_low_peak = max(nested_low_peak, seg.depth + high_peak)

    for lvl in LEVELS:
        isr = prog.isrs[lvl]
        hw = handler_walks[lvl]
        # W_L：前台各模式封闭区间 + 低级处理函数内部对高级的封闭区间
        closed_foreground = max(
            (mr.max_closed[lvl] for mr in mode_reports.values()), default=0
        )
        closed_in_lower = 0
        if lvl == HIGH:
            closed_in_lower = _max_closed_window(low_w.segments, HIGH)
        w_l = max(closed_foreground, closed_in_lower)

        interference = sum(
            prog.isrs[h].arrivals_per_period * handler_walks[h].time
            for h in _HIGHER[lvl]
        )
        latency = w_l + interference
        response = latency + hw.time
        deadline = board.deadlines[lvl] if board else None
        meets = None if deadline is None else response <= deadline
        if meets is False:
            gaps.append(
                f"响应违约: {lvl} 最坏响应 {response}us > 板卡最晚响应 {deadline}us"
                f"（封闭区间 {w_l}us，高优先级干扰 {interference}us，处理 {hw.time}us）"
            )
        level_reports[lvl] = LevelReport(
            lvl, isr.handler, isr.arrivals_per_period,
            hw.peak, hw.time,
            nested_low_peak if lvl == LOW else hw.peak,
            w_l, latency, response, deadline, meets,
        )

    # ---- 3. 两种栈接法的峰值 ---------------------------------------------
    shared_peaks: dict[str, int] = {}
    for mname, mr in mode_reports.items():
        w = _Walker(prog, mname)
        w.run_entry(mr.entry)
        peak = mr.fg_peak
        for seg in w.segments:
            for lvl in seg.mask:
                peak = max(peak, seg.depth + handler_walks[lvl].peak)
            if LOW in seg.mask and HIGH in seg.mask:
                peak = max(peak, seg.depth + nested_low_peak)
        shared_peaks[mname] = peak

    shared_peak = max(shared_peaks.values())
    fg_peak = max(mr.fg_peak for mr in mode_reports.values())
    irq_peak = max(nested_low_peak, handler_walks[HIGH].peak)

    # ---- 4. 采样周期占用 --------------------------------------------------
    period_load = max(mr.fg_time for mr in mode_reports.values()) + sum(
        prog.isrs[lvl].arrivals_per_period * handler_walks[lvl].time for lvl in LEVELS
    )
    meets_period = None
    if board is not None:
        meets_period = period_load <= board.sample_period
        if not meets_period:
            gaps.append(
                f"周期超载: 每周期最少占用 {period_load}us > 采样周期 {board.sample_period}us"
            )

    # ---- 5. 板卡内存放置 --------------------------------------------------
    plans: dict[str, StackPlan] = {}
    if board is not None:
        plans[SHARED] = _place_stack(
            board, SHARED,
            [RegionPlacement("main_stack", shared_peak, None, None)],
        )
        plans[ISOLATED] = _place_stack(
            board, ISOLATED,
            [
                RegionPlacement("foreground_stack", fg_peak, None, None),
                RegionPlacement("interrupt_stack", irq_peak, None, None),
            ],
        )
        for plan in plans.values():
            gaps.extend(plan.gaps)

    return AnalysisResult(
        program=prog,
        board=board,
        mode_reports=mode_reports,
        level_reports=level_reports,
        plans=plans,
        open_points=sorted(all_open.values(), key=lambda op: op.op_id),
        period_load=period_load,
        period=board.sample_period if board else None,
        meets_period=meets_period,
        gaps=gaps,
    )


def _place_stack(board: Board, model: str, regions: list[RegionPlacement]) -> StackPlan:
    """在保留区之外的空闲块中放置各栈区。

    区域数很少（<=2），直接穷举每个区域落在哪块空闲块的可行组合，
    避免贪心因分配顺序制造本可避免的碎片。
    """
    free = sorted(board.free_gaps(), key=lambda g: g[1], reverse=True)
    free_total = sum(size for _, size in free)
    gaps: list[str] = []
    total_need = sum(r.size for r in regions)

    placement = _search_placement([r.size for r in regions], free)
    if placement is None:
        largest = max((s for _, s in free), default=0)
        if total_need > free_total:
            reason = (f"总需求 {total_need}B 超过空闲总量 {free_total}B，"
                      "无论如何重排都放不进")
        else:
            reason = (f"空闲总量 {free_total}B 虽够 {total_need}B，但保留区把内存切成碎片，"
                      f"最大连续空闲块仅 {largest}B，需重排保留区或改用另一种栈接法")
        for region in sorted(regions, key=lambda r: r.size, reverse=True):
            gaps.append(
                f"内存不足[{model}]: 栈区 {region.name} 需 {region.size}B 无处放置（{reason}）"
            )
    else:
        for region, (gap_idx, start) in zip(regions, placement):
            region.start = start
            region.end = start + region.size

    return StackPlan(model, total_need, regions, not gaps, free_total, gaps)


def _search_placement(sizes: list[int], free: list[tuple[int, int]]):
    """返回每个 size 的 (空闲块序号, 起始地址)，不可行返回 None。"""
    n = len(sizes)

    def recurse(i, block_used: list[int], chosen: list):
        if i == n:
            return chosen
        order = sorted(range(len(free)), key=lambda k: free[k][1] - block_used[k],
                       reverse=True)
        for gap_idx in order:
            base, block_size = free[gap_idx]
            start = base + block_used[gap_idx]
            if block_size - block_used[gap_idx] >= sizes[i]:
                block_used[gap_idx] += sizes[i]
                chosen.append((gap_idx, start))
                found = recurse(i + 1, block_used, chosen)
                if found is not None:
                    return found
                chosen.pop()
                block_used[gap_idx] -= sizes[i]
        return None

    # 先放大区域，显著收缩搜索空间
    order = sorted(range(n), key=lambda i: sizes[i], reverse=True)
    ordered_sizes = [sizes[i] for i in order]
    found = recurse(0, [0] * len(free), [])
    if found is None:
        return None
    result = [None] * n
    for idx, answer in zip(order, found):
        result[idx] = answer
    return result
