import test from 'node:test';
import assert from 'node:assert/strict';

import {
  auditRelease,
  applyRedemption,
  createStation,
  issueApproval,
  issuePlanDocuments,
  publicShape,
  redeemDocument,
  addDocsToBook,
  verifyDocumentSeal,
} from '../src/core.mjs';
import {
  baselineV1,
  coincidenceV11,
  declassifiedV2,
  errorForkV3,
  makeFixedSupplementRule,
  seedApprovalForm,
  seedRecords,
} from '../src/presets.mjs';

async function approvedV2() {
  return issueApproval({
    ...seedApprovalForm,
    fields: seedApprovalForm.fieldsText.split(',').map((x) => x.trim()),
  });
}

test('基线：公开输入相同、私有值不同，公开张数/金额/错误回单完全一致', () => {
  const audit = auditRelease({
    template: baselineV1,
    records: seedRecords,
    recipientGroup: 'public-desk',
  });
  assert.equal(audit.ok, true);
  assert.equal(audit.comparisons.length, 2);

  const [a, b] = audit.comparisons;
  assert.equal(a.summary.count, 2);
  assert.equal(b.summary.count, 2);
  assert.equal(a.summary.totalAmount, 120);
  assert.equal(b.summary.totalAmount, 120);
  assert.deepEqual(a.summary.kinds, b.summary.kinds);
  assert.deepEqual(a.summary.errorCodes, []);
  assert.deepEqual(b.summary.errorCodes, []);

  const feeSlot = audit.classes[0].slots.find((s) => s.ruleId === 'fee');
  assert.equal(feeSlot.staticInvariant, true);
  assert.equal(feeSlot.status, 'release-invariant');

  // 馆内确实看到了不同的私有校对产物。
  assert.equal(a.privateCount, 1);
  assert.equal(b.privateCount, 1);
  const privateA = audit.plans['CASE-A'].privateArtifacts[0];
  const privateB = audit.plans['CASE-B'].privateArtifacts[0];
  assert.notEqual(privateA.docType, privateB.docType);
});

test('偶然同额的一对记录可被实际比较出来，但静态判定不承诺所有私有取值', () => {
  const shape = publicShape(coincidenceV11.rules.find((r) => r.id === 'fee').node.amount, seedRecords[0].public);
  assert.equal(shape.invariant, false);

  const audit = auditRelease({
    template: coincidenceV11,
    records: seedRecords,
    recipientGroup: 'public-desk',
  });
  const fee = audit.classes[0].slots.find((s) => s.ruleId === 'fee');
  assert.equal(fee.status, 'release-pair-equivalent');
  assert.equal(fee.staticInvariant, false);
  assert.deepEqual(audit.comparisons.map((c) => c.summary.totalAmount), [120, 120]);
});

test('另一版本的去密决定只对指定字段、模板版本和收件组生效', async () => {
  const approval = await approvedV2();

  const denied = auditRelease({
    template: declassifiedV2,
    records: seedRecords,
    approvals: [],
    recipientGroup: 'priority-group',
  });
  assert.equal(denied.classes[0].slots.find((s) => s.ruleId === 'fee').status, 'hold-divergent');

  const allowed = auditRelease({
    template: declassifiedV2,
    records: seedRecords,
    approvals: [approval],
    recipientGroup: 'priority-group',
  });
  const fee = allowed.classes[0].slots.find((s) => s.ruleId === 'fee');
  assert.equal(fee.status, 'release-declassified');
  assert.equal(fee.approvalId, approval.approvalId);
  assert.deepEqual(allowed.comparisons.map((c) => c.summary.totalAmount), [120, 80]);

  const wrongGroup = auditRelease({
    template: declassifiedV2,
    records: seedRecords,
    approvals: [approval],
    recipientGroup: 'public-desk',
  });
  assert.equal(wrongGroup.classes[0].slots.find((s) => s.ruleId === 'fee').status, 'hold-divergent');

  const wrongVersion = auditRelease({
    template: baselineV1,
    records: seedRecords,
    approvals: [approval],
    recipientGroup: 'priority-group',
  });
  // 核准单写的是 2.0.0，不能拿到 1.0.0 补件分叉上使用。
  assert.equal(wrongVersion.classes[0].slots.find((s) => s.ruleId === 'fee').status, 'release-invariant');
});

test('只给一个分支添公共补件回条时，无适用核准就必须在公共端消除差异', () => {
  const twisted = {
    ...baselineV1,
    rules: [...baselineV1.rules, makeFixedSupplementRule()],
  };
  const audit = auditRelease({
    template: twisted,
    records: seedRecords,
    recipientGroup: 'public-desk',
  });
  const supplement = audit.classes[0].slots.find((s) => s.ruleId === 'supplement');
  assert.equal(supplement.status, 'hold-divergent');

  for (const c of audit.comparisons) {
    assert.equal(c.summary.count, 2);
    assert.equal(c.summary.kinds.SUPPLEMENT_NOTICE, undefined);
    assert.ok(c.heldSlots.includes('supplement'));
  }
  // 受托端仍保留 A 的候选补件单，供回馆处理。
  assert.ok(audit.plans['CASE-A'].holds[0]);
});

