from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import json

from .canonical import canonical_hash, no_duplicate_object_keys
from .conditions import normalize_condition
from .errors import PackageFormatError, PackageQuotaError, UnknownPrimitiveError
from .profiles import ImportLimits


@dataclass(frozen=True)
class GlyphSpec:
    char: str
    root: str
    advance: int = 4


@dataclass(frozen=True)
class Package:
    package_id: str
    name: str
    version: str
    content_hash: str
    source_bytes: int
    glyphs: dict[str, GlyphSpec]
    primitives: dict[str, list[dict[str, Any]]]
    fallback_char: str | None
    limits: ImportLimits = field(default_factory=ImportLimits)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _depth(value: Any) -> int:
    # Iterative so a malicious deeply nested package cannot exhaust Python's
    # evaluator stack before the declarative depth quota is enforced.
    best = 0
    stack: list[tuple[Any, int]] = [(value, 0)]
    while stack:
        node, depth = stack.pop()
        best = max(best, depth)
        if isinstance(node, dict):
            stack.extend((v, depth + 1) for v in node.values())
        elif isinstance(node, list):
            stack.extend((v, depth + 1) for v in node)
    return best


def _check_id(value: Any, path: str, limits: ImportLimits) -> str:
    if not isinstance(value, str) or not value:
        raise PackageFormatError(f"{path}: expected a non-empty string id")
    if len(value) > limits.max_id_chars:
        raise PackageQuotaError(f"{path}: id exceeds {limits.max_id_chars} chars")
    return value


def _parse_block(op: dict[str, Any], path: str, limits: ImportLimits) -> dict[str, Any]:
    x = op.get("x", 0)
    y = op.get("y", 0)
    if not _is_int(x) or not _is_int(y):
        raise PackageFormatError(f"{path}: block x/y must be integers")
    if abs(x) > limits.max_abs_coordinate or abs(y) > limits.max_abs_coordinate:
        raise PackageQuotaError(f"{path}: coordinate exceeds import quota")

    # "square"/"size" sets both sides; block/rect may provide w and h.
    if "size" in op or "s" in op or "side" in op:
        side = op.get("size", op.get("s", op.get("side", 1)))
        if not _is_int(side) or side < 1 or side > limits.max_square_side:
            raise PackageFormatError(f"{path}: square size must be a positive integer within quota")
        w = h = side
    else:
        w = op.get("w", op.get("width", 1))
        h = op.get("h", op.get("height", 1))
        if not _is_int(w) or not _is_int(h) or w < 1 or h < 1:
            raise PackageFormatError(f"{path}: block w/h must be positive integers")
        if w > limits.max_square_side or h > limits.max_square_side:
            raise PackageQuotaError(f"{path}: block dimensions exceed import quota")
    return {"op": "block", "x": x, "y": y, "w": w, "h": h}


def _parse_ref(op: dict[str, Any], path: str, limits: ImportLimits) -> dict[str, Any]:
    ref = op.get("ref", op.get("id", op.get("primitive", op.get("target", op.get("use")))))
    if ref is None and isinstance(op.get("use"), str):
        ref = op["use"]
    ref = _check_id(ref, f"{path}.ref", limits)
    x = op.get("x", 0)
    y = op.get("y", 0)
    if not _is_int(x) or not _is_int(y):
        raise PackageFormatError(f"{path}: ref x/y must be integers")
    if abs(x) > limits.max_abs_coordinate or abs(y) > limits.max_abs_coordinate:
        raise PackageQuotaError(f"{path}: ref translation exceeds import quota")
    return {"op": "ref", "ref": ref, "x": x, "y": y}


def _parse_ops(raw_ops: Any, primitive_id: str, limits: ImportLimits) -> list[dict[str, Any]]:
    if not isinstance(raw_ops, list):
        raise PackageFormatError(f"primitive {primitive_id!r}: ops must be a list")
    return [_parse_op(op, f"primitive {primitive_id!r} op[{i}]", limits) for i, op in enumerate(raw_ops)]


