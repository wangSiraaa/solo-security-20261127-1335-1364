from __future__ import annotations

import math
from typing import Iterable

from .model import Circle, Rect, Shape

EPS = 1e-9


def rects_touch(a: Rect, b: Rect) -> bool:
    return not (
        a.x + a.w < b.x - EPS
        or b.x + b.w < a.x - EPS
        or a.y + a.h < b.y - EPS
        or b.y + b.h < a.y - EPS
    )


def circles_touch(a: Circle, b: Circle) -> bool:
    return math.hypot(a.x - b.x, a.y - b.y) <= a.r + b.r + EPS


def circle_rect_touch(circle: Circle, rect: Rect) -> bool:
    nearest_x = min(max(circle.x, rect.x), rect.x + rect.w)
    nearest_y = min(max(circle.y, rect.y), rect.y + rect.h)
    return math.hypot(circle.x - nearest_x, circle.y - nearest_y) <= circle.r + EPS


def shapes_touch(a: Shape, b: Shape) -> bool:
    if isinstance(a, Rect) and isinstance(b, Rect):
        return rects_touch(a, b)
    if isinstance(a, Circle) and isinstance(b, Circle):
        return circles_touch(a, b)
    if isinstance(a, Circle) and isinstance(b, Rect):
        return circle_rect_touch(a, b)
    if isinstance(a, Rect) and isinstance(b, Circle):
        return circle_rect_touch(b, a)
    raise TypeError(f"unsupported shape pair: {type(a)!r}, {type(b)!r}")


def point_in_shape(x: float, y: float, shape: Shape) -> bool:
    if isinstance(shape, Rect):
        return (
            shape.x - EPS <= x <= shape.x + shape.w + EPS
            and shape.y - EPS <= y <= shape.y + shape.h + EPS
        )
    if isinstance(shape, Circle):
        return math.hypot(x - shape.x, y - shape.y) <= shape.r + EPS
    raise TypeError(f"unsupported shape: {type(shape)!r}")


def describe_shape(shape: Shape) -> str:
    if isinstance(shape, Rect):
        return (
            f"{shape.layer} rect ({fmt(shape.x)},{fmt(shape.y)}) "
            f"{fmt(shape.w)}x{fmt(shape.h)}"
        )
    return (
        f"{shape.layer} circle pad center=({fmt(shape.x)},{fmt(shape.y)}) "
        f"r={fmt(shape.r)}"
    )


def fmt(value: float) -> str:
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return text or "0"


def bounds_contain(bounds: tuple[float, float, float, float], shape: Shape) -> bool:
    x, y, w, h = bounds
    if isinstance(shape, Rect):
        sx, sy, sw, sh = shape.x, shape.y, shape.w, shape.h
    else:
        sx, sy, sw, sh = shape.x - shape.r, shape.y - shape.r, 2 * shape.r, 2 * shape.r
    return (
        sx >= x - EPS
        and sy >= y - EPS
        and sx + sw <= x + w + EPS
        and sy + sh <= y + h + EPS
    )


def same_layer_shapes(shapes: Iterable[Shape]) -> Iterable[tuple[int, int]]:
    by_layer: dict[str, list[int]] = {}
    for index, shape in enumerate(shapes):
        by_layer.setdefault(shape.layer, []).append(index)
    for indices in by_layer.values():
        for pos, left in enumerate(indices):
            for right in indices[pos + 1 :]:
                yield left, right
