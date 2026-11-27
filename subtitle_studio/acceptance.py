from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
from typing import Any

from .errors import StaleAdmissionError
from .studio import FontStudio


PACKAGE_DIR = Path(__file__).resolve().parent.parent / "packages"


def _step(results: list[dict[str, Any]], name: str, detail: dict[str, Any]) -> None:
    results.append({"step": name, "ok": True, **detail})


def _duplicate_condition_package() -> dict[str, Any]:
    # The same predicate text occurs in two independent primitives.
    return {
        "id": "dup-condition",
        "fallback": "q",
        "glyphs": {
            "A": "root",
            "q": "q",
        },
        "primitives": {
            "left": [
                {
                    "op": "branch",
                    "cases": [
                        {"when": {"line_width_lte": 12}, "ops": [{"op": "square", "x": 0, "y": 0}]},
                        {"otherwise": True, "ops": [{"op": "square", "x": 0, "y": 4}]},
                    ],
                }
            ],
            "right": [
                {
                    "op": "branch",
                    "cases": [
                        {"when": {"max_line_width": 12}, "ops": [{"op": "square", "x": 2, "y": 0}]},
                        {"otherwise": True, "ops": [{"op": "square", "x": 2, "y": 4}]},
                    ],
                }
            ],
            "root": [
                {"op": "ref", "ref": "left"},
                {"op": "ref", "ref": "right"},
            ],
            "q": [
                {"op": "square", "x": 1, "y": 2},
            ],
        },
    }


