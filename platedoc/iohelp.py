"""Shared helpers: JSON IO, dirs, and the protected-colour guard."""
import json
import os
from .content import DEFAULT_PROTECTED_COLOURS, DEFAULT_PROTECTED_RECTS
from .common import min_dist_to_marks


def save_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=True, ensure_ascii=False)
        f.write("\n")


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def protected_colour_guard(img, rects=None, colours=None):
    """Prove a protected region still holds only its fixed allowed values.

    Returns [] when clean, else a list of violations.
    """
    rects = DEFAULT_PROTECTED_RECTS if rects is None else rects
    colours = DEFAULT_PROTECTED_COLOURS if colours is None else colours
    allowed = set(map(tuple, colours)) | {(128, 128, 128)}
    problems = []
    for (x0, y0, x1, y1) in rects:
        bad = []
        for y in range(y0, y1):
            for x in range(x0, x1):
                if img.pixels[y][x] not in allowed:
                    bad.append((x, y, img.pixels[y][x]))
        if bad:
            problems.append({"rect": [x0, y0, x1, y1],
                             "bad_pixels": len(bad), "examples": bad[:3]})
    return problems


def mark_safety_of_protected(colours=None):
    colours = DEFAULT_PROTECTED_COLOURS if colours is None else colours
    worst = None
    for c in colours:
        d = min_dist_to_marks(c)
        if worst is None or d < worst[0]:
            worst = (d, c)
    return worst