def _parse_branch(op: dict[str, Any], path: str, limits: ImportLimits) -> dict[str, Any]:
    raw_cases = op.get("cases", op.get("branches"))
    cases: list[dict[str, Any]] = []

    if raw_cases is not None:
        if not isinstance(raw_cases, list) or not raw_cases:
            raise PackageFormatError(f"{path}: cases must be a non-empty list")
        for i, raw_case in enumerate(raw_cases):
            case_path = f"{path}.cases[{i}]"
            if not isinstance(raw_case, dict):
                raise PackageFormatError(f"{case_path}: case must be an object")
            raw_otherwise = raw_case.get("otherwise", raw_case.get("else", False))
            otherwise_ops = raw_otherwise if isinstance(raw_otherwise, list) else None
            otherwise = bool(raw_otherwise)
            if otherwise:
                when = {}
            else:
                raw_when = raw_case.get(
                    "when",
                    raw_case.get("if", raw_case.get("condition", op.get("when", op.get("if", op.get("condition"))))),
                )
                when = normalize_condition(raw_when, f"{case_path}.when")
            raw_ops = raw_case.get("ops", raw_case.get("then", otherwise_ops if otherwise_ops is not None else []))
            ops = _parse_ops(raw_ops, f"{case_path}", limits)
            cases.append({"when": when, "otherwise": otherwise or not when, "ops": ops})
    else:
        raw_when = op.get("when", op.get("if", op.get("condition")))
        then_ops = op.get("then", op.get("ops"))
        else_ops = op.get("else", op.get("otherwise", []))
        when = normalize_condition(raw_when, f"{path}.when")
        if not when:
            raise PackageFormatError(f"{path}: branch requires a condition or cases")
        cases.append(
            {
                "when": when,
                "otherwise": False,
                "ops": _parse_ops(then_ops, f"{path}.then", limits),
            }
        )
        cases.append(
            {
                "when": {},
                "otherwise": True,
                "ops": _parse_ops(else_ops, f"{path}.else", limits),
            }
        )

    # At most one otherwise, and it is normalized to the end.
    otherwise_cases = [c for c in cases if c["otherwise"]]
    if len(otherwise_cases) > 1:
        raise PackageFormatError(f"{path}: branch has more than one otherwise case")
    conditional = [c for c in cases if not c["otherwise"]]
    if otherwise_cases:
        cases = conditional + otherwise_cases
    else:
        # An implicit empty fallback keeps declaration evaluation total.
        cases = conditional + [{"when": {}, "otherwise": True, "ops": []}]
    return {"op": "branch", "cases": cases}


def _parse_op(op: Any, path: str, limits: ImportLimits) -> dict[str, Any]:
    if not isinstance(op, dict):
        raise PackageFormatError(f"{path}: op must be an object")

    # Ergonomic form: {"ref": "primitive", "x": ..., "y": ...}
    if isinstance(op.get("ref"), str) and "op" not in op and "type" not in op:
        kind = "ref"
    else:
        kind = op.get("op", op.get("type"))
    if not isinstance(kind, str):
        raise PackageFormatError(f"{path}: op needs a string 'op'")

    if kind in {"square", "block", "cell", "draw", "draw_square", "rect", "rectangle", "fill"}:
        return _parse_block(op, path, limits)
    if kind in {"ref", "use", "include", "reference"}:
        return _parse_ref(op, path, limits)
    if kind in {"branch", "if", "choose", "condition", "switch"}:
        return _parse_branch(op, path, limits)
    raise PackageFormatError(f"{path}: unsupported op kind {kind!r}; only square/ref/branch are allowed")


def count_ops(ops: list[dict[str, Any]]) -> int:
    total = 0
    for op in ops:
        total += 1
        if op["op"] == "branch":
            for case in op["cases"]:
                total += count_ops(case["ops"])
    return total


def _collect_refs(ops: list[dict[str, Any]]) -> set[str]:
    refs: set[str] = set()
    for op in ops:
        if op["op"] == "ref":
            refs.add(op["ref"])
        elif op["op"] == "branch":
            for case in op["cases"]:
                refs.update(_collect_refs(case["ops"]))
    return refs


