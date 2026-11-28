"""虚构共享光路（教具，无真实设备）。

物理布局（距离自接入端 A 起算）：

    A=1.0km----j1=1.5km----j2=2.0km=====2.4km=R1（档案室接收器）
                 主干                3dB 分支器
                                      \\----j4=2.5km----3.0km=R2（访客接收器）
    对端 B（巡测仪）挂在 R1 侧末端。

  - 接头 j1..j4 的插入损耗 x1..x4 为待估量（OTDR 只给区间，不给真值）。
  - 距离格（OTDR 分辨率盒）宽 0.5 km：j2 与 j4 距 A 同为 2.0km 格；
    j2 与 j3 距 B 同为 2.0km 格 —— 单端测量只能拿到盒内回波之和。
  - 分支器关闭（分支切换记录 off）时，分支臂上的 j4 不产生回波。
  - 光纤衰耗、分支器分配损耗为标定已知量。
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Dict, List, Tuple

F = Fraction

# ---------------- 固定标定参数（dB / km） ----------------
FIBER_LOSS_PER_KM = F(4, 10)          # 0.4 dB/km
SPLITTER_LOSS = F(32, 10)             # 3.2 dB 分支分配损耗
DISTANCE_BIN_KM = F(5, 10)            # OTDR 距离格宽度 0.5 km

# 接头坐标（km），以及各端到端路径上的固定光纤长度
JOINT_POSITIONS_KM = {"j1": F(15, 10), "j2": F(20, 10), "j3": F(24, 10), "j4": F(25, 10)}
PATH_FIBER_KM: Dict[str, Fraction] = {"R1": F(24, 10), "R2": F(30, 10)}
PATH_SPLITTER: Dict[str, Fraction] = {"R1": F(0), "R2": SPLITTER_LOSS}

# 端到端路径损耗 = 固定光纤 + 分支器 + 该路径经过的接头损耗
PATH_JOINTS: Dict[str, List[str]] = {
    "R1": ["j1", "j2", "j3"],
    "R2": ["j1", "j2", "j4"],
}

# ---------------- 发射功率档（有限档，不可无限增大） ----------------
TX_LEVELS_DBM: List[Fraction] = [F(-6), F(-2), F(2), F(6), F(10)]

# 接收恢复时间：连续发送后本站切回接收所需的空闲时隙数
DEFAULT_RECOVERY_SLOTS = 1


@dataclass
class Receiver:
    name: str
    sensitivity_dbm: Fraction          # 灵敏度门限
    recovery_slots: int                # 接收恢复时间（时隙）


def distance_bin(position_km: Fraction) -> Fraction:
    """OTDR 把回波归入 floor(pos / 0.5) * 0.5 的距离格。"""
    return (position_km / DISTANCE_BIN_KM).__floor__() * DISTANCE_BIN_KM


def fixed_path_loss(path: str) -> Fraction:
    return PATH_FIBER_KM[path] * FIBER_LOSS_PER_KM + PATH_SPLITTER[path]


def path_loss_expr(path: str) -> List[Fraction]:
    """端到端损耗关于 x=(x1,x2,x3,x4) 的线性系数（外加固定项见 fixed_path_loss）。"""
    coeffs = [F(0)] * 4
    for j in PATH_JOINTS[path]:
        coeffs[int(j[1]) - 1] = F(1)
    return coeffs


def total_path_loss(path: str, x: Sequence[Fraction]) -> Fraction:
    return fixed_path_loss(path) + sum(
        c * v for c, v in zip(path_loss_expr(path), x)
    )


def received_power_dbm(tx_dbm: Fraction, path: str, x: Sequence[Fraction]) -> Fraction:
    return tx_dbm - total_path_loss(path, x)


def minimum_feasible_level(
    path: str, loss_ub: Fraction
) -> Tuple[Fraction, Fraction] | None:
    """按损耗上界选最小可用发射档；全部档位都不够则返回 None。"""
    need = loss_ub  # tx - pathloss >= sensitivity  ⇒  tx >= sensitivity + pathloss
    for lvl in TX_LEVELS_DBM:
        if lvl >= need:
            return lvl, lvl - need
    return None


# ---------------- 巡测距离格定义（观测生成与分析共用） ----------------
def otamar_bins() -> Dict[str, Dict[str, List[str]]]:
    """各巡测端、各分支状态下，每个距离格里叠合的接头。

    关键性质：四个接头中只有 j1 能被独立测到（两端各自的 solo 格）；
    j2/j3/j4 的全部回波都以求和形式出现，且独立的求和式只有两个
    （x2+x4 与 x2+x3），秩 2 < 未知量数 3 —— 单端甚至两端联合都无法
    拆分各段损耗，剩余一维歧义只能靠端到端巡测功率区间部分收束。
    分支切换记录决定每格回波里是否含分支臂上的 j4。
    """
    return {
        "A": {
            "on": {
                # 自 A 起算：j1 独占 1.5km 格；j2 与分支臂 j4 同落 2.0km 格；
                # 2.5km 格内 j3 的回波被分支器近端反射淹没，无独立读数。
                "1.5": ["j1"],
                "2.0": ["j2", "j4"],
            },
            "off": {
                # 分支关闭：j4 回波消失，2.0km 格只剩 j2，但与 on 态读数差
                # 仅用于核验切换记录，不单独构成更窄的独立约束。
                "1.5": ["j1"],
                "2.0": ["j2", "j4"],
            },
        },
        "B": {
            "on": {
                # 自 B(2.4km) 起算：j1 独占 0.5km 格；j2/j3 同落 0.0km 格；
                # j4 经分支器的回波与 j3 往返路径同格，无法单独读出 j4。
                "0.0": ["j2", "j3"],
                "0.5": ["j1"],
            },
            "off": {
                "0.0": ["j2", "j3"],
                "0.5": ["j1"],
            },
        },
    }
