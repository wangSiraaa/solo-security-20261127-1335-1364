#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import { arbitratePackage, retrieveSignedContent } from '../src/arbiter.js';

function usage() {
  console.log(`用法:
  node scripts/verify-evidence.mjs <evidence-package.json> [--query ID_OR_HASH] [--award-out award.json]

该脚本不依赖谈单服务器或真实凭据；它只用证据包内的公钥/共享模拟钥独立重算并判定。`);
}

const file = process.argv[2];
if (!file || file.startsWith('--')) {
  usage();
  process.exit(file ? 0 : 2);
}
const args = process.argv.slice(3);
const queryIndex = args.indexOf('--query');
const query = queryIndex >= 0 ? args[queryIndex + 1] : null;
const awardOutIndex = args.indexOf('--award-out');
const awardOut = awardOutIndex >= 0 ? args[awardOutIndex + 1] : null;

const pkg = JSON.parse(fs.readFileSync(path.resolve(file), 'utf8'));
const award = arbitratePackage(pkg);

console.log(`证据包: ${award.packageId}`);
console.log(`清单完整性: ${award.packageIntegrity.valid ? 'PASS' : 'FAIL'}`);
console.log(`私聊归属: ${award.messageFindings.conclusion}`);
for (const c of award.correctionFindings) {
  console.log(
    `订正 ${c.id}: citation=${c.citation.valid ? 'PASS' : 'FAIL'} signatures=${c.validSignatureCount}/${c.approvals.length} effective=${c.independentlyEffective ? 'YES' : 'NO'} late=${c.lateApprovalCount}`,
  );
  console.log(`  -> ${c.effect}`);
}
for (const r of award.receiptFindings) {
  console.log(`回单 ${r.id}: source=${r.sourceEstablished ? 'VERIFIED' : 'FAILED'} originalAccepted=${r.provesOriginalAccepted ? 'YES' : 'NO'}`);
}
console.log(`未开工前提: ${award.scope.unstartedBasis.supported ? 'SUPPORTED' : 'REJECTED'} — ${award.scope.unstartedBasis.ruling}`);

if (query) {
  console.log('\n查回结果:');
  console.log(JSON.stringify(retrieveSignedContent(pkg, query), null, 2));
}
if (awardOut) fs.writeFileSync(path.resolve(awardOut), JSON.stringify(award, null, 2));
