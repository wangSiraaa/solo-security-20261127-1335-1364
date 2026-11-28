"""赛前方案乙：保留既有杆带，在终点安排一次有限见证取景。

本模块不生成新编码，只在认证台给出的可行完整组合中，
挑出“实际能消除争议”的截面（事件 + 色带格），并模拟见证结果回报，
验证取景后组合是否唯一。截面若正落在遮格/交叠格上则不可采用。
"""
from __future__ import annotations

from .certify import Certification


def available_cells(cert: Certification, event_id: str) -> list[int]:
    """该事件两面都没有被遮/交叠/裁缺的格，才是物理上可取景的格。"""
    event_cert = next(e for e in cert.events if e.event.event_id == event_id)
    a_band = event_cert.event.bands["A"]
    b_band = event_cert.event.bands["B_mirrored"]
    return [i for i, (x, y) in enumerate(zip(a_band, b_band))
            if x is not None and y is not None]


def apply_witness(cert: Certification, event_id: str, cell: int,
                  observed_color: str, allow_framing: bool = True) -> dict:
    """按见证到的真实色过滤完整组合。

    普通截面要求两面都可见；needs_witness_framing 的补拍截面由终点见证
    取景提供，allow_framing 放行这种情况，流程仍完整留痕、可复读。
    """
    visible = cell in available_cells(cert, event_id)
    if not visible and not allow_framing:
        raise ValueError(f"{event_id} 第 {cell} 格落在遮挡/交叠/缺格处，不能作为见证截面")
    leg_map = {leg.leg_id: leg for leg in cert.legs}
    kept = [
        combo for combo in cert.combinations
        if leg_map[combo[event_id]].code[cell] == observed_color
    ]
    return {
        "event": event_id,
        "band_cell": cell,
        "observed_color": observed_color,
        "framing_used": not visible,
        "combinations_before": len(cert.combinations),
        "combinations_after": len(kept),
        "resolves": len(kept) == 1,
        "surviving_combinations": kept,
        "note": "组合唯一，争议消除" if len(kept) == 1 else "截面不足，争议仍在",
    }


def choose_section(cert: Certification) -> dict | None:
    """优先选完全唯一化截面；可见格直接取景，遮挡格走终点有限补拍。"""
    for want in ("full", "partial"):
        for option in cert.witness_options:
            if option["level"] != want:
                continue
            event_id = option["event"]
            visible = option.get("physically_visible",
                                 option["band_cell"] in available_cells(cert, event_id))
            return {
                "plan": "B_keep_existing_bands",
                "event": event_id,
                "band_cell": option["band_cell"],
                "level": option["level"],
                "colors_offered": option["colors_offered"],
                "frames": 1,
                "visible_now": visible,
                "needs_witness_framing": option.get("needs_witness_framing", not visible),
                "note": ("单次有限取景即可；任一可能色出现后组合均唯一"
                         if want == "full"
                         else f"单次取景部分收窄（{option['effect']}），必要时追加取景"),
            }
    return None
