// 规范化编码与标识：所有待签/待哈希对象先过 canonical JSON，保证跨机器字节一致。
import crypto from 'node:crypto';

export function canonical(value) {
  if (value === null || typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  const keys = Object.keys(value)
    .sort()
    .filter((k) => value[k] !== undefined); // undefined 键不参与签名，保证 JSON 往返稳定
  return '{' + keys.map((k) => JSON.stringify(k) + ':' + canonical(value[k])).join(',') + '}';
}

export function sha256(value) {
  return crypto.createHash('sha256').update(value).digest('hex');
}

export function digestJson(value) {
  return sha256(canonical(value));
}

// 名册截止页编号：内容定址，旧页只能被新页引用，不能改写。
export function pageId(page) {
  const { signature, attestations, intakeReceipts, mergeProof, ...body } = page;
  return sha256(canonical(body));
}

export function randomId(prefix) {
  return prefix + '_' + crypto.randomBytes(6).toString('hex');
}

export function nowIso() {
  return new Date().toISOString();
}
