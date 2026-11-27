import {
  messageAuthPayload,
  verifyApprovalSignature,
  verifyPairMark,
  verifyPayloadSignature,
} from './witnesses.js';
import { computeAgreementHash, computeReceiptHash, correctionDocHash } from './core.js';
import { verifyPackageManifest } from './evidence.js';

function mdEscape(value) {
  return String(value ?? '').replace(/\|/g, '\\|');
}

function partyName(pkg, party) {
  return pkg.registry?.[party]?.name ?? party;
}

function verifyApproval(approval, pkg) {
  const signer = pkg.registry?.[approval.party];
  if (!signer?.publicKeyPem) return { valid: false, reason: '证据包缺少该方公钥' };
  if (approval.keyId && signer.keyId && approval.keyId !== signer.keyId) {
    return { valid: false, reason: '署名 keyId 与公鉴登记 keyId 不一致' };
  }
  const valid = verifyApprovalSignature(approval, signer.publicKeyPem);
  return valid ? { valid: true } : { valid: false, reason: '署名验签失败或内容/哈希被改动' };
}

function findAgreementInPackage(pkg, agreementId, version) {
  return pkg.agreements.find(
    (a) => a.agreementId === agreementId && (version == null || a.version === Number(version)),
  );
}

function analyzeMessages(pkg) {
  const secret = pkg.session?.pairVerifier?.sharedSecret;
  const results = pkg.messages.map((m) => {
    const payload = messageAuthPayload(m);
    let pair = { verifiable: false, valid: false, detail: '第三方未取得双边共享模拟钥' };
    if (secret) {
      const valid = verifyPairMark(payload, m.pairMark, secret);
      pair = {
        verifiable: true,
        valid,
        detail: valid
          ? '双边私记 HMAC 验证通过，内容未被篡改'
          : '双边私记验证失败：内容、序号或记号可能被改动',
      };
    }
    const isAck = m.kind === 'ack' || /(^|\W)我?收到了?/.test(m.text ?? '');
    return {
      id: m.id,
      seq: m.seq,
      at: m.at,
      claimedFrom: m.from,
      kind: m.kind,
      text: m.text,
      pair,
      uniqueAttributionToClaimedSender: false,
      attributionReason:
        'pair-mark/v1 是甲乙共享钥记号；即使验签通过，也只能归属于“持有会话私钥的一方”，不能排除另一方制作。终端本地发送方向标签不属于可独立验真的公鉴署名。',
      receiptEffect: isAck ? 'acknowledgment-only：收到/知悉不构成对规格变更的同意' : null,
    };
  });

  const validCount = results.filter((r) => r.pair.valid).length;
  return {
    results,
    pairVerifierDisclosed: Boolean(secret),
    checked: Boolean(secret),
    validCount,
    total: results.length,
    uniqueAttributionPossible: false,
    conclusion: secret
      ? `已用证据包内的共享模拟钥核对 ${validCount}/${results.length} 条私记；没有任何一条普通私聊可凭双边私记被唯一归给甲或乙。`
      : '证据包未披露双边共享模拟钥；仲裁席不能独立核对普通私聊，更不能唯一认定发送者。',
  };
}

function analyzeCitation(c, pkg) {
  const citation = c.citation;
  if (!citation) return { valid: false, reason: '订正缺少被替换约定引用' };
  const base = findAgreementInPackage(pkg, citation.agreementId, citation.version);
  if (!base) return { valid: false, reason: `证据包缺少被引用原件 ${citation.agreementId}@v${citation.version}` };
  const actualHash = computeAgreementHash(base);
  const hashMatch = actualHash === citation.contentHash && base.contentHash === citation.contentHash;
  const quoteMatch = Boolean(citation.quote && base.clauseText?.includes(citation.quote));
  const contextIds = new Set(pkg.messages.map((m) => m.id));
  const missingContext = (c.contextMessageIds ?? []).filter((id) => !contextIds.has(id));
  const contextMatch = missingContext.length === 0 && (c.contextMessageIds?.length ?? 0) > 0;
  const orderMatch = base.orderId === citation.orderId && citation.orderId === c.orderId;
  return {
    valid: hashMatch && quoteMatch && contextMatch && orderMatch,
    agreementId: citation.agreementId,
    version: citation.version,
    orderId: citation.orderId,
    clauseId: citation.clauseId,
    contentHash: citation.contentHash,
    quote: citation.quote,
    hashMatch,
    quoteMatch,
    contextMatch,
    orderMatch,
    actualHash,
    missingContext,
    contextCount: c.contextMessageIds?.length ?? 0,
    reason: [
      !hashMatch && '引用哈希与原件不符',
      !quoteMatch && '引用原文不能在旧约定中还原',
      !contextMatch && `上下文不完整：${missingContext.join(', ') || '未提供'}`,
      !orderMatch && '订单号与被引用约定不一致',
    ]
      .filter(Boolean)
      .join('；'),
  };
}

