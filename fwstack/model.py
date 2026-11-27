"""固件程序与板卡的核心数据模型（仅标准库）。"""
from __future__ import annotations

from dataclasses import dataclass, field

# 中断等级
HIGH = "high"
LOW = "low"
LEVELS = (HIGH, LOW)

# 两种栈接法
SHARED = "shared"      # 前台与中断共用一段栈，可在可证明的短区间推迟中断
ISOLATED = "isolated"  # 隔离的中断栈，从固定内存分区，支持嵌套返回


@dataclass
class Region:
    """开放中断标记区间。mask 为区间内开放的中断等级集合。"""
    name: str
    mask: tuple[str, ...]
    body: list          # 语句列表


@dataclass
class Function:
    name: str
    frame: int          # 本函数自身栈占用（字节）
    work: int           # 本函数自身执行时间（微秒，不含被调用者）
    body: list          # 语句：call/region/irq_on/irq_off
    modes: set[str] = field(default_factory=set)          # 空=两种模式共用
    is_handler: bool = False
    handler_level: str | None = None


@dataclass
class Isr:
    level: str
    handler: str
    arrivals_per_period: int          # 每采样周期允许的最大到达次数（延迟承诺的一部分）


@dataclass
class Mode:
    name: str
    entry: str


@dataclass
class Program:
    name: str
    functions: dict[str, Function]
    isrs: dict[str, Isr]
    modes: dict[str, Mode]
    source_path: str | None = None
    source_text: str = ""


@dataclass
class Reserved:
    name: str
    start: int
    size: int

    @property
    def end(self) -> int:
        return self.start + self.size


@dataclass
class Board:
    name: str
    ram_total: int
    reserved: list[Reserved]
    sample_period: int                # 采样周期（微秒）
    deadlines: dict[str, int]         # 每个中断等级的最晚响应（微秒）
    source_path: str | None = None
    source_text: str = ""

    def free_gaps(self) -> list[tuple[int, int]]:
        """返回 RAM 中按地址排序的空闲 (start, size) 区间。"""
        blocked = sorted((r.start, r.end) for r in self.reserved)
        gaps: list[tuple[int, int]] = []
        cur = 0
        for start, end in blocked:
            if start > cur:
                gaps.append((cur, start - cur))
            cur = max(cur, end)
        if cur < self.ram_total:
            gaps.append((cur, self.ram_total - cur))
        return gaps
