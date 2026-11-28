"""Human-readable drill demo.

Runs the server process, then the recovery process (separate interpreter via
subprocess), and prints what survives -- the answer to "which submitted
exercises can still be edited after one feed is lost?"
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["online", "graded"], default="online")
    ap.add_argument("--policy", choices=["commit", "cache"], default="commit")
    ap.add_argument("--delay-index", default=None, help="CLASSROOM/ASSIGNMENT")
    ap.add_argument("--flush-ticks", type=int, default=1)
    ap.add_argument("--workdir", default="/tmp/drill_demo")
    args = ap.parse_args()

    work = os.path.abspath(args.workdir)
    os.system(f"rm -rf {work}")
    os.makedirs(work, exist_ok=True)
    store = os.path.join(work, "store")
    held = os.path.join(work, "held")

    cmd = [sys.executable, "-m", "drill", "run", "--root", store,
           "--mode", args.mode, "--policy", args.policy,
           "--flush-ticks", str(args.flush_ticks)]
    if args.delay_index:
        cmd += ["--delay-index", args.delay_index]
    meta = json.loads(subprocess.run(cmd, capture_output=True, text=True,
                                     cwd=ROOT, check=True).stdout)

    rec_cmd = [sys.executable, "-m", "drill", "recover", "--root", store,
               "--held", held, "--out", os.path.join(work, "rec.json")]
    out = json.loads(subprocess.run(rec_cmd, capture_output=True, text=True,
                                    cwd=ROOT, check=True).stdout)

    print("=" * 68)
    print(" 失电演练 / power-loss drill")
    print(f" mode={args.mode}  receipt-policy={args.policy}"
          f"  delay-index={args.delay_index or '-'}"
          f"  flush-ticks={args.flush_ticks}")
    print("=" * 68)

    print("\n供电安排 (facility telemetry, ticks with write activity):")
    for t in out["summary"]["power"]["telemetry"]:
        if t["flushes"]:
            print(f"  t={t['tick']:>2}  feeds={t['feeds_present']}"
                  f"  powered={t['powered']}  bat={t['battery_after']}"
                  f"  landed={len(t['flushes'])}")

    print("\n教师手上的回单 (receipts minted by the server):")
    for r in meta["receipts"]:
        print(f"  {r['number']:<24} status={r['status']:<9} policy={r['policy']}")

    print("\n重开编辑 (recovered usable work, with redeemed numbers):")
    for item in out["reopen"]["reopened"]:
        print(f"  {item['txid']:<6} {item['classroom']} {item['assignment']:<5}"
              f"  {item['status']:<9} 单号={item['redeemed_receipt']}")

    s = out["summary"]
    print("\n汇总:")
    print(f"  确认可取 confirmed : {s['confirmed']}")
    print(f"  待确认 pending     : {sorted(set(s['usable']) - set(s['confirmed']))}")
    print(f"  未完成 partial     : {s['partial']}")
    print(f"  幻影(仅缓存) phantom: {s['phantom']}")
    print(f"  缓存幻写已丢弃     : {s['rejected_cache_only']}")
    print(f"  收尾余能 battery   : {s['power']['battery_final']}")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