def run_acceptance(verbose: bool = True) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def say(message: str) -> None:
        if verbose:
            print(message)

    with tempfile.TemporaryDirectory(prefix="subtitle-acceptance-") as tmp:
        studio = FontStudio(store_dir=tmp)

        # 1. The acceptor can import two independent packages.
        fy_path = PACKAGE_DIR / "fangyuan.json"
        gw_path = PACKAGE_DIR / "gewu.json"
        fy = studio.import_path(fy_path)
        gw = studio.import_path(gw_path)
        assert fy["package_id"] == "fangyuan"
        assert gw["package_id"] == "gewu"
        _step(checks, "import_two_packages", {"ids": [fy["package_id"], gw["package_id"]]})
        say(f"[ok] imported {fy['package_id']} and {gw['package_id']}")

        # 2. Lock one real school-name layout and read the canvas back.
        lock = studio.lock_layout("启明学校", package_id="fangyuan", line_width=16)
        readback = studio.read_canvas(lock["lock_id"])
        assert lock["promise"] == "whole_line_committed_for_locked_profile"
        assert all(g["promised_whole"] for g in lock["glyph_results"])
        assert readback.filled_cells > 30
        assert readback.width == 16 and readback.height == 6
        _step(
            checks,
            "lock_school_name_and_read_canvas",
            {"lock_id": lock["lock_id"], "filled_cells": readback.filled_cells, "canvas": readback.matrix},
        )
        say("[ok] locked 启明学校 and read back:")
        say(readback.unicode_art)

        # The second package can also compose the name.
        lock_b = studio.lock_layout("启明学校", package_id="gewu", line_width=16)
        assert all(g["outcome"] == "committed" for g in lock_b["glyph_results"])
        _step(checks, "second_package_layout", {"lock_id": lock_b["lock_id"]})

        # Repeated references really draw repeatedly: 王 uses bar3 three times.
        wang = studio.lock_layout("王", package_id="fangyuan", line_width=16)
        assert wang["canvas"]["filled_cells"] == 11
        assert wang["glyph_results"][0]["final_attempt"]["refs_followed"] == 3
        _step(checks, "repeated_reference_draws_repeatedly", {"filled_cells": 11})

        # 3. An admitted glyph changed to self-reference invalidates admission.
        original_raw = json.loads((PACKAGE_DIR / "fangyuan.json").read_text(encoding="utf-8"))
        original_hash = fy["content_hash"]
        original_admission = fy["admission_id"]
        assert studio.verify_admission(original_admission)["valid"]

        mutated = copy.deepcopy(original_raw)
        mutated["primitives"]["yong"].append({"op": "ref", "ref": "yong"})
        mutated_receipt = studio.import_dict(mutated)
        assert "永" in mutated_receipt["rejected"]
        assert "cyclic_or_recursive_dependency" in mutated_receipt["rejected"]["永"]
        assert "yong" in mutated_receipt["cyclic_primitives"]

        stale = False
        try:
            studio.verify_admission(original_admission)
        except StaleAdmissionError:
            stale = True
        assert stale
        _step(
            checks,
            "self_reference_invalidates_original_admission",
            {"new_admission": mutated_receipt["admission_id"], "old_was_stale": True},
        )
        say("[ok] self-reference rejected by static admission; old token is stale")

        # New-machine execution must stop safely and substitute atomically.
        cyclic_lock = studio.lock_layout("永", package_id="fangyuan", line_width=16)
        result = cyclic_lock["glyph_results"][0]
        assert result["outcome"] == "substituted"
        assert result["requested_attempt"]["failure_reason"] == "CycleDetected"
        assert cyclic_lock["canvas"]["filled_cells"] == 6  # approved '?' only
        _step(checks, "cyclic_glyph_atomic_substitution", {"rendered": result["rendered_char"]})

        # 4. Restore exact original bytes, then narrow the actual line width.
        restored = studio.import_dict(original_raw)
        assert restored["content_hash"] == original_hash
        assert studio.verify_admission(original_admission)["valid"]

        wide = studio.lock_layout("永", package_id="fangyuan", line_width=16)
        narrow = studio.lock_layout("永", package_id="fangyuan", line_width=15)
        assert wide["canvas"]["matrix"] != narrow["canvas"]["matrix"]
        narrow_glyph = narrow["glyph_results"][0]
        assert narrow_glyph["requested_attempt"]["ok"] is False
        assert narrow_glyph["requested_attempt"]["failure_reason"] == "BudgetExceeded"
        assert narrow_glyph["outcome"] == "substituted"
        assert narrow["canvas"]["filled_cells"] == 6
        _step(
            checks,
            "narrow_branch_recomputes_and_substitutes_under_slice",
            {"condition": narrow["condition_decisions"], "outcome": narrow_glyph["outcome"]},
        )
        say("[ok] narrow profile re-executed the expensive branch and substituted atomically")

        # With a larger, still finite draw slice, the same narrow branch can be
        # promised whole.  This proves the compositor decides after relayout.
        narrow_whole = studio.lock_layout(
            "永",
            package_id="fangyuan",
            line_width=15,
            work_budget=40,
            cell_budget=16,
        )
        ng = narrow_whole["glyph_results"][0]
        assert ng["outcome"] == "committed"
        assert ng["final_attempt"]["cell_writes"] == 13
        assert narrow_whole["canvas"]["filled_cells"] == 13
        _step(checks, "narrow_branch_whole_glyph_feasible", {"cell_writes": 13})
        say("[ok] with enough finite budget, narrow branch is drawn as a complete 永")

        # 5. Duplicate predicates share one locked layout answer.
        dup_receipt = studio.import_dict(_duplicate_condition_package())
        assert dup_receipt["package_id"] == "dup-condition"
        dup_lock = studio.lock_layout("A", package_id="dup-condition", line_width=12)
        decisions = dup_lock["condition_decisions"]
        assert len(decisions) == 1
        assert decisions[0]["occurrences"] == 2
        assert decisions[0]["result"] is True
        assert dup_lock["canvas"]["matrix"][0][0] == 1
        assert dup_lock["canvas"]["matrix"][0][2] == 1
        _step(checks, "duplicate_condition_uses_one_layout_truth", decisions[0])

        # 6. Live mode can promise the statically expensive conditional glyph
        # because the impossible heavy branch is not actually selected.
        promises = studio.promised_chars("fangyuan", font_size=5, line_width=16, mode="live")
        assert "梦" in promises["promised_chars"]
        classic = studio.promised_chars("fangyuan", mode="classic")
        assert "梦" not in classic["promised_chars"]
        _step(checks, "live_preserves_actually_cheap_conditional_glyph", {})

    return {"ok": True, "checks": checks}


if __name__ == "__main__":
    result = run_acceptance()
    print(f"PASS: {len(result['checks'])} acceptance checks")
