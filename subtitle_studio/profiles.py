from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any


@dataclass(frozen=True)
class ImportLimits:
    """Fixed quotas charged while accepting an external declaration."""

    max_package_bytes: int = 32_768
    max_primitives: int = 64
    max_glyphs: int = 64
    max_ops: int = 384
    max_json_depth: int = 24
    max_id_chars: int = 64
    max_text_chars: int = 200
    max_abs_coordinate: int = 128
    max_square_side: int = 16


@dataclass(frozen=True)
class ClassicPolicy:
    """Install-time guarantee for an old, non-interruptible subtitle machine."""

    # Work model: every declaration dispatch costs 1; painting one grid cell
    # costs another 1.  These defaults fit the two sample packages.
    max_work: int = 64
    max_cell_writes: int = 24
    max_bbox_width: int = 3
    max_bbox_height: int = 5
    require_non_negative_origin: bool = True


@dataclass(frozen=True)
class MachinePolicy:
    """Physical canvas and one-draw sandbox slice for a new camera position."""

    canvas_width: int = 16
    canvas_height: int = 6
    line_height: int = 6
    work_budget: int = 32
    cell_budget: int = 12
    max_ref_depth: int = 48

    def validate(self) -> None:
        if self.canvas_width < 4 or self.canvas_height < 5:
            raise ValueError("canvas must be at least 4x5 cells")
        if self.work_budget < 16:
            raise ValueError("work_budget must be at least 16 for the approved fallback")
        if self.cell_budget < 6:
            raise ValueError("cell_budget must be at least 6 for the approved fallback")
        if self.max_ref_depth < 4:
            raise ValueError("max_ref_depth is too small")


@dataclass(frozen=True)
class LayoutProfile:
    """The one actual show layout that branch predicates must obey."""

    text: str = ""
    font_size: int = 5
    line_width: int = 16
    line_height: int | None = None
    mode: str = "live"

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError("text must be a string")
        if len(self.text) > 200:
            raise ValueError("text is too long")
        if not isinstance(self.font_size, int) or isinstance(self.font_size, bool):
            raise TypeError("font_size must be an integer")
        if not isinstance(self.line_width, int) or isinstance(self.line_width, bool):
            raise TypeError("line_width must be an integer")
        if self.font_size < 0 or self.line_width < 1:
            raise ValueError("font_size and line_width must be non-negative/positive")
        if self.mode not in {"live", "classic"}:
            raise ValueError("mode must be 'live' or 'classic'")

    @property
    def key(self) -> str:
        body = json.dumps(
            {
                "font_size": self.font_size,
                "line_width": self.line_width,
                "mode": self.mode,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            "font_size": self.font_size,
            "line_width": self.line_width,
            "mode": self.mode,
            "profile_key": self.key,
        }
