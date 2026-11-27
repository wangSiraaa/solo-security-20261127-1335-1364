import { newId, sha256 } from './witnesses.js';
import { publicRegistry } from './core.js';

export const PACKAGE_SCHEMA = 'arbitration-evidence-package/v1';

function packageContents(pkg) {
  const { manifest, ...contents } = pkg;
  return contents;
}

export function finalizePackage(pkg) {
  pkg.manifest = {
    schema: PACKAGE_SCHEMA,
    packageId: pkg.packageId,
    contentsHash: sha256(packageContents(pkg)),
    hashAlgorithm: 'SHA-256/canonical-json',
    generatedAt: pkg.generatedAt,
  };
  return pkg;
}

/**
 * 从终端状态中抽取可交给第三方的自包含证据包。
 * includePairVerifier=true 时会把双边共享模拟钥放入包内；仲裁可验 HMAC，
 * 但仍因钥由双方持有而不能唯一归属发送者。
 */
export function buildEvidencePackage(state, options = {}) {
  const at = options.at ?? new Date().toISOString();
  const messageIds = options.messageIds ?? state.messages.map((m) => m.id);
  const correctionIds = options.correctionIds ?? state.corrections.map((c) => c.id);
  const receiptIds = options.receiptIds ?? (options.includeAllReceipts ? state.receipts.map((r) => r.id) : (options.receiptIds ?? []));
  const agreementIds = options.agreementIds ?? null;

  const messages = messageIds.map((id) => {
    const m = state.messages.find((x) => x.id === id);
    if (!m) throw new Error(`证据包消息不存在：${id}`);
    return m;
  });
  const corrections = correctionIds.map((id) => {
    const c = state.corrections.find((x) => x.id === id);
    if (!c) throw new Error(`证据包订正不存在：${id}`);
    return c;
  });
  const receipts = receiptIds.map((id) => {
    const r = state.receipts.find((x) => x.id === id);
    if (!r) throw new Error(`证据包回单不存在：${id}`);
    return r;
  });

  let agreements;
  if (agreementIds) {
    agreements = agreementIds.map((ref) => {
      const [agreementId, versionRaw] = String(ref).split('@v');
      const version = versionRaw ? Number(versionRaw) : undefined;
      const a = state.agreements.find(
        (x) => x.agreementId === agreementId && (version == null || x.version === version),
      );
      if (!a) throw new Error(`证据包约定不存在：${ref}`);
      return a;
    });
  } else {
    agreements = state.agreements;
  }

  const orderIds = new Set([
    ...(options.orderIds ?? state.orders.map((o) => o.id)),
    ...agreements.map((a) => a.orderId),
    ...corrections.map((c) => c.orderId),
    ...receipts.map((r) => r.orderId),
  ]);
  const orders = state.orders.filter((o) => orderIds.has(o.id));

  const pkg = {
    schema: PACKAGE_SCHEMA,
    packageId: options.packageId ?? newId('PKG'),
    caseCaption: options.caseCaption ?? '拾光录制室 v. 声浪录制室：ORD-1127 交片规格/改期争议（模拟）',
    preparedBy: options.preparedBy ?? 'B',
    preparedByTerminal: options.preparedByTerminal ?? `terminal-${options.preparedBy ?? 'B'}`,
    generatedAt: at,
    title: options.title ?? '谈单终端证据包',
    supplementalNote: options.supplementalNote ?? null,
    claims: options.claims ?? [],
    session: {
      id: state.session.id,
      formalRuleVersion: state.session.formalRuleVersion,
      pairWitness: state.session.pairWitness,
      pairWitnessDescription: state.session.pairWitnessDescription,
      pairVerifier: options.includePairVerifier
        ? {
            scheme: state.session.pairWitness,
            algorithm: 'HMAC-SHA256',
            sharedSecret: state.session.pairSecret,
            caveat:
              '该模拟钥由甲乙双方共同持有；验证成功只能证明持有同一钥的一方制作且内容未被篡改，不能唯一认定是甲或乙发送。',
          }
        : {
            scheme: state.session.pairWitness,
            availableOnlyTo: ['A', 'B'],
            caveat: '未向第三方披露共享模拟钥；仲裁席不能独立核对普通私聊记号。',
          },
    },
    registry: publicRegistry(state),
    orders,
    agreements,
    messages,
    corrections,
    receipts,
    terminalExportNotice:
      '终端界面上的发送方向标签来自本地会话状态；密码学记号的对外归属力以本包验证结论为准。',
  };

  return finalizePackage(pkg);
}

export function verifyPackageManifest(pkg) {
  if (!pkg?.manifest?.contentsHash) return { valid: false, reason: '缺少清单哈希' };
  const expected = sha256(packageContents(pkg));
  return {
    valid: expected === pkg.manifest.contentsHash,
    expected,
    actual: pkg.manifest.contentsHash,
  };
}
