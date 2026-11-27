import crypto from 'node:crypto';

/**
 * 两类“见证器”的纯函数实现。
 *
 * 1. PairMarkWitness：会话双方共享一把模拟私钥。HMAC 能证明记录未被篡改，
 *    但因为甲、乙都持有同一把钥，第三方不能把记号唯一归给某一方。
 * 2. PublicSealWitness：演示用 Ed25519 非对称署名。私钥只在本模拟终端中生成/使用，
 *    证据包只附公钥；任何第三方都能独立验签并确认署名主体。
 *
 * 这里没有接入任何真实账号、电子签或外部凭据。
 */

export const WITNESS = Object.freeze({
  PAIR: 'pair-mark/v1',
  PUBLIC: 'public-seal/v1',
});

/** 稳定 JSON：键排序，并与 JSON 序列化一致地忽略 undefined 字段。 */
export function canonical(value) {
  if (value === undefined) return 'null';
  if (value === null || typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map((v) => (v === undefined ? 'null' : canonical(v))).join(',')}]`;
  const keys = Object.keys(value)
    .filter((k) => value[k] !== undefined)
    .sort();
  return `{${keys.map((k) => `${JSON.stringify(k)}:${canonical(value[k])}`).join(',')}}`;
}

export function sha256(value) {
  return crypto.createHash('sha256').update(canonical(value)).digest('hex');
}

export function shortHash(hash, head = 10) {
  return typeof hash === 'string' ? `${hash.slice(0, head)}…${hash.slice(-6)}` : '';
}

export function newId(prefix) {
  return `${prefix}-${crypto.randomBytes(6).toString('hex').toUpperCase()}`;
}

/* ---------- 第一类：只有会话双方能核对的对称私记 ---------- */

export function newPairSecret(sessionId) {
  // 明确标注为演示密钥；它不是真实服务凭据。
  return `SIM-PAIR-${sessionId}-${crypto.randomBytes(12).toString('hex')}`;
}

export function pairMark(payload, pairSecret) {
  return crypto
    .createHmac('sha256', pairSecret)
    .update(canonical(payload))
    .digest('hex');
}

export function verifyPairMark(payload, mark, pairSecret) {
  if (!mark || !pairSecret) return false;
  let expected;
  try {
    expected = pairMark(payload, pairSecret);
  } catch {
    return false;
  }
  const a = Buffer.from(String(mark));
  const b = Buffer.from(expected);
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

/** 去掉会随记号/署名本身变化的字段，得到被认证的消息体。 */
export function messageAuthPayload(message) {
  const { pairMark, publicSeal, ...payload } = message;
  return payload;
}

/* ---------- 第二类：第三方可独立验真的具名公鉴署名 ---------- */

export function generatePartySeal(label) {
  const { privateKey, publicKey } = crypto.generateKeyPairSync('ed25519');
  const privateKeyPem = privateKey.export({ type: 'pkcs8', format: 'pem' });
  const publicKeyPem = publicKey.export({ type: 'spki', format: 'pem' });
  const keyId = newId(`KEY-${label}`);
  return {
    keyId,
    alg: 'Ed25519-SIMULATED',
    privateKeyPem,
    publicKeyPem,
    fingerprint: sha256(publicKeyPem),
  };
}

export function signPayload(payload, privateKeyPem) {
  const data = Buffer.from(canonical(payload));
  return crypto.sign(null, data, crypto.createPrivateKey(privateKeyPem)).toString('base64');
}

export function verifyPayloadSignature(payload, signatureBase64, publicKeyPem) {
  if (!signatureBase64 || !publicKeyPem) return false;
  try {
    const data = Buffer.from(canonical(payload));
    const sig = Buffer.from(String(signatureBase64), 'base64');
    return crypto.verify(null, data, crypto.createPublicKey(publicKeyPem), sig);
  } catch {
    return false;
  }
}

/** 审批/回单的验签载荷；署名只覆盖这些字段，不覆盖后续可变状态。 */
export function approvalSignaturePayload(approval) {
  return {
    docId: approval.docId,
    docHash: approval.docHash,
    party: approval.party,
    at: approval.at,
    channel: approval.channel,
    decision: approval.decision,
  };
}

export function signApproval(input, partyState) {
  const base = {
    docId: input.docId,
    docHash: input.docHash,
    party: input.party,
    at: input.at,
    channel: input.channel || 'terminal',
    decision: input.decision || 'accept',
  };
  const signature = signPayload(approvalSignaturePayload(base), partyState.seal.privateKeyPem);
  return {
    ...base,
    keyId: partyState.seal.keyId,
    publicKeyFingerprint: partyState.seal.fingerprint,
    signature,
  };
}

export function verifyApprovalSignature(approval, publicKeyPem) {
  if (!approval?.signature || !publicKeyPem) return false;
  return verifyPayloadSignature(approvalSignaturePayload(approval), approval.signature, publicKeyPem);
}
