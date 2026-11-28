#!/usr/bin/env python3
"""Offline test runner (no pytest required).

Runs every ``test_*`` function in ``tests/`` with a tiny built-in shim for the
only pytest feature used (``pytest.raises``).  Keeps the suite runnable in
air-gapped environments while remaining pytest-compatible.
"""

from __future__ import annotations

import sys
import traceback
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# --- minimal pytest shim ---------------------------------------------------
if "pytest" not in sys.modules:
    fake = types.ModuleType("pytest")

    class _Raises:
        def __init__(self, exc):
            self.exc = exc

        def __enter__(self):
            return self

        def __exit__(self, et, ev, tb):
            if et is None:
                raise AssertionError(f"expected {self.exc.__name__} but nothing raised")
            return issubclass(et, self.exc)

    fake.raises = lambda exc: _Raises(exc)
    sys.modules["pytest"] = fake


def main() -> int:
    sys.path.insert(0, str(ROOT / "tests"))
    import test_channel  # noqa: F401  (discovered by import)

    ran = failed = 0
    for name in sorted(vars(test_channel)):
        if not name.startswith("test_"):
            continue
        fn = getattr(test_channel, name)
        if not callable(fn):
            continue
        ran += 1
        try:
            fn()
            print(f"PASS {name}")
        except Exception:
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    passed = ran - failed
    print(f"\n{passed}/{ran} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
