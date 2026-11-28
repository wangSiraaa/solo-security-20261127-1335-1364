# -*- coding: utf-8 -*-
"""教学手机与字形能力模型。

两台旧机的差异：
  * phone-ar：阿拉伯语字形齐全（含双数分支），缺 ✨ 与部分西里尔字形；
  * phone-ru：西里尔字形齐全（含 few/many 分支），缺阿拉伯文与 ✨ 字形。
另含一台离线复刻机 phone-replica，字形与 phone-ar 相同，用于留存复演。
"""

from __future__ import annotations

# 字形能力以标签声明：字体能渲染某标签覆盖的码位即视为可用。
_GLYPH_RANGES = {
    "arabic": [(0x0600, 0x06FF), (0x0750, 0x077F), (0xFB50, 0xFDFF),
               (0xFE70, 0xFEFF)],
    "cyrillic": [(0x0400, 0x04FF)],
    "latin": [(0x0020, 0x007E)],
    "punct": [(0x00A0, 0x00FF), (0x2000, 0x206F)],   # 通用标点与方向控制
    "star": [(0x2728, 0x2728)],          # ✨
    "cross_mark": [(0x274C, 0x274C)],    # ❌
}
# 任意字体默认具备的通用码位（空白、ASCII 标点、通用标点/方向符）
_BASELINE_TAGS = {"latin", "punct"}


class Font:
    def __init__(self, name: str, supported_tags: set[str]):
        # 基线标签始终可用：调用方只需声明语言/特殊字形能力
        self.name = name
        self.supported_tags = set(supported_tags) | _BASELINE_TAGS

    def can_render(self, text: str) -> bool:
        for char in text:
            # 只对已知标签覆盖的码位做能力判断；未登记的通用字符视为可渲染
            needed = {tag for tag, ranges in _GLYPH_RANGES.items()
                      if any(lo <= ord(char) <= hi for lo, hi in ranges)}
            if needed and not needed.issubset(self.supported_tags):
                return False
        return True


class Device:
    """教学手机：地区可切换，带各自的旧版后备目录与字体。"""

    def __init__(self, device_id: str, locale: str, font: Font,
                 supported_schema: int, key_id: str | None = None,
                 legacy_catalog: dict | None = None):
        self.device_id = device_id
        self.locale = locale
        self.font = font
        self.supported_schema = supported_schema
        self.key_id = key_id            # 获准终端才登记受限分支密钥
        self.legacy_catalog = legacy_catalog

    def switch_region(self, locale: str) -> None:
        self.locale = locale