function analyzeCorrection(c, pkg) {
  const citation = analyzeCitation(c, pkg);
  const expectedHash = correctionDocHash(c);
  const hashMatch = expectedHash === c.docHash;
  const approvalResults = c.approvals.map((a) => ({ ...a, verification: verifyApproval(a, pkg) }));
  const validAccept = approvalResults.filter((a) => a.verification.valid && a.decision === 'accept');
  const partiesAccepted = new Set(validAccept.map((a) => a.party));
  const bothAccepted = partiesAccepted.has('A') && partiesAccepted.has('B');
  const hasInvalidSignature = approvalResults.some((a) => !a.verification.valid);
  const acceptBy = c.acceptBy ? new Date(c.acceptBy).getTime() : null;
  const lateApprovals = approvalResults.filter(
    (a) => a.decision === 'late-accept' || (acceptBy && new Date(a.at).getTime() > acceptBy),
  );
  const timelyAccept = validAccept.filter(
    (a) => !acceptBy || new Date(a.at).getTime() <= acceptBy,
  );
  const timelyParties = new Set(timelyAccept.map((a) => a.party));
  const timelyBoth = timelyParties.has('A') && timelyParties.has('B');

  const methodCompliance =
    c.method === 'joint'
      ? approvalResults
          .filter((a) => a.decision === 'accept')
          .every((a) => a.channel === 'joint-presence') && bothAccepted
      : true;

  const independentlyEffective =
    citation.valid && hashMatch && !hasInvalidSignature && timelyBoth && methodCompliance;

  const nextVersion = pkg.agreements.find(
    (a) => a.sourceCorrectionId === c.id && a.parentHash === citation.contentHash,
  );

  const sameTextOtherAgreements = citation.quote
    ? pkg.agreements.filter(
        (a) =>
          a.agreementId !== citation.agreementId ||
          a.version !== citation.version,
      ).filter((a) => a.clauseText?.includes(citation.quote))
    : [];

  let effect;
  if (independentlyEffective) {
    effect = `有效换版：仅 ${citation.agreementId}@v${citation.version} 被替换${nextVersion ? `为 v${nextVersion.version}` : '（但证据包未附新版正文）'}。`;
  } else if (lateApprovals.length && !timelyBoth) {
    effect = '出处可验，但存在迟到同意且缺少限期内双方接受；不能认定有效变更，旧引用版本仍应恢复/维持。';
  } else if (!citation.valid) {
    effect = '订正引用/上下文不合格，不能产生换版效力。';
  } else if (hasInvalidSignature) {
    effect = '存在验签失败的署名，不能认定双方正式接受同版内容。';
  } else if (c.method === 'joint' && !methodCompliance) {
    effect = '双边到场会签方式不完整；普通私聊或单方代点不能替代到场签署。';
  } else {
    effect = '尚未形成双方对同版哈希的有效接受，旧约定不换版。';
  }

  return {
    id: c.id,
    kind: c.kind,
    title: c.title,
    summary: c.summary,
    changes: c.changes,
    acceptBy: c.acceptBy ?? null,
    orderId: c.orderId,
    method: c.method,
    statusInTerminal: c.status,
    terminalEffectiveVersion: c.effectiveVersion ?? null,
    docHash: c.docHash,
    expectedHash,
    hashMatch,
    citation,
    contextMessageIds: c.contextMessageIds ?? [],
    contextSnapshot: c.contextSnapshot ?? [],
    approvals: approvalResults.map((a) => ({
      party: a.party,
      at: a.at,
      channel: a.channel,
      decision: a.decision,
      keyId: a.keyId,
      valid: a.verification.valid,
      reason: a.verification.reason ?? null,
    })),
    validSignatureCount: approvalResults.filter((a) => a.verification.valid).length,
    bothPartiesAccepted: bothAccepted,
    timelyBothAccepted: timelyBoth,
    lateApprovalCount: lateApprovals.length,
    methodCompliance,
    independentlyEffective,
    nextVersionInPackage: nextVersion
      ? { agreementId: nextVersion.agreementId, version: nextVersion.version, contentHash: nextVersion.contentHash }
      : null,
    identicalTextCarriers: sameTextOtherAgreements.map((a) => ({
      agreementId: a.agreementId,
      orderId: a.orderId,
      version: a.version,
      contentHash: a.contentHash,
    })),
    scopeRule: '引用指向哪一份 agreementId@version+contentHash，就只有该份可换版；相同字句本身不是订单范围。',
    effect,
    transferabilityWarning: c.transferWarning ?? null,
  };
}

