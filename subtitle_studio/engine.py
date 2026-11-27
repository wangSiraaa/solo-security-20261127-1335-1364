from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .canvas import VirtualCanvas
from .conditions import ConditionContext
from .errors import BudgetExceeded, CanvasOverflow, CycleDetected, GlyphRenderError
from .package import GlyphSpec, Package
from .profiles import LayoutProfile, MachinePolicy
from .analysis import AdmissionReport


BUILTIN_FALLBACK_ID = "__studio_approved_question__"

# A system-font-free declarative "?" drawn from six unit squares.
BUILTIN_FALLBACK_OPS: list[dict[str, Any]] = [
    {"op": "square", "x": 0, "y": 0, "size": 1},
    {"op": "square", "x": 1, "y": 0, "size": 1},
    {"op": "square", "x": 2, "y": 0, "size": 1},
    {"op": "square", "x": 2, "y": 1, "size": 1},
    {"op": "square", "x": 1, "y": 2, "size": 1},
    {"op": "square", "x": 1, "y": 4, "size": 1},
]


@dataclass
class Meter:
    work_budget: int
    cell_budget: int
    work_used: int = 0
    cell_writes: int = 0
    ops_executed: int = 0
    refs_followed: int = 0
    branches_evaluated: int = 0
    max_depth_reached: int = 1

    def charge_dispatch(self, policy: MachinePolicy) -> None:
        if self.work_used + 1 > policy.work_budget:
            raise BudgetExceeded("work", policy.work_budget, self.work_used + 1)
        self.work_used += 1
        self.ops_executed += 1

    def charge_cell(self, policy: MachinePolicy) -> None:
        if self.cell_writes + 1 > policy.cell_budget:
            raise BudgetExceeded("cell_writes", policy.cell_budget, self.cell_writes + 1)
        if self.work_used + 1 > policy.work_budget:
            raise BudgetExceeded("work", policy.work_budget, self.work_used + 1)
        self.cell_writes += 1
        self.work_used += 1

    def to_dict(self) -> dict[str, int]:
        return {
            "work_used": self.work_used,
            "work_budget": self.work_budget,
            "cell_writes": self.cell_writes,
            "cell_budget": self.cell_budget,
            "ops_executed": self.ops_executed,
            "refs_followed": self.refs_followed,
            "branches_evaluated": self.branches_evaluated,
            "max_depth_reached": self.max_depth_reached,
        }


@dataclass
class AttemptResult:
    ok: bool
    primitive_id: str
    meter: Meter
    failure_reason: str | None = None
    failure_message: str | None = None
    stage: dict[tuple[int, int], int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out = self.meter.to_dict()
        out.update(
            {
                "ok": self.ok,
                "primitive_id": self.primitive_id,
                "failure_reason": self.failure_reason,
                "failure_message": self.failure_message,
                "staged_unique_cells": len(self.stage),
            }
        )
        return out


Resolver = Callable[[str], list[dict[str, Any]]]


def execute_declaration(
    root_id: str,
    root_ops: list[dict[str, Any]],
    resolver: Resolver,
    context: ConditionContext,
    policy: MachinePolicy,
    origin_x: int = 0,
    origin_y: int = 0,
) -> AttemptResult:
    """Execute one glyph into a private stage.

    A raised resource/safety error is converted to a failed AttemptResult.  The
    caller must only copy ``stage`` to the visible canvas when ok is true.
    """

    meter = Meter(work_budget=policy.work_budget, cell_budget=policy.cell_budget)
    stage: dict[tuple[int, int], int] = {}

    def run_ops(
        ops: list[dict[str, Any]],
        ox: int,
        oy: int,
        active: frozenset[str],
        depth: int,
    ) -> None:
        meter.max_depth_reached = max(meter.max_depth_reached, depth)
        if depth > policy.max_ref_depth:
            raise BudgetExceeded("ref_depth", policy.max_ref_depth, depth)

        for op in ops:
            kind = op["op"]
            meter.charge_dispatch(policy)

            if kind in {"block", "square"}:
                width = op.get("w", op.get("size", 1))
                height = op.get("h", op.get("size", 1))
                base_x = ox + op["x"]
                base_y = oy + op["y"]
                # Validate the whole block before charging/painting its cells.
                # The visible canvas is untouched because stage is private.
                for j in range(height):
                    for i in range(width):
                        ax = base_x + i
                        ay = base_y + j
                        if not (0 <= ax < policy.canvas_width and 0 <= ay < policy.canvas_height):
                            raise CanvasOverflow(ax, ay, policy.canvas_width, policy.canvas_height)
                for j in range(height):
                    for i in range(width):
                        meter.charge_cell(policy)
                        stage[(base_x + i, base_y + j)] = 1

            elif kind == "ref":
                ref = op["ref"]
                if ref in active:
                    chain = tuple(active) + (ref,)
                    raise CycleDetected(ref, chain)
                meter.refs_followed += 1
                ref_ops = resolver(ref)
                next_active = active | {ref}
                run_ops(
                    ref_ops,
                    ox + op["x"],
                    oy + op["y"],
                    next_active,
                    depth + 1,
                )

            else:  # branch
                meter.branches_evaluated += 1
                selected: dict[str, Any] | None = None
                for case in op["cases"]:
                    if case["otherwise"] or context.matches(case["when"]):
                        selected = case
                        break
                if selected is None:  # parser inserts implicit otherwise
                    continue
                run_ops(selected["ops"], ox, oy, active, depth)

    try:
        run_ops(root_ops, origin_x, origin_y, frozenset({root_id}), 1)
        return AttemptResult(True, root_id, meter, stage=stage)
    except GlyphRenderError as exc:
        reason = type(exc).__name__
        return AttemptResult(
            False,
            root_id,
            meter,
            failure_reason=reason,
            failure_message=str(exc),
            stage={},  # explicitly discard any partial stage
        )


@dataclass
class GlyphResult:
    index: int
    requested_char: str
    rendered_char: str
    outcome: str  # committed | substituted | classic_preapproved
    promised_whole: bool
    advance: int
    x: int
    y: int
    requested_attempt: dict[str, Any] | None
    final_attempt: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "requested_char": self.requested_char,
            "rendered_char": self.rendered_char,
            "outcome": self.outcome,
            "promised_whole": self.promised_whole,
            "advance": self.advance,
            "x": self.x,
            "y": self.y,
            "requested_attempt": self.requested_attempt,
            "final_attempt": self.final_attempt,
        }


