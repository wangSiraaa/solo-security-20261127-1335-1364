// 人物、分库、见证席与收件席的本地装配；钥匙与正文全部本地生成。
import { generateSigningKeyPair, generateEncryptionKeyPair } from './crypto.mjs';
import { Ledger } from './roster.mjs';

export class Seat {
  constructor(seatId) {
    this.seatId = seatId;
    // 一个收件席可同时持有多代钥匙；旧钥只用于打开在途旧封件。
    this.keys = new Map(); // 代次 -> { sign, enc, active }
    this.inbox = [];
  }

  generateKey(generation = 0) {
    const k = {
      generation,
      sign: generateSigningKeyPair(),
      enc: generateEncryptionKeyPair(),
      active: true,
    };
    this.keys.set(generation, k);
    return k;
  }

  // 便捷访问当前代钥匙材料
  get sign() {
    return this.activeKey()?.sign;
  }

  get enc() {
    return this.activeKey()?.enc;
  }

  activeKey() {
    return [...this.keys.values()].filter((k) => k.active).at(-1);
  }

  keyAt(generation) {
    return this.keys.get(generation);
  }

  rotateKey() {
    const gen = (this.activeKey()?.generation ?? -1) + 1;
    if (this.activeKey()) this.activeKey().active = false;
    return this.generateKey(gen);
  }

  receive(parcelId) {
    this.inbox.push(parcelId);
  }
}

export class Vault {
  constructor(vaultId, vaultKeys = new Map(), witnessKeys = new Map()) {
    this.vaultId = vaultId;
    this.sign = generateSigningKeyPair();
    this.ledger = new Ledger(vaultId, this.sign.privatePkcs8);
    // 验签名册所需的公钥簿：他库分库公钥、见证席公钥
    this.ledger.vaultKeys = vaultKeys;
    this.ledger.witnessKeys = witnessKeys;
    this.vaultKeys = vaultKeys;
    this.witnessKeys = witnessKeys;
  }

  publishKeyMaps(otherVaults, witnesses) {
    for (const v of otherVaults) this.vaultKeys.set(v.vaultId, v.sign.publicSpki);
    for (const w of witnesses) this.witnessKeys.set(w.witnessId, w.sign.publicSpki);
  }

  // 本地受理一页并自行签收（本地见过，不等于共同认可）
  intake(page) {
    this.ledger.ingest(page);
    this.ledger.markIntake(page, this.vaultId, this.sign.privatePkcs8);
  }

  // 离线带回对方链上的页：链断也先收存，保持签名可验
  bringBackLoose(pages) {
    for (const p of pages) this.ledger.ingestLoose(p);
  }
}

export class Witness {
  constructor(witnessId) {
    this.witnessId = witnessId;
    this.sign = generateSigningKeyPair();
    this.heldBasePageId = null; // 只持有自己见过的旧页，只签它的延长
  }

  holdBase(pageId) {
    this.heldBasePageId = pageId;
  }

  attest(ledger, page, scopeNames = null) {
    return ledger.attest(page, this.witnessId, this.sign.privatePkcs8, this.heldBasePageId, scopeNames);
  }
}

// 由收件席当前代钥匙生成名册条目
export function rosterEntry(type, name, seat, extra = {}) {
  const k = seat.activeKey();
  if (!k) throw new Error(`收件席 ${seat.seatId} 尚无钥匙`);
  const base = {
    type,
    name,
    signSpki: k.sign.publicSpki,
    encSpki: k.enc.publicSpki,
    seatId: seat.seatId,
  };
  if (type !== 'revoke') base.materialsKinds = extra.materialsKinds ?? ['photo', 'quote'];
  if (extra.reason) base.reason = extra.reason;
  if (extra.entryId) base.entryId = extra.entryId;
  return base;
}

export function revokeEntry(name, seat) {
  const k = seat.activeKey() ?? [...seat.keys.values()].at(-1);
  return {
    type: 'revoke',
    name,
    signSpki: k.sign.publicSpki,
    encSpki: k.enc.publicSpki,
    seatId: seat.seatId,
  };
}
