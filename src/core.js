import {
  WITNESS,
  generatePartySeal,
  newId,
  newPairSecret,
  pairMark,
  sha256,
  signApproval,
  signPayload,
  verifyPairMark,
  messageAuthPayload,
} from './witnesses.js';

const FIXED_NOW = '2026-11-26T20:10:00+08:00';
export const OLD_SPEC_SENTENCE =
  '交片规格：3840×2160、25fps、ProRes 422 HQ 母版，音频为 48kHz/24bit 立体声 WAV，次日 18:00 前交片。';

export const FORMAL_RULE_VERSION = 'terminal-rule/v1';

export function agreementHashPayload(a) {
  return {
    agreementId: a.agreementId,
    orderId: a.orderId,
    version: a.version,
    clauseId: a.clauseId,
    title: a.title,
    clauseText: a.clauseText,
    terms: a.terms,
    parentVersion: a.parentVersion,
    parentHash: a.parentHash,
    sourceCorrectionId: a.sourceCorrectionId,
    createdAt: a.createdAt,
    seal: a.seal ?? null,
  };
}

export function computeAgreementHash(a) {
  return sha256(agreementHashPayload(a));
}

function makeAgreement(input) {
  const doc = {
    status: 'effective',
    parentVersion: input.version > 1 ? input.version - 1 : null,
    parentHash: null,
    sourceCorrectionId: null,
    seal: null,
    ...input,
  };
  doc.contentHash = computeAgreementHash(doc);
  return doc;
}

function receiptHashPayload(r) {
  // receiptHash/signature 都是认证值自身，不能进入被哈希的内容。
  const { signature, receiptHash, ...payload } = r;
  return payload;
}

export function computeReceiptHash(r) {
  return sha256(receiptHashPayload(r));
}

function makeReceipt(input, state) {
  const receipt = { ...input };
  // keyId 也属于回单来源元数据，先固定再计算 receiptHash。
  receipt.keyId = state.parties[receipt.signedBy].seal.keyId;
  receipt.receiptHash = computeReceiptHash(receipt);
  receipt.signature = signPayload(
    {
      receiptId: receipt.id,
      orderId: receipt.orderId,
      receiptHash: receipt.receiptHash,
      signedBy: receipt.signedBy,
      signedAt: receipt.signedAt,
      text: receipt.text,
    },
    state.parties[receipt.signedBy].seal.privateKeyPem,
  );
  return receipt;
}

export function appendMessage(state, input) {
  const seq = input.seq ?? state.messages.length + 1;
  const message = {
    id: input.id ?? `MSG-${String(seq).padStart(3, '0')}`,
    seq,
    at: input.at ?? state.clock ?? new Date().toISOString(),
    from: input.from,
    kind: input.kind ?? 'talk',
    text: input.text,
    legalEffect: input.legalEffect ?? null,
    correctionId: input.correctionId ?? null,
    receiptId: input.receiptId ?? null,
    contextMessageIds: input.contextMessageIds ?? undefined,
    citation: input.citation ?? undefined,
    terminalNote: input.terminalNote ?? undefined,
  };

  // 认证载荷覆盖消息正文和元数据，但不覆盖 pairMark 自身。
  message.pairMark = pairMark(
    (() => {
      const { pairMark: _omit, ...payload } = message;
      return payload;
    })(),
    state.session.pairSecret,
  );

  state.messages.push(message);
  state.audit.push({ at: message.at, type: 'message-appended', id: message.id, from: message.from, kind: message.kind });
  return message;
}

