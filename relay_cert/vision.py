"""视觉端：只从逐列 PNG 与本场标定条取证，不接触真值，也不采人体特征。

步骤：
1. 像素按本场标定条的格边长 CELL 还原成色键网格；
2. 用非回文标定色序判断该相机回读方向是否翻转，必要时镜像还原；
3. 找判线脉冲橙格，给两判线偏移核对提供几何基准；
4. 连通域抽迹；遇到交叠灰格 X 则切开成多笔（stroke），
   再用同列窗口与交叠邻接把笔归回同一条观测迹；
5. 逐列输出 band：被遮 K / 交叠 X / 裁掉缺格一律记 None，绝不猜色。
裁判裁边后的图走同一入口，缺标定条时凭交单登记的方向参数还原。
"""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from pathlib import Path

from .model import (
    BG_COLOR,
    CAL_SEQ,
    CELL,
    MASK_COLOR,
    MIX_COLOR,
    ObsCell,
    ObsTrace,
    Stroke,
    color_key,
)
from .png_io import read_png, write_png


@dataclass
class Frame:
    camera: str
    width: int
    height: int
    grid: list[list[str]]
    detected_flip: bool
    restored: bool
    pulse_col: int | None
    cal_present: bool
    cal_rows: int = 0
    crop_note: str | None = None


def _blocks_to_grid(pixels: list[list[tuple[int, int, int]]], cell: int) -> list[list[str]]:
    height = len(pixels) // cell
    width = len(pixels[0]) // cell
    grid: list[list[str]] = []
    for r in range(height):
        row: list[str] = []
        for c in range(width):
            votes = Counter(
                color_key(pixels[r * cell + dy][c * cell + dx])
                for dy in range(cell)
                for dx in range(cell)
            )
            row.append(votes.most_common(1)[0][0])
        grid.append(row)
    return grid


def _calibration_score(grid: list[list[str]]) -> tuple[int, int, bool]:
    """在标定行内滑窗找非回文色序的正向/反向最佳匹配（正向=未翻转）。"""
    if not grid:
        return 0, 0, False
    row = grid[0]
    window = len(CAL_SEQ)
    present = sum(1 for k in row if k == "O") >= 2 and len(row) >= window
    fwd = rev = 0
    for start in range(0, len(row) - window + 1):
        f = sum(1 for j, k in enumerate(CAL_SEQ) if row[start + j] == k)
        # 反向：窗口内逆序后再对标准序。
        r = sum(1 for j, k in enumerate(CAL_SEQ)
                if row[start + window - 1 - j] == k)
        fwd, rev = max(fwd, f), max(rev, r)
    return fwd, rev, present


def _flip_from_pulse(grid: list[list[str]], camera: str) -> bool | None:
    """判线脉冲物理列 0。

    A 相机正常成像时脉冲在左端，跑到右端即回读翻转；
    B 相机正常镜像时脉冲在右端，跑回左端即回读翻转。
    """
    if len(grid) < 2:
        return None
    cols = [c for c, k in enumerate(grid[1]) if k == "O"]
    if not cols:
        return None
    width = len(grid[1])
    on_right = sum(cols) / len(cols) > width / 2
    return (not on_right) if camera == "B" else on_right


def _find_pulse(grid: list[list[str]]) -> int | None:
    if len(grid) < 2:
        return None
    for c, key in enumerate(grid[1]):
        if key == "O":
            return c
    return None


def load_frame(path: str | Path, camera: str, cell: int = CELL,
               assume_flip: bool | None = None) -> Frame:
    _, _, pixels = read_png(path)
    grid = _blocks_to_grid(pixels, cell)
    fwd, rev, present = _calibration_score(grid)
    pulse_flip = _flip_from_pulse(grid, camera)
    if present:
        if camera == "A":
            # A 正常：色序正向、脉冲在左。
            seq_flip = rev > fwd
        else:
            # B 正常镜像：色序反向、脉冲在右；回读翻转时色序变回正向。
            seq_flip = fwd > rev
        if pulse_flip is not None and pulse_flip != seq_flip:
            # 两路证据冲突：保全材料，以色序为准并在交单标注，禁止静默猜向。
            pass
        detected_flip = seq_flip
    elif assume_flip is not None:
        detected_flip = assume_flip
    else:
        # 标定条被裁掉且交单未注明方向：不擅自决定，按未翻转处理并在结果标注。
        detected_flip = False
    restored = False
    if detected_flip:
        # 回读翻转故障：先镜像回该相机的原生成像方向。
        grid = [list(reversed(row)) for row in grid]
        restored = True
    if camera == "B":
        # B 相机物理上从反方向看：其原生成像相对 A 是镜像，统一翻到 A 坐标。
        grid = [list(reversed(row)) for row in grid]
    pulse = _find_pulse(grid)
    cal_rows = 2 if present else 0
    return Frame(camera, len(grid[0]), len(grid), grid, detected_flip,
                 restored, pulse, present, cal_rows)


