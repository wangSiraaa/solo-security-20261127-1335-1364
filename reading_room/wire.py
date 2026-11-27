"""In-memory model of the wire mailbox and its letters.

The wire service hands over an *issue*: a bundle of letters that arrived
together, plus one shared map illustration.  Letters carry

* text-version activity numbers (so a reader can select a lecture),
* an entry-direction description (the reliable, longer route), and
* optionally an accompanying map together with that map's textual description.

The accompanying map and its description are the only place the organiser's
step-free / accessible detour may appear, so both are modelled explicitly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Iterable

from .assets import AssetRef, parse_asset

STEP_FREE_ACCESS = {"corridor", "ramp", "lift", "level"}
STAIRS_ACCESS = {"stairs", "stair", "steps"}


class WireError(ValueError):
    """Malformed wire content."""


@dataclass(frozen=True)
class Edge:
    """A directed-or-undirected passage between two site nodes."""

    u: str
    v: str
    access: str = "corridor"
    weight: float = 1.0
    one_way: bool = False
    note: str = ""

    @property
    def is_stairs(self) -> bool:
        return self.access in STAIRS_ACCESS

    @property
    def step_free(self) -> bool:
        return not self.is_stairs

    @classmethod
    def from_dict(cls, raw: dict) -> "Edge":
        if not isinstance(raw, dict):
            raise WireError("edge must be an object")
        for key in ("u", "v"):
            if not raw.get(key):
                raise WireError(f"edge missing endpoint {key!r}: {raw!r}")
        access = raw.get("access", "corridor")
        if access not in STEP_FREE_ACCESS and access not in STAIRS_ACCESS:
            raise WireError(f"unknown edge access {access!r}")
        try:
            weight = float(raw.get("weight", 1.0))
        except (TypeError, ValueError):
            raise WireError(f"non-numeric edge weight: {raw!r}")
        if weight <= 0:
            raise WireError(f"edge weight must be positive: {raw!r}")
        return cls(
            u=raw["u"],
            v=raw["v"],
            access=access,
            weight=weight,
            one_way=bool(raw.get("one_way", False)),
            note=raw.get("note", ""),
        )


@dataclass
class RouteDescription:
    """A set of passages plus prose notes, as delivered with a letter/map."""

    edges: list[Edge] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    label: str = ""

    @classmethod
    def from_dict(cls, raw: dict | None, label: str = "") -> "RouteDescription | None":
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise WireError("route description must be an object")
        edges = [Edge.from_dict(e) for e in raw.get("edges", [])]
        notes = [str(n) for n in raw.get("notes", [])]
        return cls(edges=edges, notes=notes, label=label)


@dataclass(frozen=True)
class Event:
    """One lecture, selected by its text-version activity number."""

    event_number: str
    title: str
    venue: str
    venue_node: str
    note: str = ""


@dataclass
class Letter:
    letter_id: str
    subscriber: str
    title: str
    body_text: str
    arrival_gate: str
    assets: list[AssetRef]
    directions: RouteDescription
    events: list[Event]
    letter_map: AssetRef | None = None
    map_description: RouteDescription | None = None

    def event(self, event_number: str) -> Event:
        for ev in self.events:
            if ev.event_number == event_number:
                return ev
        raise WireError(
            f"letter {self.letter_id} has no activity number {event_number!r}"
        )


@dataclass
class WireMailbox:
    issue: str
    letters: list[Letter]
    common_map: AssetRef
    common_map_description: RouteDescription
    node_labels: dict[str, str]

    def letter(self, letter_id: str) -> Letter:
        for letter in self.letters:
            if letter.letter_id == letter_id:
                return letter
        raise WireError(f"no letter {letter_id!r} in issue {self.issue}")

    def all_assets(self) -> Iterable[tuple[str, AssetRef]]:
        """Yield ``(owner, asset)`` for every referenced image in the issue."""
        yield ("__issue__", self.common_map)
        for letter in self.letters:
            if letter.letter_map is not None:
                yield (letter.letter_id, letter.letter_map)
            for asset in letter.assets:
                yield (letter.letter_id, asset)

    def label(self, node: str) -> str:
        return self.node_labels.get(node, node)


def load_mailbox(path) -> WireMailbox:
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    return parse_mailbox(raw)


def parse_mailbox(raw: dict) -> WireMailbox:
    if not isinstance(raw, dict):
        raise WireError("mailbox must be an object")
    issue = raw.get("issue")
    if not issue:
        raise WireError("mailbox needs an issue id")
    node_labels = {str(k): str(v) for k, v in raw.get("node_labels", {}).items()}

    common = raw.get("common_map")
    if not isinstance(common, dict):
        raise WireError("mailbox needs a common_map")
    common_asset = parse_asset(common)
    common_desc = RouteDescription.from_dict(
        common.get("description"), label="common_map"
    )
    if common_desc is None:
        raise WireError("common_map needs a textual description")

    letters = []
    seen_ids = set()
    seen_numbers = set()
    for raw_letter in raw.get("letters", []):
        letter = _parse_letter(raw_letter)
        if letter.letter_id in seen_ids:
            raise WireError(f"duplicate letter id {letter.letter_id!r}")
        seen_ids.add(letter.letter_id)
        for ev in letter.events:
            if ev.event_number in seen_numbers:
                raise WireError(f"duplicate activity number {ev.event_number!r}")
            seen_numbers.add(ev.event_number)
        letters.append(letter)
    if not letters:
        raise WireError("mailbox has no letters")

    known = set(node_labels)
    for desc in [common_desc] + [l.directions for l in letters] + [
        l.map_description for l in letters if l.map_description is not None
    ]:
        for e in desc.edges:
            for node in (e.u, e.v):
                if node not in known:
                    raise WireError(
                        f"edge references unknown node {node!r}; add it to node_labels"
                    )
    for letter in letters:
        if letter.arrival_gate not in known:
            raise WireError(
                f"letter {letter.letter_id!r} arrival_gate {letter.arrival_gate!r} "
                "is not a known node"
            )
        for ev in letter.events:
            if ev.venue_node not in known:
                raise WireError(
                    f"event {ev.event_number!r} venue_node {ev.venue_node!r} "
                    "is not a known node"
                )

    return WireMailbox(
        issue=issue,
        letters=letters,
        common_map=common_asset,
        common_map_description=common_desc,
        node_labels=node_labels,
    )


def _parse_letter(raw: dict) -> Letter:
    if not isinstance(raw, dict):
        raise WireError("letter must be an object")
    letter_id = raw.get("letter_id")
    if not letter_id:
        raise WireError("letter needs a letter_id")
    assets = [parse_asset(a) for a in raw.get("assets", [])]
    directions = RouteDescription.from_dict(raw.get("directions"))
    if directions is None:
        raise WireError(f"letter {letter_id!r} needs text directions")

    letter_map = None
    map_desc = None
    raw_map = raw.get("letter_map")
    if raw_map is not None:
        letter_map = parse_asset(raw_map)
        if letter_map.role != "map":
            raise WireError(f"letter {letter_id!r} accompanying map must have role 'map'")
        map_desc = RouteDescription.from_dict(
            raw_map.get("description"), label=f"{letter_id}:map"
        )
        if map_desc is None:
            raise WireError(
                f"letter {letter_id!r} accompanying map needs a textual description"
            )

    events = []
    for raw_ev in raw.get("events", []):
        if not isinstance(raw_ev, dict):
            raise WireError("event must be an object")
        number = raw_ev.get("event_number")
        if not number:
            raise WireError(f"letter {letter_id!r} event needs an event_number")
        venue_node = raw_ev.get("venue_node")
        if not venue_node:
            raise WireError(
                f"letter {letter_id!r} event {number!r} needs a venue_node"
            )
        events.append(
            Event(
                event_number=number,
                title=raw_ev.get("title", ""),
                venue=raw_ev.get("venue", ""),
                venue_node=venue_node,
                note=raw_ev.get("note", ""),
            )
        )

    arrival_gate = raw.get("arrival_gate", "arrival_gate")

    return Letter(
        letter_id=letter_id,
        subscriber=raw.get("subscriber", ""),
        title=raw.get("title", ""),
        body_text=raw.get("body_text", ""),
        arrival_gate=arrival_gate,
        assets=assets,
        directions=directions,
        events=events,
        letter_map=letter_map,
        map_description=map_desc,
    )
