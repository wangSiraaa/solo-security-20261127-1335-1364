"""逻辑时钟：让演练输出和重放保持确定。"""

from __future__ import annotations


class Clock:
    def __init__(self, start: int = 0) -> None:
        self.tick = start

    def now(self) -> int:
        self.tick += 1
        return self.tick

    def current(self) -> int:
        return self.tick
