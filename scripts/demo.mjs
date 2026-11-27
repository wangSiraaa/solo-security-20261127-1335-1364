// 零依赖端到端演示：npm run demo
import {
  addDocsToBook,
  applyRedemption,
  auditRelease,
  createStation,
  issueApproval,
  issuePlanDocuments,
  redeemDocument,
} from '../src/core.mjs';
import {
  baselineV1,
  declassifiedV2,
  makeFixedSupplementRule,
  seedApprovalForm,
  seedRecords,
} from '../src/presets.mjs';

const hr = '─'.repeat(78);

function printTitle(text) {
  console.log(`\n${hr}\n${text}\n${hr}`);
}

function printComparison(audit) {
  for (const c of audit.comparisons) {
    console.log(
      `${c.recordId}  张数=${c.summary.count}  总金额=${c.summary.totalAmount}  ` +
      `种类=${JSON.stringify(c.summary.kinds)}  错误回单=${c.summary.errorCount} ` +
      `代码=${JSON.stringify(c.summary.errorCodes)}  扣留槽位=${JSON.stringify(c.heldSlots)}`,
    );
  }
}

async function redeemAndPrint(station, doc, at) {
  const result = await redeemDocument(station, doc, at);
  console.log(`  兑换 ${doc.docNumber} [${doc.docType}/${doc.amount}] => ${result.code}：${result.reason}`);
  return applyRedemption(station, result);
}

printTitle('步骤 1｜两份公开输入相同、私有值不同的记录，在馆内跑 v1 模板');
console.log('公开输入：', JSON.stringify(seedRecords[0].public));
console.log('私有 A：', JSON.stringify(seedRecords[0].private));
console.log('私有 B：', JSON.stringify(seedRecords[1].private));

let audit = auditRelease({
  template: baselineV1,
  records: seedRecords,
  recipientGroup: 'public-desk',
});
printComparison(audit);
console.log('槽位判定：');
for (const s of audit.classes[0].slots) {
  console.log(`- ${s.ruleId.padEnd(18)} ${s.status.padEnd(24)} static=${s.staticInvariant}  ${s.reason}`);
}
console.log('馆内私有产物（不出信任边界）：');
for (const id of Object.keys(audit.plans)) {
  for (const p of audit.plans[id].privateArtifacts) {
    console.log(`- ${id}: ${p.docType} amount=${p.amount}`);
  }
}

printTitle('步骤 2｜实际签发 v1 公开投影，社区站装入离线底册并兑换');
const v1Docs = await issuePlanDocuments({
  audit,
  records: seedRecords,
  template: baselineV1,
  recipientGroup: 'public-desk',
  issuedAt: '2026-11-27T09:00:00+08:00',
});
console.log(`馆内签发 ${v1Docs.length} 张公开单：`);
for (const d of v1Docs) console.log(`- ${d.docNumber}  ${d.docType}  amount=${d.amount}  seal=${d.seal.slice(0, 12)}…`);

let publicStation = createStation({ id: 'public-desk', name: '栖霞社区公开站', acceptedTemplateVersions: ['1.0.0'] });
publicStation = addDocsToBook(publicStation, v1Docs);
const feeA = v1Docs.find((d) => d.recordId === 'CASE-A' && d.docType === 'PAYMENT_ORDER');
publicStation = await redeemAndPrint(publicStation, feeA, '2026-11-27T09:20:00+08:00');

// 限定收件组也有一套 v1 旧编号，供最后演示版本底册轮换。
const v1PriorityDocs = await issuePlanDocuments({
  audit,
  records: seedRecords,
  template: baselineV1,
  recipientGroup: 'priority-group',
  issuedAt: '2026-11-27T09:05:00+08:00',
});

printTitle('步骤 3｜换 v2 模板，独立核准席实际签发限定收件组的去密决定');
const approval = await issueApproval({
  ...seedApprovalForm,
  fields: seedApprovalForm.fieldsText.split(',').map((x) => x.trim()),
}, '2026-11-27T09:30:00+08:00');
console.log('核准单：');
console.log({
  approvalId: approval.approvalId,
  fields: approval.fields,
  template: `${approval.templateId}@${approval.templateVersion}`,
  recipientGroup: approval.recipientGroup,
  issuedBy: approval.issuedBy,
  seal: `${approval.seal.slice(0, 16)}…`,
});

