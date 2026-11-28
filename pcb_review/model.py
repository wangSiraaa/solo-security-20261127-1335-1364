from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple, Union


Number = float
Point = Tuple[float, float]
Bounds = Tuple[float, float, float, float]


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float
    layer: str
    name: str = ""

    @property
    def bounds(self) -> Bounds:
        return (self.x, self.y, self.x + self.w, self.y + self.h)


@dataclass(frozen=True)
class Circle:
    x: float
    y: float
    r: float
    layer: str
    name: str = ""

    @property
    def bounds(self) -> Bounds:
        return (self.x - self.r, self.y - self.r, self.x + self.r, self.y + self.r)


Shape = Union[Rect, Circle]


@dataclass(frozen=True)
class Hole:
    name: str
    x: float
    y: float
    diameter: float
    plated: bool
    layers: Tuple[str, str] = ("top", "bottom")


@dataclass(frozen=True)
class Terminal:
    name: str
    x: float
    y: float
    layer: str
    net: str = ""


@dataclass(frozen=True)
class IsolationRule:
    a: str
    b: str


@dataclass
class Board:
    name: str
    grid_pitch: float
    public_bounds: Bounds
    copper: List[Rect] = field(default_factory=list)
    openings: List[Rect] = field(default_factory=list)
    pads: List[Circle] = field(default_factory=list)
    holes: List[Hole] = field(default_factory=list)
    private_routes: List[Rect] = field(default_factory=list)
    terminals: List[Terminal] = field(default_factory=list)
    isolation: List[IsolationRule] = field(default_factory=list)

    @property
    def layers(self) -> Tuple[str, str]:
        return ("top", "bottom")


def _rect(value: Dict[str, Any], layer_default: str | None = None) -> Rect:
    return Rect(
        float(value["x"]),
        float(value["y"]),
        float(value["w"]),
        float(value["h"]),
        str(value.get("layer", layer_default or "top")),
        str(value.get("name", "")),
    )


def _circle(value: Dict[str, Any], layer_default: str | None = None) -> Circle:
    return Circle(
        float(value["x"]),
        float(value["y"]),
        float(value["r"]),
        str(value.get("layer", layer_default or "top")),
        str(value.get("name", "")),
    )


def load_board(path: str) -> Board:
    import json

    with open(path, "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    bounds = raw["public_bounds"]
    board = Board(
        name=str(raw.get("name", "board")),
        grid_pitch=float(raw["grid_pitch"]),
        public_bounds=(
            float(bounds["x"]),
            float(bounds["y"]),
            float(bounds["w"]),
            float(bounds["h"]),
        ),
    )
    board.copper = [_rect(item) for item in raw.get("copper", [])]
    board.openings = [_rect(item) for item in raw.get("openings", [])]
    board.pads = [_circle(item) for item in raw.get("pads", [])]
    board.private_routes = [_rect(item) for item in raw.get("private_routes", [])]
    board.holes = [
        Hole(
            name=str(item["name"]),
            x=float(item["x"]),
            y=float(item["y"]),
            diameter=float(item["diameter"]),
            plated=bool(item["plated"]),
            layers=tuple(item.get("layers", ["top", "bottom"])),  # type: ignore[arg-type]
        )
        for item in raw.get("holes", [])
    ]
    board.terminals = [
        Terminal(
            name=str(item["name"]),
            x=float(item["x"]),
            y=float(item["y"]),
            layer=str(item["layer"]),
            net=str(item.get("net", "")),
        )
        for item in raw.get("terminals", [])
    ]
    board.isolation = [
        IsolationRule(str(item["a"]), str(item["b"]))
        for item in raw.get("isolation", [])
    ]
    return board
