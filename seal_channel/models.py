"""领域常量、参数解析与返回结构。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .errors import ValidationError

ALPHA = "alpha"
BETA = "beta"

TRIP_LABELS = {ALPHA: "甲", BETA: "乙"}
LABEL_TO_TRIP = {v: k for k, v in TRIP_LABELS.items()}

_TRIP_ALIASES = {
    "a": ALPHA,
    "alpha": ALPHA,
    "jia": ALPHA,
    "jiafang": ALPHA,
    "甲": ALPHA,
    "甲方": ALPHA,
    "1": ALPHA,
    "b": BETA,
    "beta": BETA,
    "yi": BETA,
    "yifang": BETA,
    "乙": BETA,
    "乙方": BETA,
    "2": BETA,
}

GRANTED = "granted"
UNASSIGNED = "unassigned"
REQUESTED = "requested"


def normalize_trip(trip: str) -> str:
    key = str(trip).strip().lower()
    if key in _TRIP_ALIASES:
        return _TRIP_ALIASES[key]
    # 中文不受 lower 影响；再直接查一次。
    if str(trip).strip() in _TRIP_ALIASES:
        return _TRIP_ALIASES[str(trip).strip()]
    raise ValidationError(
        f"未知终端/行程：{trip!r}，请使用 alpha/甲 或 beta/乙",
        code="UNKNOWN_TRIP",
    )


def trip_label(trip: str) -> str:
    return TRIP_LABELS[normalize_trip(trip)]


def parse_seals(value: str | int | Iterable[int], book_size: int = 6) -> List[int]:
    """把 ``"1,3,6"``、``"1-3"`` 或可迭代对象解析成排序后的封条号。"""

    if isinstance(value, int):
        values = [value]
    elif isinstance(value, str):
        values: List[int] = []
        for chunk in value.replace("，", ",").replace("、", ",").split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if "-" in chunk:
                start_s, end_s = chunk.split("-", 1)
                start, end = int(start_s), int(end_s)
                if start > end:
                    start, end = end, start
                values.extend(range(start, end + 1))
            else:
                values.append(int(chunk))
    else:
        values = [int(v) for v in value]

    seals = sorted(set(values))
    if not seals:
        raise ValidationError("封条号列表不能为空", code="EMPTY_SEAL_LIST")
    for n in seals:
        if not 1 <= n <= book_size:
            raise ValidationError(
                f"封条号 {n} 超出全册范围 1-{book_size}",
                code="SEAL_OUT_OF_RANGE",
                details={"seal": n, "book_size": book_size},
            )
    return seals


def validate_partition(
    alpha: Sequence[int],
    beta: Sequence[int],
    book_size: int = 6,
    per_trip: int = 3,
) -> tuple[List[int], List[int]]:
    a = parse_seals(alpha, book_size)
    b = parse_seals(beta, book_size)
    if len(a) != per_trip:
        raise ValidationError(
            f"甲必须分得 {per_trip} 枚封条号，实际 {len(a)} 枚：{a}",
            code="BAD_SEAL_COUNT",
        )
    if len(b) != per_trip:
        raise ValidationError(
            f"乙必须分得 {per_trip} 枚封条号，实际 {len(b)} 枚：{b}",
            code="BAD_SEAL_COUNT",
        )
    if set(a) & set(b):
        raise ValidationError(
            f"两台终端的封条号不能重叠：{sorted(set(a) & set(b))}",
            code="OVERLAPPING_SEALS",
        )
    if sorted(a + b) != list(range(1, book_size + 1)):
        raise ValidationError(
            "两台各三枚时必须恰好分完整册 1-6，不能漏号或留空",
            code="INCOMPLETE_PARTITION",
            details={"alpha": a, "beta": b, "book_size": book_size},
        )
    return a, b


@dataclass
class Verification:
    ok: bool
    code: str = "OK"
    reason: str = ""
    seal: Optional[int] = None
    owner: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ReceiveResult:
    status: str
    ok: bool
    seal: Optional[int] = None
    owner: Optional[str] = None
    code: str = "OK"
    message: str = ""
    high_water: Optional[int] = None
    window: Optional[List[int]] = None
    ledger_id: Optional[str] = None
    receipt_id: Optional[str] = None
    duplicate: bool = False
    manual_required: bool = False
    rejected: bool = False
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
