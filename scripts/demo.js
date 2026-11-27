import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  OLD_SPEC_SENTENCE,
  approveCorrection,
  createCorrection,
  createState,
  exportTranscript,
} from '../src/core.js';
import { buildEvidencePackage, finalizePackage } from '../src/evidence.js';
import { arbitratePackage, retrieveSignedContent } from '../src/arbiter.js';
import { transcriptMarkdown } from '../public/common.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.join(__dirname, '..', 'artifacts');
fs.mkdirSync(OUT, { recursive: true });

function write(name, content) {
  const file = path.join(OUT, name);
  fs.writeFileSync(file, typeof content === 'string' ? content : JSON.stringify(content, null, 2));
  console.log('  ✓', path.relative(process.cwd(), file));
  return file;
}

function writeAward(prefix, pkg) {
  const award = arbitratePackage(pkg, { renderedAt: '2026-11-26T21:30:00+08:00' });
  write(`${prefix}-award.json`, award);
  write(`${prefix}-award.md`, award.markdown);
  return award;
}

function transcriptFiles(prefix, state) {
  for (const party of ['A', 'B']) {
    const transcript = exportTranscript(state, party, { includePairVerifier: true });
    write(`${prefix}-transcript-${party}.json`, transcript);
    write(`${prefix}-transcript-${party}.md`, transcriptMarkdown(state, party));
  }
}

const context = ['MSG-002', 'MSG-003', 'MSG-004', 'MSG-005', 'MSG-006'];
const specChanges = {
  terms: {
    resolution: '1920x1080',
    fps: 25,
    codec: 'H.264 MP4 审片版；4K ProRes 422 HQ 母版延后',
    masterDueAt: '2026-11-28T12:00:00+08:00',
  },
  clauseText:
    '双方确认：2026-11-27T18:00:00+08:00 前先交付 1920×1080、25fps、H.264 MP4 审片版；3840×2160/ProRes 422 HQ 母版改于 2026-11-28T12:00:00+08:00 前交付。本订正仅替换 AGR-1127-SPEC@v1 中与之冲突的交片规格。',
  title: '交片规格订正（引用换版）',
};

console.log('\n[1/5] 甲在场：同一轮谈单 + 双边到场会签');
{
  const state = createState({ scenario: 'present' });
  createCorrection(state, {
    id: 'CR-PRESENT-001',
    kind: 'spec',
    orderId: 'ORD-1127',
    agreementId: 'AGR-1127-SPEC',
    version: 1,
    quote: OLD_SPEC_SENTENCE,
    contextMessageIds: context,
    summary: '甲在场，双方对同一引用订正和同一 docHash 完成会签：先交 1080p 审片版，4K 母版延后。',
    changes: specChanges,
    method: 'joint',
    proposedBy: 'A',
    at: '2026-11-26T20:08:00+08:00',
  });
  approveCorrection(state, {
    correctionId: 'CR-PRESENT-001',
    party: 'B',
    at: '2026-11-26T20:09:00+08:00',
    channel: 'joint-presence',
  });

  transcriptFiles('present', state);
  const pkg = buildEvidencePackage(state, {
    packageId: 'PKG-PRESENT-001',
    preparedBy: 'A+B',
    at: '2026-11-26T20:12:00+08:00',
    includePairVerifier: true,
    receiptIds: [],
    title: '甲在场双边会签证据包',
    claims: [{ type: 'confirm-effective-correction', correctionId: 'CR-PRESENT-001' }],
  });
  write('present-package.json', pkg);
  writeAward('present', pkg);
}

