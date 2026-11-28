"""Synthesis, WAV I/O and power measurement (pure stdlib).

Level convention
----------------
All levels ``L`` are *measured mean-square power over a beat window*, in dB:
    mean(x**2) over the window  ==  10**(L/10).
A tone of amplitude A filling a fraction phi of the window has mean-square
phi*A**2/2, hence the amplitude helpers below normalise by the duty fraction.

Coherence
---------
Every independent source gets a small in-band frequency offset (see
``source_freq``) so that the delivered mixture is an incoherent power sum of the
stems.  Acceptance itself measures each stem alone (power-additive by
construction), which is the physical power of that source.
"""

from __future__ import annotations

import math
import struct
from typing import List, Sequence

from .model import FS, TONE_FRAC


# --------------------------------------------------------------------------- #
# dB helpers
# --------------------------------------------------------------------------- #
def amp_from_db(db: float) -> float:
    return 10.0 ** (db / 20.0)


def pow_from_db(db: float) -> float:
    return 10.0 ** (db / 10.0)


def db_from_pow(p: float, floor: float = 1e-30) -> float:
    return 10.0 * math.log10(max(p, floor))


def amp_full_window(level_db: float) -> float:
    """Amplitude so a full-window tone has mean-square = 10**(level_db/10)."""
    return math.sqrt(2.0 * pow_from_db(level_db))


def amp_duty(level_db: float, frac: float = TONE_FRAC) -> float:
    """Amplitude so a tone filling `frac` of the beat has window mean-square
    10**(level_db/10)."""
    return math.sqrt(2.0 * pow_from_db(level_db) / frac)


# --------------------------------------------------------------------------- #
# WAV I/O
# --------------------------------------------------------------------------- #
def write_wav(path: str, samples: Sequence[float], fs: int = FS) -> None:
    with wave_open(path, fs) as w:
        w.writeframes(_pack(samples))


class wave_open:
    """Tiny context wrapper so callers never import `wave` directly."""
    def __init__(self, path: str, fs: int):
        self.path, self.fs, self._w = path, fs, None

    def __enter__(self):
        import wave
        self._w = wave.open(self.path, "wb")
        self._w.setnchannels(1)
        self._w.setsampwidth(2)
        self._w.setframerate(self.fs)
        return self._w

    def __exit__(self, *exc):
        self._w.close()


def _pack(samples: Sequence[float]) -> bytes:
    out = bytearray()
    for s in samples:
        v = int(round(max(-1.0, min(1.0, s)) * 32767))
        out += struct.pack("<h", v)
    return bytes(out)


def read_wav(path: str) -> List[float]:
    import wave
    with wave.open(path, "rb") as w:
        n = w.getnframes()
        raw = w.readframes(n)
    return [v / 32767.0 for v in struct.unpack("<%dh" % n, raw)]


def normalize(samples: Sequence[float], peak: float = 0.98) -> List[float]:
    m = max((abs(x) for x in samples), default=0.0)
    if m <= 0:
        return list(samples)
    g = peak / m
    return [x * g for x in samples]


# --------------------------------------------------------------------------- #
# Tone synthesis
# --------------------------------------------------------------------------- #
def tone(freq: float, dur_s: float, amp: float, fs: int = FS,
         phase: float = 0.0) -> List[float]:
    n = int(round(dur_s * fs))
    w = 2.0 * math.pi * freq / fs
    return [amp * math.sin(w * i + phase) for i in range(n)]


def source_freq(center: float, tag: int) -> float:
    """Small in-band offset so independent sources are mutually incoherent.

    Offset is within +/-0.5% of the band centre (well inside an octave band).
    """
    frac = ((tag % 7) - 3) * 0.0015     # -0.45% .. +0.45%
    return center * (1.0 + frac)


def beat_place(buf: List[float], seg: Sequence[float], at: int) -> None:
    for i, s in enumerate(seg):
        j = at + i
        if j >= len(buf):
            break
        buf[j] += s


# --------------------------------------------------------------------------- #
# Power measurement
# --------------------------------------------------------------------------- #
def mean_square(samples: Sequence[float], start: int, length: int) -> float:
    seg = samples[start:start + length]
    if not seg:
        return 0.0
    return sum(x * x for x in seg) / len(seg)


def mean_square_beats(samples: Sequence[float], beat_len: int,
                      beats: Sequence[int]) -> float:
    if not beats:
        return 0.0
    vals = [mean_square(samples, (b % 10_000) * beat_len, beat_len) for b in beats]
    return sum(vals) / len(vals)