function seedMessages(state) {
  appendMessage(state, {
    id: 'MSG-001',
    seq: 1,
    at: '2026-11-26T19:50:00+08:00',
    from: 'SYSTEM',
    kind: 'system',
    text: '本地谈单会话已建立。普通消息只加盖甲乙共享的双边私记；订单规格变更必须生成引用旧约定及上下文的订正，并按所选办法完成签署。',
    legalEffect: 'formal-boundary-notice',
  });
  appendMessage(state, {
    id: 'MSG-002',
    seq: 2,
    at: '2026-11-26T20:00:00+08:00',
    from: 'B',
    kind: 'talk',
    text: '甲，明天 ORD-1127 的访谈成片还是按刚才确认的老规格交吧？',
  });
  appendMessage(state, {
    id: 'MSG-003',
    seq: 3,
    at: '2026-11-26T20:02:00+08:00',
    from: 'A',
    kind: 'proposal-talk',
    text: '明天交片规格改一下：先出 1920×1080、25fps、H.264 MP4 审片版，4K ProRes 母版往后放。',
    terminalNote: '普通私聊中的意向表达；尚未引用并替换 AGR-1127-SPEC@v1。',
  });
  appendMessage(state, {
    id: 'MSG-004',
    seq: 4,
    at: '2026-11-26T20:03:00+08:00',
    from: 'B',
    kind: 'ack',
    text: '我收到了。',
    legalEffect: 'acknowledgment-only',
    terminalNote: '终端已标记：“收到”只证明送达/知悉，不构成对规格变更的承诺或会签。',
  });
  appendMessage(state, {
    id: 'MSG-005',
    seq: 5,
    at: '2026-11-26T20:04:00+08:00',
    from: 'A',
    kind: 'talk',
    text: '这些私聊就我们俩能核对就行，别将来截一段就拿去对外说是我正式改单。真要改，我们另封一份正式约定。',
  });
  appendMessage(state, {
    id: 'MSG-006',
    seq: 6,
    at: '2026-11-26T20:05:00+08:00',
    from: 'B',
    kind: 'talk',
    text: '可以。若我联系不上你，至少你发言端直接署名的具名修订单得能让仲裁验出处；没这个，我不能只拿私聊改已接订单。',
  });
}