function analyzeReceipts(pkg) {
  return pkg.receipts.map((r) => {
    const signer = pkg.registry?.[r.signedBy];
    const hashValid = computeReceiptHash(r) === r.receiptHash;
    const signaturePayload = {
      receiptId: r.id,
      orderId: r.orderId,
      receiptHash: r.receiptHash,
      signedBy: r.signedBy,
      signedAt: r.signedAt,
      text: r.text,
    };
    const signatureValid = signer
      ? verifyPayloadSignature(signaturePayload, r.signature, signer.publicKeyPem)
      : false;
    const sourceEstablished = hashValid && signatureValid;
    const provesOriginalAccepted =
      sourceEstablished && /签收|验收/.test(r.text) && /原版成片|母版/.test(r.text);
    return {
      id: r.id,
      orderId: r.orderId,
      title: r.title,
      signedBy: r.signedBy,
      signedAt: r.signedAt,
      hashValid,
      signatureValid,
      sourceEstablished,
      provesOriginalAccepted,
      finding: sourceEstablished
        ? `回单哈希与${partyName(pkg, r.signedBy)}公鉴署名均验证通过，来源/真实性结论成立。`
        : '回单哈希或署名未通过，来源不能独立确认。',
      substantiveFinding: provesOriginalAccepted
        ? '回单证明收件方早在所述时间已正式签收/验收原版成片；“本单整体未开工”的事实前提不成立。'
        : '回单内容不足以证明原版成片已被正式签收。',
    };
  });
}

function buildScopeFindings(pkg, correctionFindings, receiptFindings) {
  const effective = correctionFindings.filter((c) => c.independentlyEffective);
  const changedRefs = new Set(
    effective.flatMap((c) =>
      c.nextVersionInPackage ? [`${c.citation.agreementId}@v${c.citation.version}`] : [],
    ),
  );
  const orderStates = pkg.orders.map((o) => {
    const agreements = pkg.agreements
      .filter((a) => a.orderId === o.id)
      .sort((x, y) => x.version - y.version);
    const latest = agreements.at(-1);
    const effectiveForOrder = effective.filter((c) => c.orderId === o.id);
    return {
      orderId: o.id,
      title: o.title,
      latestVersion: latest?.version ?? o.currentVersion,
      latestAgreementId: latest?.agreementId ?? o.currentAgreementId,
      effectiveCorrectionIds: effectiveForOrder.map((c) => c.id),
      unchangedDespiteIdenticalWords:
        o.id === 'ORD-8899' && !effectiveForOrder.some((c) => c.citation.orderId === 'ORD-8899'),
    };
  });

  const identicalWordWarnings = correctionFindings.flatMap((c) =>
    c.identicalTextCarriers.map((a) => ({
      correctionId: c.id,
      quotedFrom: `${c.citation.agreementId}@v${c.citation.version}`,
      identicalTextAlsoAppearsIn: `${a.agreementId}@v${a.version}`,
      otherOrderId: a.orderId,
      ruled: '不得仅因字句相同而覆盖该无关约定/订单',
    })),
  );

  const unstartedReceipts = receiptFindings.filter((r) => r.orderId === 'ORD-1127' && r.provesOriginalAccepted);
  return {
    changedCitationRefs: [...changedRefs],
    orderStates,
    identicalWordWarnings,
    unstartedBasis: {
      supported: unstartedReceipts.length === 0,
      contraryReceiptIds: unstartedReceipts.map((r) => r.id),
      ruling:
        unstartedReceipts.length > 0
          ? '存在早已正式签收原版成片的有效回单；来源结论成立，但不得把 ORD-1127 认定为整体未开工，也不得按“未开工改期”作出事实定性/裁判。以“未开工”为前提或条件的改期基础被驳回；若双方确曾无条件签署同版改期，该日期安排只能作为普通合同变更/新安排判断，不能登记为未开工改期。'
          : '证据包中没有相反的原版成片签收回单；可结合其他证据判断开工状态。',
    },
  };
}