test('私有错误回单分叉也不能从公共观察读出', () => {
  const audit = auditRelease({
    template: errorForkV3,
    records: seedRecords,
    recipientGroup: 'public-desk',
  });
  const errorSlot = audit.classes[0].slots.find((s) => s.ruleId === 'private-error');
  assert.equal(errorSlot.status, 'hold-divergent');
  for (const c of audit.comparisons) {
    assert.equal(c.summary.errorCount, 0);
    assert.deepEqual(c.summary.errorCodes, []);
  }
});

test('离线站只认上一版编号时，签章正确的新版投影也不能兑换；旧单可兑现且不可重复', async () => {
  const v1Audit = auditRelease({
    template: baselineV1,
    records: seedRecords,
    recipientGroup: 'public-desk',
  });
  const v1Docs = await issuePlanDocuments({
    audit: v1Audit,
    records: seedRecords,
    template: baselineV1,
    recipientGroup: 'public-desk',
    issuedAt: '2026-11-27T09:00:00.000Z',
  });
  assert.equal(v1Docs.length, 4); // 两条记录 × 受理回执 + 缴费单

  const v2Audit = auditRelease({
    template: declassifiedV2,
    records: seedRecords,
    recipientGroup: 'public-desk', // 无核准：金额槽扣留，但受理回执仍可签发
  });
  const v2Docs = await issuePlanDocuments({
    audit: v2Audit,
    records: seedRecords,
    template: declassifiedV2,
    recipientGroup: 'public-desk',
    issuedAt: '2026-11-27T10:00:00.000Z',
  });
  const v2Acceptance = v2Docs.find((d) => d.recordId === 'CASE-A' && d.docType === 'ACCEPTANCE_RECEIPT');
  assert.ok(v2Acceptance);
  assert.equal(await verifyDocumentSeal(v2Acceptance), true);

  let station = createStation({ id: 'public-desk', acceptedTemplateVersions: ['1.0.0'] });
  // 站点的离线底册只装入上一版 v1 编号。
  station = addDocsToBook(station, v1Docs);

  const rejected = await redeemDocument(station, v2Acceptance, '2026-11-27T11:00:00.000Z');
  assert.equal(rejected.ok, false);
  assert.equal(rejected.code, 'UNKNOWN_DOC_NUMBER');
  station = applyRedemption(station, rejected);

  const oldFee = v1Docs.find((d) => d.recordId === 'CASE-A' && d.docType === 'PAYMENT_ORDER');
  const ok = await redeemDocument(station, oldFee, '2026-11-27T11:05:00.000Z');
  assert.equal(ok.ok, true);
  station = applyRedemption(station, ok);

  const replay = await redeemDocument(station, oldFee, '2026-11-27T11:06:00.000Z');
  assert.equal(replay.ok, false);
  assert.equal(replay.code, 'ALREADY_REDEEMED');
  station = applyRedemption(station, replay);

  assert.equal(station.log.length, 3);
  // applyRedemption 使用 unshift，最新记录在最前。
  assert.deepEqual(station.log.map((e) => e.code), [
    'ALREADY_REDEEMED',
    'REDEEMED',
    'UNKNOWN_DOC_NUMBER',
  ]);
});

test('旧底册也会拒绝金额完全相同的新版缴费投影', async () => {
  const approval = await approvedV2();
  const v1Audit = auditRelease({ template: baselineV1, records: seedRecords, recipientGroup: 'priority-group' });
  const v1PriorityDocs = await issuePlanDocuments({
    audit: v1Audit,
    records: seedRecords,
    template: baselineV1,
    recipientGroup: 'priority-group',
  });

  const v2Audit = auditRelease({
    template: declassifiedV2,
    records: seedRecords,
    approvals: [approval],
    recipientGroup: 'priority-group',
  });
  const v2PriorityDocs = await issuePlanDocuments({
    audit: v2Audit,
    records: seedRecords,
    template: declassifiedV2,
    recipientGroup: 'priority-group',
  });

  const oldFee = v1PriorityDocs.find((d) => d.recordId === 'CASE-A' && d.docType === 'PAYMENT_ORDER');
  const newFee = v2PriorityDocs.find((d) => d.recordId === 'CASE-A' && d.docType === 'PAYMENT_ORDER');
  assert.equal(oldFee.amount, 120);
  assert.equal(newFee.amount, oldFee.amount);
  assert.notEqual(oldFee.docNumber, newFee.docNumber);

  let station = createStation({ id: 'priority-group', acceptedTemplateVersions: ['1.0.0'] });
  station = addDocsToBook(station, v1PriorityDocs);
  const result = await redeemDocument(station, newFee);
  assert.equal(result.ok, false);
  assert.equal(result.code, 'UNKNOWN_DOC_NUMBER');
});

test('未知节点类型会被发布器拒绝，不会被当作数据执行', () => {
  const bad = {
    id: 'bad',
    version: '9.9.9',
    rules: [{ id: 'r', node: { t: 'eval', code: 'process.exit()' } }],
  };
  const audit = auditRelease({ template: bad, records: seedRecords });
  assert.equal(audit.ok, false);
  assert.match(audit.templateErrors.join('\n'), /非法节点类型/);
});