const denied = auditRelease({ template: declassifiedV2, records: seedRecords, recipientGroup: 'priority-group' });
console.log('无核准 / 错误收件组时 fee 槽：', denied.classes[0].slots.find((s) => s.ruleId === 'fee').status);

const allowed = auditRelease({
  template: declassifiedV2,
  records: seedRecords,
  approvals: [approval],
  recipientGroup: 'priority-group',
});
console.log('有范围匹配核准后：');
printComparison(allowed);
const v2PriorityDocs = await issuePlanDocuments({
  audit: allowed,
  records: seedRecords,
  template: declassifiedV2,
  recipientGroup: 'priority-group',
  issuedAt: '2026-11-27T09:40:00+08:00',
});
for (const d of v2PriorityDocs.filter((d) => d.docType === 'PAYMENT_ORDER')) {
  console.log(`- 限定组签发 ${d.docNumber} amount=${d.amount} approval=${d.approvalId}`);
}

printTitle('步骤 4｜只给 A 添一张公共补件回条；核准缺席时公共端不得显出差异');
const supplementTemplate = {
  ...baselineV1,
  rules: [...baselineV1.rules, makeFixedSupplementRule()],
};
const blocked = auditRelease({
  template: supplementTemplate,
  records: seedRecords,
  recipientGroup: 'public-desk',
  ignoreApprovals: true,
});
printComparison(blocked);
const supplementSlot = blocked.classes[0].slots.find((s) => s.ruleId === 'supplement');
console.log(`补件槽状态：${supplementSlot.status}；原因：${supplementSlot.reason}`);
console.log(`馆内 A 仍可见候选/扣留原因：${blocked.plans['CASE-A'].holds.some((h) => h.ruleId === 'supplement')}`);
console.log(`公共端 A/B 张数仍相同，且都没有 SUPPLEMENT_NOTICE。`);

printTitle('步骤 5｜移除补件分叉，恢复两边一致');
audit = auditRelease({ template: baselineV1, records: seedRecords, recipientGroup: 'public-desk' });
printComparison(audit);

printTitle('步骤 6｜站点只装入/认可上一版 v1 编号；正确的 v2 投影也不能兑换');

let priorityStation = createStation({
  id: 'priority-group',
  name: '限定收件组离线站',
  acceptedTemplateVersions: ['1.0.0'],
});
priorityStation = addDocsToBook(priorityStation, v1PriorityDocs);
const v2FeeA = v2PriorityDocs.find((d) => d.recordId === 'CASE-A' && d.docType === 'PAYMENT_ORDER');
console.log(`A 的 v1/v2 金额均为 ${v2FeeA.amount}，但新版编号未进入旧底册：`);
priorityStation = await redeemAndPrint(priorityStation, v2FeeA, '2026-11-27T10:05:00+08:00');

const v2PublicAudit = auditRelease({
  template: declassifiedV2,
  records: seedRecords,
  recipientGroup: 'public-desk', // fee 被扣留，但 acceptance 是正确的 v2 公共投影
});
const v2PublicDocs = await issuePlanDocuments({
  audit: v2PublicAudit,
  records: seedRecords,
  template: declassifiedV2,
  recipientGroup: 'public-desk',
  issuedAt: '2026-11-27T10:00:00+08:00',
});
const v2Acceptance = v2PublicDocs.find((d) => d.recordId === 'CASE-A' && d.docType === 'ACCEPTANCE_RECEIPT');
console.log(`v2 投影签章正确：${v2Acceptance.docNumber}`);
publicStation = await redeemAndPrint(publicStation, v2Acceptance, '2026-11-27T10:10:00+08:00');

console.log('\n公开站兑现记录：');
for (const e of publicStation.log) {
  console.log(`- ${e.at} ${e.docNumber} => ${e.code} ${e.reason}`);
}
console.log('\n限定收件组站兑现记录：');
for (const e of priorityStation.log) {
  console.log(`- ${e.at} ${e.docNumber} => ${e.code} ${e.reason}`);
}

console.log(`\n${hr}\n演示完成：测试请运行 npm test；交互界面请运行 npm start。\n`);
