"""封卷前递给档主的保障单。

两种保证无法对同一卷同时许诺，原因在份额配置本身：

SITE_RECOVERY（就近恢复，2-of-4，两库各 2 份）
  - 西库断线：东库两人凑齐 2 份即可恢复 ✔
  - 东库两只柜整体交管：代管方手里就是东库那 2 份，等于全部门限材料，可独自开卷 ✘
  - 底线：任何单人 1 份 < 门限 2，仍不能独自开卷 ✔

SPLIT_SITE（不可替代材料分处两库，3-of-4，每库 2 份）
  - 西库断线：东库最多 2 份 < 门限 3，无法独自恢复 ✘
  - 东库整批交管：代管方只有 2 份 < 门限 3，缺西库材料，不能开卷 ✔
  - 西库任一保管人出事：本库另一人 + 东库两人 = 3 份，替补可兑现 ✔

同一套静态份额里，“一库即可恢复”与“一库被整体拿走也安全”要求的门限方向相反，
因此档主必须在保障单上二选一：签收，或退件。
"""

from dataclasses import dataclass

EAST = "东库"
WEST = "西库"
SITES = (EAST, WEST)

SITE_RECOVERY = "SITE_RECOVERY"   # 就近恢复：西库失联时东库可自力恢复
SPLIT_SITE = "SPLIT_SITE"         # 分处两库：整库交管也安全

DEFAULT_CUSTODIANS = {
    "东甲": {"site": EAST, "index": 1},
    "东乙": {"site": EAST, "index": 2},
    "西甲": {"site": WEST, "index": 3},
    "西乙": {"site": WEST, "index": 4},
}


@dataclass
class Policy:
    name: str
    threshold: int
    indices: list[int]

    @property
    def title(self) -> str:
        return {
            SITE_RECOVERY: "就近恢复（一库到场即可开卷）",
            SPLIT_SITE: "分处两库（整库交管仍安全）",
        }[self.name]

    def site_of(self, index: int) -> str:
        return EAST if index <= 2 else WEST

    def can_combine(self, present_indices: set[int]) -> bool:
        present = {i for i in present_indices if i in self.indices}
        return len(present) >= self.threshold

    def east_alone_ok(self) -> bool:
        east = {i for i in self.indices if self.site_of(i) == EAST}
        return self.can_combine(east)

    def escrow_safe(self) -> bool:
        """代管方拿到东库整批（两只柜）后，是否仍凑不齐门限。"""
        east = {i for i in self.indices if self.site_of(i) == EAST}
        return not self.can_combine(east)

    def guarantee_lines(self) -> list[str]:
        lines = [
            f"配置：{len(self.indices)} 份，门限 {self.threshold} 份；东库 2 份（份位 1、2），西库 2 份（份位 3、4）。",
            "共同底线：任何一名保管人独自只有 1 份，均不能开卷。",
            f"西库全失联、东库自力恢复：{'可以承诺' if self.east_alone_ok() else '不能承诺'}。",
            f"东库两只柜整体暂交代管、代管方独自开卷：{'不能防止（代管方可凑齐）' if not self.escrow_safe() else '可以防止（代管方份位不足门限）'}。",
        ]
        if self.name == SPLIT_SITE:
            lines.append("替补安排：每库两人互为替补；恢复须两库都有人到场（东 2 + 西 1，或东 1 + 西 2）。")
        else:
            lines.append("替补安排：同库两人即可凑齐本库 2 份；任一库到场都能独立恢复。")
        return lines


POLICIES = {
    SITE_RECOVERY: Policy(SITE_RECOVERY, threshold=2, indices=[1, 2, 3, 4]),
    SPLIT_SITE: Policy(SPLIT_SITE, threshold=3, indices=[1, 2, 3, 4]),
}


def build_guarantee(volume_id: str, policy_name: str, pages: int) -> dict:
    if policy_name not in POLICIES:
        raise ValueError(f"未知配置：{policy_name}，可选 {list(POLICIES)}")
    p = POLICIES[policy_name]
    return {
        "volume_id": volume_id,
        "policy": policy_name,
        "title": p.title,
        "pages": pages,
        "threshold": p.threshold,
        "share_count": len(p.indices),
        "lines": p.guarantee_lines(),
        "status": "issued",       # issued -> signed（签收）/ rejected（退件）
    }
