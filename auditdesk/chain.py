"""摘要链与分叉账本：重建、验页、最后共同历史、条目归属证明。"""
from __future__ import annotations

from dataclasses import dataclass

from . import crypto
from .models import dumps_canonical
from .store import CaseStore

GENESIS = "GENESIS"


class ChainError(ValueError):
    pass


@dataclass
class HeadView:
    """一个见证站在某一时刻实际见过的全部链头（按它持有的 VERIFIED 签收）。

    同一前页的两条后续会形成两支，链头全部保留，谁也不覆盖谁。
    """
    station_id: str
    tips: list[str]                  # 全部链头 page_digest
    tip_nos: list[int]
    branches: dict[str, list[str]]   # tip_digest -> 创世到该链头的摘要序列
    gaps: list[int]                  # 已签收但中间缺页的页号


class Chain:
    def __init__(self, store: CaseStore):
        self.store = store

    # ---------- 单页结构核对 ----------
    def verify_page_integrity(self, page) -> None:
        """重算摘要并核对条目根；不涉及链接关系。"""
        root = crypto.sha256_hex(dumps_canonical(page.entry_digests))
        if root != page.entries_root:
            raise ChainError(f"第{page.page_no}页条目根不符（页被改动）")
        digest = crypto.sha256_hex(dumps_canonical(page.body_for_verify()))
        if digest != page.page_digest:
            raise ChainError(f"第{page.page_no}页摘要不符（页被改动）")
        for ed in page.entry_digests:
            known = [e for e in self.store.entries.values()
                     if e["digest"] == ed]
            if not known:
                raise ChainError(f"第{page.page_no}页引用未知条目 {ed[:12]}")

    def verify_prefix(self, tip_digest: str) -> list:
        """从链头沿 prev_digest 回放到创世，返回有序页列表；缺页/改动即失败。"""
        pages = []
        cursor = tip_digest
        seen = set()
        while cursor and cursor != GENESIS:
            if cursor in seen:
                raise ChainError("链接出现环")
            seen.add(cursor)
            page = self.store.get_page(cursor)
            if page is None:
                raise ChainError(f"缺少中间页 {cursor[:12]}")
            self.verify_page_integrity(page)
            pages.append(page)
            cursor = page.prev_digest
        if cursor != GENESIS:
            raise ChainError("未能回到创世点")
        pages.reverse()
        for i, page in enumerate(pages, start=1):
            if page.page_no != i:
                raise ChainError(f"页号不连续：期望 {i}，实际 {page.page_no}")
            expect_prev = GENESIS if i == 1 else pages[i - 2].page_digest
            if page.prev_digest != expect_prev:
                raise ChainError(f"第{i}页前页摘要不匹配")
        return pages

    def _prefix_seq(self, tip_digest: str) -> list[str]:
        seq, cursor, seen = [], tip_digest, set()
        while cursor and cursor != GENESIS and cursor not in seen:
            seen.add(cursor)
            page = self.store.get_page(cursor)
            if page is None:
                break
            seq.append(cursor)
            cursor = page.prev_digest
        seq.reverse()
        return seq

    # ---------- 见证站视角 ----------
    def station_views(self) -> dict[str, HeadView]:
        """根据各站 VERIFIED 签收重建它实际见过的每一支链头。

        签收只说明“见过此页且核对通过”，不声称全局唯一。
        后收到、日期晚或链更长，都不会抹掉另一支链头。
        """
        receipts = [r for r in self.store.all_receipts() if r.kind == "VERIFIED"]
        by_station: dict[str, list] = {}
        for r in receipts:
            by_station.setdefault(r.station_id, []).append(r)

        views: dict[str, HeadView] = {}
        for station, rs in by_station.items():
            tips, gaps = [], []
            for r in rs:
                if r.page_digest is None:
                    continue
                page = self.store.get_page(r.page_digest)
                if page is None:
                    gaps.append(r.page_no or -1)
                    continue
                is_tip = True
                for r2 in rs:
                    if r2.page_digest and r2.page_digest != r.page_digest:
                        p2 = self.store.get_page(r2.page_digest)
                        if p2 is not None and p2.prev_digest == r.page_digest:
                            is_tip = False
                            break
                if is_tip:
                    tips.append(r.page_digest)
            tips = sorted(set(tips))
            branches = {t: self._prefix_seq(t) for t in tips}
            tip_nos = []
            for t in tips:
                p = self.store.get_page(t)
                if p:
                    tip_nos.append(p.page_no)
            views[station] = HeadView(
                station_id=station,
                tips=tips,
                tip_nos=tip_nos,
                branches=branches,
                gaps=sorted(set(gaps)),
            )
        return views

    def last_common_history(self, tip_a: str, tip_b: str) -> dict:
        """重启后查出两支（可分属两站）最后一段共同历史（最长公共前缀）。"""
        sa, sb = self._prefix_seq(tip_a), self._prefix_seq(tip_b)
        common = []
        for da, db in zip(sa, sb):
            if da != db:
                break
            common.append(da)
        last = common[-1] if common else None
        last_page = self.store.get_page(last) if last else None
        pa = self.store.get_page(tip_a)
        pb = self.store.get_page(tip_b)
        return {
            "common_digests": common,
            "last_common_digest": last,
            "last_common_no": last_page.page_no if last_page else None,
            "tip_a_no": pa.page_no if pa else None,
            "tip_b_no": pb.page_no if pb else None,
        }

    # ---------- 条目归属证明 ----------
    def inclusion_proof(self, entry_id: str, tip_digest: str) -> dict:
        """验证提交到柜台的具体条目确实包含在某链头以内的某页。"""
        digest = self.store.entry_digest(entry_id)
        if digest is None:
            raise ChainError(f"未知条目 {entry_id}")
        pages = self.verify_prefix(tip_digest)
        for page in pages:
            if digest in page.entry_digests:
                return {
                    "entry_id": entry_id,
                    "entry_digest": digest,
                    "page_no": page.page_no,
                    "page_digest": page.page_digest,
                    "tip_digest": tip_digest,
                    "entries_root": page.entries_root,
                }
        raise ChainError(f"条目 {entry_id} 不在链头 {tip_digest[:12]} 之内")

    def find_receipt_for_page(self, station_id: str, page_digest: str,
                              kind: str | None = None):
        for r in self.store.all_receipts():
            if r.station_id != station_id:
                continue
            if r.page_digest != page_digest:
                continue
            if kind and r.kind != kind:
                continue
            return r
        return None
