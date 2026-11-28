"""``python3 -m puzzle`` — run the full brief against two local directories.

    python3 -m puzzle            # temp dirs, prints JSON summary
    python3 -m puzzle /tmp/out   # keep the referee/ and demo-station/ dirs
"""
import json
import sys
import tempfile

from .scenario import run_all


def main(argv: list) -> int:
    if len(argv) > 1:
        root = argv[1]
        import os

        os.makedirs(root, exist_ok=True)
        res = run_all(root)
        print(json.dumps(res, indent=2, default=list))
        print(f"\nartifacts under: {root}/referee  and  {root}/demo-station")
    else:
        with tempfile.TemporaryDirectory() as root:
            res = run_all(root)
        print(json.dumps(res, indent=2, default=list))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