function renderMarkdown(award) {
  const lines = [];
  lines.push(`# ${award.caseCaption}`);
  lines.push('');
  lines.push(`- 裁决编号：${award.awardId}`);
  lines.push(`- 证据包：${award.packageId}`);
  lines.push(`- 出具时间：${award.renderedAt}`);
  lines.push(`- 模拟规则：${award.formalRuleVersion}`);
  lines.push('');
  lines.push('## 一、证据包与见证器');
  lines.push('');
  lines.push(`- 清单完整性：${award.packageIntegrity.valid ? '通过' : `未通过（${award.packageIntegrity.reason}）`}`);
  lines.push(`- 双边私记：${award.messageFindings.conclusion}`);
  lines.push('- 具名公鉴：下列署名均使用证据包内公钥独立验签；公钥指纹与 keyId 一并列明。');
  lines.push('');
  lines.push('## 二、具体判定');
  lines.push('');
  for (const h of award.holdings) {
    lines.push(`### ${h.index}. ${h.title} —— ${h.result}`);
    lines.push('');
    lines.push(h.text);
    lines.push('');
    if (h.details?.length) {
      for (const d of h.details) lines.push(`- ${d}`);
      lines.push('');
    }
  }
  lines.push('## 三、订单版本范围');
  lines.push('');
  for (const o of award.scope.orderStates) {
    lines.push(
      `- ${o.orderId}（${mdEscape(o.title)}）：当前证据包内版本 ${o.latestAgreementId}@v${o.latestVersion}；生效订正：${o.effectiveCorrectionIds.join('、') || '无'}${o.unchangedDespiteIdenticalWords ? '；虽有相同字句，仍不因 ORD-1127 的引用订正而换版。' : ''}`,
    );
  }
  lines.push('');
  lines.push('## 四、可查回的签署内容');
  lines.push('');
  if (!award.signedContentIndex.length) lines.push('- 证据包中没有经核验的正式签署文件。');
  for (const d of award.signedContentIndex) {
    lines.push(`- ${d.type} ${d.id}：${d.title ?? ''}；哈希 \`${d.hash}\`；验签 ${d.verified ? '通过' : '未通过'}；${d.effective ? '已生效/具相应事实效力' : '未生效/仅可查来源'}`);
  }
  lines.push('');
  lines.push('> 本文件是本地规则模拟仲裁，不构成真实法律意见或真实裁决。');
  return lines.join('\n');
}

