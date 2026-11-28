"""提前安排周期与临时改约的代价（方案一：匿名连续区段 + 具名回执）。

规则对所有馆相同、可在报价前向用户公开：

==================== ==================================================
操作                 代价
==================== ==================================================
新周期预约           提前 ≥ 7 天受理，无改约费；不足 7 天按临时预订
周期内改时/取消      提前 ≥ 48 小时免费（每周期每实例一次）；
                     24–48 小时收半日房费；< 24 小时收全日房费
临时（单次）预订     提前 ≥ 24 小时按标准价；不足 24 小时加收 50%
连续两场同一口译员   需两个时段都订妥方可确认（原子），口译员占用
                     以独立资源占用表登记，避免重复占用
==================== ==================================================

“既有服务在报价有效期间要兑现”：报价一旦出具，在其有效期内（默认 24h）
按报价锁定的余量必须保留，期间别人不得抢走该余量。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

HALF_DAY_RATE = 300.0
FULL_DAY_RATE = 600.0
URGENT_SURCHARGE = 0.5
QUOTE_TTL = timedelta(hours=24)

RECURRING_LEAD = timedelta(days=7)
FREE_RESCHEDULE_LEAD = timedelta(hours=48)
HALF_DAY_LEAD = timedelta(hours=24)
TEMP_BOOKING_LEAD = timedelta(hours=24)


@dataclass(frozen=True)
class Quote:
    quote_id: str
    room: str
    start: datetime
    end: datetime
    amount: float
    currency: str
    created_at: datetime
    valid_until: datetime
    kind: str                      # "RECURRING" | "ADHOC" | "RESCHEDULE"
    held: bool = True              # 有效期内锁定余量

    def is_valid(self, moment: datetime) -> bool:
        return self.created_at <= moment <= self.valid_until


def describe_costs() -> str:
    """用户可读的代价说明（CLI 的 cost 命令输出同一文本）。"""
    return (
        "可提前安排的周期与临时改约代价：\n"
        "1. 新周期预约：提前 7 天受理，免改约费；不足 7 天转临时预订。\n"
        "2. 周期内单实例改时/取消：提前 48 小时免费（每实例一次）；\n"
        "   24–48 小时收半日房费 300；不足 24 小时收全日房费 600。\n"
        "3. 临时预订：提前 24 小时按标准价；不足 24 小时加收 50%。\n"
        "4. 报价有效期 24 小时，期内锁定的房间余量按报价兑现。\n"
        "5. 需无障碍房且连续两场同一口译员的请求：两个时段原子订妥后\n"
        "   才发通知；口译员占用独立登记，已占的半小时不可重复占用。"
    )


def reschedule_fee(now: datetime, original_start: datetime) -> tuple[float, str]:
    """按“距离原实例的时间”计算改约/取消费。"""
    lead = original_start - now
    if lead >= FREE_RESCHEDULE_LEAD:
        return 0.0, "提前 ≥48h，免费改约"
    if lead >= HALF_DAY_LEAD:
        return HALF_DAY_RATE, "24–48h 内改约，收半日房费"
    return FULL_DAY_RATE, "<24h 改约，收全日房费"


def adhoc_fee(now: datetime, start: datetime, base: float = FULL_DAY_RATE):
    lead = start - now
    if lead >= TEMP_BOOKING_LEAD:
        return base, "临时预订，标准价"
    return base * (1 + URGENT_SURCHARGE), "不足 24h，加收 50%"


def make_quote(quote_id: str, room: str, start: datetime, end: datetime,
               created_at: datetime, kind: str, amount: float,
               currency: str = "CNY") -> Quote:
    return Quote(quote_id=quote_id, room=room, start=start, end=end,
                 amount=amount, currency=currency, created_at=created_at,
                 valid_until=created_at + QUOTE_TTL, kind=kind)
