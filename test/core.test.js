import test from 'node:test';
import assert from 'node:assert/strict';

import {
  OLD_SPEC_SENTENCE,
  addChat,
  approveCorrection,
  createCorrection,
  createState,
} from '../src/core.js';
import { buildEvidencePackage } from '../src/evidence.js';
import { arbitratePackage, retrieveSignedContent } from '../src/arbiter.js';
import { pairMark, verifyPairMark, generatePartySeal, signPayload, verifyPayloadSignature } from '../src/witnesses.js';

const CTX = ['MSG-002', 'MSG-003', 'MSG-004', 'MSG-005', 'MSG-006'];
const changes = {
  clauseText: '新版：1920×1080/H.264 MP4 审片版；4K ProRes 母版延后。',
  terms: { resolution: '1920x1080', codec: 'H.264' },
};

test('两类见证器：双边记号可验篡改但不唯一归属；公鉴署名可独立验真', () => {
  const secret = 'sim-secret';
  const payload = { id: 'MSG-X', from: 'A', text: '明天改规格' };
  const mark = pairMark(payload, secret);
  assert.equal(verifyPairMark(payload, mark, secret), true);
  assert.equal(verifyPairMark({ ...payload, text: '被改写' }, mark, secret), false);
  // 共享钥同时发给 A/B：技术上任一方都能生成同一形式的 mark，因此不能唯一归属。
  assert.equal(pairMark(payload, secret), mark);

  const seal = generatePartySeal('A');
  const sig = signPayload({ doc: 'CR-1', hash: 'abc' }, seal.privateKeyPem);
  assert.equal(verifyPayloadSignature({ doc: 'CR-1', hash: 'abc' }, sig, seal.publicKeyPem), true);
  assert.equal(verifyPayloadSignature({ doc: 'CR-1', hash: 'tampered' }, sig, seal.publicKeyPem), false);
});

test('“我收到了”只是知悉，普通聊天不改变版本', () => {
  const s = createState({ scenario: 'live' });
  const before = s.agreements.find((a) => a.agreementId === 'AGR-1127-SPEC' && a.status === 'effective');
  addChat(s, { from: 'B', text: '我收到了。', at: '2026-11-26T20:03:00+08:00' });
  const msg = s.messages.at(-1);
  assert.equal(msg.legalEffect, 'acknowledgment-only');
  const after = s.agreements.find((a) => a.agreementId === 'AGR-1127-SPEC' && a.status === 'effective');
  assert.equal(after.version, before.version);
  assert.equal(s.orders[0].currentVersion, 1);
});

test('订正缺少上下文或原文引用时被终端拒绝', () => {
  const s = createState({ scenario: 'live' });
  assert.throws(
    () => createCorrection(s, {
      proposedBy: 'A', agreementId: 'AGR-1127-SPEC', version: 1, quote: OLD_SPEC_SENTENCE,
      contextMessageIds: [], summary: 'x', changes, method: 'joint',
    }),
    /上下文/,
  );
  assert.throws(
    () => createCorrection(s, {
      proposedBy: 'A', agreementId: 'AGR-1127-SPEC', version: 1, quote: '这句不在旧约定里',
      contextMessageIds: ['MSG-003'], summary: 'x', changes, method: 'joint',
    }),
    /原文/,
  );
});

test('甲在场：双边会签只让引用的 AGR-1127 换版，相同字句的 ORD-8899 保持 v1', () => {
  const s = createState({ scenario: 'present' });
  createCorrection(s, {
    id: 'CR-T-1', proposedBy: 'A', at: '2026-11-26T20:08+08:00',
    agreementId: 'AGR-1127-SPEC', version: 1, quote: OLD_SPEC_SENTENCE,
    contextMessageIds: CTX, summary: '规格订正', changes, method: 'joint',
  });
  approveCorrection(s, { correctionId: 'CR-T-1', party: 'B', at: '2026-11-26T20:09+08:00', channel: 'joint-presence' });
  assert.equal(s.orders.find((o) => o.id === 'ORD-1127').currentVersion, 2);
  assert.equal(s.orders.find((o) => o.id === 'ORD-8899').currentVersion, 1);

  const pkg = buildEvidencePackage(s, { includePairVerifier: true, correctionIds: ['CR-T-1'] });
  const award = arbitratePackage(pkg);
  const c = award.correctionFindings[0];
  assert.equal(c.independentlyEffective, true);
  assert.equal(c.citation.valid, true);
  assert.ok(c.identicalTextCarriers.some((x) => x.orderId === 'ORD-8899'));
  assert.ok(award.scope.orderStates.find((o) => o.orderId === 'ORD-8899').unchangedDespiteIdenticalWords);
});