export function createState({ scenario = 'live', now = FIXED_NOW } = {}) {
  const sessionId = `SES-${String(scenario).toUpperCase()}-1127`;
  const state = {
    schema: 'negotiation-terminal-state/v1',
    scenario,
    createdAt: now,
    clock: now,
    session: {
      id: sessionId,
      pairWitness: WITNESS.PAIR,
      publicWitness: WITNESS.PUBLIC,
      formalRuleVersion: FORMAL_RULE_VERSION,
      pairSecret: newPairSecret(sessionId),
      pairWitnessDescription: 'HMAC 式双边私记：甲乙共享模拟钥，可验完整性，但不能向第三方唯一归属发送者。',
      publicWitnessDescription: 'Ed25519 式具名公鉴：证据包附公钥，第三方可独立验真。',
    },
    presence: {
      A: { status: 'online', since: '2026-11-26T19:50:00+08:00' },
      B: { status: 'online', since: '2026-11-26T19:50:00+08:00' },
    },
    parties: {
      A: {
        id: 'A',
        name: '拾光录制室',
        role: '甲方 / 委托及收件方',
        seal: generatePartySeal('A'),
      },
      B: {
        id: 'B',
        name: '声浪录制室',
        role: '乙方 / 承制及交片方',
        seal: generatePartySeal('B'),
      },
    },
    orders: [
      {
        id: 'ORD-1127',
        title: '次日访谈成片交片单',
        status: 'partially_accepted_original_master',
        currentAgreementId: 'AGR-1127-SPEC',
        currentVersion: 1,
        receiptIds: ['RCPT-ORIG-001'],
      },
      {
        id: 'ORD-8899',
        title: '另一场访谈录制交片单（无关订单）',
        status: 'accepted',
        currentAgreementId: 'AGR-8899-SPEC',
        currentVersion: 1,
        receiptIds: [],
      },
    ],
    agreements: [],
    receipts: [],
    messages: [],
    corrections: [],
    packages: [],
    awards: [],
    audit: [],
  };

  state.agreements.push(
    makeAgreement({
      agreementId: 'AGR-1127-SPEC',
      orderId: 'ORD-1127',
      version: 1,
      clauseId: 'DELIVERY_SPEC',
      title: 'ORD-1127 交片规格约定',
      clauseText: `双方确认次日访谈成片交片口径。${OLD_SPEC_SENTENCE} 交付方式为录制室内网或加密移动硬盘。`,
      terms: {
        deliverable: '访谈成片母版',
        dueAt: '2026-11-27T18:00:00+08:00',
        resolution: '3840x2160',
        fps: 25,
        codec: 'ProRes 422 HQ',
        audio: '48kHz/24bit stereo WAV',
        delivery: '内网/加密硬盘',
      },
      createdAt: '2026-11-26T19:45:00+08:00',
    }),
    makeAgreement({
      agreementId: 'AGR-8899-SPEC',
      orderId: 'ORD-8899',
      version: 1,
      clauseId: 'DELIVERY_SPEC',
      title: 'ORD-8899 交片规格约定',
      clauseText: `ORD-8899 沿用同一套通用交片口径。${OLD_SPEC_SENTENCE} 嘉宾字幕名单另行确认；本约定与 ORD-1127 的订正互不覆盖。`,
      terms: {
        deliverable: '另一场访谈成片母版',
        dueAt: '2026-11-28T18:00:00+08:00',
        resolution: '3840x2160',
        fps: 25,
        codec: 'ProRes 422 HQ',
        audio: '48kHz/24bit stereo WAV',
        delivery: '内网/加密硬盘',
      },
      createdAt: '2026-11-26T18:30:00+08:00',
    }),
  );

  state.receipts.push(
    makeReceipt(
      {
        id: 'RCPT-ORIG-001',
        orderId: 'ORD-1127',
        deliverableId: 'DLV-ORIG-MASTER',
        title: '原版成片母版正式签收回单',
        signedAt: '2026-11-20T15:30:00+08:00',
        signedBy: 'A',
        receivedByPerson: '甲值班制片',
        text: '今收到并验收 ORD-1127 原版成片母版一支，画面、声音与初版交付清单一致，同意正式签收。本回单不证明后续修订审片版或延期母版已经交付。',
      },
      state,
    ),
  );

  seedMessages(state);

  if (scenario === 'missing') {
    createCorrection(state, {
      id: 'CR-NAMED-001',
      kind: 'spec',
      title: '甲失联前交付的具名规格修订单',
      orderId: 'ORD-1127',
      agreementId: 'AGR-1127-SPEC',
      version: 1,
      quote: OLD_SPEC_SENTENCE,
      contextMessageIds: ['MSG-002', 'MSG-003', 'MSG-004', 'MSG-005', 'MSG-006'],
      summary: '先交 1080p/H.264 MP4 审片版，4K ProRes 母版延后至 11 月 28 日 12:00。',
      changes: {
        terms: {
          resolution: '1920x1080',
          fps: 25,
          codec: 'H.264 MP4 审片版；4K ProRes 422 HQ 母版延后',
          masterDueAt: '2026-11-28T12:00:00+08:00',
        },
        clauseText:
          '双方确认：2026-11-27T18:00:00+08:00 前先交付 1920×1080、25fps、H.264 MP4 审片版；3840×2160/ProRes 422 HQ 母版改于 2026-11-28T12:00:00+08:00 前交付。本单仅替换 AGR-1127-SPEC@v1 中与之冲突的交片规格。',
      },
      method: 'named',
      proposedBy: 'A',
      at: '2026-11-26T20:06:00+08:00',
      acceptBy: '2026-11-26T21:00:00+08:00',
      deliveredAt: '2026-11-26T20:06:30+08:00',
    });
    state.presence.A = {
      status: 'offline',
      since: '2026-11-26T20:08:00+08:00',
      note: '甲在发出一份限时具名修订单后失联',
    };
    appendMessage(state, {
      id: 'MSG-009',
      seq: 9,
      at: '2026-11-26T20:08:00+08:00',
      from: 'SYSTEM',
      kind: 'system',
      text: '甲终端现已失联。乙可以提交双边私聊证据，但第三方不能凭共享私记唯一认定 MSG-003 出自甲；CR-NAMED-001 则可凭甲的公鉴署名查验出处。若乙在 21:00 后才接受，同意迟到的后果由仲裁单独判断。',
      correctionId: 'CR-NAMED-001',
      legalEffect: 'absence-and-late-acceptance-warning',
    });
  }

  return state;
}

export function publicRegistry(state) {
  return Object.fromEntries(
    Object.entries(state.parties).map(([id, p]) => [
      id,
      {
        party: id,
        name: p.name,
        role: p.role,
        keyId: p.seal.keyId,
        alg: p.seal.alg,
        publicKeyPem: p.seal.publicKeyPem,
        fingerprint: p.seal.fingerprint,
      },
    ]),
  );
}

export function setPresence(state, party, status, at = new Date().toISOString()) {
  if (!state.presence[party]) throw new Error(`未知当事方：${party}`);
  state.presence[party] = { ...state.presence[party], status, since: at };
  state.audit.push({ at, type: 'presence-changed', party, status });
  return state.presence[party];
}

