"""Self-contained test runner (stdlib only; pytest is not available offline).

Run:  python3 -m tests.run_tests
Each ``test_*`` function is executed and a summary printed.  Exit code 1 on
any failure.
"""

from __future__ import annotations

import sys
import traceback


def main() -> int:
    from tests import test_suite

    tests = [
        (name, fn)
        for name, fn in sorted(vars(test_suite).items())
        if name.startswith("test_") and callable(fn)
    ]
    failures = []
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception:  # noqa: BLE001
            failures.append(name)
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
