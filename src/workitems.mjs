// 局部病害图 / 工项结价。
// 报价标在照片的局部图上：一张新局部图可能同时重叠两张旧图。
// 网格剖分保证重叠部分不重复结价；接班席只能取获准局部、自己的定位点和已结案记录；
// 对不准的一笔单独挂起，不并入任何相邻工项。
import { randomId } from './util.mjs';

// 局部矩形，坐标对齐到整数网格单元 [x, y, w, h]
export function cellsOf(region) {
  const cells = [];
  for (let x = region.x; x < region.x + region.w; x++) {
    for (let y = region.y; y < region.y + region.h; y++) cells.push(`${x},${y}`);
  }
  return cells;
}

function rectsOverlap(a, b) {
  return !(a.x + a.w <= b.x || b.x + b.w <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y);
}

// 报价登记：一张新局部图可能重叠多张旧图。
// 拆成网格单元后，每个单元记录完整报价 id，结算时按覆盖面积分摊，绝不重复。
export class WorkLedger {
  constructor() {
    this.quotes = new Map(); // quoteId -> 报价（含 region、单价、来源封件）
    this.settledCells = new Map(); // cell -> { quoteId, workItemId }
    this.workItems = new Map(); // workItemId -> 工项
  }

  registerQuote(quote) {
    const id = quote.quoteId ?? randomId('quote');
    const cells = cellsOf(quote.region);
    const stored = {
      quoteId: id,
      photoId: quote.photoId,
      region: { ...quote.region },
      unitPrice: quote.unitPrice, // 每单元修复价
      anchor: quote.anchor ?? null, // 定位点（接班席据此对准）
      ownerSeat: quote.ownerSeat ?? null,
      parcelId: quote.parcelId ?? null,
      cells,
      status: 'open',
      note: quote.note ?? '',
      isBaseline: this.quotes.size === 0, // 首张报价是后续对准的基准，永不挂起
    };
    this.quotes.set(id, stored);
    return stored;
  }

  // 把一张新局部图对准已有报价：要求锚点命中或矩形相交，否则返回 null（整笔挂起）。
  alignToExisting(newQuote) {
    const hits = [...this.quotes.values()]
      .filter((q) => q.quoteId !== newQuote.quoteId)
      .filter((q) => {
        if (newQuote.anchor && q.anchor && newQuote.anchor === q.anchor) return true;
        return rectsOverlap(q.region, newQuote.region);
      });
    return hits.length ? hits : null;
  }

  // 接班席重配本地工项。
  // seatView 限定其可见局部与定位点；已结案单元跳过，避免相邻两笔重复结价；
  // claimLimit 限定本工项最多认领的单元数（超出部分留给后续工项 → partial）；
  // 没有任何可对准单元的报价 -> suspended，单独挂起。
  assignToSeat(seatId, seatView, claimLimit = Infinity) {
    const allowed = new Set(seatView.allowedRegionIds ?? []);
    const allowedAnchors = new Set(seatView.allowedAnchors ?? []);
    const workItem = {
      workItemId: randomId('work'),
      seatId,
      quoteIds: [],
      cells: [],
      estimate: 0,
      suspended: [],
    };
    this.workItems.set(workItem.workItemId, workItem);

    for (const q of this.quotes.values()) {
      if (q.status === 'closed' || q.status === 'suspended') continue;
      const regionAllowed = allowed.has(q.quoteId) || allowed.size === 0;
      const anchorAllowed = !q.anchor || allowedAnchors.has(q.anchor) || allowedAnchors.size === 0;
      if (!regionAllowed || !anchorAllowed) continue;

      const claimable = q.cells.filter((c) => !this.settledCells.has(c));
      if (claimable.length === 0) {
        // 全部单元都已结案：不重复结价，直接标记已覆盖
        q.status = 'covered';
        continue;
      }
      const aligned = this.alignToExisting(q);
      if (aligned === null && this.quotes.size > 1 && !q.isBaseline) {
        // 还有别的局部可参照却对不准：整笔单独挂起，不拆开并入相邻工项。
        q.status = 'suspended';
        q.suspendReason = '无法与既有局部图对准';
        workItem.suspended.push(q.quoteId);
        continue;
      }
      const taken = claimable.slice(0, Math.max(0, claimLimit - workItem.cells.length));
      for (const c of taken) {
        this.settledCells.set(c, { quoteId: q.quoteId, workItemId: workItem.workItemId });
        workItem.cells.push(c);
      }
      if (taken.length === 0) {
        // 工项认领额度已用尽：本报价原封不动留给下一个工项
        continue;
      }
      workItem.quoteIds.push(q.quoteId);
      workItem.estimate += taken.length * q.unitPrice;
      q.status = 'assigned';
    }
    return workItem;
  }

  // 结案：工项完工后锁定其单元，后续重叠图只能在剩余单元上结价。
  closeWorkItem(workItemId) {
    const wi = this.workItems.get(workItemId);
    if (!wi) throw new Error('工项不存在');
    wi.closedAt = new Date().toISOString();
    wi.status = 'closed';
    for (const qid of wi.quoteIds) {
      const q = this.quotes.get(qid);
      // 报价覆盖的每个单元都已被某个工项认领 → closed；还有完全未认领单元 → partial
      const claimed = q.cells.filter((c) => this.settledCells.has(c)).length;
      q.status = claimed === q.cells.length ? 'closed' : 'partial';
    }
    return wi;
  }

  // 接班席视图：只取获准局部、自己的定位点和已结案记录。
  static seatView(workLedger, seatId, permissions) {
    const allowedRegionIds = permissions.allowedRegionIds ?? [];
    const allowedAnchors = permissions.allowedAnchors ?? [];
    const closedRecords = [...workLedger.workItems.values()]
      .filter((w) => w.status === 'closed')
      .map((w) => ({
        workItemId: w.workItemId,
        seatId: w.seatId,
        cells: w.cells,
        estimate: w.estimate,
        closedAt: w.closedAt,
      }));
    const regions = [...workLedger.quotes.values()]
      .filter((q) => {
        const regionOk = allowedRegionIds.includes(q.quoteId);
        const anchorOk = !q.anchor || allowedAnchors.includes(q.anchor);
        return regionOk && anchorOk;
      })
      .map((q) => ({
        quoteId: q.quoteId,
        photoId: q.photoId,
        region: q.region,
        anchor: q.anchor,
        status: q.status,
      }));
    return { seatId, allowedRegionIds, allowedAnchors, regions, closedRecords };
  }

  snapshot() {
    return {
      quotes: [...this.quotes.values()],
      settledCells: [...this.settledCells.entries()],
      workItems: [...this.workItems.values()],
    };
  }
}