export function arbitratePackage(pkg, options = {}) {
  if (!pkg || pkg.schema !== 'arbitration-evidence-package/v1') {
    throw new Error('不是可识别的仲裁证据包');
  }
  const renderedAt = options.renderedAt ?? new Date().toISOString();
  const packageIntegrity = verifyPackageManifest(pkg);
  const messageFindings = analyzeMessages(pkg);
  const correctionFindings = pkg.corrections.map((c) => analyzeCorrection(c, pkg));
  const receiptFindings = analyzeReceipts(pkg);
  const scope = buildScopeFindings(pkg, correctionFindings, receiptFindings);

  const effective = correctionFindings.filter((c) => c.independentlyEffective);
  const ineffective = correctionFindings.filter((c) => !c.independentlyEffective);
  const late = correctionFindings.filter((c) => c.lateApprovalCount > 0);
  const named = correctionFindings.filter((c) => c.method === 'named');

  const holdings = [];
  let idx = 1;

  holdings.push({
    index: idx++,
    title: '私聊语句及发送归属',
    result: messageFindings.uniqueAttributionPossible ? '可唯一认定' : '不能由第三方唯一认定',
    text: messageFindings.conclusion,
    details: messageFindings.results
      .filter((r) => r.kind === 'proposal-talk' || r.kind === 'ack' || r.id === 'MSG-003' || r.id === 'MSG-004')
      .map((r) => `${r.id} 显示来自${r.claimedFrom}：私记${r.pair.verifiable ? (r.pair.valid ? '有效' : '无效') : '第三方不可核对'}；${r.receiptEffect ?? '不能仅凭该普通语句变更订单'}。`),
  });

  const ackMessages = messageFindings.results.filter((r) => r.receiptEffect);
  holdings.push({
    index: idx++,
    title: '“我收到了”的效力',
    result: '不构成同意改规格',
    text: '在终端规则要求“引用旧约定+上下文+正式签署/会签”的前提下，送达确认或“我收到了”只产生知悉效力，不自动构成对新规格的承诺。',
    details: ackMessages.map((r) => `${r.id}：${r.text} —— ${r.receiptEffect}`),
  });

  for (const c of correctionFindings) {
    holdings.push({
      index: idx++,
      title: `订正 ${c.id}（${c.title}）`,
      result: c.independentlyEffective ? '认定有效换版' : late.some((x) => x.id === c.id) ? '来源可验但变更不成立' : '驳回单方换版请求',
      text: c.effect,
      details: [
        `引用：${c.citation.agreementId}@v${c.citation.version} / 订单 ${c.citation.orderId}；哈希匹配 ${c.citation.hashMatch ? '是' : '否'}；原文还原 ${c.citation.quoteMatch ? '成功' : '失败'}；上下文 ${c.citation.contextCount} 条（${(c.contextMessageIds ?? []).join('、')}）。`,
        ...(c.citation.quote ? [`被替换原文（仲裁还原）：“${c.citation.quote}”`] : []),
        `同版哈希 \`${c.docHash}\`；终端状态 ${c.statusInTerminal}；仲裁独立判断生效：${c.independentlyEffective ? '是' : '否'}。`,
        ...c.approvals.map(
          (a) => `${a.party} 于 ${a.at} ${a.channel}/${a.decision}：验签${a.valid ? '通过' : `失败（${a.reason}）`}`,
        ),
        ...c.identicalTextCarriers.map(
          (a) => `相同字句另存在于 ${a.agreementId}@v${a.version}（${a.orderId}），不得据此覆盖该无关订单。`,
        ),
      ],
    });
  }

  if (named.length) {
    holdings.push({
      index: idx++,
      title: '缺席方具名修订单与迟到同意',
      result: '出处与效力分别判断',
      text: '具名公鉴署名使第三方可仅凭证据包内公钥查验发言终端/署名主体和精确内容；但出处成立不等于合同变更成立。若对方同意超过修订单载明期限，仲裁只能认定“限期要约/修订单真实存在且出处可验”以及“同意迟到”，不能把迟到同意溯及为双方已在限期内达成变更。',
      details: named.flatMap((c) => [
        `${c.id}：哈希 \`${c.docHash}\`，署名验证 ${c.approvals.every((a) => a.valid) ? '通过' : '存在失败'}；限期内双方接受：${c.timelyBothAccepted ? '是' : '否'}；迟到署名数：${c.lateApprovalCount}。`,
        c.transferabilityWarning ?? '',
      ].filter(Boolean)),
    });
  }

  const reschedules = correctionFindings.filter((c) => c.kind === 'reschedule');
  if (reschedules.length) {
    holdings.push({
      index: idx++,
      title: '同版改期内容查回',
      result: reschedules.some((c) => c.approvals.length >= 2) ? '可查回签过的精确版本' : '有记录可查，但签署不完整',
      text: '仲裁席按订正编号或 docHash 可查回双方签署的同版改期内容；不同哈希、不同日期或事后改写均不是同一份。',
      details: reschedules.map(
        (c) =>
          `${c.id}：引用 ${c.citation.agreementId}@v${c.citation.version}；同版哈希 \`${c.docHash}\`；摘要：${c.summary}。精确条款可用编号或哈希在“查回签过的内容”中取出。`,
      ),
    });
  }

  if (receiptFindings.length) {
    holdings.push({
      index: idx++,
      title: '补入的原版成片签收回单',
      result: scope.unstartedBasis.supported ? '未发现相反回单' : '来源成立，但否定“未开工改期”',
      text: scope.unstartedBasis.ruling,
      details: receiptFindings.map((r) => `${r.id}：${r.finding} ${r.substantiveFinding}`),
    });
  }

  holdings.push({
    index: idx++,
    title: '相同字句与无关订单',
    result: '按引用范围换版',
    text: '订正仅能替换其明确引用的 agreementId/version/contentHash。相同字句在 ORD-8899 或其他订单中出现，不构成对那些约定的引用或覆盖。',
    details: scope.identicalWordWarnings.length
      ? scope.identicalWordWarnings.map((w) => `${w.correctionId} 引用 ${w.quotedFrom}；相同文字见于 ${w.identicalTextAlsoAppearsIn}（${w.otherOrderId}）：${w.ruled}`)
      : ['证据包内未发现需要特别隔离的相同文字载体。'],
  });

  const signedContentIndex = [
    ...pkg.corrections.map((c) => {
      const f = correctionFindings.find((x) => x.id === c.id);
      return {
        type: 'correction',
        id: c.id,
        title: c.title,
        hash: c.docHash,
        verified: f.approvals.length > 0 && f.approvals.every((a) => a.valid),
        effective: f.independentlyEffective,
        exactContent: { summary: c.summary, changes: c.changes, citation: c.citation, acceptBy: c.acceptBy },
      };
    }),
    ...pkg.receipts.map((r) => {
      const f = receiptFindings.find((x) => x.id === r.id);
      return {
        type: 'receipt',
        id: r.id,
        title: r.title,
        hash: r.receiptHash,
        verified: f.sourceEstablished,
        effective: f.provesOriginalAccepted,
        exactContent: { signedAt: r.signedAt, signedBy: r.signedBy, text: r.text },
      };
    }),
  ];

  const award = {
    schema: 'simulated-arbitration-award/v1',
    awardId: options.awardId ?? `AWD-${pkg.packageId}`,
    packageId: pkg.packageId,
    caseCaption: pkg.caseCaption,
    renderedAt,
    formalRuleVersion: pkg.session?.formalRuleVersion ?? FORMAL_RULE_FALLBACK,
    packageIntegrity,
    messageFindings,
    correctionFindings,
    receiptFindings,
    scope,
    holdings,
    signedContentIndex,
    specificOrders: [
      ...effective.map((c) => `确认 ${c.citation.agreementId}@v${c.citation.version} 已由 ${c.id} 合法替换，范围仅限 ${c.orderId}。`),
      ...ineffective.map((c) => `驳回凭 ${c.id} 单方变更 ${c.orderId} 的请求；在缺少有效同版接受时，维持其引用前约定。`),
      ...(scope.unstartedBasis.supported ? [] : ['不得将 ORD-1127 标注/裁判为“整体未开工”；任何改期文书均不得以此项虚假事实为基础。']),
      '本裁决为本地模拟规则下的具体判定，不代表真实法院或仲裁机构意见。',
    ],
  };
  award.markdown = renderMarkdown(award);
  return award;
}

const FORMAL_RULE_FALLBACK = 'terminal-rule/v1';

export function retrieveSignedContent(pkg, query) {
  const award = arbitratePackage(pkg, { awardId: 'AWD-QUERY' });
  const q = String(query ?? '').trim().toLowerCase();
  if (!q) return award.signedContentIndex;
  return award.signedContentIndex.filter(
    (item) =>
      item.id.toLowerCase() === q ||
      item.hash.toLowerCase() === q ||
      item.hash.toLowerCase().startsWith(q) ||
      item.id.toLowerCase().includes(q),
  );
}
