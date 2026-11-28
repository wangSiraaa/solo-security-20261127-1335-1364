"""Sheet geometry: leaf orientation frames and forward fold simulation.

Model
-----
A printed sheet lives in 3-D sheet coordinates: +X right, +Y up, +Z toward
the viewer of side A (the "front").  Every leaf carries two frames:

* frame A -- orientation of sheet side A: at the flat sheet (X, Y, Z)
* frame B -- orientation of sheet side B, fixed to frame A by the
  contract's flip axis (how the back view is composed):
    flip_axis "vertical":   (-u, +v, -n)
    flip_axis "horizontal": (+u, -v, -n)

A frame is (u, v, n): the side's "right", "up" and outward normal as axis
unit vectors.  Folding a packet along a vertical line rotates the moving
half 180 degrees about the Y axis; along a horizontal line, about the X
axis.  Frames of moved leaves transform with them.

The *content* frame of a printed page is the sheet frame rotated in the
plane by the print rotation rho chosen at imposition time.  The reader
rotates the finished booklet in the plane (0 or 180 deg) so the cover is
upright; the spine must then sit on the side the binding direction wants.
"""

X = (1, 0, 0)
Y = (0, 1, 0)
Z = (0, 0, 1)


def neg(v):
    return (-v[0], -v[1], -v[2])


def flip_v(v):
    """Rotate 180 deg about the vertical (Y) axis."""
    return (-v[0], v[1], -v[2])


def flip_h(v):
    """Rotate 180 deg about the horizontal (X) axis."""
    return (v[0], -v[1], -v[2])


def rotz180(v):
    """Rotate 180 deg in the plane of the sheet (about Z)."""
    return (-v[0], -v[1], v[2])


def rotz_cw(v):
    """Rotate 90 deg clockwise in the plane (visual, y-up coords)."""
    return (v[1], -v[0], v[2])


def rotz_cw_k(v, k):
    for _ in range(k % 4):
        v = rotz_cw(v)
    return v


def mirror_dir_v(d):
    return {"W": "E", "E": "W", "N": "N", "S": "S"}[d]


def mirror_dir_h(d):
    return {"N": "S", "S": "N", "W": "W", "E": "E"}[d]


def rot_dir_180(d):
    return {"N": "S", "S": "N", "W": "E", "E": "W"}[d]


def back_frame(fa, flip_axis):
    u, v, n = fa
    if flip_axis == "vertical":
        return (neg(u), v, neg(n))
    if flip_axis == "horizontal":
        return (u, neg(v), neg(n))
    raise ValueError("bad flip axis %r" % flip_axis)


class Leaf:
    """A physical leaf: two printed sides plus both frames."""

    __slots__ = ("front", "back", "fa", "fb")

    def __init__(self, front, back, fa, fb):
        self.front = front  # payload attached to side A (position/image)
        self.back = back    # payload attached to side B
        self.fa = list(fa)
        self.fb = list(fb)

    def copy(self):
        return Leaf(self.front, self.back, list(self.fa), list(self.fb))

    def flip_v(self):
        self.fa = [flip_v(v) for v in self.fa]
        self.fb = [flip_v(v) for v in self.fb]

    def flip_h(self):
        self.fa = [flip_h(v) for v in self.fa]
        self.fb = [flip_h(v) for v in self.fb]

    def rotz180(self):
        self.fa = [rotz180(v) for v in self.fa]
        self.fb = [rotz180(v) for v in self.fb]


# content "up" vector -> quarter turns CW needed to bring it to +Y
DEROTATE_CW = {Y: 0, X: 3, neg(Y): 2, neg(X): 1}
# content "up" vector -> quarter turns CW the content appears rotated
RENDER_CW = {Y: 0, X: 1, neg(Y): 2, neg(X): 3}
# expected content "right" vector given content "up" (proper rotations)
_EXPECT_U = {Y: X, X: neg(Y), neg(Y): neg(X), neg(X): Y}


def check_frame(u, v):
    if _EXPECT_U[v] != u:
        raise ValueError("degenerate frame u=%r v=%r" % (u, v))


# ------------------------------------------------------------------- folding

def fold_sheet(grid, W, H, folds):
    """Fold a flat sheet (dict (c,r) -> [Leaf]) per the fold sequence.

    Returns (stack, closed_edges): the final packet stack (top first) and
    the set of packet edges ('N','E','S','W' in world directions) that are
    closed folds and therefore need cutting unless they are the spine.
    """
    closed = set()
    for fold in folds:
        grid, W, H = _fold_once(grid, W, H, fold)
        if fold["axis"] == "v":
            crease = "E" if fold["move"] == "right" else "W"
            mirror = mirror_dir_v
        else:
            crease = "N" if fold["move"] == "top" else "S"
            mirror = mirror_dir_h
        # an edge of the new packet is closed if it (or the edge mirrored
        # onto it from the moved half) was closed before; the crease itself
        # is always a new closed edge
        closed = {
            d
            for d in ("N", "E", "S", "W")
            if d != crease and (d in closed or mirror(d) in closed)
        }
        closed.add(crease)
    stacks = list(grid.values())
    if len(stacks) != 1:
        raise ValueError("folding did not converge to one packet")
    return stacks[0], closed


def _fold_once(grid, W, H, fold):
    kept_parts = {}
    moved_parts = {}
    if fold["axis"] == "v":
        W2 = W // 2
        for (c, r), S in grid.items():
            if fold["move"] == "right":
                tgt, moving = ((c, r), False) if c < W2 else ((W - 1 - c, r), True)
            else:  # left
                tgt, moving = ((c - W2, r), False) if c >= W2 else ((W2 - 1 - c, r), True)
            if moving:
                moved_parts[tgt] = _flipped(S, "v")
            else:
                kept_parts[tgt] = list(S)
        H2 = H
    else:
        H2 = H // 2
        for (c, r), S in grid.items():
            if fold["move"] == "top":
                tgt, moving = ((c, r), False) if r < H2 else ((c, H - 1 - r), True)
            else:  # bottom
                tgt, moving = ((c, r - H2), False) if r >= H2 else ((c, H2 - 1 - r), True)
            if moving:
                moved_parts[tgt] = _flipped(S, "h")
            else:
                kept_parts[tgt] = list(S)
        W2 = W
    new = {}
    for tgt in set(kept_parts) | set(moved_parts):
        k = kept_parts.get(tgt, [])
        m = moved_parts.get(tgt, [])
        new[tgt] = (m + k) if fold["place"] == "over" else (k + m)
    return new, W2, H2


def _flipped(stack, axis):
    out = []
    for leaf in reversed(stack):
        l2 = leaf.copy()
        if axis == "v":
            l2.flip_v()
        else:
            l2.flip_h()
        out.append(l2)
    return out


def grid_dims_from_folds(folds):
    W = H = 1
    for f in folds:
        if f["axis"] == "v":
            W *= 2
        else:
            H *= 2
    return W, H


def make_flat_grid(W, H, flip_axis):
    """A flat sheet of fresh leaves with sheet frames."""
    grid = {}
    for c in range(W):
        for r in range(H):
            fa = (X, Y, Z)
            grid[(c, r)] = [Leaf(None, None, fa, back_frame(fa, flip_axis))]
    return grid
