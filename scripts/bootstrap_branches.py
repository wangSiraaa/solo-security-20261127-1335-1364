#!/usr/bin/env python3
"""Create the two fictional maintenance branches and same-named merge records."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from reviewdesk.gitutil import git  # noqa: E402

WORKTREE_ROOT = ROOT / ".worktrees"
PATCH_TITLE = "fix(offline): resolve promised entries without external query"

BRANCHES = [
    {
        "branch": "maint/qianzi-native",
        "slug": "qianzi",
        "base": "main",
        "overlay": ROOT / "lab" / "overlays" / "qianzi",
        "remove": ["mojiao/reviewed_resolver.py"],
    },
    {
        "branch": "maint/bihe-sealed-component",
        "slug": "bihe",
        "base": "main",
        "overlay": ROOT / "lab" / "overlays" / "bihe",
        "remove": [],
    },
    {
        "branch": "trial/swapped-called-impl",
        "slug": "swapped",
        "base": "maint/qianzi-native",
        "overlay": ROOT / "lab" / "overlays" / "swapped",
        "remove": [],
    },
]


def run(cmd: list[str], cwd: Path) -> None:
    proc = subprocess.run(cmd, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} in {cwd} failed:\n{proc.stdout}\n{proc.stderr}")
    if proc.stdout.strip():
        print(proc.stdout.strip())


def copy_overlay(overlay: Path, target: Path) -> None:
    for source in overlay.rglob("*"):
        if source.is_dir() or "__pycache__" in source.parts:
            continue
        rel = source.relative_to(overlay)
        destination = target / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def main() -> int:
    git("config", "user.name", "Exercise Review Desk")
    git("config", "user.email", "review-desk@example.invalid")
    WORKTREE_ROOT.mkdir(exist_ok=True)

    for spec in BRANCHES:
        branch = spec["branch"]
        slug = spec["slug"]
        worktree = WORKTREE_ROOT / slug
        if worktree.exists():
            run(["git", "worktree", "remove", "--force", str(worktree)], ROOT)
        run(["git", "worktree", "prune"], ROOT)
        base = spec.get("base", "main")
        run(["git", "worktree", "add", "-B", branch, str(worktree), base], ROOT)

        for rel in spec["remove"]:
            path = worktree / rel
            if path.exists():
                path.unlink()

        copy_overlay(spec["overlay"], worktree)
        run(["git", "add", "-A"], worktree)
        run(["git", "checkout", "-B", f"fix/{slug}-offline-port"], worktree)
        run(["git", "commit", "-m", PATCH_TITLE], worktree)
        run(["git", "checkout", branch], worktree)
        run(
            [
                "git",
                "merge",
                "--no-ff",
                f"fix/{slug}-offline-port",
                "-m",
                f"Merge branch 'fix/{slug}-offline-port'\n\n{PATCH_TITLE}",
            ],
            worktree,
        )
        print(f"prepared {branch} at {worktree}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