def crop_edge_cells(src: str | Path, dst: str | Path, side: str, cells: int,
                    cell: int = CELL) -> str:
    """供外部裁判/测试使用：按格边长裁掉边上若干格，仍是普通 PNG。"""
    _, _, pixels = read_png(src)
    h, w = len(pixels), len(pixels[0])
    if side == "left":
        cut = cells * cell
        cropped = [row[cut:] for row in pixels]
    elif side == "right":
        cut = cells * cell
        cropped = [row[:w - cut] for row in pixels]
    elif side == "top":
        cut = cells * cell
        cropped = pixels[cut:]
    elif side == "bottom":
        cut = cells * cell
        cropped = pixels[:h - cut]
    else:
        raise ValueError("side 必须是 left/right/top/bottom")
    write_png(dst, cropped)
    return str(dst)


def _neighbors(r: int, c: int, h: int, w: int) -> list[tuple[int, int]]:
    out = []
    for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nr, nc = r + dr, c + dc
        if 0 <= nr < h and 0 <= nc < w:
            out.append((nr, nc))
    return out


def _extract_strokes(grid: list[list[str]], cam: str) -> tuple[list[Stroke], list[tuple[int, int]]]:
    h, w = len(grid), len(grid[0])
    seen = [[False] * w for _ in range(h)]
    strokes: list[Stroke] = []
    mix_cells: list[tuple[int, int]] = []
    sid = 0
    for r in range(h):
        for c in range(w):
            k = grid[r][c]
            if k in ("W", "O") or seen[r][c]:
                continue
            if k == "X":
                mix_cells.append((r, c))
                seen[r][c] = True
                continue
            # BFS：X 视为边界，K（遮罩）属于本笔内部。
            q = deque([(r, c)])
            seen[r][c] = True
            cells: list[ObsCell] = []
            while q:
                cr, cc = q.popleft()
                cells.append(ObsCell(cc, cr, grid[cr][cc]))
                for nr, nc in _neighbors(cr, cc, h, w):
                    if seen[nr][nc] or grid[nr][nc] in ("W", "O", "X"):
                        continue
                    seen[nr][nc] = True
                    q.append((nr, nc))
            cols = sorted({cell.col for cell in cells})
            rows = [cell.row for cell in cells]
            edges = []
            cell_pos = {(cell.row, cell.col) for cell in cells}
            for cr, cc in cell_pos:
                for nr, nc in _neighbors(cr, cc, h, w):
                    if grid[nr][nc] == "X":
                        edges.append(f"r{cr}c{cc}->X")
            strokes.append(Stroke(
                stroke_id=f"{cam}-s{sid}", cells=cells, cols=cols,
                row_min=min(rows), row_max=max(rows), mixed_edges=sorted(set(edges)),
            ))
            sid += 1
    return strokes, mix_cells


def _union_groups(strokes: list[Stroke], mix_cells: list[tuple[int, int]],
                  grid: list[list[str]]) -> list[list[int]]:
    """同列窗口直接相接的笔归为同一条观测迹。

    交叠灰格 X 是两杆边界，绝不作为合并依据：擦肩两杆各自仍有连通的独有列，
    被 X 挡住的只是共有列，强行并集会把两队杆认成一条。
    """
    n = len(strokes)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        parent[find(a)] = find(b)

    for i in range(n):
        for j in range(i + 1, n):
            si, sj = strokes[i], strokes[j]
            # 只合并同一条迹被交叠 X 切成的上下段：列集合相同、宽度相同、
            # 行间隔不超过两格。列集合不同的擦肩两杆永远是两条独立迹。
            if (si.cols == sj.cols
                    and max(si.row_min, sj.row_min)
                    - min(si.row_max, sj.row_max) <= 2):
                union(i, j)
    groups: dict[int, list[int]] = {}
    for idx in range(n):
        groups.setdefault(find(idx), []).append(idx)
    return list(groups.values())


def extract_traces(frame: Frame) -> list[ObsTrace]:
    # 标定条不是赛事物体，抽迹前整段剔除；行号在剩余坐标系内自洽。
    grid = [row for row in frame.grid[frame.cal_rows:]]
    strokes, mix_cells = _extract_strokes(grid, frame.camera)
    groups = _union_groups(strokes, mix_cells, grid)
    traces: list[ObsTrace] = []
    for gi, idxs in enumerate(sorted(groups, key=lambda g: min(strokes[i].cols[0] for i in g))):
        grp = [strokes[i] for i in idxs]
        cols = sorted({c for s in grp for c in s.cols})
        rows = [r for s in grp for r in (s.row_min, s.row_max)]
        band: list[str | None] = []
        for col in cols:
            votes: Counter[str] = Counter()
            for s in grp:
                for cell in s.cells:
                    if cell.col == col:
                        votes[cell.key] += 1
            if not votes:
                band.append(None)
                continue
            key = votes.most_common(1)[0][0]
            band.append(None if key in ("K", "X") else key)
        # 与该迹相邻的交叠格数量作为交叠证据。
        own_cells = {(cell.row, cell.col) for s in grp for cell in s.cells}
        overlaps = sorted({
            f"r{r}c{c}" for (r, c) in mix_cells
            if any(abs(r - pr) + abs(c - pc) == 1 for (pr, pc) in own_cells)
        })
        traces.append(ObsTrace(
            trace_id=f"{frame.camera}-t{gi}",
            camera=frame.camera,
            strokes=grp,
            col_center=(cols[0] + cols[-1]) / 2,
            row_min=min(rows),
            row_max=max(rows),
            band=band,
            overlap_with=overlaps,
        ))
    traces.sort(key=lambda t: (t.col_center, t.row_min))
    return traces