export function addChat(state, input) {
  if (!input.from || !state.parties[input.from]) throw new Error('普通消息必须来自甲或乙终端');
  if (!input.text?.trim()) throw new Error('消息内容不能为空');
  const at = input.at ?? new Date().toISOString();
  const kind = input.kind ?? 'talk';
  const result = appendMessage(state, {
    from: input.from,
    at,
    kind,
    text: input.text.trim(),
    legalEffect:
      kind === 'ack' || /我收到了|收到/.test(input.text)
        ? 'acknowledgment-only'
        : input.legalEffect ?? 'ordinary-talk-no-automatic-variation',
    terminalNote:
      kind === 'ack' || /我收到了|收到/.test(input.text)
        ? '“收到”不自动同意改变规格；规格变更须走引用订正与正式签署。'
        : input.terminalNote,
  });
  return result;
}

export function findAgreement(state, agreementId, version) {
  const matches = state.agreements.filter((a) => a.agreementId === agreementId);
  if (!matches.length) throw new Error(`找不到约定：${agreementId}`);
  if (version == null) return matches.sort((x, y) => y.version - x.version)[0];
  const found = matches.find((a) => a.version === Number(version));
  if (!found) throw new Error(`找不到约定版本：${agreementId}@v${version}`);
  return found;
}

export function makeCitation(state, input) {
  const base = findAgreement(state, input.agreementId, input.version);
  const quote = input.quote ?? base.clauseText;
  if (!base.clauseText.includes(quote)) {
    throw new Error('引用原文必须是被替换约定中的原文片段；不得用概括文字冒充原文引用。');
  }
  return {
    agreementId: base.agreementId,
    orderId: base.orderId,
    version: base.version,
    clauseId: base.clauseId,
    contentHash: base.contentHash,
    quote,
    quoteLength: quote.length,
  };
}

function correctionHashPayload(c) {
  return {
    id: c.id,
    kind: c.kind,
    title: c.title,
    orderId: c.orderId,
    method: c.method,
    citation: c.citation,
    changes: c.changes,
    summary: c.summary,
    contextMessageIds: c.contextMessageIds,
    contextSnapshot: c.contextSnapshot,
    proposedBy: c.proposedBy,
    createdAt: c.createdAt,
    acceptBy: c.acceptBy ?? null,
  };
}

export function correctionDocHash(c) {
  return sha256(correctionHashPayload(c));
}

function snapshotContext(state, ids) {
  if (!Array.isArray(ids) || ids.length === 0) {
    throw new Error('每次订正必须引用至少一条上下文消息。');
  }
  return ids.map((id) => {
    const m = state.messages.find((x) => x.id === id);
    if (!m) throw new Error(`上下文消息不存在：${id}`);
    return { id: m.id, seq: m.seq, at: m.at, from: m.from, kind: m.kind, text: m.text };
  });
}

export function createCorrection(state, input) {
  const at = input.at ?? new Date().toISOString();
  const citation = makeCitation(state, input);
  if (citation.orderId !== input.orderId && input.orderId) {
    throw new Error('订正单的订单号必须与被引用约定的订单一致。');
  }
  const contextSnapshot = snapshotContext(state, input.contextMessageIds);
  if (!input.summary?.trim()) throw new Error('请填写订正摘要。');
  if (!input.changes || Object.keys(input.changes).length === 0) {
    throw new Error('订正必须包含具体变更内容。');
  }
  const method = input.method === 'named' ? 'named' : 'joint';
  const id = input.id ?? newId('CR');
  const correction = {
    id,
    schema: 'formal-correction/v1',
    kind: input.kind === 'reschedule' ? 'reschedule' : 'spec',
    title: input.title ?? (input.kind === 'reschedule' ? '同版改期订正单' : '交片规格订正单'),
    orderId: citation.orderId,
    method,
    citation,
    changes: structuredClone(input.changes),
    summary: input.summary.trim(),
    contextMessageIds: [...input.contextMessageIds],
    contextSnapshot,
    proposedBy: input.proposedBy,
    createdAt: at,
    acceptBy: input.acceptBy ?? null,
    status: 'pending',
    approvals: [],
    docHash: '',
    deliveredAt: input.deliveredAt ?? at,
    transferWarning:
      method === 'named'
        ? '具名公鉴修订单一旦交付对方，即可被转发给第三方验真；发言端不能再承诺其不可转证。'
        : '双边会签件由双方共同封定；对外提交范围由双方/证据包决定。',
  };
  correction.docHash = correctionDocHash(correction);
  state.corrections.push(correction);

  appendMessage(state, {
    at,
    from: 'TERMINAL',
    kind: 'formal-draft',
    text: `终端已形成${method === 'named' ? '具名公鉴' : '双边到场会签'}订正 ${id}，引用 ${citation.agreementId}@v${citation.version}（${citation.contentHash.slice(0, 10)}…）及 ${contextSnapshot.length} 条上下文。该单尚未完成签署，旧约定暂不换版。`,
    correctionId: id,
    legalEffect: 'formal-draft-no-variation-until-required-seals',
  });

  if (input.signProposer !== false) {
    approveCorrection(state, {
      correctionId: id,
      party: input.proposedBy,
      at: input.proposerSignedAt ?? at,
      channel: method === 'named' ? 'named-terminal' : 'joint-presence',
      decision: 'accept',
    });
  }
  return correction;
}