console.log('\n[2/5] 甲失联：乙只能拿出双边私聊和失败的代签尝试');
{
  const state = createState({ scenario: 'missing' });
  let absentError = null;
  createCorrection(state, {
    id: 'CR-MISSING-JOINT-ATTEMPT',
    kind: 'spec',
    orderId: 'ORD-1127',
    agreementId: 'AGR-1127-SPEC',
    version: 1,
    quote: OLD_SPEC_SENTENCE,
    contextMessageIds: context,
    summary: '乙在甲失联后试图把同样的规格改动做成双边会签；只有乙一方签署。',
    changes: specChanges,
    method: 'joint',
    proposedBy: 'B',
    at: '2026-11-26T20:09:00+08:00',
  });
  try {
    approveCorrection(state, {
      correctionId: 'CR-MISSING-JOINT-ATTEMPT',
      party: 'A',
      at: '2026-11-26T20:10:00+08:00',
      channel: 'joint-presence',
    });
  } catch (err) {
    absentError = { code: err.code, message: err.message };
  }

  transcriptFiles('missing', state);
  write('missing-absent-sign-attempt.json', { rejected: true, ...absentError });

  const pkg = buildEvidencePackage(state, {
    packageId: 'PKG-MISSING-PAIR-001',
    preparedBy: 'B',
    at: '2026-11-26T20:20:00+08:00',
    includePairVerifier: true,
    correctionIds: ['CR-MISSING-JOINT-ATTEMPT'],
    receiptIds: [],
    title: '甲失联后的双边私聊/单方会签尝试包',
    claims: [
      { type: 'attribute-message', messageId: 'MSG-003', targetParty: 'A' },
      { type: 'enforce-correction', correctionId: 'CR-MISSING-JOINT-ATTEMPT' },
    ],
  });
  write('missing-pair-only-package.json', pkg);
  writeAward('missing-pair-only', pkg);
}

console.log('\n[3/5] 甲失联：具名修订单出处可验，但乙 21:14 才接受（期限 21:00）');
{
  const state = createState({ scenario: 'missing' });
  approveCorrection(state, {
    correctionId: 'CR-NAMED-001',
    party: 'B',
    at: '2026-11-26T21:14:00+08:00',
    channel: 'named-terminal',
  });

  const pkg = buildEvidencePackage(state, {
    packageId: 'PKG-MISSING-NAMED-LATE-001',
    preparedBy: 'B',
    at: '2026-11-26T21:16:00+08:00',
    includePairVerifier: true,
    correctionIds: ['CR-NAMED-001'],
    messageIds: state.messages.map((m) => m.id),
    receiptIds: [],
    title: '甲具名修订单真实但乙接受迟到的证据包',
    claims: [
      { type: 'verify-named-source', correctionId: 'CR-NAMED-001' },
      { type: 'late-acceptance', correctionId: 'CR-NAMED-001' },
    ],
  });
  write('missing-named-late-package.json', pkg);
  writeAward('missing-named-late', pkg);
}

console.log('\n[4/5] 双方按选定的双边会签办法接受同版改期；仲裁查回签过的内容');
let reschedulePackage;
{
  const state = createState({ scenario: 'present' });
  createCorrection(state, {
    id: 'RSK-1127-001',
    kind: 'reschedule',
    title: 'ORD-1127 同版改期单',
    orderId: 'ORD-1127',
    agreementId: 'AGR-1127-SPEC',
    version: 1,
    quote: OLD_SPEC_SENTENCE,
    contextMessageIds: ['MSG-002', 'MSG-003', 'MSG-004', 'MSG-006'],
    summary: '双方对同一个 RSK-1127-001 docHash 接受：交付时间改至 2026-11-29 18:00；申请措辞称“未开工”，该事实另行用回单检验。',
    changes: {
      clauseText:
        '双方同意 ORD-1127 项下交付时间改至 2026-11-29T18:00:00+08:00；交片规格仍按引用版本执行。本改期单仅替换 AGR-1127-SPEC@v1 中的交付时间/日期条款。',
      terms: { dueAt: '2026-11-29T18:00:00+08:00' },
      schedule: { dueAt: '2026-11-29T18:00:00+08:00', claimedBasis: '本单尚未开工' },
      basis: '申请人称按“未开工”改期；开工/签收状态由 RCPT-ORIG-001 等证据独立判断。',
      title: 'ORD-1127 同版改期单',
    },
    method: 'joint',
    proposedBy: 'A',
    at: '2026-11-26T20:15:00+08:00',
  });
  approveCorrection(state, {
    correctionId: 'RSK-1127-001',
    party: 'B',
    at: '2026-11-26T20:16:00+08:00',
    channel: 'joint-presence',
  });

  // 安全测试：相同字句 + 指向无关订单号，但引用仍是 ORD-1127，核心必须拒绝订单错配。
  let scopeRejection = null;
  try {
    createCorrection(state, {
      id: 'CR-SCOPE-INVALID',
      kind: 'spec',
      orderId: 'ORD-8899',
      agreementId: 'AGR-1127-SPEC',
      version: 2,
      quote: state.agreements.find((a) => a.agreementId === 'AGR-1127-SPEC' && a.version === 2).clauseText,
      contextMessageIds: ['MSG-003'],
      summary: '试图让 ORD-8899 搭相同字句便车换版。',
      changes: specChanges,
      method: 'joint',
      proposedBy: 'B',
      at: '2026-11-26T20:17:00+08:00',
    });
  } catch (err) {
    scopeRejection = { rejected: true, code: err.code ?? 'SCOPE_MISMATCH', message: err.message };
  }
  write('scope-test.json', {
    rule: 'Only the cited agreementId/version/hash may be superseded; identical words cannot target an unrelated order.',
    invalidAttempt: scopeRejection,
    unrelatedOrderAfterValidReschedule: state.orders.find((o) => o.id === 'ORD-8899'),
    unrelatedAgreement: state.agreements.find((a) => a.agreementId === 'AGR-8899-SPEC'),
  });

  reschedulePackage = buildEvidencePackage(state, {
    packageId: 'PKG-RESCHEDULE-001',
    preparedBy: 'A+B',
    at: '2026-11-26T20:20:00+08:00',
    includePairVerifier: true,
    correctionIds: ['RSK-1127-001'],
    messageIds: ['MSG-001', 'MSG-002', 'MSG-003', 'MSG-004', 'MSG-006'],
    receiptIds: [],
    title: '双方接受同版改期的证据包（未补回单）',
  });
  write('reschedule-package.json', reschedulePackage);
  const award = writeAward('reschedule', reschedulePackage);
  write('reschedule-query.json', retrieveSignedContent(reschedulePackage, 'RSK-1127-001'));
  write('scope-test-award.json', award);
}

