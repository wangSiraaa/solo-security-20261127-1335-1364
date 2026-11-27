from __future__ import annotations

from pathlib import Path
from typing import Any

from .engine import BUILTIN_FALLBACK_ID, BUILTIN_FALLBACK_OPS
from .package import Package
from .analysis import AdmissionReport


def make_classic_cut(
    package: Package,
    report: AdmissionReport,
    *,
    chars: list[str] | None = None,
) -> dict[str, Any]:
    """Return a smaller self-contained package containing only guaranteed glyphs.

    The cut keeps exactly the primitives reachable from the selected glyph
    roots.  If the package's own fallback is not guaranteed, the studio's
    six-square approved fallback is embedded as declarative data.
    """

    if chars is None:
        selected = list(report.classic_glyphs)
    else:
        missing = [c for c in chars if c not in report.glyphs or not report.glyphs[c].admitted]
        if missing:
            raise ValueError(f"not classically admitted: {''.join(missing)}")
        selected = list(chars)

    selected_set = set(selected)
    primitive_ids: set[str] = set()
    glyphs: dict[str, Any] = {}

    for char in selected:
        admission = report.glyphs[char]
        primitive_ids.update(admission.dependencies)
        spec = package.glyphs[char]
        glyphs[char] = {"primitive": spec.root, "advance": spec.advance}

    fallback_char = package.fallback_char
    use_builtin_fallback = fallback_char is None or not report.fallback_admitted

    if use_builtin_fallback:
        # Do not let an unguaranteed original fallback remain as the named
        # fallback.  Add the small approved '?' under a stable glyph key.
        fallback_char = "?"
        if "?" not in glyphs:
            glyphs["?"] = {"primitive": BUILTIN_FALLBACK_ID, "advance": 4}
            selected_set.add("?")
        primitive_ids.add(BUILTIN_FALLBACK_ID)

    primitives: dict[str, Any] = {}
    for pid in sorted(primitive_ids):
        if pid == BUILTIN_FALLBACK_ID:
            primitives[pid] = BUILTIN_FALLBACK_OPS
        else:
            primitives[pid] = package.primitives[pid]

    return {
        "schema": "grid-font-pack/1",
        "id": f"{package.package_id}-classic",
        "name": f"{package.name}（旧机整字保证子集）",
        "version": package.version,
        "fallback": fallback_char,
        "source_package_hash": package.content_hash,
        "glyphs": glyphs,
        "primitives": primitives,
    }


def write_classic_cut(
    out_path: str | Path,
    package: Package,
    report: AdmissionReport,
    *,
    chars: list[str] | None = None,
) -> dict[str, Any]:
    import json

    cut = make_classic_cut(package, report, chars=chars)
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cut, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return cut
