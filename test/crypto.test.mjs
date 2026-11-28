import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  generateSigningKeyPair,
  generateEncryptionKeyPair,
  signJson,
  verifySignedJson,
  sealJson,
  openSeal,
  fingerprint,
} from '../src/crypto.mjs';

test('Ed25519：签名可验真，篡改即失败', () => {
  const k = generateSigningKeyPair();
  const msg = { a: 1, b: [2, 3] };
  const sig = signJson(k.privatePkcs8, msg);
  assert.equal(verifySignedJson(k.publicSpki, msg, sig), true);
  assert.equal(verifySignedJson(k.publicSpki, { a: 2, b: [2, 3] }, sig), false);
  const other = generateSigningKeyPair();
  assert.equal(verifySignedJson(other.publicSpki, msg, sig), false);
});

test('规范化 JSON：键序不同但内容相同，签名仍有效', () => {
  const k = generateSigningKeyPair();
  const sig = signJson(k.privatePkcs8, { x: 1, y: 2 });
  assert.equal(verifySignedJson(k.publicSpki, { y: 2, x: 1 }, sig), true);
});

test('封缄：只有收件私钥能开，AAD 改动导致认证失败', () => {
  const r = generateEncryptionKeyPair();
  const s = generateEncryptionKeyPair();
  const pt = { secret: '修复方案', n: 42 };
  const aad = { parcelId: 'p1', kind: 'quote' };
  const env = sealJson(r.publicSpki, pt, aad);
  assert.deepEqual(openSeal(r.privatePkcs8, env, aad), pt);
  // 旁人私钥打不开
  assert.throws(() => openSeal(s.privatePkcs8, env, aad));
  // AAD（封件头）被改也打不开
  assert.throws(() => openSeal(r.privatePkcs8, env, { parcelId: 'p2', kind: 'quote' }));
  // 密文被篡改
  const tampered = { ...env, ciphertext: env.ciphertext.slice(0, -2) + 'AA' };
  assert.throws(() => openSeal(r.privatePkcs8, tampered, aad));
});

test('一次性临时钥：同一明文两次封缄产生不同密文', () => {
  const r = generateEncryptionKeyPair();
  const e1 = sealJson(r.publicSpki, { v: 1 }, { h: 1 });
  const e2 = sealJson(r.publicSpki, { v: 1 }, { h: 1 });
  assert.notEqual(e1.ephPublic, e2.ephPublic);
  assert.notEqual(e1.ciphertext, e2.ciphertext);
});

test('指纹稳定且不同钥不同指纹', () => {
  const a = generateEncryptionKeyPair();
  const b = generateEncryptionKeyPair();
  assert.equal(fingerprint(a.publicSpki).length, 16);
  assert.equal(fingerprint(a.publicSpki), fingerprint(a.publicSpki));
  assert.notEqual(fingerprint(a.publicSpki), fingerprint(b.publicSpki));
});