console.log('\n[5/5] 还原同一引用，只补入早已签署的原版成片回单');
{
  const state = createState({ scenario: 'present' });
  createCorrection(state, {
    id: 'RSK-RECEIPT-EDGE-001',
    kind: 'reschedule',
    title: '同版改期单（用于回单边界演练）',
    orderId: 'ORD-1127',
    agreementId: 'AGR-1127-SPEC',
    version: 1,
    quote: OLD_SPEC_SENTENCE,
    contextMessageIds: ['MSG-002', 'MSG-003', 'MSG-004', 'MSG-006'],
    summary: '同版改期措辞称“未开工”；随后只补入一份早就存在的原版成片签收回单。',
    changes: {
      clauseText: '双方同意 ORD-1127 交付时间改至 2026-11-29T18:00:00+08:00；本单申请理由写作“整体未开工”，但该事实由回单检验。',
      terms: { dueAt: '2026-11-29T18:00:00+08:00' },
      schedule: { dueAt: '2026-11-29T18:00:00+08:00', claimedBasis: '整体未开工' },
      title: '同版改期单（回单边界）',
    },
    method: 'named',
    proposedBy: 'A',
    at: '2026-11-26T20:21:00+08:00',
    acceptBy: '2026-11-26T20:40:00+08:00',
  });
  approveCorrection(state, {
    correctionId: 'RSK-RECEIPT-EDGE-001',
    party: 'B',
    at: '2026-11-26T20:25:00+08:00',
    channel: 'named-terminal',
  });

  const pkg = buildEvidencePackage(state, {
    packageId: 'PKG-RECEIPT-EDGE-001',
    preparedBy: 'B',
    at: '2026-11-26T20:30:00+08:00',
    includePairVerifier: true,
    correctionIds: ['RSK-RECEIPT-EDGE-001'],
    messageIds: ['MSG-001', 'MSG-002', 'MSG-003', 'MSG-004', 'MSG-006'],
    receiptIds: ['RCPT-ORIG-001'],
    supplementalNote: '引用、上下文、订正哈希和消息均保持不变；本补充包只新增 RCPT-ORIG-001。',
    title: '还原引用并仅补入早先原版成片签收回单',
  });
  pkg.supplemental = {
    addsOnly: ['RCPT-ORIG-001'],
    doesNotChange: ['citation', 'contextMessageIds', 'correction.docHash', 'messages', 'registry'],
    citationRestored: true,
  };
  finalizePackage(pkg);
  write('receipt-edge-package.json', pkg);
  writeAward('receipt-edge', pkg);
  write('receipt-query.json', retrieveSignedContent(pkg, 'RCPT-ORIG-001'));
}

console.log('\nDemo artifacts written to /artifacts. Run: npm start\n');
