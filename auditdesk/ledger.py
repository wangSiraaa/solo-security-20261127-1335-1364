"""链构建辅助：造条目、封页、按前页派生分叉支。"""
from __future__ import annotations

from .chain import GENESIS
from .models import Entry, Page
from .store import CaseStore


class LedgerBuilder:
    def __init__(self, store: CaseStore):
        self.store = store
        self._seq = 0

    def add_entry(self, shift: str, author: str, payload: str,
                  ts: str, entry_id: str | None = None) -> Entry:
        self._seq += 1
        eid = entry_id or f"E{self._seq:04d}"
        e = Entry(entry_id=eid, shift=shift, author=author,
                  payload=payload, ts=ts)
        self.store.add_entry(e)
        return e

    def seal_page(self, page_no: int, shift: str, entries: list[Entry],
                  prev_digest: str, ts: str) -> Page:
        page = Page(
            page_no=page_no,
            shift=shift,
            prev_digest=prev_digest,
            entry_digests=[e.digest() for e in entries],
        ).seal(ts)
        self.store.add_page(page)
        return page

    def extend(self, page_no: int, shift: str, entries: list[Entry],
               parent: Page, ts: str) -> Page:
        return self.seal_page(page_no, shift, entries, parent.page_digest, ts)


def render_chain_text(chain, tip_digest: str) -> str:
    pages = chain.verify_prefix(tip_digest)
    lines = []
    for p in pages:
        lines.append(f"  第{p.page_no}页 {p.shift} 摘要={p.page_digest[:16]}… "
                     f"条目数={len(p.entry_digests)}")
    return "\n".join(lines)