def parse_package_data(raw: Any, *, source_bytes: int | None = None, limits: ImportLimits | None = None) -> Package:
    limits = limits or ImportLimits()
    if source_bytes is not None and source_bytes > limits.max_package_bytes:
        raise PackageQuotaError(
            f"package is {source_bytes} bytes; import quota is {limits.max_package_bytes}"
        )
    if not isinstance(raw, dict):
        raise PackageFormatError("package root must be a JSON object")
    if _depth(raw) > limits.max_json_depth:
        raise PackageQuotaError(f"JSON nesting exceeds {limits.max_json_depth}")

    raw_primitives = raw.get("primitives", {})
    raw_glyphs = raw.get("glyphs", {})
    if not isinstance(raw_primitives, dict) or not isinstance(raw_glyphs, dict):
        raise PackageFormatError("'primitives' and 'glyphs' must be objects")
    if len(raw_primitives) > limits.max_primitives:
        raise PackageQuotaError("too many primitives")
    if len(raw_glyphs) > limits.max_glyphs:
        raise PackageQuotaError("too many glyphs")

    package_id = raw.get("id")
    if package_id is None:
        package_id = f"pack_{canonical_hash(raw)[:12]}"
    package_id = _check_id(package_id, "id", limits)
    name = raw.get("name", package_id)
    version = str(raw.get("version", "0.0.0"))
    if not isinstance(name, str):
        raise PackageFormatError("name must be a string")

    primitives: dict[str, list[dict[str, Any]]] = {}
    total_ops = 0
    for primitive_id, raw_primitive in raw_primitives.items():
        pid = _check_id(primitive_id, "primitives key", limits)
        if isinstance(raw_primitive, list):
            raw_ops = raw_primitive
        elif isinstance(raw_primitive, dict) and isinstance(raw_primitive.get("ops"), list):
            raw_ops = raw_primitive["ops"]
        else:
            raise PackageFormatError(f"primitive {pid!r} must be an ops list or {{'ops': [...]}}")
        ops = _parse_ops(raw_ops, pid, limits)
        total_ops += count_ops(ops)
        if total_ops > limits.max_ops:
            raise PackageQuotaError(f"package exceeds {limits.max_ops} operations")
        primitives[pid] = ops

    glyphs: dict[str, GlyphSpec] = {}
    for char, raw_glyph in raw_glyphs.items():
        if not isinstance(char, str) or len(char) == 0:
            raise PackageFormatError("glyph keys must be non-empty strings")
        if len(char) > 4:
            raise PackageFormatError(f"glyph key {char!r} is not a single character")
        if len(glyphs) >= limits.max_glyphs:
            raise PackageQuotaError("too many glyphs")

        implicit_id: str | None = None
        advance = 4
        if isinstance(raw_glyph, str):
            root = _check_id(raw_glyph, f"glyph {char!r}", limits)
        elif isinstance(raw_glyph, list):
            implicit_id = f"__glyph_{ord(char):x}"
            if implicit_id in primitives:
                raise PackageFormatError(f"implicit primitive id collision for {char!r}")
            ops = _parse_ops(raw_glyph, implicit_id, limits)
            total_ops += count_ops(ops)
            if total_ops > limits.max_ops:
                raise PackageQuotaError("package exceeds operation quota")
            primitives[implicit_id] = ops
            root = implicit_id
        elif isinstance(raw_glyph, dict):
            root_value = raw_glyph.get("primitive", raw_glyph.get("root", raw_glyph.get("ref")))
            if root_value is None and isinstance(raw_glyph.get("ops"), list):
                implicit_id = f"__glyph_{ord(char):x}"
                ops = _parse_ops(raw_glyph["ops"], implicit_id, limits)
                total_ops += count_ops(ops)
                if total_ops > limits.max_ops:
                    raise PackageQuotaError("package exceeds operation quota")
                primitives[implicit_id] = ops
                root = implicit_id
            else:
                root = _check_id(root_value, f"glyph {char!r}.primitive", limits)
            raw_advance = raw_glyph.get("advance", 4)
            if not _is_int(raw_advance) or raw_advance < 1 or raw_advance > 16:
                raise PackageFormatError(f"glyph {char!r}.advance must be an integer 1..16")
            advance = raw_advance
        else:
            raise PackageFormatError(f"glyph {char!r} must be a primitive id, ops list, or object")

        if root not in primitives:
            raise UnknownPrimitiveError(f"glyph {char!r} references missing primitive {root!r}")
        glyphs[char] = GlyphSpec(char=char, root=root, advance=advance)

    # Every reference must be internal: an imported pack is self-contained.
    for pid, ops in primitives.items():
        for ref in _collect_refs(ops):
            if ref not in primitives:
                raise UnknownPrimitiveError(f"primitive {pid!r} references missing primitive {ref!r}")

    fallback_char = raw.get("fallback")
    if fallback_char is not None:
        if not isinstance(fallback_char, str) or fallback_char not in glyphs:
            raise PackageFormatError("fallback must name a glyph in the package")

    if source_bytes is None:
        source_bytes = len(canonical_hash(raw).encode())  # not real byte size
    content_hash = canonical_hash(raw)
    return Package(
        package_id=package_id,
        name=name,
        version=version,
        content_hash=content_hash,
        source_bytes=source_bytes,
        glyphs=glyphs,
        primitives=primitives,
        fallback_char=fallback_char,
        limits=limits,
    )


def load_package_file(path: str | Path, limits: ImportLimits | None = None) -> Package:
    p = Path(path)
    limits = limits or ImportLimits()
    try:
        file_size = p.stat().st_size
    except FileNotFoundError:
        raise
    if file_size > limits.max_package_bytes:
        raise PackageQuotaError(f"{p} is {file_size} bytes; import quota is {limits.max_package_bytes}")
    data = p.read_bytes()
    if len(data) > limits.max_package_bytes:
        raise PackageQuotaError(f"{p} exceeds import quota of {limits.max_package_bytes} bytes")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PackageFormatError("package must be UTF-8 JSON") from exc

    def reject_constant(value: str) -> None:
        raise PackageFormatError(f"invalid JSON constant {value}")

    try:
        raw = json.loads(text, object_pairs_hook=no_duplicate_object_keys, parse_constant=reject_constant)
    except PackageFormatError:
        raise
    except RecursionError as exc:
        raise PackageQuotaError("JSON nesting is too deep") from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise PackageFormatError(f"invalid JSON package: {exc}") from exc
    return parse_package_data(raw, source_bytes=len(data), limits=limits)
