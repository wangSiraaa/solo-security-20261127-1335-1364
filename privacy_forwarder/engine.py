"""
Local privacy-screen forwarder engine.

Core invariant
--------------
An operation is signed **only** against the receiver's single *actual displayed
view* for the current tick.  Never against:

  * the layers the sender intended to draw,
  * a frame the sender withheld,
  * a stale block the receiver happens to still hold,
  * an archived/historical screenshot.

Geometry / model
----------------
Two rectangular *regions* (``L`` and ``R``) tile a fixed virtual desktop.  Each
region is a grid of ``TILE x TILE`` cells (we call them *blocks* over the wire).

Layers, painted back-to-front by z-order:

  * base    -- the desktop (a spreadsheet-ish order document), one per region,
               carrying a *version* (e.g. ``"甲单"``).
  * window  -- a public popup (renew dialog); may move up / relocate / cover base.
  * privacy -- the secret layer (a private message).  Highest priority.

Fail-closed redaction
---------------------
For every tile, privacy is resolved *first and exclusively*.  A privacy-covered
tile is replaced by an opaque ``REDACTION`` block carrying only the sentinel
colour -- the private pixels *and* the base underneath are never consulted, so
nothing leaks and the hidden base cannot be guessed/split later.  Redaction is
strictly larger-or-equal than the true secret, never smaller.

The only honest source of truth for an operation is the endpoint: it physically
holds the last pixels it actually saw.  ``decide()`` accepts nothing but that
view.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, Optional, Tuple

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

REDACTION_COLOR = "#000000"
REDACTION_SOURCE = "privacy"
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

SOURCE_BASE = "base"
SOURCE_WINDOW = "window"
SOURCE_PRIVACY = "privacy"


def valid_color(color: str) -> str:
    if not isinstance(color, str) or not _COLOR_RE.match(color):
        raise ValueError(f"invalid colour {color!r}; expected #rrggbb")
    return color.lower()


def clean_label(label: Optional[str]) -> Optional[str]:
    if label is None:
        return None
    if not isinstance(label, str):
        raise ValueError("label must be a string")
    # keep it short, strip control chars; labels are fictional & user-facing
    s = re.sub(r"[\x00-\x1f\x7f]", "", label).strip()
    if len(s) > 40:
        s = s[:40]
    return s or None


def tile_hash(rid: str, ci: int, cj: int, content: Dict) -> str:
    payload = json.dumps(
        {"r": rid, "c": ci, "j": cj,
         "s": content["source"], "col": content["color"],
         "l": content["label"], "v": content.get("ver")},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def tile_key(rid: str, ci: int, cj: int) -> str:
    return f"{rid}/{ci}/{cj}"


def split_key(key: str) -> Tuple[str, int, int]:
    rid, ci, cj = key.split("/")
    return rid, int(ci), int(cj)


# --------------------------------------------------------------------------- #
# Rectangles & regions
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    def intersects(self, other: "Rect") -> bool:
        return not (
            self.x + self.w <= other.x
            or other.x + other.w <= self.x
            or self.y + self.h <= other.y
            or other.y + other.h <= self.y
        )

    def contains_point(self, px: int, py: int) -> bool:
        return self.x <= px < self.x + self.w and self.y <= py < self.y + self.h

    @staticmethod
    def from_str(spec: str) -> "Rect":
        parts = [int(p) for p in spec.split(",")]
        if len(parts) != 4:
            raise ValueError("rect must be x,y,w,h")
        x, y, w, h = parts
        if w <= 0 or h <= 0:
            raise ValueError("rect width/height must be positive")
        return Rect(x, y, w, h)

    def to_list(self):
        return [self.x, self.y, self.w, self.h]


@dataclass
class Region:
    rid: str
    rect: Rect
    tile: int

    @property
    def tiles_x(self) -> int:
        return self.rect.w // self.tile

    @property
    def tiles_y(self) -> int:
        return self.rect.h // self.tile

    def coords(self) -> Iterable[Tuple[int, int]]:
        for cj in range(self.tiles_y):
            for ci in range(self.tiles_x):
                yield ci, cj

    def tile_rect(self, ci: int, cj: int) -> Rect:
        return Rect(self.rect.x + ci * self.tile,
                    self.rect.y + cj * self.tile,
                    self.tile, self.tile)

    def point_to_tile(self, px: int, py: int) -> Optional[Tuple[int, int]]:
        if not self.rect.contains_point(px, py):
            return None
        ci = (px - self.rect.x) // self.tile
        cj = (py - self.rect.y) // self.tile
        return ci, cj

    def title_tile(self) -> Tuple[int, int]:
        return 0, 0

    def dest_tile(self) -> Optional[Tuple[int, int]]:
        # the "confirm" (右区确认栏) / "前台" (左区入口) anchor lives at the
        # top-right tile for L and the bottom-right tile for R
        if self.rid == "R":
            return self.tiles_x - 1, self.tiles_y - 1
        if self.rid == "L":
            return self.tiles_x - 1, 0
        return None


# --------------------------------------------------------------------------- #
# Layers
# --------------------------------------------------------------------------- #

@dataclass
class BaseLayer:
    """The desktop order document for one region, tied to a version."""
    rid: str
    version: str          # e.g. "甲单"
    color: str
    z: int = 0


@dataclass
class WindowLayer:
    """A public popup.  Public => its blocks ship normally (no secrecy)."""
    wid: str
    rect: Rect
    color: str
    label: Optional[str] = None
    z: int = 10


@dataclass
class PrivacyLayer:
    """The secret layer.  Covered tiles become fail-closed REDACTION blocks."""
    pid: str
    rect: Rect
    color: str = "#ff00ff"   # stored but never emitted; redaction colour is used
    z: int = 100


# --------------------------------------------------------------------------- #
# Frame composition  (intent: what the sender would *like* to show)
# --------------------------------------------------------------------------- #

class FrameComposer:
    def __init__(self, regions: Dict[str, Region]):
        self.regions = regions
        self.base: Dict[str, BaseLayer] = {}
        self.windows: Dict[str, WindowLayer] = {}
        self.privacies: Dict[str, PrivacyLayer] = {}

    # -- mutation helpers --------------------------------------------------- #
    def set_base(self, rid: str, version: str, color: str) -> None:
        if rid not in self.regions:
            raise KeyError(f"unknown region {rid!r}")
        self.base[rid] = BaseLayer(rid, version, valid_color(color))

    def set_window(self, wid: str, rect: Rect, color: str,
                   label: Optional[str] = None, z: int = 10) -> None:
        self.windows[wid] = WindowLayer(wid, rect, valid_color(color),
                                        clean_label(label), z)

    def set_privacy(self, pid: str, rect: Rect,
                    color: str = "#ff00ff", z: int = 100) -> None:
        self.privacies[pid] = PrivacyLayer(pid, rect, valid_color(color), z)

    def remove_privacy(self, pid: str) -> None:
        self.privacies.pop(pid, None)

    # -- resolution --------------------------------------------------------- #
    def _base_tile_content(self, region: Region, ci: int, cj: int) -> Dict:
        base = self.base[region.rid]
        label = None
        if (ci, cj) == region.title_tile():
            label = base.version
        elif region.rid == "L" and (ci, cj) == region.dest_tile():
            label = "前台"
        elif region.rid == "R" and (ci, cj) == region.dest_tile():
            label = "确认"
        # every base tile carries the order version it belongs to; the visible
        # version is decided by whole-region consensus, not by whichever label
        # happens to survive.
        return {"source": SOURCE_BASE, "color": base.color,
                "label": label, "ver": base.version}

    def _resolve(self, region: Region, ci: int, cj: int) -> Dict:
        trect = region.tile_rect(ci, cj)

        # 1) privacy has exclusive priority and fails closed: if it touches the
        #    tile at all, emit an opaque redaction and NEVER consult base/window.
        for p in sorted(self.privacies.values(), key=lambda l: l.z, reverse=True):
            if p.rect.intersects(trect):
                # colour-only sentinel; no label, no base, no window, no version.
                return {"source": SOURCE_PRIVACY,
                        "color": REDACTION_COLOR, "label": None, "ver": None}

        # 2) public window on top of base
        wins = [w for w in self.windows.values() if w.rect.intersects(trect)]
        if wins:
            w = max(wins, key=lambda l: l.z)
            return {"source": SOURCE_WINDOW, "color": w.color,
                    "label": w.label, "ver": None}

        # 3) base document
        return self._base_tile_content(region, ci, cj)

    def compose(self) -> Dict[str, Dict]:
        """Return the full intended frame: key -> tile content + hash."""
        frame: Dict[str, Dict] = {}
        for rid, region in self.regions.items():
            for ci, cj in region.coords():
                content = self._resolve(region, ci, cj)
                key = tile_key(rid, ci, cj)
                content = dict(content)
                content["hash"] = tile_hash(rid, ci, cj, content)
                frame[key] = content
        return frame

    def region_versions(self) -> Dict[str, Optional[str]]:
        """Intended version per region (NOT to be used for authorization)."""
        return {rid: (self.base[rid].version if rid in self.base else None)
                for rid in self.regions}


def frame_has_redaction(frame: Dict[str, Dict]) -> bool:
    return any(t["source"] == SOURCE_PRIVACY for t in frame.values())


# --------------------------------------------------------------------------- #
# Endpoint  (the customer terminal: only ever holds last-seen pixels)
# --------------------------------------------------------------------------- #

class Endpoint:
    """
    Two fixed display regions holding only the last pixels actually received.

    There is deliberately no API to load a historical frame or to revert a tile
    to an older screenshot.  Writes are monotonic in tick and, once a tile is
    updated, an older revision is rejected -- updated regions cannot be rolled
    back to history.
    """

    def __init__(self, regions: Dict[str, Region]):
        self.regions = regions
        self._view: Dict[str, Dict] = {}
        self._tick: Dict[str, int] = {}

    # ------------------------------------------------------------------ #
    def receive(self, key: str, content: Dict, tick: int) -> bool:
        prev = self._tick.get(key)
        if prev is not None and tick < prev:
            # refuse to travel back in time: no reverting to an old screenshot
            return False
        stored = dict(content)
        stored["tick"] = tick
        self._view[key] = stored
        self._tick[key] = tick
        return True

    def reconnect(self) -> None:
        """A reconnect keeps whatever old blocks are physically retained."""
        # explicitly a no-op: old blocks stay, nothing is refreshed or erased.
        return None

    def view(self) -> Dict[str, Dict]:
        return copy.deepcopy(self._view)

    # ------------------------------------------------------------------ #
    def report(self, tick: int) -> Dict:
        """What this terminal *actually displays*, tile by tile + per region."""
        regions_out = {}
        for rid, region in self.regions.items():
            tiles = []
            fully_visible = True
            redactions = 0
            missing = 0
            stale = 0
            base_versions = set()
            for ci, cj in region.coords():
                key = tile_key(rid, ci, cj)
                t = self._view.get(key)
                if t is None:
                    fully_visible = False
                    missing += 1
                    tiles.append({"tile": [ci, cj], "status": "missing"})
                    continue
                is_fresh = self._tick.get(key) == tick
                if not is_fresh:
                    stale += 1
                if t["source"] == SOURCE_PRIVACY:
                    fully_visible = False
                    redactions += 1
                elif t["source"] == SOURCE_BASE and t.get("ver") is not None:
                    base_versions.add(t["ver"])
                status = "fresh" if is_fresh else "stale"
                tiles.append({
                    "tile": [ci, cj],
                    "status": status,
                    "source": t["source"],
                    "color": t["color"],
                    "label": t.get("label"),
                    "ver": t.get("ver"),
                    "hash": t.get("hash"),
                    "tick": t.get("tick"),
                })
            # visible version = consensus over ALL base tiles in the region.
            # disagreement (torn / partially stale) => None, fail closed.
            if len(base_versions) == 1:
                version_seen = next(iter(base_versions))
            elif not base_versions:
                version_seen = None       # everything covered / no base shown
            else:
                version_seen = None       # conflicting versions => ambiguous
            regions_out[rid] = {
                "fully_visible": fully_visible,
                "version_seen": version_seen,
                "versions_visible": sorted(base_versions),
                "redactions": redactions,
                "missing": missing,
                "stale": stale,
                "dest": self._dest_info(region, tick),
                "tiles": tiles,
            }
        return {"tick": tick, "regions": regions_out, "tiles": self.view()}

    def _dest_info(self, region: Region, tick: int) -> Optional[Dict]:
        """The destination / affordance tile actually displayed, if any.

        L's affordance is the 前台 (send-to-front) entrance; R's is the 确认
        (confirm) bar.  Whether the *base* document is visible there decides if
        the customer could physically complete the action.
        """
        dest = region.dest_tile()
        if dest is None:
            return None
        key = tile_key(region.rid, *dest)
        t = self._view.get(key)
        if t is None:
            return {"tile": list(dest), "present": False,
                    "base_visible": False, "status": "missing"}
        return {
            "tile": list(dest),
            "present": True,
            "source": t["source"],
            "ver": t.get("ver"),
            "label": t.get("label"),
            "status": "fresh" if self._tick.get(key) == tick else "stale",
            "base_visible": t["source"] == SOURCE_BASE,
        }


# --------------------------------------------------------------------------- #
# Block segmentation & the forwarder
# --------------------------------------------------------------------------- #

def segment_region(frame: Dict[str, Dict], rid: str,
                   region: Region) -> Dict[str, Dict]:
    out = {}
    for ci, cj in region.coords():
        key = tile_key(rid, ci, cj)
        out[key] = frame[key]
    return out


@dataclass
class DeliveryPlan:
    """Per-region delivery behaviour for the next refresh."""
    mode: Dict[str, str] = field(default_factory=lambda: {"L": "deliver", "R": "deliver"})

    def set(self, rid: str, mode: str) -> None:
        if mode not in ("deliver", "drop", "hold", "late"):
            raise ValueError("mode must be deliver|drop|hold|late")
        self.mode[rid] = mode


class Forwarder:
    def __init__(self, regions: Dict[str, Region], policy: str = "partitioned"):
        if policy not in ("partitioned", "atomic"):
            raise ValueError("policy must be partitioned|atomic")
        self.regions = regions
        self.composer = FrameComposer(regions)
        self.endpoint = Endpoint(regions)
        self.policy = policy
        self.delivery = DeliveryPlan()
        self.tick_no = 0
        self._deferred: list = []          # (due_tick, key, content)
        self.history: list = []            # forensic log only, never for signing

    # -- delivery plumbing -------------------------------------------------- #
    def _deliver_now(self, key: str, content: Dict) -> None:
        self.endpoint.receive(key, content, self.tick_no)

    def _flush_deferred(self) -> int:
        arrived = 0
        remain = []
        for due, key, content in self._deferred:
            if due <= self.tick_no:
                self.endpoint.receive(key, content, self.tick_no)
                arrived += 1
            else:
                remain.append((due, key, content))
        self._deferred = remain
        return arrived

    # -- the single refresh of a tick --------------------------------------- #
    def tick(self, ops: Optional[list] = None) -> Dict:
        self.tick_no += 1
        tick = self.tick_no

        # (0) late blocks from earlier ticks may arrive just before we refresh
        arrived_late = self._flush_deferred()

        # (1) compose the intended frame exactly ONCE for this tick
        frame = self.composer.compose()

        actions = {"sent": [], "withheld": [], "dropped": [], "held": [],
                   "deferred": [], "arrived_late": arrived_late}

        dirty = frame_has_redaction(frame)

        if self.policy == "atomic" and dirty:
            # whole-frame handoff: if the frame cannot be shown faithfully,
            # withhold ALL of it so the terminal keeps its last honest pixels.
            for rid, region in self.regions.items():
                for key in segment_region(frame, rid, region):
                    actions["withheld"].append(key)
        else:
            # partitioned handoff: ship region by region under the delivery plan
            for rid, region in self.regions.items():
                mode = self.delivery.mode.get(rid, "deliver")
                segment = segment_region(frame, rid, region)
                for key, content in segment.items():
                    if mode == "deliver":
                        self._deliver_now(key, content)
                        actions["sent"].append(key)
                    elif mode == "late":
                        self._deferred.append((tick + 1, key, content))
                        actions["deferred"].append(key)
                    elif mode == "drop":
                        actions["dropped"].append(key)
                    else:  # hold
                        actions["held"].append(key)

        # (2) the terminal reports the single actual view of this tick
        report = self.endpoint.report(tick)

        # (3) decide due operations against that actual view only
        decisions = []
        for op in (ops or []):
            decisions.append(decide(op, report, self.regions, tick))

        # (3b) view-level verdict: which old pictures must be invalidated
        invalidation = assess_view(report)

        # (4) forensic record (history is for audit, never for signing)
        self.history.append({
            "tick": tick,
            "policy": self.policy,
            "intended_versions": self.composer.region_versions(),
            "dirty": dirty,
            "actions": {k: len(v) if isinstance(v, list) else v
                        for k, v in actions.items()},
            "report": _report_digest(report),
            "invalidation": invalidation,
            "decisions": decisions,
        })

        return {
            "tick": tick,
            "policy": self.policy,
            "dirty": dirty,
            "actions": actions,
            "report": report,
            "invalidation": invalidation,
            "decisions": decisions,
        }


def _report_digest(report: Dict) -> Dict:
    out = {}
    for rid, r in report["regions"].items():
        out[rid] = {
            "fully_visible": r["fully_visible"],
            "version_seen": r["version_seen"],
            "redactions": r["redactions"],
            "missing": r["missing"],
            "stale": r["stale"],
        }
    return out


def assess_view(report: Dict) -> Dict:
    """
    View-level verdict on the *single actual display* of a tick.

    Returns which pictures must be discarded outright and which are merely
    incomplete this tick.  This is what "哪些旧画面必须作废" refers to -- it is a
    property of the shown pixels, independent of any particular operation.

      * ``void_regions``        -- a region holds two conflicting versions (torn
        refresh).  Untrustworthy; discard.
      * ``incomplete_regions``  -- covered / missing / not fully arrived.  Wait
        for the next refresh; not yet void.
      * ``cross_region_same``   -- whether all regions that are *coherently
        visible* agree on one version.  The product expects 两区对应同一版交易;
        when a coherent-but-old region lags a newer one, that old picture must be
        invalidated (you may not confirm a cross-region op across versions).
      * ``versions``            -- the version each region currently shows.
    """
    void_regions = []
    incomplete_regions = []
    versions = {}
    coherent = {}
    for rid, r in report["regions"].items():
        versions[rid] = r["version_seen"]
        if len(r.get("versions_visible", [])) > 1:
            void_regions.append(rid)
            coherent[rid] = False
        elif not r["fully_visible"]:
            incomplete_regions.append(rid)
            coherent[rid] = False
        else:
            coherent[rid] = True
    shown = {v for rid, v in versions.items()
             if coherent.get(rid) and v is not None}
    cross_region_same = len(shown) <= 1

    # Which coherent regions conflict?  From the shown pixels alone we cannot
    # tell which version is newer, so we neither wait nor guess: every coherent
    # region that shows a version differing from the others is an old picture
    # that must be refreshed before any same-version cross-region work.
    disagreeing = []
    if not cross_region_same:
        disagreeing = [rid for rid, v in versions.items()
                       if coherent.get(rid) and v is not None]

    return {
        "void_regions": void_regions,
        "incomplete_regions": incomplete_regions,
        "versions": versions,
        "cross_region_same": cross_region_same,
        "disagreeing_regions": disagreeing,
        "consistent": not void_regions and cross_region_same,
    }


# --------------------------------------------------------------------------- #
# Operations & the decider
# --------------------------------------------------------------------------- #

@dataclass
class Operation:
    oid: str
    kind: str            # "cross" (needs both regions, same version) or "left"
    version: str         # target order version, e.g. "甲单"
    due_tick: Optional[int] = None

    @staticmethod
    def from_dict(d: Dict) -> "Operation":
        return Operation(
            oid=str(d["oid"]),
            kind=str(d["kind"]),
            version=str(d["version"]),
            due_tick=(int(d["due_tick"]) if d.get("due_tick") is not None else None),
        )


def _region_reason(rep_region: Dict, prefix: str) -> str:
    """Explain why a region is not usable, fail-closed and unambiguous."""
    if rep_region["redactions"]:
        return f"{prefix}_redacted"
    if rep_region["missing"]:
        return f"{prefix}_missing"
    if len(rep_region.get("versions_visible", [])) > 1:
        return f"{prefix}_view_ambiguous"
    return f"{prefix}_not_fully_visible"


OUTCOME_AUTHORIZED = "authorized"   # 可办理：本拍实际视图即可证明
OUTCOME_WAIT = "wait"               # 等齐：缺块/遮罩/未到版，下一拍可能补齐
OUTCOME_VOID = "void"               # 作废：实际视图自相矛盾，旧画面必须丢弃


def _result(op: Operation, authorized: bool, reason: str,
            outcome: str, seen: Optional[Dict] = None) -> Dict:
    out = {"oid": op.oid, "kind": op.kind, "version": op.version,
           "authorized": authorized, "outcome": outcome, "reason": reason}
    if seen is not None:
        out["seen_versions"] = seen
    return out


def decide(op: Operation, report: Dict, regions: Dict[str, Region],
           tick: int) -> Dict:
    """
    Authorize strictly against the terminal's actual displayed view.

    An operation is provable only if what the customer *currently sees* would let
    them complete it right now:

      * left  : region L is fully visible (no redaction, no missing block) and
                the version the customer sees on L equals the target version.
      * cross : additionally region R is fully visible and shows the *same*
                version as L, both equal to the target.  "清空或留有旧版" on R
                therefore blocks a cross-region confirmation.

    Every refusal is classified, in this order, so the far end knows what to do:

      1. intra-region tearing (one region holds two versions) -> ``void``: the
         region is untrustworthy, discard.
      2. a needed region is *incomplete* (redacted / missing block) -> ``wait``:
         the confirm bar may reappear, a block may still arrive; re-query next
         tick.  (A redaction is transient, so it does NOT void the picture.)
      3. both needed regions complete but visibly on *different* versions ->
         ``void`` (cross_region_disagree): the screen is self-contradictory, an
         old picture must be refreshed, never averaged or guessed.
      4. both complete and agreeing but not the target version -> ``wait``: the
         target simply has not arrived on screen yet.
      5. otherwise provable -> ``authorized`` (subject to affordance visibility).
    """
    if op.due_tick is not None and op.due_tick > tick:
        return _result(op, False, "not_due", OUTCOME_WAIT)

    L = report["regions"].get("L")
    R = report["regions"].get("R")
    if L is None:
        return _result(op, False, "no_left_region", OUTCOME_WAIT)

    l_ver = L["version_seen"]
    seen = {"L": l_ver, "R": (R["version_seen"] if R else None)}

    # (1) intra-region tearing is always a contradiction, for any op kind
    if len(L.get("versions_visible", [])) > 1:
        return _result(op, False, "left_view_ambiguous", OUTCOME_VOID, seen)

    # ---- left-only operation: only L matters ----------------------------- #
    if op.kind == "left":
        if not L["fully_visible"]:                       # (2) incomplete -> wait
            return _result(op, False, _region_reason(L, "left"), OUTCOME_WAIT, seen)
        if l_ver is None or l_ver != op.version:         # (4) target not on screen
            return _result(op, False, "version_mismatch", OUTCOME_WAIT, seen)
        ld = L.get("dest") or {}
        if not ld.get("base_visible"):
            return _result(op, False, "left_dest_hidden", OUTCOME_WAIT, seen)
        return _result(op, True, "ok", OUTCOME_AUTHORIZED, seen)

    if op.kind != "cross":
        return _result(op, False, "unknown_kind", OUTCOME_VOID, seen)

    if R is None:
        return _result(op, False, "no_right_region", OUTCOME_WAIT, seen)

    # (1) tearing in R is a contradiction too
    if len(R.get("versions_visible", [])) > 1:
        return _result(op, False, "right_view_ambiguous", OUTCOME_VOID, seen)

    # (2) completeness before disagreement: a redaction/missing block is
    #     transient (privacy may lift, a block may arrive) -> wait, not void.
    if not L["fully_visible"]:
        return _result(op, False, _region_reason(L, "left"), OUTCOME_WAIT, seen)
    if not R["fully_visible"]:
        return _result(op, False, _region_reason(R, "right"), OUTCOME_WAIT, seen)

    # (3) both complete & coherent: a version disagreement is a contradiction
    r_ver = R["version_seen"]
    if l_ver is None or r_ver is None:
        return _result(op, False, "base_not_visible", OUTCOME_WAIT, seen)
    if l_ver != r_ver:
        return _result(op, False, "cross_region_disagree", OUTCOME_VOID, seen)

    # (4) same version but not the target yet -> wait
    if l_ver != op.version:
        return _result(op, False, "version_mismatch", OUTCOME_WAIT, seen)

    # (5) both affordances must be physically visible on the actual view
    ld = L.get("dest") or {}
    rd = R.get("dest") or {}
    if not ld.get("base_visible"):
        return _result(op, False, "left_dest_hidden", OUTCOME_WAIT, seen)
    if not rd.get("base_visible"):
        return _result(op, False, "right_confirm_hidden", OUTCOME_WAIT, seen)

    return _result(op, True, "ok", OUTCOME_AUTHORIZED, seen)

    # both affordances must be physically visible on the actual view
    ld = L.get("dest") or {}
    rd = R.get("dest") or {}
    if not ld.get("base_visible"):
        return _result(op, False, "left_dest_hidden", OUTCOME_WAIT, seen)
    if not rd.get("base_visible"):
        return _result(op, False, "right_confirm_hidden", OUTCOME_WAIT, seen)

    return _result(op, True, "ok", OUTCOME_AUTHORIZED, seen)


# --------------------------------------------------------------------------- #
# Click checking  (a click may only land on content actually displayed)
# --------------------------------------------------------------------------- #

def check_click(x: int, y: int, report: Dict, regions: Dict[str, Region]) -> Dict:
    for rid, region in regions.items():
        ci_cj = region.point_to_tile(x, y)
        if ci_cj is None:
            continue
        ci, cj = ci_cj
        key = tile_key(rid, ci, cj)
        tile = report["tiles"].get(key)
        if tile is None:
            return {"accepted": False, "region": rid, "tile": [ci, cj],
                    "reason": "nothing_displayed_here"}
        if tile["source"] == SOURCE_PRIVACY:
            return {"accepted": False, "region": rid, "tile": [ci, cj],
                    "reason": "redacted", "source": tile["source"]}
        return {"accepted": True, "region": rid, "tile": [ci, cj],
                "source": tile["source"], "color": tile["color"],
                "label": tile.get("label"),
                "version_seen": report["regions"][rid]["version_seen"]}
    return {"accepted": False, "reason": "outside_all_regions"}


# --------------------------------------------------------------------------- #
# Serialization  (for CLI state between commands)
# --------------------------------------------------------------------------- #

def forwarder_to_dict(fw: Forwarder) -> Dict:
    return {
        "tick": fw.tick_no,
        "policy": fw.policy,
        "regions": {rid: {"rect": r.rect.to_list(), "tile": r.tile}
                    for rid, r in fw.regions.items()},
        "base": {rid: {"version": b.version, "color": b.color}
                 for rid, b in fw.composer.base.items()},
        "windows": {wid: {"rect": w.rect.to_list(), "color": w.color,
                          "label": w.label, "z": w.z}
                    for wid, w in fw.composer.windows.items()},
        "privacies": {pid: {"rect": p.rect.to_list(), "color": p.color, "z": p.z}
                      for pid, p in fw.composer.privacies.items()},
        "delivery": dict(fw.delivery.mode),
        "endpoint": {"view": {k: v for k, v in fw.endpoint._view.items()},
                     "tick": {k: v for k, v in fw.endpoint._tick.items()}},
        "deferred": [[d, k, c] for d, k, c in fw._deferred],
        "history": fw.history,
    }


def forwarder_from_dict(d: Dict) -> Forwarder:
    regions = {rid: Region(rid, Rect(*info["rect"]), info["tile"])
               for rid, info in d["regions"].items()}
    fw = Forwarder(regions, policy=d.get("policy", "partitioned"))
    for rid, b in d.get("base", {}).items():
        fw.composer.set_base(rid, b["version"], b["color"])
    for wid, w in d.get("windows", {}).items():
        fw.composer.set_window(wid, Rect(*w["rect"]), w["color"],
                               w.get("label"), w.get("z", 10))
    for pid, p in d.get("privacies", {}).items():
        fw.composer.set_privacy(pid, Rect(*p["rect"]), p["color"], p.get("z", 100))
    fw.delivery.mode = dict(d.get("delivery", {"L": "deliver", "R": "deliver"}))
    fw.tick_no = int(d.get("tick", 0))
    ep = d.get("endpoint", {})
    fw.endpoint._view = {k: dict(v) for k, v in ep.get("view", {}).items()}
    fw.endpoint._tick = {k: int(v) for k, v in ep.get("tick", {}).items()}
    fw._deferred = [(int(due), k, dict(c)) for due, k, c in d.get("deferred", [])]
    fw.history = d.get("history", [])
    return fw


def default_regions(tile: int = 60) -> Dict[str, Region]:
    return {
        "L": Region("L", Rect(120, 140, 360, 240), tile),
        "R": Region("R", Rect(540, 140, 360, 240), tile),
    }
