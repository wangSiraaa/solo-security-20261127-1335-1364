from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any

from .errors import PackageFormatError
from .profiles import LayoutProfile


_CONDITION_ALIASES = {
    "font_size_gte": "font_size_gte",
    "min_font_size": "font_size_gte",
    "font_size_min": "font_size_gte",
    "size_gte": "font_size_gte",
    "min_size": "font_size_gte",
    "font_size_lte": "font_size_lte",
    "max_font_size": "font_size_lte",
    "font_size_max": "font_size_lte",
    "size_lte": "font_size_lte",
    "max_size": "font_size_lte",
    "line_width_gte": "line_width_gte",
    "min_line_width": "line_width_gte",
    "line_width_min": "line_width_gte",
    "width_gte": "line_width_gte",
    "min_width": "line_width_gte",
    "line_width_lte": "line_width_lte",
    "max_line_width": "line_width_lte",
    "line_width_max": "line_width_lte",
    "width_lte": "line_width_lte",
    "max_width": "line_width_lte",
    # Exact forms become both bounds during normalization.
    "font_size": "font_size_exact",
    "size": "font_size_exact",
    "line_width": "line_width_exact",
    "width": "line_width_exact",
}

_CANONICAL_KEYS = (
    "font_size_gte",
    "font_size_lte",
    "line_width_gte",
    "line_width_lte",
)


def normalize_condition(raw: Any, path: str) -> dict[str, int]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise PackageFormatError(f"{path}: branch condition must be an object")

    exact_font: int | None = None
    exact_width: int | None = None
    out: dict[str, int] = {}

    for raw_key, raw_value in raw.items():
        if not isinstance(raw_key, str):
            raise PackageFormatError(f"{path}: condition key must be a string")
        key = _CONDITION_ALIASES.get(raw_key)
        if key is None:
            raise PackageFormatError(f"{path}: unknown condition predicate {raw_key!r}")
        if not isinstance(raw_value, int) or isinstance(raw_value, bool) or raw_value < 0:
            raise PackageFormatError(f"{path}.{raw_key}: expected a non-negative integer")

        if key == "font_size_exact":
            exact_font = raw_value
        elif key == "line_width_exact":
            exact_width = raw_value
        else:
            out[key] = raw_value

    if exact_font is not None:
        out["font_size_gte"] = exact_font
        out["font_size_lte"] = exact_font
    if exact_width is not None:
        out["line_width_gte"] = exact_width
        out["line_width_lte"] = exact_width

    return {key: out[key] for key in _CANONICAL_KEYS if key in out}


def condition_key(condition: dict[str, int]) -> str:
    return json.dumps(condition, sort_keys=True, separators=(",", ":"))


@dataclass
class ConditionDecision:
    condition: dict[str, int]
    result: bool
    occurrences: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition": self.condition,
            "result": self.result,
            "occurrences": self.occurrences,
        }


@dataclass
class ConditionContext:
    """Evaluates each distinct predicate once for one locked layout profile."""

    profile: LayoutProfile
    _cache: dict[str, bool] = field(default_factory=dict)
    decisions: dict[str, ConditionDecision] = field(default_factory=dict)

    def matches(self, raw_condition: dict[str, int]) -> bool:
        condition = normalize_condition(raw_condition, "<runtime-condition>")
        key = condition_key(condition)
        if key in self._cache:
            decision = self.decisions[key]
            decision.occurrences += 1
            return self._cache[key]

        result = True
        if "font_size_gte" in condition:
            result &= self.profile.font_size >= condition["font_size_gte"]
        if "font_size_lte" in condition:
            result &= self.profile.font_size <= condition["font_size_lte"]
        if "line_width_gte" in condition:
            result &= self.profile.line_width >= condition["line_width_gte"]
        if "line_width_lte" in condition:
            result &= self.profile.line_width <= condition["line_width_lte"]

        self._cache[key] = bool(result)
        self.decisions[key] = ConditionDecision(condition, bool(result))
        return bool(result)

    def decision_records(self) -> list[dict[str, Any]]:
        return [d.to_dict() for d in self.decisions.values()]
