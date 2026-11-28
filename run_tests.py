#!/usr/bin/env python3
"""Minimal test runner (no external deps).

Runs every ``test_*`` function in ``tests/``.  Supports the pytest-style
``tmp_path`` fixture used by the CLI smoke test, yielding a fresh temporary
directory per test.  Plain ``assert`` statements are the assertions.
"""

from __future__ import annotations

import importlib.util
import inspect
import pathlib
import sys
import tempfile
import traceback


def load_module(path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(path.stem, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def call_test(fn):
    params = inspect.signature(fn).parameters
    if "tmp_path" in params:
        with tempfile.TemporaryDirectory() as d:
            fn(tmp_path=pathlib.Path(d))
    else:
        fn()


def main() -> int:
    root = pathlib.Path(__file__).resolve().parent
    sys.path.insert(0, str(root))
    files = sorted((root / "tests").glob("test_*.py"))
    passed = failed = skipped = 0
    failures = []
    for f in files:
        mod = load_module(f)
        for name, fn in inspect.getmembers(mod, inspect.isfunction):
            if not name.startswith("test_") or fn.__module__ != mod.__name__:
                continue
            try:
                call_test(fn)
                passed += 1
                print(f"PASS {f.stem}.{name}")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"FAIL {f.stem}.{name}: {exc}")
                failures.append((f.stem, name, traceback.format_exc()))
    print(f"\n{passed} passed, {failed} failed, {skipped} skipped")
    if failures:
        print("\n--- failure details ---")
        for stem, name, tb in failures:
            print(f"\n{stem}.{name}\n{tb}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
