from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git(*args: str, cwd: str | Path = ROOT, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def head_commit(ref: str = "HEAD") -> str:
    return git("rev-parse", ref)


def branch_exists(branch: str) -> bool:
    proc = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=ROOT,
    )
    return proc.returncode == 0


def tree_exists(ref: str, path: str) -> bool:
    proc = subprocess.run(
        ["git", "cat-file", "-e", f"{ref}:{path}"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc.returncode == 0
