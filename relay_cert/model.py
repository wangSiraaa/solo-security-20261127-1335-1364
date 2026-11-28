"""数据对象与本场色板。所有结构都可 JSON 化，便于认证材料复读。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

# 有限色格：杆带只允许使用这些色。标定条另用橙色，背景白、遮罩黑、交叠灰。
PALETTE: dict[str, tuple[int, int, int]] = {
    "R": (214, 40, 40),
    "G": (46, 160, 67),
    "B": (36, 90, 210),
    "Y": (240, 200, 40),
    "M": (190, 60, 180),
    "C": (40, 170, 190),
}
CAL_COLOR = (240, 130, 30)      # 标定条专用橙
BG_COLOR = (255, 255, 255)      # 背景白
MASK_COLOR = (20, 20, 20)       # 被遮去的一格
MIX_COLOR = (120, 120, 120)     # 两杆交叠混色
CAL_SEQ = ["R", "G", "B", "Y", "M", "C", "R", "M"]  # 非回文，正反向可辨
CELL = 12                        # 每色格像素边长


def color_key(rgb: tuple[int, int, int]) -> str:
    """把像素颜色归到最近的已知色键；未知色归入对应特殊键。"""
    table = dict(PALETTE)
    table["K"] = MASK_COLOR
    table["X"] = MIX_COLOR
    table["O"] = CAL_COLOR
    best, best_d = "W", 10 ** 9
    for key, cand in table.items():
        d = sum((a - b) ** 2 for a, b in zip(rgb, cand))
        if d < best_d:
            best, best_d = key, d
    white_d = sum((a - b) ** 2 for a, b in zip(rgb, BG_COLOR))
    if white_d < best_d:
        return "W"
    return best


@dataclass
class Leg:
    """一棒的本场登记：队伍、棒次、本场可用图样（色带循环序列）。"""
    leg_id: str
    team: str
    order: int
    code: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TraceSpec:
    """一次越线真值（仅渲染端可见，认证端看不到）。"""
    event_id: str
    leg_id: str
    col: int                 # 相机 A 格坐标下的中心列
    row0: int                # 迹在图像中的起始行（含标定条后的相对行）
    length: int              # 纵向持续格数
    rotation: int            # 色带转动造成的循环移位
    mask_cell: int | None    # 被遮去的一格索引（转动后的局部坐标），None 为无遮挡


@dataclass
class CameraTruth:
    name: str
    traces: list[TraceSpec]
    flipped: bool = False    # 该相机回读方向是否被翻转


@dataclass
class Scene:
    scene_id: str
    width: int
    height: int
    line_offset_rows: int
    legs: list[Leg]
    camera_a: CameraTruth
    camera_b: CameraTruth
    cal_top_rows: int = 2    # 标定条占两行：色块行 + 判线脉冲行

    def leg_by_id(self, leg_id: str) -> Leg:
        for leg in self.legs:
            if leg.leg_id == leg_id:
                return leg
        raise KeyError(leg_id)


@dataclass
class ObsCell:
    col: int
    row: int
    key: str                # 色键；W 空，K 遮，X 交叠，O 标定橙


@dataclass
class Stroke:
    """同一迹在交叠混色处被切开后的一笔。"""
    stroke_id: str
    cells: list[ObsCell]
    cols: list[int]
    row_min: int
    row_max: int
    mixed_edges: list[str]   # 与交叠区相接的边界描述


@dataclass
class ObsTrace:
    """认证端从一面相机实际抽到的迹（不含任何身份信息）。"""
    trace_id: str
    camera: str
    strokes: list[Stroke]
    col_center: int
    row_min: int
    row_max: int
    band: list[str | None]   # 逐列色键，None 表示该格未取到（遮/混/缺）
    overlap_with: list[str] = field(default_factory=list)


@dataclass
class MatchEvent:
    """两面相机配对后的一次“越线事件”。"""
    event_id: str
    camera_a_trace: str
    camera_b_trace: str
    col_a: int
    col_b: int
    row_a: int
    row_b: int
    row_delta: int
    offset_residual: int
    bands: dict[str, list[str | None]]

    def to_dict(self) -> dict:
        return asdict(self)
