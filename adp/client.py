"""查词席 / 排字工坊端（client）。

本地只保存：信任的验签键、已验凭据的版本高水位、收下来的页面、
以及排字工坊自己的稿件。客户端永远拿不到全库快照。

验页完全独立：重算 Merkle 根 → 对签名凭据验签 → 检查范围证明的
资格窗口 → 检查页内条目与翻页语义。任何一步失败都抛
``VerificationError``，调用方不得把该页并入本地稿件。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from .credential import Credential
from .crypto import Verifier
from .entries import Entry, prefix_bounds, key_in_range
from .tree import verify_proof_structure, VerificationError, _node_span


class StaleCursorError(VerificationError):
    """游标属于旧修订：旧游标不能跨版续用，须从前缀首页重取。"""


def _collect_pruned(proof: dict[str, Any],
                    acc: list[tuple[tuple, tuple]]) -> None:
    if "pruned" in proof:
        first, last = _node_span(proof)
        acc.append((first, last))
    elif "internal" in proof:
        for kp in proof["internal"]:
            _collect_pruned(kp, acc)


class Client:
    def __init__(self, verifier: Verifier):
        self.verifier = verifier
        self.last_seen_version: Optional[int] = None

    # ------------------------------------------------ 验页

    def verify_response(self, resp: dict[str, Any],
                        allow_old_version: bool = False) -> list[Entry]:
        """独立验证一张答页，返回其中**可安全并入**的条目。

        失败一律抛 VerificationError（含其子类 StaleCursorError）。
        """
        # 1) 凭据：签名、key_id、版本字段一致性
        cred = Credential.from_json(resp["credential"])
        cred.verify_with(self.verifier)
        if resp["version"] != cred.version:
            raise VerificationError("答页版本与凭据版本不一致")
        if not allow_old_version and self.last_seen_version is not None \
                and cred.version < self.last_seen_version:
            raise VerificationError(
                f"降级攻击：已见过版本 {self.last_seen_version}，"
                f"却收到更旧的版本 {cred.version}"
            )

        # 2) 游标版本绑定：跨版游标直接拒绝
        cursor = resp.get("cursor")
        after: Optional[tuple] = None
        if cursor is not None:
            if int(cursor["version"]) != cred.version:
                raise StaleCursorError(
                    f"游标属于版本 {cursor['version']}，"
                    f"当前凭据是版本 {cred.version}，须从前缀首页重取"
                )
            if cursor.get("after"):
                after = tuple(cursor["after"])

        prefix = resp["prefix"]
        limit = int(resp["limit"])
        lo, hi = prefix_bounds(prefix)

        # 3) 重算 Merkle 根并做结构检查（根哈希、键序、剪枝边界）
        leaves = verify_proof_structure(resp["proof"], cred.root_hash_hex)
        leaf_by_key = {e.key: e for e in leaves}
        if len(leaf_by_key) != len(leaves):
            raise VerificationError("证明中出现重复键")

        # 4) 解析本页条目并做页面级检查
        page = [Entry.from_json(d) for d in resp["entries"]]
        if len(page) > limit:
            raise VerificationError("页面条目数超过 limit")
        prev: Optional[tuple] = after
        for e in page:
            if not key_in_range(e.key, lo, hi):
                raise VerificationError(f"条目 {e.key} 落在前缀区间之外")
            if prev is not None and not (prev < e.key):
                raise VerificationError("页面内键未严格递增或越过游标")
            if e.key not in leaf_by_key:
                raise VerificationError(
                    f"页面条目 {e.key} 不在 Merkle 证明中"
                )
            # 键命中还不够：读音、字义也必须与叶子提交值逐字节一致
            if leaf_by_key[e.key] != e:
                raise VerificationError(
                    f"条目 {e.key} 的读音或字义与认证叶子不符（内容被篡改）"
                )
            prev = e.key

        # 5) 确定资格窗口 [wl, wh) 与前驱/后继语义。
        #    窗口边界直接由证明中真实展开的叶子推导，不采信服务器字段。
        leaf_keys = sorted(leaf_by_key)
        if page:
            first, last = page[0].key, page[-1].key
            below = [k for k in leaf_keys if k < first]
            above = [k for k in leaf_keys if k > last]
            page_keys = {e.key for e in page}
            extra = set(leaf_keys) - page_keys - set(below) - set(above)
            if extra:
                raise VerificationError(f"证明中含有未交付条目: {sorted(extra)}")
            if len(below) > 1 or len(above) > 1:
                raise VerificationError("证明展开了与本页无关的多余条目")

            # 前驱：无游标时不得是区间内条目（否则就是漏给）
            if below:
                pred = below[0]
                if after is None:
                    if key_in_range(pred, lo, hi):
                        raise VerificationError(
                            f"区间内前驱 {pred} 未交付，首页存在漏项"
                        )
                elif not (pred <= after):
                    raise VerificationError("前驱条目晚于游标，存在漏项")
                wl = pred
            else:
                wl = lo

            # 后继：决定 has_more；后继在区间内则必须还有下一页
            if above:
                succ = above[0]
                in_range_succ = key_in_range(succ, lo, hi)
                if in_range_succ != bool(resp["has_more"]):
                    raise VerificationError(
                        "has_more 与后继边界证明矛盾"
                    )
                # 后继叶子自身已展开；窗口上界就卡在后继键：后继之后的
                # 子树全部落在窗口外，可以整棵剪枝
                wh = succ
            else:
                if resp["has_more"]:
                    raise VerificationError(
                        "服务器自称还有下一页，却给不出区间内后继证明"
                    )
                wh = hi
        else:
            # 空页：窗口即整个前缀区间；展开的叶子必须全部在区间外
            if resp["has_more"]:
                raise VerificationError("空页不应声明 has_more")
            for k in leaf_keys:
                if key_in_range(k, lo, hi):
                    raise VerificationError(
                        f"前缀非空：条目 {k} 被证明展开却未交付"
                    )
            wl, wh = lo, hi

        # 6) 剪枝子树一律不得与资格窗口相交——这是“没有漏词”的硬保证。
        #    注意剪枝节点的边界被父节点哈希链提交，谎报边界会使根哈希失配。
        pruned: list[tuple[tuple, tuple]] = []
        _collect_pruned(resp["proof"], pruned)
        for p_first, p_last in pruned:
            below_window = wl[0] is not None and p_last < wl
            above_window = wh is not None and p_first >= wh
            covers_window = not (below_window or above_window)
            if covers_window:
                raise VerificationError(
                    f"剪枝子树 {p_first}..{p_last} 覆盖了资格窗口，"
                    "服务器可能隐藏了区间内条目"
                )

        # 7) next_cursor 与 has_more 自洽
        nc = resp.get("next_cursor")
        if resp["has_more"]:
            if nc is None or tuple(nc["after"]) != last:
                raise VerificationError("next_cursor 与本页末条不一致")
            if int(nc["version"]) != cred.version:
                raise StaleCursorError("next_cursor 绑定了别的版本")
        elif nc is not None:
            raise VerificationError("已到前缀末尾却仍给出 next_cursor")

        self.last_seen_version = cred.version
        return page

    # ------------------------------------------------ 取页 + 自动重取

    def fetch_page(self, transport: "Transport", prefix: str, limit: int,
                   cursor: Optional[dict[str, Any]] = None,
                   want_version: Optional[int] = None) -> dict[str, Any]:
        version = want_version or self.last_seen_version
        if version is None:
            raise ValueError("尚不知道任何版本，需先指定 want_version")
        resp = transport.request(version, prefix, limit, cursor)
        self.verify_response(resp)
        return resp

    def fetch_all(self, transport: "Transport", prefix: str, limit: int,
                  want_version: Optional[int] = None) -> list[Entry]:
        """翻完整个前缀。若中途遇到跨版旧游标，则按规则从首页重取——
        重取会丢弃按旧版累积的半程结果，整个前缀以新版本重新组装。"""
        version = want_version or self.last_seen_version
        while True:
            out: list[Entry] = []
            cursor = None
            try:
                while True:
                    resp = self.fetch_page(transport, prefix, limit,
                                           cursor, want_version=version)
                    out.extend(Entry.from_json(d) for d in resp["entries"])
                    if not resp["has_more"]:
                        return out
                    cursor = resp["next_cursor"]
            except StaleCursorError:
                # 修订后旧游标作废：该前缀已收的半程页全部不要，从首页重取
                version = transport.latest_version()


# ------------------------------------------------ 传输抽象

class Transport:
    """检索台通道接口。"""

    def request(self, version: int, prefix: str, limit: int,
                cursor: Optional[dict[str, Any]]) -> dict[str, Any]:
        raise NotImplementedError

    def latest_version(self) -> int:
        raise NotImplementedError


class NetworkTransport(Transport):
    """在线通道：直接调用本地 Server（生产中替换为 HTTP RPC）。"""

    def __init__(self, server):
        self.server = server

    def request(self, version, prefix, limit, cursor):
        return self.server.get_page(version, prefix, limit, cursor)

    def latest_version(self) -> int:
        return self.server.latest_version()


class OfflineTransport(Transport):
    """离线通道：读取事先落盘的交件目录，按序号逐张交出。
    用于排字工坊关掉服务器后核验真实交件。"""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self._files = sorted(self.directory.glob("page_*.json"))
        self._pos = 0

    def request(self, version, prefix, limit, cursor):
        if self._pos >= len(self._files):
            raise RuntimeError("离线交件已全部取完")
        resp = json.loads(self._files[self._pos].read_text(encoding="utf-8"))
        self._pos += 1
        return resp

    def latest_version(self) -> int:
        # 离线状态不主动探测版本；重取需要重新联网
        raise RuntimeError("离线通道无法获知最新版本")


# ------------------------------------------------ 排字工坊稿件

class Manuscript:
    """工坊本地稿件：只接收通过完整验证的页面，按义项独立存注。"""

    def __init__(self):
        # (word, sense) -> {"pron","gloss","version"}
        self.notes: dict[tuple, dict[str, Any]] = {}

    def inject_page(self, verified_entries: list[Entry], version: int) -> int:
        """原子并入：调用方必须已通过 verify_response。
        返回新写入/更新的义项数。"""
        # 先在临时结构里整理，全部成功后才提交，避免半页污染
        pending = {
            e.key: {"pron": e.pron, "gloss": e.gloss, "version": version}
            for e in verified_entries
        }
        self.notes.update(pending)
        return len(pending)

    def get(self, word: str, sense: int) -> Optional[dict[str, Any]]:
        return self.notes.get((word, sense))

    def words_used(self) -> list[str]:
        return sorted({k[0] for k in self.notes})

    def __len__(self) -> int:
        return len(self.notes)
