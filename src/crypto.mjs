// 成熟密码库 node:crypto（OpenSSL）的薄封装。
// 签名/名册页用 Ed25519；封缄正文用 X25519 ECDH + HKDF-SHA256 + AES-256-GCM。
import crypto from 'node:crypto';
import { canonical, digestJson } from './util.mjs';

// ---------- 钥匙 ----------
export function generateSigningKeyPair() {
  const { publicKey, privateKey } = crypto.generateKeyPairSync('ed25519');
  return {
    publicSpki: exportPublic(publicKey),
    privatePkcs8: exportPrivate(privateKey),
  };
}

export function generateEncryptionKeyPair() {
  const { publicKey, privateKey } = crypto.generateKeyPairSync('x25519');
  return {
    publicSpki: exportPublic(publicKey),
    privatePkcs8: exportPrivate(privateKey),
  };
}

export function exportPublic(keyObject) {
  // 私钥对象不能直接导出 SPKI，先转成公钥对象
  const pub = keyObject.type === 'private' ? crypto.createPublicKey(keyObject) : keyObject;
  return pub.export({ format: 'der', type: 'spki' }).toString('base64');
}

export function exportPrivate(keyObject) {
  return keyObject.export({ format: 'der', type: 'pkcs8' }).toString('base64');
}

export function parsePublic(spkiB64) {
  return crypto.createPublicKey({
    key: Buffer.from(spkiB64, 'base64'),
    format: 'der',
    type: 'spki',
  });
}

export function parsePrivate(pkcs8B64) {
  return crypto.createPrivateKey({
    key: Buffer.from(pkcs8B64, 'base64'),
    format: 'der',
    type: 'pkcs8',
  });
}

// 从 PKCS#8 私钥安全反推 SPKI 公钥；传入的若是公钥材料则原样解析
export function publicFromPrivate(pkcs8B64) {
  const keyObj = parsePrivate(pkcs8B64);
  if (keyObj.type !== 'private') throw new Error('材料不是私钥，无法反推公钥');
  return exportPublic(keyObj);
}

// ---------- 签名（Ed25519，对规范化 JSON 字节签名） ----------
export function signJson(privatePkcs8, value) {
  const data = Buffer.from(canonical(value));
  return crypto.sign(null, data, parsePrivate(privatePkcs8)).toString('base64');
}

export function verifySignedJson(publicSpki, value, signatureB64) {
  const data = Buffer.from(canonical(value));
  return crypto.verify(
    null,
    data,
    parsePublic(publicSpki),
    Buffer.from(signatureB64, 'base64'),
  );
}

// ---------- 封缄（混合加密：一次性 X25519 + HKDF + AES-GCM） ----------
// 每封件生成一次性临时钥；只有持对应收件私钥者能开件。
export function sealJson(recipientPublicSpki, plaintextObject, aadObject) {
  const ephemeral = crypto.generateKeyPairSync('x25519');
  const salt = crypto.randomBytes(32);
  const iv = crypto.randomBytes(12);
  const shared = crypto.diffieHellman({
    privateKey: ephemeral.privateKey,
    publicKey: parsePublic(recipientPublicSpki),
  });
  const key = Buffer.from(
    crypto.hkdfSync('sha256', shared, salt, Buffer.from('relic-coop/seal/v1'), 32),
  );
  const cipher = crypto.createCipheriv('aes-256-gcm', key, iv);
  if (aadObject !== undefined) cipher.setAAD(Buffer.from(canonical(aadObject)));
  const ct = Buffer.concat([cipher.update(Buffer.from(canonical(plaintextObject))), cipher.final()]);
  const tag = cipher.getAuthTag();
  return {
    v: 1,
    ephPublic: exportPublic(ephemeral.publicKey),
    salt: salt.toString('base64'),
    iv: iv.toString('base64'),
    ciphertext: ct.toString('base64'),
    tag: tag.toString('base64'),
  };
}

export function openSeal(privatePkcs8, envelope, aadObject) {
  const shared = crypto.diffieHellman({
    privateKey: parsePrivate(privatePkcs8),
    publicKey: parsePublic(envelope.ephPublic),
  });
  const key = Buffer.from(
    crypto.hkdfSync(
      'sha256',
      shared,
      Buffer.from(envelope.salt, 'base64'),
      Buffer.from('relic-coop/seal/v1'),
      32,
    ),
  );
  const decipher = crypto.createDecipheriv(
    'aes-256-gcm',
    key,
    Buffer.from(envelope.iv, 'base64'),
  );
  if (aadObject !== undefined) decipher.setAAD(Buffer.from(canonical(aadObject)));
  decipher.setAuthTag(Buffer.from(envelope.tag, 'base64'));
  const pt = Buffer.concat([
    decipher.update(Buffer.from(envelope.ciphertext, 'base64')),
    decipher.final(),
  ]);
  return JSON.parse(pt.toString('utf8'));
}

// 指纹（用于日志与回执里指代某把公钥）
export function fingerprint(publicSpki) {
  return digestJson({ k: publicSpki }).slice(0, 16);
}