function hasApproval(c, party, decisionPrefix = 'accept') {
  return c.approvals.some(
    (a) => a.party === party && (decisionPrefix === 'accept' ? a.decision === 'accept' : true),
  );
}

export function approveCorrection(state, input) {
  const c = state.corrections.find((x) => x.id === input.correctionId);
  if (!c) throw new Error(`找不到订正：${input.correctionId}`);
  if (c.status === 'effective') return { correction: c, changed: false, reason: 'already-effective' };

  const party = input.party;
  if (!state.parties[party]) throw new Error(`未知签署方：${party}`);
  const presence = state.presence[party]?.status;
  const channel = input.channel ?? (c.method === 'named' ? 'named-terminal' : 'joint-presence');

  // 新签署必须由当前到场/在线终端作出。失联前已经写入单据的署名属于历史事实，仍可在缺席后验真。
  if (presence === 'offline' && input.allowOffline !== true) {
    const at = input.at ?? new Date().toISOString();
    appendMessage(state, {
      at,
      from: 'TERMINAL',
      kind: 'seal-rejected',
      text: `${party} 终端当前不在场/已失联，不能作出新的有效签署；普通私聊、对方代点或事后补录都不能替代。失联前已经生成的具名署名仍可验出处。`,
      correctionId: c.id,
      legalEffect: 'no-new-seal-while-absent',
    });
    const err = new Error(`${party} 当前不在场/失联，不能完成新的正式签署`);
    err.code = 'PARTY_ABSENT';
    throw err;
  }

  const at = input.at ?? new Date().toISOString();
  const isLate = Boolean(c.acceptBy && new Date(at).getTime() > new Date(c.acceptBy).getTime());
  const decision = input.decision ?? (isLate ? 'late-accept' : 'accept');

  // 同一方对同一哈希只能有一个有效接受署名；允许保留迟到接受记录但不覆盖此前记录。
  const approval = signApproval(
    {
      docId: c.id,
      docHash: c.docHash,
      party,
      at,
      channel,
      decision,
    },
    state.parties[party],
  );
  c.approvals.push(approval);

  appendMessage(state, {
    at,
    from: 'TERMINAL',
    kind: isLate ? 'late-acceptance' : 'formal-signature',
    text:
      decision === 'late-accept'
        ? `${party} 的同意已于 ${at} 迟到。署名可验、时间可查，但不能溯及既往地完成限期接受；旧约定不自动换版。`
        : `${party} 已对同版订正 ${c.id}（${c.docHash.slice(0, 10)}…）完成${channel === 'joint-presence' ? '到场会签' : '具名签署'}。`,
    correctionId: c.id,
    legalEffect: decision === 'late-accept' ? 'late-acceptance-no-retroactive-variation' : 'signature-recorded',
  });

  const AOk = c.approvals.some((a) => a.party === 'A' && a.decision === 'accept');
  const BOk = c.approvals.some((a) => a.party === 'B' && a.decision === 'accept');

  if (decision === 'late-accept' || isLate) {
    c.status = 'late-accepted';
    c.lateAcceptedAt = at;
    return { correction: c, changed: false, late: true };
  }

  if (AOk && BOk) finalizeCorrection(state, c, at);
  return { correction: c, changed: c.status === 'effective' };
}

