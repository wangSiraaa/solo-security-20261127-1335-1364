# -*- coding: utf-8 -*-
"""
relay.py —— 离线信箱（哑管道）。

终端离线期间各自发信，上线后可以按任意顺序取回；
信箱不区分会话语义、不持有密钥，只负责暂存与乱序投递。
"""
from __future__ import annotations

import os
from collections import defaultdict

from . import codec


class Relay:
    def __init__(self, relay_dir: str):
        self.dir = relay_dir
        os.makedirs(relay_dir, exist_ok=True)
        self.path = os.path.join(relay_dir, "mailbox.json")
        if os.path.exists(self.path):
            self.box = codec.from_json(open(self.path, "rb").read())
        else:
            self.box = defaultdict(list)
            self._flush()

    def _flush(self) -> None:
        with open(self.path + ".tmp", "wb") as f:
            f.write(codec.to_json(self.box))
        os.replace(self.path + ".tmp", self.path)

    def send(self, recipient: str, envelope: dict) -> None:
        self.box[recipient].append(codec.b64e(codec.envelope_to_wire(envelope)))
        self._flush()

    def pending(self, recipient: str) -> list[dict]:
        return [codec.wire_to_envelope(codec.b64d(x)) for x in self.box.get(recipient, [])]

    def take(self, recipient: str, permutation=None) -> list[dict]:
        """按给定顺序取信（默认乱序：逆序）。取出即从信箱移除。"""
        items = self.pending(recipient)
        if permutation is not None:
            items = [items[i] for i in permutation]
        self.box[recipient] = []
        self._flush()
        return items

    def take_one(self, recipient: str, index: int) -> dict:
        raw = self.box[recipient].pop(index)
        self._flush()
        return codec.wire_to_envelope(codec.b64d(raw))
