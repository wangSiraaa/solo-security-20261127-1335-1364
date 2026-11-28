"""和弦模板与“获准音域内选和弦”。

两路导出方案共用：学员事后只能由授权音拼出和弦，
任何引用未授权键的候选都会被过滤。
"""
from __future__ import annotations

from .tuning import NOTE_NAMES, note_name

# 以半音为单位的和弦结构
CHORD_SHAPES = {
    "maj": [0, 4, 7],
    "min": [0, 3, 7],
    "dim": [0, 3, 6],
    "aug": [0, 4, 8],
    "sus4": [0, 5, 7],
    "maj7": [0, 4, 7, 11],
    "min7": [0, 3, 7, 10],
    "dom7": [0, 4, 7, 10],
}


def chord_tones(root_midi: int, shape: str) -> list[int]:
    return [root_midi + iv for iv in CHORD_SHAPES[shape]]


def enumerate_chords(authorized_notes: set[int],
                     shapes: list[str] | None = None,
                     root_lo: int = 48, root_hi: int = 84) -> list[dict]:
    """枚举所有组成音全部落在授权集合内的和弦。"""
    shapes = shapes or list(CHORD_SHAPES)
    out: list[dict] = []
    for root in range(root_lo, root_hi + 1):
        for shape in shapes:
            tones = chord_tones(root, shape)
            if all(t in authorized_notes for t in tones):
                out.append({
                    "root": root,
                    "root_name": note_name(root),
                    "shape": shape,
                    "tones": tones,
                    "tone_names": [note_name(t) for t in tones],
                })
    return out


def selectable(chord: dict, authorized_notes: set[int]) -> bool:
    return all(t in authorized_notes for t in chord["tones"])


def describe(chord: dict) -> str:
    return f"{chord['root_name']}{chord['shape']} [{','.join(chord['tone_names'])}]"
