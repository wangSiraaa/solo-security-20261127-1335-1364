"""本地解释器：按交付配置回放一个采样周期内的短事件列。

VM 逐语句步进，仅在开放中断点（call 边界、irq on、region 入口）派发挂起
事件；调用者帧原样保留在栈上，中断返回后按帧上记录的屏蔽掩码恢复现场，
因此高打断低的嵌套返回可被真实观测。work 是函数体末尾的隐式计时语句。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from .deliver import verify_fingerprints
from .model import HIGH, LOW, LEVELS, Function, Program
from .parser import parse_board_file, parse_program_file

_PRIORITY = {HIGH: 1, LOW: 0}


class ReplayError(Exception):
    pass


@dataclass
class Frame:
    func: Function
    stmts: list
    pc: int = 0
    mask: frozenset[str] = frozenset()       # 本帧当前开放的中断等级
    in_region: bool = False                  # pc 是否正停在 region 语句内


@dataclass
class Event:
    at: int
    level: str
    seq: int


@dataclass
class EventRecord:
    seq: int
    level: str
    arrival: int
    started: int | None = None
    finished: int | None = None
    latency: int | None = None
    response: int | None = None
    state: str = "arrived"                   # arrived / served / deferred_overflow


@dataclass
class ReplayResult:
    ok: bool
    mode: str
    stack_model: str
    peak_bytes: int
    capacity_bytes: int
    overflow: bool
    finished_at: int
    records: list[EventRecord]
    violations: list[str] = field(default_factory=list)


def _load_sources(cfg: dict, base_dir: str):
    problems = verify_fingerprints(cfg, base_dir)
    if problems:
        raise ReplayError("；".join(problems))
    prog_path, board_path = cfg["program"]["path"], cfg["board"]["path"]
    if not os.path.isabs(prog_path):
        prog_path = os.path.join(base_dir, prog_path)
        board_path = os.path.join(base_dir, board_path)
    return parse_program_file(prog_path), parse_board_file(board_path, cfg["board"]["name"])


class _VM:
    def __init__(self, prog: Program, board, cfg: dict, mode: str,
                 events: list[Event]):
        self.prog = prog
        self.board = board
        self.cfg = cfg
        self.mode = mode
        self.model = cfg["stack_model"]
        self.events = sorted(events, key=lambda e: (e.at, -_PRIORITY[e.level]))
        self.t = 0
        self.ctx = "fg"                                    # fg / low / high
        self.stacks: dict[str, list[Frame]] = {"fg": [], HIGH: [], LOW: []}
        self.queued: dict[str, Event | None] = {HIGH: None, LOW: None}
        self._active_events: list[int] = []
        self.records = {e.seq: EventRecord(e.seq, e.level, e.at) for e in self.events}
        self.peak = 0
        self.overflow = False
        self.capacity = sum(r["size"] for r in cfg["memory_layout"])

    # -- 栈记账 --------------------------------------------------------------
    def _depth(self) -> int:
        if self.model == "shared":
            return sum(f.func.frame for stk in self.stacks.values() for f in stk)
        if self.ctx == "fg":
            return sum(f.func.frame for f in self.stacks["fg"])
        return sum(f.func.frame for ctx in (LOW, HIGH) for f in self.stacks[ctx])

    def _charge(self) -> None:
        self.peak = max(self.peak, self._depth())
        if self._depth() > self.capacity:
            self.overflow = True

    def _top(self) -> Frame:
        return self.stacks[self.ctx][-1]

    def _push_frame(self, fn: Function) -> Frame:
        stmts = list(fn.body) + ([("work", fn.work)] if fn.work else [])
        frame = Frame(fn, stmts)
        self.stacks[self.ctx].append(frame)
        self._charge()
        return frame

    # -- 事件 ---------------------------------------------------------------
    def _inject(self) -> None:
        for ev in self.events:
            rec = self.records[ev.seq]
            if rec.state != "arrived" or ev.at > self.t:
                continue
            if self.queued[ev.level] is None:
                self.queued[ev.level] = ev
                rec.state = "queued"            # 已挂起，避免重复注入
            # 槽位被占：保持 arrived，等当前同级事件处理完后下一轮再入队

    def _pump(self) -> None:
        """开放点：反复派发当前可运行的最高优先级挂起事件。"""
        while True:
            choice = None
            for lvl in (HIGH, LOW):
                ev = self.queued[lvl]
                if ev is None or lvl not in self._top().mask:
                    continue
                if self.ctx == HIGH or self.ctx == lvl:
                    continue
                choice = lvl
                break
            if choice is None:
                return
            self._enter_isr(choice)
            self._run()
            self._leave_isr(choice)

    def _enter_isr(self, level: str) -> None:
        ev = self.queued[level]
        self.queued[level] = None
        rec = self.records[ev.seq]
        rec.started = self.t
        rec.latency = self.t - ev.at
        self.ctx = level
        self._active_events.append(ev.seq)
        fn = self.prog.functions[self.prog.isrs[level].handler]
        self._push_frame(fn)                       # 处理函数从全关状态起步

    def _leave_isr(self, level: str) -> None:
        seq = self._active_events.pop()
        rec = self.records[seq]
        rec.finished = self.t
        rec.response = self.t - rec.arrival
        rec.state = "served"
        # 处理函数帧已在 _run 退出时弹出；防御式清空残留
        self.stacks[level].clear()
        self.ctx = LOW if self.stacks[LOW] else "fg"
        self._charge()

    # -- 执行 ---------------------------------------------------------------
    def _eligible(self, fn: Function) -> bool:
        if fn.is_handler or not fn.modes:
            return True
        return self.mode in fn.modes

    def _run(self) -> None:
        stk = self.stacks[self.ctx]
        while stk:
            self._inject()
            frame = stk[-1]
            if frame.pc >= len(frame.stmts):
                stk.pop()
                self._charge()
                if not stk:
                    return
                continue
            st = frame.stmts[frame.pc]
            frame.pc += 1
            kind = st[0]
            if kind == "work":
                self.t += st[1]
                self._inject()                      # 时间推进后新事件可能已到达
            elif kind == "call":
                callee = self.prog.functions[st[1]]
                if not self._eligible(callee):
                    continue
                child = self._push_frame(callee)
                child.mask = frame.mask           # 继承调用者的开放状态
                self._pump()                       # call 边界是开放中断点
            elif kind == "irq":
                action, levels = st[1], st[2]
                cur = set(frame.mask)
                for lvl in levels:
                    (cur.add if action == "on" else cur.discard)(lvl)
                frame.mask = frozenset(cur)
                frame.in_region = False
                if action == "on":
                    self._pump()                   # irq on 是开放中断点
            elif kind == "region":
                region = st[1]
                frame.in_region = True
                frame.mask = frozenset(region.mask)
                self._pump()                       # region 入口是开放中断点
                self._run_region(region.body, frame)
                frame.in_region = False

    def _run_region(self, body: list, owner: Frame) -> None:
        # 区间语句借用属主帧执行，不新增栈帧
        for st in body:
            kind = st[0]
            if kind == "call":
                callee = self.prog.functions[st[1]]
                if not self._eligible(callee):
                    continue
                child = self._push_frame(callee)
                child.mask = owner.mask
                self._pump()
                self._run()
            elif kind == "irq":
                action, levels = st[1], st[2]
                cur = set(owner.mask)
                for lvl in levels:
                    (cur.add if action == "on" else cur.discard)(lvl)
                owner.mask = frozenset(cur)
                if action == "on":
                    self._pump()
            elif kind == "region":
                old = owner.mask
                owner.mask = frozenset(st[1].mask)
                self._pump()
                self._run_region(st[1].body, owner)
                owner.mask = old
            # 区间内不允许直接 work（work 属于具体函数帧）

    def run(self) -> ReplayResult:
        entry = self.prog.functions[self.cfg["modes"][self.mode]]
        self._push_frame(entry)
        self._run()
        # 前台结束后继续抽空：按到达时刻注入并处理周期内剩余事件
        while True:
            self._inject()
            pending = [ev for ev in self.queued.values() if ev is not None]
            if not pending:
                break
            level = HIGH if self.queued[HIGH] is not None else LOW
            self.ctx = level
            self._enter_isr(level)
            self._run()
            self._leave_isr(level)
        for rec in self.records.values():
            if rec.state == "arrived":
                rec.state = "deferred_overflow"
        violations = list(self._violations())
        return ReplayResult(
            ok=not violations and not self.overflow,
            mode=self.mode,
            stack_model=self.model,
            peak_bytes=self.peak,
            capacity_bytes=self.capacity,
            overflow=self.overflow,
            finished_at=self.t,
            records=sorted(self.records.values(), key=lambda r: r.seq),
            violations=violations,
        )

    def _violations(self) -> list[str]:
        g = self.cfg["guarantees"]
        for rec in self.records.values():
            if rec.state == "deferred_overflow":
                yield f"事件 #{rec.seq}({rec.level}) 到达时同级事件仍挂起，按丢弃/合并处理"
                continue
            dl = g["deadline_us"][rec.level]
            if rec.response is not None and rec.response > dl:
                yield (f"事件 #{rec.seq}({rec.level}) 实测响应 {rec.response}us "
                       f"超过最晚响应 {dl}us")
        if self.overflow:
            yield f"物理栈溢出：实测峰值 {self.peak}B 超过配置容量 {self.capacity}B"
        if self.t > self.board.sample_period:
            yield f"回放历时 {self.t}us 超过采样周期 {self.board.sample_period}us"


def replay(cfg: dict, event_list: list[dict], mode: str | None = None,
           base_dir: str = ".") -> ReplayResult:
    prog, board = _load_sources(cfg, base_dir)
    mode = mode or next(iter(cfg["modes"]))
    if mode not in cfg["modes"]:
        raise ReplayError(f"配置中没有模式 {mode!r}，可选 {sorted(cfg['modes'])}")
    events = [Event(at=int(e["at"]), level=e["level"], seq=i)
              for i, e in enumerate(event_list)]
    for ev in events:
        if ev.level not in LEVELS:
            raise ReplayError(f"事件等级 {ev.level!r} 非法")
        if not 0 <= ev.at < board.sample_period:
            raise ReplayError(
                f"事件 #{ev.seq} 到达 {ev.at}us 超出采样周期 [0,{board.sample_period})"
            )
    for lvl in LEVELS:
        n = sum(1 for e in events if e.level == lvl)
        bound = cfg["guarantees"]["arrivals_per_period"][lvl]
        if n > bound:
            raise ReplayError(
                f"{lvl} 事件 {n} 个超过交付承诺的每周期 {bound} 个；该事件列不在保证范围内"
            )
    return _VM(prog, board, cfg, mode, events).run()
