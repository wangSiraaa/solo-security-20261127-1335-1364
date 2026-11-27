import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createSystem, EXPORT_MATRIX, titleSimilarity } from '../src/system.js';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const outDir = join(root, 'exports');
const sheetDir = join(outDir, 'screening-sheets');
const snapshotDir = join(outDir, 'snapshots');

await mkdir(sheetDir, { recursive: true });
await mkdir(snapshotDir, { recursive: true });

const system = createSystem();
const writeJson = async (relative, value) => {
  const path = join(outDir, relative);
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, `${JSON.stringify(value, null, 2)}\n`, 'utf8');
};

function latestRound(wo) {
  return wo.rounds.at(-1);
}
function decisionMap(wo) {
  const round = latestRound(wo);
  const decisions = round.evaluation?.screeningDecisions || round.screeningDecisions || [];
  return Object.fromEntries(decisions.map((d) => [d.screeningId, d]));
}

// 0. 近似名启发式只用于排序/提示，不构成同版见证。
const lookalike = titleSimilarity('《午夜信笺》', '《午夜信札》');
assert.ok(lookalike >= 0.7, `测试数据应足够像，实际相似度 ${lookalike}`);

// 1. 初始：一次性会话、两馆分别确认、散会、推送最小包、排映台复核。
const initial = system.prepareInitialCollision();
let desk = system.roleExport('desk');
await writeJson('snapshots/01-initial-desk.json', desk);

assert.equal(desk.confirmedReleases.length, 2, '只能有 HB、SP 两个同版发布包');
assert.ok(!desk.confirmedReleases.some((r) => r.workKey === 'LT'), '近似名 LT 不得进入排映台');
assert.equal(desk.workOrders.length, 2);

const hbWo = desk.workOrders.find((w) => w.id === 'WO-HB-1212');
assert.equal(hbWo.status, 'PAUSED');
assert.deepEqual(hbWo.targetScreeningIds, ['SCR-B-HB']);
let hbDecisions = decisionMap(hbWo);
assert.equal(hbDecisions['SCR-B-HB'].decision, 'PAUSE');
assert.equal(hbDecisions['SCR-A-HB'].decision, 'PROCEED_FIRST_GRANT');
assert.equal(hbDecisions['SCR-B-HB'].sold, 63);

const spWoInitial = desk.workOrders.find((w) => w.id === 'WO-SP-1219');
assert.equal(spWoInitial.status, 'PAUSED');
assert.deepEqual([...spWoInitial.targetScreeningIds].sort(), ['SCR-A-SP-EVE', 'SCR-A-SP-PM']);

let archiveA = system.roleExport('archive-a');
let archiveB = system.roleExport('archive-b');
assert.equal(archiveA.electronicScreeningSheets.find((s) => s.id === 'SCR-B-HB'), undefined);
assert.equal(archiveB.electronicScreeningSheets.find((s) => s.id === 'SCR-A-HB'), undefined);

// 2. 改名/版次订正为不同剪辑版；方案 B 必须重开 bilateral session 并复办原 WO。
const correction = system.runCorrectionAndReplayHb();
desk = system.roleExport('desk');
await writeJson('snapshots/02-after-cut-correction-desk.json', desk);

assert.equal(correction.workOrder.status, 'LIFTED_DIFFERENT_CUT');
assert.deepEqual(correction.workOrder.targetScreeningIds, []);
const hbAfterCut = desk.workOrders.find((w) => w.id === 'WO-HB-1212');
assert.equal(hbAfterCut.status, 'LIFTED_DIFFERENT_CUT');
assert.equal(latestRound(hbAfterCut).reasonCode, 'DIFFERENT_CUT');
const bHb = system.roleExport('archive-b').electronicScreeningSheets.find((s) => s.id === 'SCR-B-HB');
assert.equal(bHb.holdStatus, 'PLAYABLE_AFTER_RECHECK');
assert.equal(bHb.deskWorkOrderId, 'WO-HB-1212');

const trustAfterCorrection = system.roleExport('trust');
assert.equal(trustAfterCorrection.activeOneTimeSessions.length, 0, '散会后不得保留活动会话');
assert.equal(JSON.stringify(trustAfterCorrection.activeOneTimeSessions).includes('oneTimeHandle'), false);

const alone = system.attemptCaseNumberOnlyRecheck('WO-HB-1212');
assert.equal(alone.allowed, false);
assert.equal(alone.code, 'NEEDS_BOTH_PARTIES');
assert.equal(alone.canReadStoredWorkOrder, true);

// 3. 保持 SP 同版，只把甲馆独占期缩短到 18:00（19:30 晚场前），复办原 WO。
const amendment = system.shortenSpExclusivityToBeforeEvening();
desk = system.roleExport('desk');
await writeJson('snapshots/03-after-exclusivity-amendment-desk.json', desk);

const spWo = desk.workOrders.find((w) => w.id === 'WO-SP-1219');
assert.equal(spWo.status, 'PAUSED');
assert.deepEqual(spWo.targetScreeningIds, ['SCR-A-SP-PM']);
const spDecisions = decisionMap(spWo);
assert.equal(spDecisions['SCR-A-SP-PM'].decision, 'PAUSE');
assert.equal(spDecisions['SCR-A-SP-PM'].inMutualExclusiveOverlap, true);
assert.equal(spDecisions['SCR-A-SP-EVE'].decision, 'PLAYABLE');
assert.equal(spDecisions['SCR-A-SP-EVE'].inMutualExclusiveOverlap, false);