class LayoutEngine:
    def __init__(self, package: Package, admission: AdmissionReport, policy: MachinePolicy | None = None):
        self.package = package
        self.admission = admission
        self.policy = policy or MachinePolicy()
        self.policy.validate()

    def resolver(self) -> Resolver:
        def resolve(pid: str) -> list[dict[str, Any]]:
            if pid == BUILTIN_FALLBACK_ID:
                return BUILTIN_FALLBACK_OPS
            return self.package.primitives[pid]

        return resolve

    def _execute_spec(
        self,
        spec: GlyphSpec,
        context: ConditionContext,
        x: int,
        y: int,
    ) -> AttemptResult:
        ops = self.package.primitives[spec.root]
        return execute_declaration(
            spec.root,
            ops,
            self.resolver(),
            context,
            self.policy,
            x,
            y,
        )

    def _builtin_spec(self) -> GlyphSpec:
        return GlyphSpec("?", BUILTIN_FALLBACK_ID, advance=4)

    def _approved_fallback_spec(self) -> tuple[GlyphSpec, str]:
        fb_char = self.package.fallback_char
        if fb_char is not None:
            spec = self.package.glyphs[fb_char]
            if self.admission.fallback_admitted:
                return spec, "package_classic_fallback"
        return self._builtin_spec(), "studio_builtin_fallback"

    def _ink_width(self, char: str) -> int:
        glyph = self.package.glyphs.get(char)
        if glyph is None:
            return 3
        ga = self.admission.glyphs.get(char)
        if ga is not None and ga.bound.finite and ga.bound.bbox is not None:
            return max(1, ga.bound.bbox.width)
        return 3

    def lock(self, profile: LayoutProfile, lock_id: str) -> dict[str, Any]:
        if profile.line_width > self.policy.canvas_width:
            from .errors import LayoutOverflow

            raise LayoutOverflow(
                f"line_width {profile.line_width} exceeds canvas width {self.policy.canvas_width}"
            )

        canvas = VirtualCanvas(self.policy.canvas_width, self.policy.canvas_height)
        context = ConditionContext(profile)
        results: list[GlyphResult] = []
        x = 0
        y = 0
        line_height = profile.line_height or self.policy.line_height

        for index, char in enumerate(profile.text):
            if char == "\n":
                x = 0
                y += line_height
                continue
            if char == " ":
                x += 2
                continue

            requested_spec = self.package.glyphs.get(char)
            advance = requested_spec.advance if requested_spec else 4
            ink_width = self._ink_width(char)

            if x > 0 and x + ink_width > profile.line_width:
                x = 0
                y += line_height

            if y + 5 > self.policy.canvas_height:
                from .errors import LayoutOverflow

                raise LayoutOverflow(f"line {y // line_height + 1} exceeds canvas height")
            if x + min(ink_width, 3) > self.policy.canvas_width:
                from .errors import LayoutOverflow

                raise LayoutOverflow(f"glyph at x={x} exceeds canvas width")

            requested_record: dict[str, Any] | None = None
            final_attempt: AttemptResult | None = None
            rendered_char = char
            outcome = "committed"

            classic_allowed = (
                profile.mode == "classic"
                and char in self.admission.glyphs
                and self.admission.glyphs[char].admitted
            )
            live_allowed = profile.mode == "live"

            if profile.mode == "classic" and not classic_allowed:
                fallback_spec, fallback_kind = self._approved_fallback_spec()
                requested_record = {
                    "ok": False,
                    "skipped": "not_classically_admitted",
                    "admission_reasons": (
                        self.admission.glyphs[char].reasons
                        if char in self.admission.glyphs
                        else ["missing_glyph"]
                    ),
                }
                final_attempt = self._execute_spec(fallback_spec, context, x, y)
                if not final_attempt.ok:
                    builtin = self._builtin_spec()
                    final_attempt = execute_declaration(
                        builtin.root,
                        BUILTIN_FALLBACK_OPS,
                        self.resolver(),
                        context,
                        self.policy,
                        x,
                        y,
                    )
                    fallback_kind = "studio_builtin_fallback_after_package_failure"
                rendered_char = fallback_spec.char
                outcome = "classic_preapproved_substitution"

            elif profile.mode in {"live", "classic"}:
                if requested_spec is None:
                    requested_record = {
                        "ok": False,
                        "failure_reason": "MissingGlyph",
                        "failure_message": f"package has no glyph for {char!r}",
                    }
                else:
                    attempt = self._execute_spec(requested_spec, context, x, y)
                    requested_record = attempt.to_dict()
                    if attempt.ok:
                        final_attempt = attempt

                if final_attempt is None or not final_attempt.ok:
                    fallback_spec, fallback_kind = self._approved_fallback_spec()
                    fallback_attempt = self._execute_spec(fallback_spec, context, x, y)
                    if fallback_attempt.ok:
                        final_attempt = fallback_attempt
                        rendered_char = fallback_spec.char
                    else:
                        builtin = self._builtin_spec()
                        builtin_attempt = execute_declaration(
                            builtin.root,
                            BUILTIN_FALLBACK_OPS,
                            self.resolver(),
                            context,
                            self.policy,
                            x,
                            y,
                        )
                        if not builtin_attempt.ok:
                            # Minimum policy is validated; this should be unreachable.
                            raise RuntimeError(f"approved builtin fallback failed: {builtin_attempt.failure_message}")
                        final_attempt = builtin_attempt
                        rendered_char = "?"
                        fallback_kind = "studio_builtin_fallback"
                    outcome = "substituted"
            else:
                # Defensive: profile mode is validated above.
                raise ValueError(f"unknown layout mode {profile.mode}")

            assert final_attempt is not None and final_attempt.ok
            for (px, py), value in final_attempt.stage.items():
                canvas.paint(px, py, value)

            results.append(
                GlyphResult(
                    index=index,
                    requested_char=char,
                    rendered_char=rendered_char,
                    outcome=outcome,
                    promised_whole=(outcome == "committed"),
                    advance=advance,
                    x=x,
                    y=y,
                    requested_attempt=requested_record,
                    final_attempt=final_attempt.to_dict() | {"fallback_kind": fallback_kind if outcome != "committed" else "requested_glyph"},
                )
            )

            x += advance

        any_substitution = any(r.outcome != "committed" for r in results)
        if profile.mode == "classic" and not any_substitution:
            promise = "whole_line_install_time_guaranteed"
        elif not any_substitution:
            promise = "whole_line_committed_for_locked_profile"
        else:
            promise = "complete_line_with_approved_substitutions"

        return {
            "lock_id": lock_id,
            "status": "locked",
            "promise": promise,
            "package_id": self.package.package_id,
            "package_hash": self.package.content_hash,
            "admission_id": self.admission.admission_id,
            "profile": profile.to_dict() | {
                "text": profile.text,
                "canvas_width": self.policy.canvas_width,
                "canvas_height": self.policy.canvas_height,
                "work_budget": self.policy.work_budget,
                "cell_budget": self.policy.cell_budget,
            },
            "condition_decisions": context.decision_records(),
            "glyph_results": [r.to_dict() for r in results],
            "canvas": {
                "width": canvas.width,
                "height": canvas.height,
                "matrix": canvas.matrix(),
                "ascii": canvas.render_ascii(),
                "unicode": canvas.render_unicode(),
                "filled_cells": canvas.filled_count(),
            },
        }
