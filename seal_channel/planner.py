"""在完全相同的一批电文上，真实运行两种整批抵达次序并比较预分方案。"""

from __future__ import annotations

import tempfile
from dataclasses import asdict, dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .case import SealCase
from .models import ALPHA, BETA, TRIP_LABELS


@dataclass
class OrderSummary:
    order: List[str]
    direct: List[int] = field(default_factory=list)
    pending: List[int] = field(default_factory=list)
    duplicates: List[int] = field(default_factory=list)
    rejected: List[Dict[str, Any]] = field(default_factory=list)
    high_water: Optional[int] = None
    window: Optional[List[int]] = None
    late_trip: Optional[str] = None
    late_direct: List[int] = field(default_factory=list)
    late_pending: List[int] = field(default_factory=list)

    @property
    def direct_count(self) -> int:
        return len(self.direct)

    @property
    def manual_count(self) -> int:
        return len(self.pending)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["direct_count"] = self.direct_count
        data["manual_count"] = self.manual_count
        return data


@dataclass
class PlanReport:
    alpha: List[int]
    beta: List[int]
    runs: Dict[str, OrderSummary]
    preset: Optional[str] = None

    @property
    def worst_direct(self) -> int:
        return min(run.direct_count for run in self.runs.values())

    @property
    def total_direct(self) -> int:
        return sum(run.direct_count for run in self.runs.values())

    @property
    def worst_manual(self) -> int:
        return max(run.manual_count for run in self.runs.values())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "alpha": self.alpha,
            "beta": self.beta,
            "preset": self.preset,
            "worst_direct": self.worst_direct,
            "total_direct": self.total_direct,
            "worst_manual": self.worst_manual,
            "runs": {k: v.to_dict() for k, v in self.runs.items()},
        }


BodyFactory = Callable[[str, int, int], str]


def default_body(trip: str, seal: int, index: int) -> str:
    return (
        f"流动服务队第1357批｜{TRIP_LABELS[trip]}端｜"
        f"本端第{index + 1}封｜封条号 {seal}"
    )


class Planner:
    @staticmethod
    def evaluate(
        alpha: Sequence[int],
        beta: Sequence[int],
        orders: Sequence[Sequence[str]] = ((ALPHA, BETA), (BETA, ALPHA)),
        body_factory: BodyFactory = default_body,
        root: Optional[str | Path] = None,
        preset: Optional[str] = None,
    ) -> PlanReport:
        """先固定号码和同一批正文，再把相同信封送进两个全新收端状态。"""

        alpha = sorted(int(n) for n in alpha)
        beta = sorted(int(n) for n in beta)

        temp_dir: Optional[tempfile.TemporaryDirectory[str]] = None
        if root is None:
            temp_dir = tempfile.TemporaryDirectory(prefix="seal-plan-")
            case_root = Path(temp_dir.name)
        else:
            case_root = Path(root)

        try:
            case = SealCase.init(case_root, force=root is not None)
            case.allocate(alpha=alpha, beta=beta)
            case.depart_all()

            batches = {ALPHA: alpha, BETA: beta}
            for trip, seals in batches.items():
                for index, seal in enumerate(seals):
                    # 按从小到大的预定顺序写信；不显式挑选未来号码。
                    case.write(trip, body_factory(trip, seal, index))

            envelopes = {
                trip: case.terminal(trip).all_envelopes() for trip in (ALPHA, BETA)
            }

            runs: Dict[str, OrderSummary] = {}
            for order_tuple in orders:
                order = [str(x) for x in order_tuple]
                key = ",".join(order)
                receiver_name = "plan-" + "-".join(order)
                results = []
                for trip in order:
                    receiver = case.receiver(receiver_name)
                    for envelope in envelopes[trip]:
                        results.append(receiver.receive_envelope(envelope))

                summary = OrderSummary(order=order)
                for result in results:
                    d = result.to_dict() if hasattr(result, "to_dict") else dict(result)
                    status = d["status"]
                    if status == "recorded":
                        summary.direct.append(d["seal"])
                    elif status in ("pending_reconciliation", "duplicate_pending"):
                        summary.pending.append(d["seal"])
                    elif status == "duplicate":
                        summary.duplicates.append(d["seal"])
                    elif status in ("rejected", "conflict"):
                        summary.rejected.append(
                            {
                                "seal": d.get("seal"),
                                "code": d.get("code"),
                                "message": d.get("message"),
                            }
                        )

                receiver = case.receiver(receiver_name)
                dash = receiver.dashboard()
                summary.high_water = dash["high_water"]
                summary.window = dash["window"]
                summary.direct.sort()
                summary.pending.sort()
                summary.duplicates.sort()

                late_trip = order[-1]
                summary.late_trip = late_trip
                summary.late_direct = sorted(
                    seal
                    for seal in summary.direct
                    if any(
                        int(e["seal"]) == seal and e.get("owner") == late_trip
                        for e in receiver.data["ledger"]
                    )
                )
                summary.late_pending = sorted(
                    seal
                    for seal in summary.pending
                    if any(
                        int(p["seal"]) == seal and p.get("owner") == late_trip
                        for p in receiver.data["pending"]
                    )
                )
                runs[key] = summary

            return PlanReport(alpha=alpha, beta=beta, runs=runs, preset=preset)
        finally:
            if temp_dir is not None:
                temp_dir.cleanup()

    @staticmethod
    def compare_all() -> List[PlanReport]:
        reports: List[PlanReport] = []
        for alpha_tuple in combinations(range(1, 7), 3):
            alpha = list(alpha_tuple)
            beta = [n for n in range(1, 7) if n not in alpha]
            reports.append(Planner.evaluate(alpha, beta))

        reports.sort(
            key=lambda r: (
                -r.worst_direct,
                -r.total_direct,
                r.worst_manual,
                r.alpha,
                r.beta,
            )
        )
        return reports

    @staticmethod
    def recommended() -> PlanReport:
        return Planner.compare_all()[0]