archiveA = system.roleExport('archive-a');
const pm = archiveA.electronicScreeningSheets.find((s) => s.id === 'SCR-A-SP-PM');
const eve = archiveA.electronicScreeningSheets.find((s) => s.id === 'SCR-A-SP-EVE');
assert.equal(pm.holdStatus, 'PAUSED_FOR_RIGHTS_COLLISION');
assert.equal(eve.holdStatus, 'PLAYABLE_AFTER_RECHECK');

// 4. 最终四岗导出。
const finalExports = {
  'archive-a.json': system.roleExport('archive-a'),
  'archive-b.json': system.roleExport('archive-b'),
  'trust.json': system.roleExport('trust'),
  'desk.json': desk,
  'governance.json': system.roleExport('governance'),
};
for (const [name, data] of Object.entries(finalExports)) await writeJson(name, data);

// 5. 边界断言：排映台不得见全合同/非重合目录；各馆不得导出对馆原册。
const deskText = JSON.stringify(desk);
assert.ok(!deskText.includes('feePrivate'), '排映台导出不得含私有费用字段');
assert.ok(!deskText.includes('internalNote'), '排映台导出不得含内部备注字段');
assert.ok(!deskText.includes('午夜信笺'), '排映台不得见 A 馆非重合 LT 目录标题');
assert.ok(!deskText.includes('午夜信札'), '排映台不得见 B 馆非重合 LT 目录标题');
assert.ok(!deskText.includes('RCPT-A-LT'), '排映台不得见非重合回执号');

const aText = JSON.stringify(finalExports['archive-a.json']);
assert.ok(!aText.includes('B-CAT-'), '甲馆导出不得含乙馆目录行号');
assert.ok(!aText.includes('LIC-B-'), '甲馆导出不得含乙馆合同');
const bText = JSON.stringify(finalExports['archive-b.json']);
assert.ok(!bText.includes('A-CAT-'), '乙馆导出不得含甲馆目录行号');
assert.ok(!bText.includes('LIC-A-'), '乙馆导出不得含甲馆合同');
const trustText = JSON.stringify(finalExports['trust.json']);
assert.ok(!trustText.includes('feePrivate'), '回执岗不应掌握合同价格');
assert.ok(!trustText.includes('SCR-A-SP-PM'), '回执岗不应掌握放映单');

// 6. 生成几张本地电子放映单 CSV。
function csvCell(value) {
  const s = value == null ? '' : String(value);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}
function sheetCsv(roleExport) {
  const rows = roleExport.electronicScreeningSheets.map((r) => ({ ...r, screeningId: r.id }));
  const header = ['sheetId', 'screeningId', 'workKey', 'hall', 'start', 'end', 'sold', 'capacity', 'holdStatus', 'deskWorkOrderId'];
  const lines = [header.join(',')];
  for (const r of rows) lines.push(header.map((k) => csvCell(r[k])).join(','));
  return `${lines.join('\n')}\n`;
}
await writeFile(join(sheetDir, 'archive-a-electronic-sheets.csv'), sheetCsv(finalExports['archive-a.json']), 'utf8');
await writeFile(join(sheetDir, 'archive-b-electronic-sheets.csv'), sheetCsv(finalExports['archive-b.json']), 'utf8');

const matrixMd = [
  '# 各岗位最终可单独导出的资料',
  '',
  ...EXPORT_MATRIX.flatMap((row) => [
    `## ${row.postName} (` + '`' + row.post + '`)',
    '',
    '**可以单独导出：**',
    ...row.aloneCanExport.map((x) => `- ${x}`),
    '',
    '**不能单独还原/导出：**',
    ...row.aloneCannotReconstruct.map((x) => `- ${x}`),
    '',
  ]),
].join('\n');
await writeFile(join(outDir, 'export-matrix.md'), `${matrixMd}\n`, 'utf8');

const summary = {
  ok: true,
  architecture: 'B_ONE_TIME_SESSION',
  lookalikeTitleSimilarity: Number(lookalike.toFixed(2)),
  initialPause: { workOrder: 'WO-HB-1212', screeningId: 'SCR-B-HB', start: '2026-12-12T19:00:00+08:00' },
  decoy: { pair: 'P-LT', releasedToDesk: false },
  cutCorrection: { correction: 'CR-2026-011', workOrder: 'WO-HB-1212', status: 'LIFTED_DIFFERENT_CUT' },
  exclusivityAmendment: {
    amendment: 'AMEND-LIC-A-SP-01',
    exclusiveEnd: '2026-12-19T18:00:00+08:00',
    paused: ['SCR-A-SP-PM 14:30-16:20'],
    playable: ['SCR-A-SP-EVE 19:30-21:20'],
  },
  exports: Object.keys(finalExports).map((f) => `exports/${f}`),
};
await writeJson('selftest-summary.json', summary);

console.log(JSON.stringify(summary, null, 2));
console.log('\n自检通过：初始撞期、近似名隔离、改名订正解除、独占期边界、岗位导出边界均符合预期。');