test('甲失联：共享私记不能唯一归属，单方会签尝试被仲裁驳回', () => {
  const s = createState({ scenario: 'missing' });
  createCorrection(s, {
    id: 'CR-B-ONLY', proposedBy: 'B', at: '2026-11-26T20:09+08:00',
    agreementId: 'AGR-1127-SPEC', version: 1, quote: OLD_SPEC_SENTENCE,
    contextMessageIds: CTX, summary: '乙单方尝试', changes, method: 'joint',
  });
  assert.throws(
    () => approveCorrection(s, { correctionId: 'CR-B-ONLY', party: 'A', at: '2026-11-26T20:10+08:00', channel: 'joint-presence' }),
    /不在场/,
  );
  const pkg = buildEvidencePackage(s, { includePairVerifier: true, correctionIds: ['CR-B-ONLY'] });
  const award = arbitratePackage(pkg);
  assert.equal(award.messageFindings.uniqueAttributionPossible, false);
  assert.equal(award.messageFindings.validCount > 0, true);
  assert.equal(award.correctionFindings[0].independentlyEffective, false);
  assert.equal(s.orders.find((o) => o.id === 'ORD-1127').currentVersion, 1);
});

test('具名修订单：迟到接受时出处成立但不生效；限期内接受则生效', () => {
  const lateState = createState({ scenario: 'missing' });
  approveCorrection(lateState, { correctionId: 'CR-NAMED-001', party: 'B', at: '2026-11-26T21:14+08:00', channel: 'named-terminal' });
  const latePkg = buildEvidencePackage(lateState, { correctionIds: ['CR-NAMED-001'] });
  const lateAward = arbitratePackage(latePkg);
  const late = lateAward.correctionFindings.find((x) => x.id === 'CR-NAMED-001');
  assert.equal(late.hashMatch, true);
  assert.equal(late.approvals.every((a) => a.valid), true);
  assert.equal(late.lateApprovalCount, 1);
  assert.equal(late.independentlyEffective, false);

  const timelyState = createState({ scenario: 'missing' });
  approveCorrection(timelyState, { correctionId: 'CR-NAMED-001', party: 'B', at: '2026-11-26T20:40+08:00', channel: 'named-terminal' });
  const timelyPkg = buildEvidencePackage(timelyState, { correctionIds: ['CR-NAMED-001'] });
  const timelyAward = arbitratePackage(timelyPkg);
  assert.equal(timelyAward.correctionFindings[0].independentlyEffective, true);
  assert.equal(timelyState.orders.find((o) => o.id === 'ORD-1127').currentVersion, 2);
});

test('订单号与引用约定错配时不能借相同字句覆盖无关订单', () => {
  const s = createState({ scenario: 'present' });
  assert.throws(
    () => createCorrection(s, {
      id: 'CR-BAD-SCOPE', proposedBy: 'B', orderId: 'ORD-8899',
      agreementId: 'AGR-1127-SPEC', version: 1, quote: OLD_SPEC_SENTENCE,
      contextMessageIds: ['MSG-003'], summary: '搭便车', changes, method: 'joint',
    }),
    /订单/,
  );
});

test('同版改期可按编号/hash 查回精确签署内容', () => {
  const s = createState({ scenario: 'present' });
  createCorrection(s, {
    id: 'RSK-Q', kind: 'reschedule', proposedBy: 'A', at: '2026-11-26T20:15+08:00',
    agreementId: 'AGR-1127-SPEC', version: 1, quote: OLD_SPEC_SENTENCE,
    contextMessageIds: ['MSG-003', 'MSG-004'], summary: '同版改期至 29 日',
    changes: { clauseText: '交付改至 11-29', terms: { dueAt: '2026-11-29T18:00+08:00' } },
    method: 'joint',
  });
  approveCorrection(s, { correctionId: 'RSK-Q', party: 'B', at: '2026-11-26T20:16+08:00', channel: 'joint-presence' });
  const pkg = buildEvidencePackage(s, { correctionIds: ['RSK-Q'] });
  const byId = retrieveSignedContent(pkg, 'RSK-Q');
  assert.equal(byId.length, 1);
  assert.equal(byId[0].verified, true);
  assert.equal(byId[0].effective, true);
  const byHash = retrieveSignedContent(pkg, pkg.corrections[0].docHash.slice(0, 12));
  assert.equal(byHash.length, 1);
  assert.match(JSON.stringify(byId[0].exactContent), /2026-11-29/);
});

test('补入早先签收回单：来源成立，但“未开工改期”前提被否定', () => {
  const s = createState({ scenario: 'present' });
  const pkg = buildEvidencePackage(s, { receiptIds: ['RCPT-ORIG-001'] });
  const award = arbitratePackage(pkg);
  const r = award.receiptFindings[0];
  assert.equal(r.hashValid, true);
  assert.equal(r.signatureValid, true);
  assert.equal(r.sourceEstablished, true);
  assert.equal(r.provesOriginalAccepted, true);
  assert.equal(award.scope.unstartedBasis.supported, false);
  assert.match(award.scope.unstartedBasis.ruling, /不得.*未开工/);
});

test('证据包封包后篡改消息会被清单和双边私记发现', () => {
  const s = createState({ scenario: 'live' });
  const pkg = buildEvidencePackage(s, { includePairVerifier: true });
  pkg.messages.find((m) => m.id === 'MSG-003').text = '被截断/改写的私聊';
  const award = arbitratePackage(pkg);
  assert.equal(award.packageIntegrity.valid, false);
  const msg = award.messageFindings.results.find((r) => r.id === 'MSG-003');
  assert.equal(msg.pair.valid, false);
});
