import assert from 'node:assert/strict';

const ARCHITECTURE = 'ONE_TIME_LOCAL_LEDGER';

const t = (iso) => Date.parse(iso);
const fmt = (d = new Date()) => d.toISOString();

function intervalOverlap(aStart, aEnd, bStart, bEnd) {
  return t(aStart) < t(bEnd) && t(aEnd) > t(bStart);
}

function intersection(aStart, aEnd, bStart, bEnd) {
  const start = new Date(Math.max(t(aStart), t(bStart))).toISOString();
  const end = new Date(Math.min(t(aEnd), t(bEnd))).toISOString();
  return t(start) < t(end) ? { start, end } : null;
}

function levenshtein(a, b) {
  const m = a.length;
  const n = b.length;
  if (m === 0) return n;
  if (n === 0) return m;
  const dp = Array.from({ length: m + 1 }, (_, i) => [i, ...Array(n).fill(0)]);
  for (let j = 0; j <= n; j += 1) dp[0][j] = j;
  for (let i = 1; i <= m; i += 1) {
    for (let j = 1; j <= n; j += 1) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      dp[i][j] = Math.min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + cost);
    }
  }
  return dp[m][n];
}

export function titleSimilarity(a, b) {
  const norm = (s) => String(s).replace(/[《》\s（）()：:·,，.!！?？'’"“”\-_]/g, '').toLowerCase();
  const x = norm(a);
  const y = norm(b);
  if (!x.length || !y.length) return 0;
  return Math.max(0, 1 - levenshtein(x, y) / Math.max(x.length, y.length));
}

function seedState() {
  return {
    architecture: ARCHITECTURE,
    seq: 0,
    activeSessionId: null,
    sessions: {},
    receipts: [
      {
        id: 'RCPT-A-HB',
        archive: 'a',
        alias: 'Echoes from the Fog',
        lock: 'LOCK-HB-4K',
        status: 'VALID',
        issuedAt: '2026-08-18T10:00:00+08:00',
        statement: '卖方回执：该别名在回执柜中对应一个版次封签；持回执不能直接读出封签。',
      },
      {
        id: 'RCPT-B-HB',
        archive: 'b',
        alias: '夜航雾港 / Fog Night Harbor',
        lock: 'LOCK-HB-4K',
        status: 'VALID',
        issuedAt: '2026-08-25T11:20:00+08:00',
        statement: '卖方回执：旧别名等价回执，等待本地相等性接口核验。',
      },
      {
        id: 'RCPT-A-LT',
        archive: 'a',
        alias: 'Midnight Letter Paper',
        lock: 'LOCK-LT-PAPER',
        status: 'VALID',
        issuedAt: '2026-08-19T09:00:00+08:00',
        statement: '卖方回执：《午夜信笺》别名。',
      },
      {
        id: 'RCPT-B-LT',
        archive: 'b',
        alias: 'Midnight Letter',
        lock: 'LOCK-LT-MAIL',
        status: 'VALID',
        issuedAt: '2026-08-26T15:00:00+08:00',
        statement: '卖方回执：《午夜信札》别名；书名近似不构成同版见证。',
      },
      {
        id: 'RCPT-A-SP',
        archive: 'a',
        alias: 'Spring Express',
        lock: 'LOCK-SP-35',
        status: 'VALID',
        issuedAt: '2026-07-10T10:00:00+08:00',
        statement: '卖方回执：《春分特快》。',
      },
      {
        id: 'RCPT-B-SP',
        archive: 'b',
        alias: 'Train at Spring Equinox',
        lock: 'LOCK-SP-35',
        status: 'VALID',
        issuedAt: '2026-07-12T10:00:00+08:00',
        statement: '卖方回执：《春分列车》。',
      },
    ],
    corrections: [],
    witnesses: [],
    outbox: [],
    candidatePairs: [
      {
        pairId: 'P-HB',
        workKey: 'HB',
        aCatalogId: 'A-CAT-01',
        bCatalogId: 'B-CAT-01',
        agreedTitle: '《雾港回声》4K 修复版（含乙馆旧别名《夜航雾港》）',
        workOrderId: 'WO-HB-1212',
      },
      {
        pairId: 'P-LT',
        workKey: 'LT',
        aCatalogId: 'A-CAT-02',
        bCatalogId: 'B-CAT-02',
        agreedTitle: null,
        workOrderId: null,
      },
      {
        pairId: 'P-SP',
        workKey: 'SP',
        aCatalogId: 'A-CAT-03',
        bCatalogId: 'B-CAT-03',
        agreedTitle: '《春分特快/春分列车》35mm 同版',
        workOrderId: 'WO-SP-1219',
      },
    ],
    archives: {
      a: {
        id: 'a',
        name: '东屿资料馆',
        catalog: [
          { id: 'A-CAT-01', workKey: 'HB', title: '《雾港回声》', aliases: ['Echoes from the Fog'], receiptId: 'RCPT-A-HB' },
          { id: 'A-CAT-02', workKey: 'LT', title: '《午夜信笺》', aliases: [], receiptId: 'RCPT-A-LT' },
          { id: 'A-CAT-03', workKey: 'SP', title: '《春分特快》', aliases: ['Spring Express'], receiptId: 'RCPT-A-SP' },
        ],
        licenses: [
          {
            id: 'LIC-A-HB', workKey: 'HB', archive: 'a',
            territoryId: 'TERR-LZ', territoryName: '澜州都会放映圈（虚构）',
            exhibitionStart: '2026-10-01T00:00:00+08:00', exhibitionEnd: '2026-12-31T23:59:59+08:00',
            exclusive: true, exclusiveStart: '2026-11-01T00:00:00+08:00', exclusiveEnd: '2026-12-20T23:59:59+08:00',
            grantedAt: '2026-09-02T10:00:00+08:00',
            feePrivate: '人民币 18 万元（采购底价，不对外）',
            internalNote: '甲馆首任独占；合同约定先授予者优先。',
            publicText: '澜州都会放映圈独占；独占期 2026-11-01 至 2026-12-20；先授予顺位。',
          },
          {
            id: 'LIC-A-LT', workKey: 'LT', archive: 'a',
            territoryId: 'TERR-LZ', territoryName: '澜州都会放映圈（虚构）',
            exhibitionStart: '2026-11-01T00:00:00+08:00', exhibitionEnd: '2026-12-31T23:59:59+08:00',
            exclusive: true, exclusiveStart: '2026-12-01T00:00:00+08:00', exclusiveEnd: '2026-12-31T23:59:59+08:00',
            grantedAt: '2026-09-05T10:00:00+08:00',
            feePrivate: '人民币 6 万元', internalNote: '仅是近似名作品，不得因书名发送排映台。',
            publicText: '《午夜信笺》澜州独占许可。',
          },
          {
            id: 'LIC-A-SP', workKey: 'SP', archive: 'a',
            territoryId: 'TERR-LZ', territoryName: '澜州都会放映圈（虚构）',
            exhibitionStart: '2026-11-15T00:00:00+08:00', exhibitionEnd: '2026-12-31T23:59:59+08:00',
            exclusive: true, exclusiveStart: '2026-12-01T00:00:00+08:00', exclusiveEnd: '2026-12-19T22:30:00+08:00',
            grantedAt: '2026-08-20T15:00:00+08:00',
            feePrivate: '人民币 11 万元', internalNote: '后授予；独占截止时间待一次合同订正。',
            publicText: '《春分特快》澜州独占；初始独占截止 2026-12-19 22:30。',
          },
        ],
        screenings: [
          {
            id: 'SCR-A-HB', workKey: 'HB', sheetId: 'ESH-A-20261212', hall: '东屿一号厅',
            start: '2026-12-12T19:30:00+08:00', end: '2026-12-12T21:35:00+08:00',
            sold: 118, capacity: 160, holdStatus: 'SCHEDULED', deskWorkOrderId: null,
          },
          {
            id: 'SCR-A-LT', workKey: 'LT', sheetId: 'ESH-A-20261212', hall: '东屿二号厅',
            start: '2026-12-12T20:00:00+08:00', end: '2026-12-12T21:40:00+08:00',
            sold: 40, capacity: 120, holdStatus: 'AWAIT_SAME_EDITION_WITNESS', deskWorkOrderId: null,
          },
          {
            id: 'SCR-A-SP-PM', workKey: 'SP', sheetId: 'ESH-A-20261219', hall: '东屿一号厅',
            start: '2026-12-19T14:30:00+08:00', end: '2026-12-19T16:20:00+08:00',
            sold: 41, capacity: 160, holdStatus: 'SCHEDULED', deskWorkOrderId: null,
          },
          {
            id: 'SCR-A-SP-EVE', workKey: 'SP', sheetId: 'ESH-A-20261219', hall: '东屿一号厅',
            start: '2026-12-19T19:30:00+08:00', end: '2026-12-19T21:20:00+08:00',
            sold: 77, capacity: 160, holdStatus: 'SCHEDULED', deskWorkOrderId: null,
          },
        ],
        confirmations: [],
      },
      b: {
        id: 'b',
        name: '西潮影像馆',
        catalog: [
          { id: 'B-CAT-01', workKey: 'HB', title: '《夜航雾港》', aliases: ['Fog Night Harbor'], receiptId: 'RCPT-B-HB' },
          { id: 'B-CAT-02', workKey: 'LT', title: '《午夜信札》', aliases: ['Midnight Letter'], receiptId: 'RCPT-B-LT' },
          { id: 'B-CAT-03', workKey: 'SP', title: '《春分列车》', aliases: ['Train at Spring Equinox'], receiptId: 'RCPT-B-SP' },
        ],
        licenses: [
          {
            id: 'LIC-B-HB', workKey: 'HB', archive: 'b',
            territoryId: 'TERR-LZ', territoryName: '澜州都会放映圈（虚构）',
            exhibitionStart: '2026-10-10T00:00:00+08:00', exhibitionEnd: '2026-12-20T23:59:59+08:00',
            exclusive: true, exclusiveStart: '2026-11-10T00:00:00+08:00', exclusiveEnd: '2026-12-15T23:59:59+08:00',
            grantedAt: '2026-09-20T14:00:00+08:00',
            feePrivate: '人民币 14 万元', internalNote: '后授予；卖方疑似重复出售独占档。',
            publicText: '澜州都会放映圈独占；独占期 2026-11-10 至 2026-12-15。',
          },
          {
            id: 'LIC-B-LT', workKey: 'LT', archive: 'b',
            territoryId: 'TERR-LZ', territoryName: '澜州都会放映圈（虚构）',
            exhibitionStart: '2026-11-05T00:00:00+08:00', exhibitionEnd: '2026-12-25T23:59:59+08:00',
            exclusive: true, exclusiveStart: '2026-12-01T00:00:00+08:00', exclusiveEnd: '2026-12-25T23:59:59+08:00',
            grantedAt: '2026-09-08T11:00:00+08:00',
            feePrivate: '人民币 5 万元', internalNote: '书名近似但卖方版次封签不同。',
            publicText: '《午夜信札》澜州独占许可。',
          },
          {
            id: 'LIC-B-SP', workKey: 'SP', archive: 'b',
            territoryId: 'TERR-LZ', territoryName: '澜州都会放映圈（虚构）',
            exhibitionStart: '2026-11-01T00:00:00+08:00', exhibitionEnd: '2026-12-31T23:59:59+08:00',
            exclusive: true, exclusiveStart: '2026-12-01T00:00:00+08:00', exclusiveEnd: '2026-12-19T22:00:00+08:00',
            grantedAt: '2026-08-01T09:30:00+08:00',
            feePrivate: '人民币 12 万元', internalNote: '先授予。',
            publicText: '《春分列车》澜州独占；独占期至 2026-12-19 22:00；先授予顺位。',
          },
        ],
        screenings: [
          {
            id: 'SCR-B-HB', workKey: 'HB', sheetId: 'ESH-B-20261212', hall: '西潮二厅',
            start: '2026-12-12T19:00:00+08:00', end: '2026-12-12T21:05:00+08:00',
            sold: 63, capacity: 90, holdStatus: 'SCHEDULED', deskWorkOrderId: null,
          },
          {
            id: 'SCR-B-LT', workKey: 'LT', sheetId: 'ESH-B-20261212', hall: '西潮一小厅',
            start: '2026-12-12T20:15:00+08:00', end: '2026-12-12T22:05:00+08:00',
            sold: 55, capacity: 80, holdStatus: 'AWAIT_SAME_EDITION_WITNESS', deskWorkOrderId: null,
          },
          {
            id: 'SCR-B-SP-EVE', workKey: 'SP', sheetId: 'ESH-B-20261219', hall: '西潮大厅',
            start: '2026-12-19T20:00:00+08:00', end: '2026-12-19T21:40:00+08:00',
            sold: 102, capacity: 200, holdStatus: 'SCHEDULED', deskWorkOrderId: null,
          },
        ],
        confirmations: [],
      },
    },
    desk: {
      releases: [],
      screeningSubmissions: [],
      workOrders: [],
      actions: [],
      protocol: {
        sameEditionRequired: true,
        territoryRule: 'territoryId 相同或授权地域区间相交；本样例使用相同地域。',
        collisionRule: '同版 + 同地域 + 两份许可均在该时段主张独占 + 放映场落入双重独占区间。',
        priorityRule: '双重独占区间内，后授予许可方的场次暂停；先授予方场次标记 FIRST_IN_TIME_PROCEED。',
        eveningRule: '场次不在两份独占期交集内时，联合排映台不作撞期暂停，按本场许可的公开片段排映。',
      },
    },
    events: [],
  };
}

export const EXPORT_MATRIX = [
  {
    post: 'archive-a',
    name: '东屿资料馆岗位',
    aloneCanExport: ['甲馆原目录', '甲馆原始许可合同（含底价/内部备注）', '甲馆电子放映单与售票数', '甲馆自己的比对确认记录', '排映台发给甲馆的工单状态'],
    aloneCannotReconstruct: ['乙馆原目录', '乙馆合同底价/内部备注', '未命中的近似名记录是否在乙馆', '散会后的一次性会话映射'],
  },
  {
    post: 'archive-b',
    name: '西潮影像馆岗位',
    aloneCanExport: ['乙馆原目录', '乙馆原始许可合同（含底价/内部备注）', '乙馆电子放映单与售票数', '乙馆自己的比对确认记录', '排映台发给乙馆的工单状态'],
    aloneCannotReconstruct: ['甲馆原目录', '甲馆合同底价/内部备注', '未命中的近似名记录是否在甲馆', '散会后的一次性会话映射'],
  },
  {
    post: 'trust',
    name: '本地卖方回执/相等性接口岗',
    aloneCanExport: ['回执编号、别名与内部版次封签', '改名/版次订正台账', '已签发的同版见证号及其状态', '尚在进行中的一次性会话；散会后为空'],
    aloneCannotReconstruct: ['两馆完整采购目录', '许可合同条款和价格', '电子放映单/售票场次', '已删除的本轮句柄与两馆目录行映射'],
  },
  {
    post: 'desk',
    name: '联合排映台岗位',
    aloneCanExport: ['双方确认同版后的发布包', '两馆准许公开的合同片段', '仅同版作品的最小放映字段', '撞期核查、暂停/解除/复办工单'],
    aloneCannotReconstruct: ['未确认重合的目录和近似名候选', '完整合同、底价、内部备注', '散会后的一次性句柄', '仅凭案号重新向两馆原册发起同版核验'],
  },
];

export function createSystem() {
  let state = seedState();

  function nextId(prefix) {
    state.seq += 1;
    return `${prefix}-${String(state.seq).padStart(4, '0')}`;
  }

  function log(scope, action, detail = {}) {
    state.events.push({ at: fmt(), scope, action, detail });
  }

  function clone(value) {
    return structuredClone(value);
  }

  function publicReceipt(receipt) {
    if (!receipt) return null;
    const { lock, ...safe } = receipt;
    return safe;
  }

  function catalogItem(side, id) {
    return state.archives[side].catalog.find((x) => x.id === id);
  }

  function currentReceiptForItem(item) {
    return state.receipts.find((r) => r.id === item.receiptId);
  }

  function pairById(session, pairId) {
    const pair = session.pairs.find((p) => p.pairId === pairId);
    assert(pair, `未知比对项 ${pairId}`);
    return pair;
  }

  function getWo(woId) {
    const wo = state.desk.workOrders.find((w) => w.id === woId);
    assert(wo, `未知工单 ${woId}`);
    return wo;
  }

  function findRelease(workKey) {
    return state.desk.releases.find((r) => r.workKey === workKey && r.status === 'ACTIVE');
  }

  function publicFragment(lic) {
    return {
      licenseId: lic.id,
      archive: lic.archive,
      territoryId: lic.territoryId,
      territoryName: lic.territoryName,
      exclusive: lic.exclusive,
      exclusiveStart: lic.exclusiveStart,
      exclusiveEnd: lic.exclusiveEnd,
      exhibitionStart: lic.exhibitionStart,
      exhibitionEnd: lic.exhibitionEnd,
      grantedAt: lic.grantedAt,
      allowedPublicText: lic.publicText,
    };
  }

  function minimalScreening(scr, releaseId) {
    return {
      screeningId: scr.id,
      releaseId,
      archive: scr.id.startsWith('SCR-A') ? 'a' : 'b',
      sheetId: scr.sheetId,
      start: scr.start,
      end: scr.end,
      sold: scr.sold,
    };
  }

  function buildPacket(pair, witness) {
    const aItem = catalogItem('a', pair.aCatalogId);
    const bItem = catalogItem('b', pair.bCatalogId);
    const aLic = state.archives.a.licenses.find((l) => l.workKey === pair.workKey);
    const bLic = state.archives.b.licenses.find((l) => l.workKey === pair.workKey);
    return {
      releaseId: `REL-${pair.workKey}`,
      pairId: pair.pairId,
      workKey: pair.workKey,
      witnessId: witness.id,
      agreedTitle: pair.agreedTitle,
      status: 'READY_TO_DISPATCH',
      confirmedAt: fmt(),
      archiveCatalogRefs: { a: aItem.id, b: bItem.id },
      publicFragments: [publicFragment(aLic), publicFragment(bLic)],
    };
  }

  function openSession({ purpose = 'ONE_TIME_MATCH', pairIds = null } = {}) {
    if (state.activeSessionId) {
      throw Object.assign(new Error('已有进行中的一次性会话；请先散会'), { code: 'SESSION_OPEN' });
    }
    const selected = state.candidatePairs.filter((p) => !pairIds || pairIds.includes(p.pairId));
    const session = {
      id: nextId('SES'),
      purpose,
      status: 'OPEN',
      createdAt: fmt(),
      pairs: selected.map((p) => ({
        pairId: p.pairId,
        workKey: p.workKey,
        aCatalogId: p.aCatalogId,
        bCatalogId: p.bCatalogId,
        submissionA: null,
        submissionB: null,
        equality: null,
        witnessId: null,
        confirmA: false,
        confirmB: false,
        acknowledgeA: false,
        acknowledgeB: false,
        status: 'AWAITING_SUBMISSIONS',
      })),
    };
    state.sessions[session.id] = session;
    state.activeSessionId = session.id;
    log('trust', 'SESSION_OPENED', { purpose, pairIds: selected.map((p) => p.pairId) });
    return sessionView(session.id);
  }

  function submitSide(sessionId, pairId, side) {
    const session = state.sessions[sessionId];
    assert(session, '会话不存在或已散会删除');
    const pair = pairById(session, pairId);
    const catalogId = side === 'a' ? pair.aCatalogId : pair.bCatalogId;
    const item = catalogItem(side, catalogId);
    const receipt = currentReceiptForItem(item);
    const submission = {
      receiptId: receipt.id,
      oneTimeHandle: `H-${side.toUpperCase()}-${state.seq + 1}-${Math.random().toString(16).slice(2, 8)}`,
      submittedAt: fmt(),
    };
    if (side === 'a') pair.submissionA = submission;
    else pair.submissionB = submission;

    if (pair.submissionA && pair.submissionB) {
      const ra = state.receipts.find((r) => r.id === pair.submissionA.receiptId);
      const rb = state.receipts.find((r) => r.id === pair.submissionB.receiptId);
      const equal = ra.lock === rb.lock && ra.status === 'VALID' && rb.status === 'VALID';
      pair.equality = {
        equal,
        checkedAt: fmt(),
        // 受托接口只在相等时形成见证；不相等时没有“相等回执”。
        witnessIssuedOnlyWhenBothConfirmed: equal,
      };
      pair.status = equal ? 'EQUAL_PENDING_SEPARATE_CONFIRMATION' : 'NOT_EQUAL_PENDING_ACKNOWLEDGEMENT';
      log('trust', equal ? 'EQUALITY_FOUND' : 'NO_EQUAL_RECEIPT', { pairId });
    } else {
      pair.status = `WAITING_${side === 'a' ? 'B' : 'A'}_SUBMISSION`;
    }
    return sessionView(sessionId);
  }

  function confirmHit(sessionId, pairId, side) {
    const session = state.sessions[sessionId];
    assert(session, '会话不存在或已散会删除');
    const pair = pairById(session, pairId);
    assert(pair.equality?.equal, '只有相等项可由两馆确认命中');
    if (side === 'a') pair.confirmA = true;
    else pair.confirmB = true;

    state.archives[side].confirmations.push({
      pairId,
      workKey: pair.workKey,
      result: 'SAME_EDITION_HIT_CONFIRMED',
      confirmedAt: fmt(),
      sessionPurpose: session.purpose,
    });

    if (pair.confirmA && pair.confirmB && !pair.witnessId) {
      const witness = {
        id: nextId('WIT'),
        workKey: pair.workKey,
        status: 'ACTIVE',
        issuedAt: fmt(),
        statement: '本地受托相等性接口：两份当前有效回执在签发时对应同一版次封签。',
      };
      state.witnesses.push(witness);
      pair.witnessId = witness.id;
      const candidate = state.candidatePairs.find((p) => p.pairId === pairId);
      state.outbox.push(buildPacket(candidate, witness));
      pair.status = 'HIT_CONFIRMED_PACKET_READY';
      log('trust', 'WITNESS_ISSUED_AFTER_TWO_CONFIRMATIONS', { pairId, witnessId: witness.id });
    }
    return sessionView(sessionId);
  }

  function acknowledgeMismatch(sessionId, pairId, side) {
    const session = state.sessions[sessionId];
    assert(session, '会话不存在或已散会删除');
    const pair = pairById(session, pairId);
    assert(pair.equality && pair.equality.equal === false, '仅可确认不相等结果');
    if (side === 'a') pair.acknowledgeA = true;
    else pair.acknowledgeB = true;
    state.archives[side].confirmations.push({
      pairId,
      workKey: pair.workKey,
      result: 'NOT_SAME_EDITION_ACKNOWLEDGED',
      acknowledgedAt: fmt(),
      sessionPurpose: session.purpose,
    });
    if (pair.acknowledgeA && pair.acknowledgeB) pair.status = 'MISMATCH_ACKNOWLEDGED_NO_PACKET';
    return sessionView(sessionId);
  }

  function closeSession(sessionId) {
    const session = state.sessions[sessionId];
    assert(session, '会话不存在或已删除');
    const summary = {
      sessionId: session.id,
      purpose: session.purpose,
      closedAt: fmt(),
      deletedHandles: session.pairs.reduce((n, p) => n + Number(Boolean(p.submissionA?.oneTimeHandle)) + Number(Boolean(p.submissionB?.oneTimeHandle)), 0),
      issuedWitnessIds: session.pairs.map((p) => p.witnessId).filter(Boolean),
    };
    delete state.sessions[sessionId];
    if (state.activeSessionId === sessionId) state.activeSessionId = null;
    // 不把 sessionId 或 one-time handle 写入持久 events；只记录用途和删除数量。
    log('trust', 'SESSION_CLOSED_CROSS_ROUND_HANDLES_PURGED', {
      purpose: session.purpose,
      deletedHandles: summary.deletedHandles,
      issuedWitnessIds: summary.issuedWitnessIds,
    });
    return summary;
  }

  function runFullComparisonRound({ purpose = 'INITIAL_ONE_TIME_MATCH', pairIds = null } = {}) {
    const opened = openSession({ purpose, pairIds });
    const id = opened.session.id;
    for (const p of opened.session.pairs) {
      submitSide(id, p.pairId, 'a');
      submitSide(id, p.pairId, 'b');
      const current = state.sessions[id].pairs.find((x) => x.pairId === p.pairId);
      if (current.equality.equal) {
        confirmHit(id, p.pairId, 'a');
        confirmHit(id, p.pairId, 'b');
      } else {
        acknowledgeMismatch(id, p.pairId, 'a');
        acknowledgeMismatch(id, p.pairId, 'b');
      }
    }
    const view = sessionView(id);
    const closed = closeSession(id);
    return { sessionBeforeClose: view, closeSummary: closed };
  }

  function dispatchToDesk() {
    const ready = state.outbox.filter((p) => p.status === 'READY_TO_DISPATCH');
    for (const packet of ready) {
      packet.status = 'DISPATCHED';
      const existing = state.desk.releases.find((r) => r.releaseId === packet.releaseId);
      if (!existing) {
        state.desk.releases.push({ ...packet, status: 'ACTIVE', dispatchedAt: fmt() });
      }
      for (const side of ['a', 'b']) {
        const archive = state.archives[side];
        for (const scr of archive.screenings.filter((s) => s.workKey === packet.workKey)) {
          if (!state.desk.screeningSubmissions.some((x) => x.screeningId === scr.id)) {
            state.desk.screeningSubmissions.push(minimalScreening(scr, packet.releaseId));
          }
        }
      }
      state.desk.actions.push({ at: fmt(), action: 'RELEASE_RECEIVED_MINIMIZED', releaseId: packet.releaseId });
      log('desk', 'RELEASE_AND_PUBLIC_FRAGMENTS_RECEIVED', { releaseId: packet.releaseId });
    }
    return { dispatched: ready.map((p) => p.releaseId) };
  }

  function evaluateRelease(release) {
    const [fa, fb] = release.publicFragments;
    const sameTerritory = fa.territoryId === fb.territoryId;
    const bothExclusive = Boolean(fa.exclusive && fb.exclusive);
    const overlap = bothExclusive ? intersection(fa.exclusiveStart, fa.exclusiveEnd, fb.exclusiveStart, fb.exclusiveEnd) : null;
    const submissions = state.desk.screeningSubmissions.filter((s) => s.releaseId === release.releaseId);

    const screeningDecisions = submissions.map((scr) => {
      const mine = scr.archive === 'a' ? fa : fb;
      const other = scr.archive === 'a' ? fb : fa;
      const withinExhibition = intervalOverlap(scr.start, scr.end, mine.exhibitionStart, mine.exhibitionEnd);
      const inMutualExclusiveOverlap = overlap ? intervalOverlap(scr.start, scr.end, overlap.start, overlap.end) : false;
      const priority = t(mine.grantedAt) <= t(other.grantedAt) ? 'FIRST_GRANT' : 'LATER_GRANT';
      const conflict = Boolean(sameTerritory && bothExclusive && overlap && withinExhibition && inMutualExclusiveOverlap);
      let decision = 'PLAYABLE';
      let reasonCode = 'OUTSIDE_MUTUAL_EXCLUSIVE_OVERLAP';
      if (!sameTerritory) {
        decision = 'PLAYABLE';
        reasonCode = 'TERRITORY_DISJOINT';
      } else if (!bothExclusive) {
        reasonCode = 'NOT_BOTH_EXCLUSIVE';
      } else if (!overlap) {
        reasonCode = 'EXCLUSIVE_WINDOWS_DO_NOT_OVERLAP';
      } else if (!withinExhibition) {
        decision = 'NO_LICENSE_FOR_SCREENING_TIME';
        reasonCode = 'OUTSIDE_EXHIBITION_WINDOW';
      } else if (conflict && priority === 'LATER_GRANT') {
        decision = 'PAUSE';
        reasonCode = 'SOLD_SHOW_IN_DOUBLE_EXCLUSIVE_OVERLAP_LATER_GRANT';
      } else if (conflict && priority === 'FIRST_GRANT') {
        decision = 'PROCEED_FIRST_GRANT';
        reasonCode = 'IN_OVERLAP_BUT_FIRST_GRANT_PRIORITY';
      }
      return {
        screeningId: scr.screeningId,
        archive: scr.archive,
        sheetId: scr.sheetId,
        start: scr.start,
        end: scr.end,
        sold: scr.sold,
        priority,
        withinExhibition,
        inMutualExclusiveOverlap,
        decision,
        reasonCode,
      };
    });

    return {
      releaseId: release.releaseId,
      workKey: release.workKey,
      witnessId: release.witnessId,
      sameEdition: true,
      sameTerritory,
      bothExclusive,
      exclusiveOverlap: overlap,
      screeningDecisions,
      pauseScreeningIds: screeningDecisions.filter((d) => d.decision === 'PAUSE').map((d) => d.screeningId),
    };
  }

  function applyDecisionsToSheets(woId, decisions) {
    for (const d of decisions) {
      const side = d.screeningId.startsWith('SCR-A') ? 'a' : 'b';
      const scr = state.archives[side].screenings.find((x) => x.id === d.screeningId);
      if (!scr) continue;
      scr.deskWorkOrderId = woId;
      if (d.decision === 'PAUSE') scr.holdStatus = 'PAUSED_FOR_RIGHTS_COLLISION';
      else if (d.decision === 'PLAYABLE') scr.holdStatus = 'PLAYABLE_AFTER_RECHECK';
      else if (d.decision === 'PROCEED_FIRST_GRANT') scr.holdStatus = 'PROCEED_FIRST_GRANT';
      else scr.holdStatus = d.decision;
    }
  }

  function adjudicateAll({ trigger = 'DESK_REVIEW' } = {}) {
    const results = [];
    for (const release of state.desk.releases.filter((r) => r.status === 'ACTIVE')) {
      const evaluation = evaluateRelease(release);
      const woId = state.candidatePairs.find((p) => p.workKey === release.workKey)?.workOrderId || `WO-${release.workKey}`;
      let wo = state.desk.workOrders.find((w) => w.id === woId);
      const round = wo ? wo.rounds.length + 1 : 1;
      const pauseIds = evaluation.pauseScreeningIds;
      const status = pauseIds.length ? 'PAUSED' : 'NO_PAUSE_REQUIRED';
      const summary = {
        workOrderId: woId,
        round,
        at: fmt(),
        trigger,
        evaluation,
        status,
        targetScreeningIds: pauseIds,
      };
      if (!wo) {
        wo = {
          id: woId,
          workKey: release.workKey,
          releaseId: release.releaseId,
          status,
          createdAt: fmt(),
          updatedAt: fmt(),
          currentSummary: status,
          targetScreeningIds: pauseIds,
          rounds: [summary],
        };
        state.desk.workOrders.push(wo);
      } else {
        wo.rounds.push(summary);
        wo.status = status;
        wo.updatedAt = fmt();
        wo.currentSummary = status;
        wo.targetScreeningIds = pauseIds;
      }
      applyDecisionsToSheets(woId, evaluation.screeningDecisions);
      state.desk.actions.push({ at: fmt(), action: 'ADJUDICATION_ROUND', woId, round, status, pauseIds });
      results.push(summary);
    }
    return results;
  }

  function replayWorkOrder(woId, { trigger = 'ORIGINAL_WORK_ORDER_REPLAY', reasonCode = 'RECHECK' } = {}) {
    const wo = getWo(woId);
    const release = state.desk.releases.find((r) => r.releaseId === wo.releaseId);
    const round = wo.rounds.length + 1;
    const at = fmt();

    if (!release || release.status !== 'ACTIVE') {
      const reason = release?.status === 'WITHDRAWN_DIFFERENT_CUT'
        ? '同版见证已被改名/版次订正推翻；当前回执只支持不同剪辑版，排映台不再有同版管辖基础。'
        : '发布包已失效或不存在。';
      const previousTargets = wo.targetScreeningIds?.length
        ? wo.targetScreeningIds
        : wo.rounds.flatMap((r) => r.targetScreeningIds || []);
      const uniqueTargets = [...new Set(previousTargets)];
      const decisions = uniqueTargets.map((screeningId) => ({
        screeningId,
        decision: 'PLAYABLE',
        reasonCode,
        inMutualExclusiveOverlap: false,
      }));
      wo.rounds.push({
        workOrderId: woId, round, at, trigger, reasonCode,
        status: 'LIFTED',
        releaseStatus: release?.status || 'MISSING',
        reason,
        screeningDecisions: decisions,
        targetScreeningIds: [],
      });
      wo.status = 'LIFTED_DIFFERENT_CUT';
      wo.updatedAt = at;
      wo.currentSummary = reason;
      wo.targetScreeningIds = [];
      applyDecisionsToSheets(woId, decisions);
      state.desk.actions.push({ at, action: 'PAUSE_LIFTED_AFTER_REPLAY', woId, reasonCode });
      log('desk', 'WORK_ORDER_REPLAY_LIFTED', { woId, reasonCode });
      return wo;
    }

    const evaluation = evaluateRelease(release);
    const status = evaluation.pauseScreeningIds.length ? 'PAUSED' : 'NO_PAUSE_REQUIRED';
    wo.rounds.push({
      workOrderId: woId, round, at, trigger, reasonCode,
      evaluation,
      status,
      targetScreeningIds: evaluation.pauseScreeningIds,
    });
    wo.status = status;
    wo.updatedAt = at;
    wo.currentSummary = `第 ${round} 次复办：${status}`;
    wo.targetScreeningIds = evaluation.pauseScreeningIds;
    applyDecisionsToSheets(woId, evaluation.screeningDecisions);
    state.desk.actions.push({ at, action: 'WORK_ORDER_REPLAYED', woId, round, status });
    log('desk', 'WORK_ORDER_REPLAYED', { woId, round, status });
    return wo;
  }

  function correctHbToAlternateCut() {
    const oldReceipt = state.receipts.find((r) => r.id === 'RCPT-B-HB');
    assert(oldReceipt.status === 'VALID', '旧回执当前不是有效状态');
    const newReceipt = {
      id: 'RCPT-B-HB-C2',
      archive: 'b',
      alias: '夜航雾港（导演剪辑版）',
      lock: 'LOCK-HB-DIRECTORS-CUT',
      status: 'VALID',
      issuedAt: fmt(),
      correctionOrderId: 'CR-2026-011',
      statement: '卖方订正回执：旧别名误挂 4K 修复版，实际对应另一剪辑版。',
    };
    state.receipts.push(newReceipt);
    oldReceipt.status = 'CORRECTED';
    oldReceipt.supersededBy = newReceipt.id;
    oldReceipt.correctionOrderId = 'CR-2026-011';
    state.archives.b.catalog.find((x) => x.id === 'B-CAT-01').receiptId = newReceipt.id;
    state.corrections.push({
      id: 'CR-2026-011',
      at: fmt(),
      type: 'ALIAS_EDITION_REASSIGNMENT',
      oldReceiptId: oldReceipt.id,
      newReceiptId: newReceipt.id,
      oldLock: oldReceipt.lock,
      newLock: newReceipt.lock,
      reason: '旧别名后来更正：《夜航雾港》为导演剪辑版，不是甲馆《雾港回声》4K 修复版。',
      chosenArchitectureConsequence: '一次性会话句柄已删除；必须由两馆再次配合召开新会话。',
    });
    const oldWitness = state.witnesses.find((w) => w.workKey === 'HB' && w.status === 'ACTIVE');
    if (oldWitness) {
      oldWitness.status = 'SUPERSEDED_BY_CORRECTION';
      oldWitness.supersededByCorrection = 'CR-2026-011';
    }
    const release = state.desk.releases.find((r) => r.workKey === 'HB' && r.status === 'ACTIVE');
    if (release) release.status = 'PENDING_CORRECTION_RECHECK';
    log('trust', 'ALIAS_CORRECTED', { correctionId: 'CR-2026-011' });
    return state.corrections.at(-1);
  }

  function runCorrectionAndReplayHb() {
    const correction = correctHbToAlternateCut();
    // 方案 B：仅凭旧案号不能重算同版；两馆必须用新回执，仅针对原 HB 候选再开一次性会话。
    const round = runFullComparisonRound({
      purpose: 'CORRECTION_CR-2026-011_WO-HB-1212',
      pairIds: ['P-HB'],
    });
    const release = state.desk.releases.find((r) => r.workKey === 'HB');
    if (release) {
      release.status = 'WITHDRAWN_DIFFERENT_CUT';
      release.withdrawnAt = fmt();
      release.withdrawalReason = 'CR-2026-011 后新会话未产生同版见证；两馆确认不重合。';
      state.desk.actions.push({ at: fmt(), action: 'RELEASE_WITHDRAWN_DIFFERENT_CUT', releaseId: release.releaseId });
    }
    const wo = replayWorkOrder('WO-HB-1212', {
      trigger: 'CORRECTION_CR-2026-011_ORIGINAL_WO_REPLAY',
      reasonCode: 'DIFFERENT_CUT',
    });
    return { correction, comparison: round, workOrder: wo };
  }

  function shortenSpExclusivityToBeforeEvening() {
    const targetEnd = '2026-12-19T18:00:00+08:00';
    const lic = state.archives.a.licenses.find((l) => l.id === 'LIC-A-SP');
    let amendment = state.corrections.find((c) => c.id === 'AMEND-LIC-A-SP-01');
    if (lic.exclusiveEnd !== targetEnd) {
      const before = lic.exclusiveEnd;
      lic.exclusiveEnd = targetEnd;
      lic.publicText = '《春分特快》澜州独占；合同订正后独占截止 2026-12-19 18:00（晚场 19:30 前）；放映权本身至 2026-12-31。';
      amendment = {
        id: 'AMEND-LIC-A-SP-01',
        at: fmt(),
        licenseId: lic.id,
        field: 'exclusiveEnd',
        before,
        after: lic.exclusiveEnd,
        note: '只缩短独占档期，不缩短普通放映权；晚场仍在 exhibitionEnd 内。',
      };
      state.corrections.push({ id: amendment.id, type: 'LICENSE_EXCLUSIVITY_AMENDMENT', ...amendment });
      log('desk', 'EXCLUSIVE_WINDOW_AMENDED', { licenseId: lic.id, amendmentId: amendment.id });
    }
    // 已送交排映台的公开片段也必须替换为订正后版本。
    const release = state.desk.releases.find((r) => r.workKey === 'SP');
    if (release) {
      const idx = release.publicFragments.findIndex((f) => f.licenseId === lic.id);
      if (idx >= 0) {
        release.publicFragments[idx] = publicFragment(lic);
        release.publicFragments[idx].amendedBy = amendment.id;
      }
    }
    const wo = replayWorkOrder('WO-SP-1219', {
      trigger: 'LICENSE_AMENDMENT_AMEND-LIC-A-SP-01_ORIGINAL_WO_REPLAY',
      reasonCode: 'EXCLUSIVITY_SHORTENED_BEFORE_EVENING',
    });
    return { amendment, workOrder: wo };
  }

  function attemptCaseNumberOnlyRecheck(woId) {
    const wo = getWo(woId);
    return {
      allowed: false,
      code: 'NEEDS_BOTH_PARTIES',
      caseNumber: woId,
      canReadStoredWorkOrder: true,
      canRecomputeSameEditionFromOriginalCatalogs: false,
      reason: '本样例选择方案 B：散会已删除一次性会话句柄，也没有保管室留存两本原目录的映射。仅凭案号可以读旧工单和已公开片段，但不能重新核验误配；旧别名更正后必须两馆再次配合。',
      storedReviewPackage: reviewPackage(woId),
    };
  }

  function reviewPackage(woId) {
    const wo = getWo(woId);
    const release = state.desk.releases.find((r) => r.releaseId === wo.releaseId) || null;
    return { workOrder: clone(wo), release: clone(release) };
  }

  function sessionView(sessionId) {
    const session = state.sessions[sessionId];
    if (!session) return null;
    return clone({ session });
  }

  function roleExport(role) {
    const at = fmt();
    if (role === 'archive-a' || role === 'archive-b') {
      const side = role === 'archive-a' ? 'a' : 'b';
      const archive = state.archives[side];
      const receiptIds = new Set(archive.catalog.map((c) => c.receiptId));
      // 也包含已被替换的本馆旧回执，便于本馆自查订正链。
      state.receipts.filter((r) => r.archive === side).forEach((r) => receiptIds.add(r.id));
      return {
        exportedAt: at,
        post: role,
        architecture: ARCHITECTURE,
        postName: archive.name,
        originalCatalog: clone(archive.catalog),
        ownReceipts: state.receipts.filter((r) => receiptIds.has(r.id)).map(publicReceipt),
        originalLicenses: clone(archive.licenses),
        electronicScreeningSheets: clone(archive.screenings),
        ownConfirmations: clone(archive.confirmations),
        boundary: EXPORT_MATRIX.find((x) => x.post === role),
      };
    }
    if (role === 'trust') {
      return {
        exportedAt: at,
        post: 'trust',
        architecture: ARCHITECTURE,
        activeOneTimeSessions: clone(Object.values(state.sessions)),
        receiptLedgerIncludingInternalLock: clone(state.receipts),
        correctionLedger: clone(state.corrections),
        issuedWitnesses: clone(state.witnesses),
        boundary: EXPORT_MATRIX.find((x) => x.post === 'trust'),
      };
    }
    if (role === 'desk') {
      return {
        exportedAt: at,
        post: 'desk',
        architecture: ARCHITECTURE,
        protocol: clone(state.desk.protocol),
        confirmedReleases: clone(state.desk.releases),
        minimizedScreeningSubmissions: clone(state.desk.screeningSubmissions),
        workOrders: clone(state.desk.workOrders),
        deskActions: clone(state.desk.actions),
        boundary: EXPORT_MATRIX.find((x) => x.post === 'desk'),
      };
    }
    if (role === 'governance') {
      return {
        exportedAt: at,
        architecture: ARCHITECTURE,
        choice: 'B：各馆只持本地原册，一次性会话比对、分别确认，散会删除跨轮标识。',
        rejectedAlternative: 'A：双方认可的保管室暂存原目录并可凭案号复查误配；本样例不采用该集中留存路径。',
        exportMatrix: EXPORT_MATRIX,
        caseNumberOnlyRecheck: 'DISABLED_FOR_RECOMPUTATION__BOTH_PARTIES_REQUIRED',
      };
    }
    throw new Error(`未知岗位 ${role}`);
  }

  function importCatalog(side, catalog) {
    assert(side === 'a' || side === 'b', '馆别必须为 a 或 b');
    assert(Array.isArray(catalog), '目录必须是 JSON 数组');
    for (const item of catalog) {
      assert(item && typeof item.id === 'string', '目录行缺少 id');
      assert(typeof item.title === 'string' && item.title.trim(), `${item?.id || '未知行'} 缺少 title`);
      assert(typeof item.receiptId === 'string' && item.receiptId.trim(), `${item.id} 缺少 receiptId`);
    }
    state.archives[side].catalog = clone(catalog).map((item) => ({
      aliases: [],
      ...item,
    }));
    log(`archive-${side}`, 'LOCAL_CATALOG_IMPORTED', { rows: catalog.length });
    return roleExport(`archive-${side}`);
  }

  function reset() {
    state = seedState();
    log('system', 'RESET', {});
    return { ok: true };
  }

  function prepareInitialCollision() {
    const comparison = runFullComparisonRound({ purpose: 'INITIAL_THREE_PROGRAMS' });
    dispatchToDesk();
    const adjudications = adjudicateAll({ trigger: 'INITIAL_LOCAL_SCREENING_SHEETS' });
    return { comparison, adjudications };
  }

  function overviewForUi() {
    return {
      architecture: ARCHITECTURE,
      activeSession: state.activeSessionId ? sessionView(state.activeSessionId) : null,
      events: clone(state.events.slice(-80)),
      counts: {
        catalogA: state.archives.a.catalog.length,
        catalogB: state.archives.b.catalog.length,
        activeSessions: Object.keys(state.sessions).length,
        deskReleases: state.desk.releases.length,
        workOrders: state.desk.workOrders.length,
        corrections: state.corrections.length,
      },
    };
  }

  return {
    reset,
    importCatalog,
    openSession,
    submitSide,
    confirmHit,
    acknowledgeMismatch,
    closeSession,
    runFullComparisonRound,
    dispatchToDesk,
    adjudicateAll,
    replayWorkOrder,
    correctHbToAlternateCut,
    runCorrectionAndReplayHb,
    shortenSpExclusivityToBeforeEvening,
    attemptCaseNumberOnlyRecheck,
    reviewPackage,
    roleExport,
    overviewForUi,
    prepareInitialCollision,
    titleSimilarity,
    // 测试/脚本可直接读取投影，不直接暴露可变 state。
    _debugState: () => state,
  };
}
