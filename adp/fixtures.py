"""验收词库构造。

两部分：
  1. 验收人手工编定的小词表（含同形异义词稿件）；
  2. 上千条合成术语，使“短页证明 << 全库”的对比有意义。

并提供三个修订：
  v1  初版
  v2  在同形词组中插入新义项、并在词库中部插入一个全新前缀词
  v3  仅改工坊所用某义项的一个字段（注音与解释可独立变动的演示基础）
"""
from __future__ import annotations

from adp.entries import Entry

KEY_ID = "compiler-2026"

# --------------------------------------------------------------- 手工词表

# 同形异义词组：同一显示词、多个义项；"长" 还兼有不同读音
CURATED_V1: list[Entry] = [
    Entry("行", 0, "xíng", "行走；走"),
    Entry("行", 1, "háng", "行列；行业"),
    Entry("行", 2, "xíng", "做；办（行礼、行医）"),
    Entry("花", 0, "huā", "种子植物的有性繁殖器官"),
    Entry("花", 1, "huā", "耗费（花钱、花时间）"),
    Entry("花", 2, "huā", "颜色或种类错杂的（花猫、花白）"),
    Entry("长", 0, "cháng", "两端之间距离大（与“短”相对）"),
    Entry("长", 1, "zhǎng", "生物体在发育过程中由小到大"),
    Entry("长", 2, "zhǎng", "领导人；负责人（校长、班长）"),
    Entry("包袱", 0, "bāo fu", "包衣服等东西的布"),
    Entry("包袱", 1, "bāo fu", "比喻某种负担（思想包袱）"),
    Entry("仪表", 0, "yí biǎo", "人的外表（包括容貌、姿态、风度）"),
    Entry("仪表", 1, "yí biǎo", "测定温度、气压等的仪器"),
    Entry("杜鹃", 0, "dù juān", "鸟，身体黑灰色，又叫杜宇、布谷"),
    Entry("杜鹃", 1, "dù juān", "常绿或落叶灌木，观赏植物"),
    # v2 的新插入词（前缀“花”邻域 + 全新中部词），先留空在此说明
    Entry("木简", 0, "mù jiǎn", "古代用来写字的木片"),
    Entry("木马", 0, "mù mǎ", "木制的马；也指儿童体育器械"),
    Entry("木排", 0, "mù pái", "结集成片的木材，借水流外运"),
]

# v2 相对 v1 的变化
V2_INSERTED_SENSE = Entry("花", 3, "huā", "（v2 新义项）指事业上的精华部分（文艺之花）")
V2_INSERTED_WORD = Entry("花岗石", 0, "huā gǎng shí", "（v2 新词）火成岩的一种，质地坚硬")

# v3：只改 “行” 义项 2 的解释，注音不动（字段独立变动演示）
V3_GLOSS_PATCH = ("行", 2, "xíng", "（v3 改注）从事；做（行医、行礼）")


# --------------------------------------------------------------- 合成大库

_SYL = ["bai", "bao", "can", "cen", "da", "dai", "fu", "gang",
        "hua", "ji", "jian", "kai", "lin", "mu", "na", "pai",
        "qing", "ran", "shan", "tai", "wan", "xing", "ya", "zan"]
_TAIL = ["术", "论", "谱", "表", "记", "法", "鉴", "编"]


def synthetic_entries(n: int = 1200) -> list[Entry]:
    """生成 n 条确定性合成术语（可重复生成，便于比对修订）。"""
    out: list[Entry] = []
    i = 0
    while len(out) < n:
        a = _SYL[i % len(_SYL)]
        b = _SYL[(i // len(_SYL)) % len(_SYL)]
        tail = _TAIL[(i // (len(_SYL) * len(_SYL))) % len(_TAIL)]
        word = f"术语{i:04d}{tail}"
        pron = f"{a} {b}"
        gloss = f"合成术语第 {i:04d} 号，读音 {a}-{b}，类目 {tail}"
        out.append(Entry(word, 0, pron, gloss))
        i += 1
    return out


def build_revisions(compiler) -> dict:
    """由编纂端产出 v1/v2/v3 三个修订对象。"""
    base = CURATED_V1 + synthetic_entries()
    v1 = compiler.publish(1, list(base), "2026-11-01T09:00:00Z")

    v2_entries = list(base) + [V2_INSERTED_SENSE, V2_INSERTED_WORD]
    v2 = compiler.publish(2, v2_entries, "2026-11-15T09:00:00Z")

    v3_entries = []
    for e in v2_entries:
        if e.key == (V3_GLOSS_PATCH[0], V3_GLOSS_PATCH[1]):
            v3_entries.append(Entry(*V3_GLOSS_PATCH))
        else:
            v3_entries.append(e)
    v3 = compiler.publish(3, v3_entries, "2026-11-22T09:00:00Z")
    return {"v1": v1, "v2": v2, "v3": v3}
