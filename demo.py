#!/usr/bin/env python3
"""Demo entry point: in-memory logic scenario + on-disk simulation.

Run:  python3 demo.py            # both
      python3 demo.py --fs       # on-disk only
      python3 demo.py --logic    # pure-logic only
"""

import sys
import tempfile
import shutil

from sealink.scenarios import run_demo
from sealink.fs_demo import run_fs_demo


def main(argv):
    mode = "both"
    if "--fs" in argv:
        mode = "fs"
    elif "--logic" in argv:
        mode = "logic"

    if mode in ("both", "logic"):
        run_demo()

    if mode in ("both", "fs"):
        print("\n" + "#" * 72)
        print("# ON-DISK SIMULATION (local directories, offline resumes)")
        print("#" * 72)
        root = tempfile.mkdtemp(prefix="sealink_demo_")
        keep = "--keep" in argv
        try:
            run_fs_demo(root)
            if keep:
                print(f"\n(artifacts left in {root})")
            else:
                shutil.rmtree(root, ignore_errors=True)
        except Exception:
            shutil.rmtree(root, ignore_errors=True)
            raise


if __name__ == "__main__":
    main(sys.argv[1:])
