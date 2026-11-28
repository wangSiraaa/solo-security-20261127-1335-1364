import { test } from 'node:test';
import assert from 'node:assert/strict';
import { WorkLedger, cellsOf } from '../src/index.mjs';

function q(id, region, anchor, unitPrice = 10) {
  return { quoteId: id, photoId: 'P', region, unitPrice, anchor, ownerSeat: '报价席' };
}

test('网格单元与面积计算', () => {
  assert.equal(cellsOf({ x: 0, y: 0, w: 2, h: 3 }).length, 6);
  assert.deepEqual(cellsOf({ x: 1, y: 1, w: 1, h: 1 }), ['1,1']);
});

test('重叠局部不重复结价：共享单元只归一个工项', () => {
  const wl = new WorkLedger();
  wl.registerQuote(q('Q1', { x: 0, y: 0, w: 4, h: 4 }, 'A1')); // 16 单元
  wl.registerQuote(q('Q2', { x: 3, y: 3, w: 4, h: 4 }, 'A2')); // 与 Q1 共享 (3,3)
  wl.registerQuote(q('Q3', { x: 2, y: 2, w: 4, h: 4 }, 'A1')); // 同时压住 Q1、Q2
  const wi = wl.assignToSeat('接班席', { allowedRegionIds: [], allowedAnchors: [] });
  // 并集 16 + 15 + (36-重叠) ；关键是无重复单元
  assert.equal(wi.cells.length, new Set(wi.cells).size);
  const totalCells = 8 * 8; // 三张图覆盖范围 0..7
  assert.ok(wi.cells.length <= totalCells);
  // 估算金额 = 单元数 * 单价，重叠单元只计一次
  const expected = wi.cells.length * 10;
  assert.equal(wi.estimate, expected);
});

test('对不准的一笔整笔挂起，不并入相邻工项', () => {
  const wl = new WorkLedger();
  wl.registerQuote(q('Q1', { x: 0, y: 0, w: 2, h: 2 }, 'A1'));
  wl.registerQuote(q('QOUT', { x: 50, y: 50, w: 2, h: 2 }, 'ZZ'));
  const wi = wl.assignToSeat('接班席', { allowedRegionIds: [], allowedAnchors: [] });
  assert.deepEqual(wi.suspended, ['QOUT']);
  assert.ok(!wi.quoteIds.includes('QOUT'));
  assert.equal(wl.quotes.get('QOUT').status, 'suspended');
  assert.equal(wl.quotes.get('Q1').status, 'assigned');
});

test('结案后第二接班席不会对已结单元重复结价', () => {
  const wl = new WorkLedger();
  wl.registerQuote(q('Q1', { x: 0, y: 0, w: 3, h: 3 }, 'A1'));
  const wi1 = wl.assignToSeat('席一', { allowedRegionIds: [], allowedAnchors: [] });
  wl.closeWorkItem(wi1.workItemId);
  assert.equal(wl.quotes.get('Q1').status, 'closed');

  // 同区域再来一张图：全部单元已结案 → covered，不产生新费用
  wl.registerQuote(q('Q2', { x: 0, y: 0, w: 3, h: 3 }, 'A1'));
  const wi2 = wl.assignToSeat('席二', { allowedRegionIds: [], allowedAnchors: [] });
  assert.equal(wi2.estimate, 0);
  assert.equal(wl.quotes.get('Q2').status, 'covered');
});

test('接班席视图：未获准局部与定位点不可见，已结案记录可见', () => {
  const wl = new WorkLedger();
  wl.registerQuote(q('Q1', { x: 0, y: 0, w: 2, h: 2 }, 'A1'));
  wl.registerQuote(q('Q2', { x: 5, y: 5, w: 2, h: 2 }, 'A2'));
  const wi = wl.assignToSeat('席一', {
    allowedRegionIds: ['Q1', 'Q2'],
    allowedAnchors: ['A1', 'A2'],
  });
  wl.closeWorkItem(wi.workItemId);

  const view = WorkLedger.seatView(wl, '席二', {
    allowedRegionIds: ['Q1'],
    allowedAnchors: ['A1'],
  });
  assert.deepEqual(view.regions.map((r) => r.quoteId), ['Q1']);
  assert.equal(view.closedRecords.length, 1);
  assert.equal(view.closedRecords[0].workItemId, wi.workItemId);
});

test('部分重叠：先结的工项吃掉共享单元，后笔只结剩余部分', () => {
  const wl = new WorkLedger();
  wl.registerQuote(q('Q1', { x: 0, y: 0, w: 4, h: 4 }, 'A1'));
  const wi1 = wl.assignToSeat('席一', { allowedRegionIds: [], allowedAnchors: [] });
  wl.closeWorkItem(wi1.workItemId);
  wl.registerQuote(q('Q2', { x: 2, y: 2, w: 4, h: 4 }, 'A2')); // 4 单元与 Q1 重叠
  const wi2 = wl.assignToSeat('席二', { allowedRegionIds: [], allowedAnchors: [] });
  assert.equal(wi2.cells.length, 12); // 16 - 4 已结
  assert.equal(wi2.estimate, 120);
  wl.closeWorkItem(wi2.workItemId);
  // Q2 的 16 个单元均被认领（4 个归前案、12 个归本案）→ 全案结清
  assert.equal(wl.quotes.get('Q2').status, 'closed');

  // 真正的 partial：工项认领额度只覆盖报价的一半单元，其余留给后续工项
  const wl2 = new WorkLedger();
  wl2.registerQuote(q('R2', { x: 0, y: 0, w: 4, h: 4 }, 'A2'));
  const wiA = wl2.assignToSeat('席一', { allowedRegionIds: ['R2'], allowedAnchors: ['A2'] }, 8);
  assert.equal(wiA.cells.length, 8);
  wl2.closeWorkItem(wiA.workItemId);
  assert.equal(wl2.quotes.get('R2').status, 'partial');
});