function finalizeCorrection(state, c, at) {
  const base = findAgreement(state, c.citation.agreementId, c.citation.version);
  if (base.contentHash !== c.citation.contentHash) {
    throw new Error('被引用约定的哈希已经变化，拒绝换版。');
  }

  base.status = 'superseded';
  base.supersededAt = at;
  base.supersededBy = c.id;

  const nextTerms = structuredClone(base.terms);
  if (c.changes.terms) Object.assign(nextTerms, c.changes.terms);

  const next = makeAgreement({
    agreementId: base.agreementId,
    orderId: base.orderId,
    version: base.version + 1,
    clauseId: base.clauseId,
    title: c.changes.title ?? base.title,
    clauseText: c.changes.clauseText ?? base.clauseText,
    terms: nextTerms,
    createdAt: at,
    parentHash: base.contentHash,
    sourceCorrectionId: c.id,
    seal: {
      method: c.method,
      correctionId: c.id,
      docHash: c.docHash,
      approvals: c.approvals.map((a) => ({
        party: a.party,
        at: a.at,
        keyId: a.keyId,
        fingerprint: a.publicKeyFingerprint,
        signature: a.signature,
        channel: a.channel,
        decision: a.decision,
      })),
    },
  });
  state.agreements.push(next);
  const order = state.orders.find((o) => o.id === base.orderId);
  if (order) {
    order.currentVersion = next.version;
    order.currentAgreementId = next.agreementId;
  }
  c.status = 'effective';
  c.effectiveAt = at;
  c.effectiveVersion = next.version;
  c.effectiveContentHash = next.contentHash;

  appendMessage(state, {
    at,
    from: 'TERMINAL',
    kind: 'version-effective',
    text: `订正 ${c.id} 已由双方对同版哈希 ${c.docHash.slice(0, 10)}… 完成接受。仅 ${base.agreementId}@v${base.version} 换版为 v${next.version}；相同字句不影响其他订单。`,
    correctionId: c.id,
    legalEffect: 'cited-agreement-superseded-only',
  });
  state.audit.push({ at, type: 'agreement-version', agreementId: next.agreementId, version: next.version, correctionId: c.id });
}

export function addReceipt(state, input) {
  if (!state.orders.find((o) => o.id === input.orderId)) throw new Error('回单必须关联已存在订单');
  const receipt = makeReceipt(
    {
      id: input.id ?? newId('RCPT'),
      orderId: input.orderId,
      deliverableId: input.deliverableId,
      title: input.title,
      signedAt: input.signedAt,
      signedBy: input.signedBy,
      text: input.text,
    },
    state,
  );
  state.receipts.push(receipt);
  const order = state.orders.find((o) => o.id === input.orderId);
  order.receiptIds.push(receipt.id);
  appendMessage(state, {
    at: input.addedAt ?? new Date().toISOString(),
    from: 'TERMINAL',
    kind: 'receipt-added',
    text: `已补入 ${receipt.title} ${receipt.id}，回单原始签署时间为 ${receipt.signedAt}。`,
    receiptId: receipt.id,
    legalEffect: 'preexisting-formal-receipt',
  });
  return receipt;
}

export function verifySessionMarks(state) {
  return state.messages.map((m) => ({
    id: m.id,
    valid: verifyPairMark(messageAuthPayload(m), m.pairMark, state.session.pairSecret),
  }));
}

/** 终端显示件导出：默认不带共享私钥；可读 Markdown 由调用方/前端生成。 */
export function exportTranscript(state, party, { includePairVerifier = false } = {}) {
  return {
    schema: 'terminal-transcript-export/v1',
    exportedBy: party,
    exportedAt: new Date().toISOString(),
    terminal: {
      party,
      partyName: state.parties[party]?.name ?? party,
      presence: state.presence[party]?.status,
      formalRuleVersion: state.session.formalRuleVersion,
    },
    session: {
      id: state.session.id,
      pairWitness: state.session.pairWitness,
      pairVerifierIncluded: includePairVerifier,
      ...(includePairVerifier ? { pairSecret: state.session.pairSecret } : {}),
    },
    messages: state.messages,
    orders: state.orders,
    agreements: state.agreements,
    corrections: state.corrections,
    receipts: state.receipts,
    notice:
      '普通消息的双边私记只向持有共享模拟钥的甲乙证明完整性；对外归属以具名署名/共同会签为准。',
  };
}
