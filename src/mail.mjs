// 实际封件寄送台。
// 封件 = 收件人封装信封（X25519+AES-GCM）+ 发件人对封件头的 Ed25519 签名。
// 收件席只凭自己实际收到的封件和手里的钥匙开件；旧钥打不开改寄后的新报价。
import {
  sealJson,
  openSeal,
  signJson,
  verifySignedJson,
  fingerprint,
} from './crypto.mjs';
import { digestJson, randomId, nowIso } from './util.mjs';

export const MATERIALS = Object.freeze({ PHOTO: 'photo', QUOTE: 'quote' });

// 轮班单：两个经办人各自允收的材料与收件名。
export class ShiftRoster {
  constructor() {
    this.operators = new Map();
  }

  addOperator({ operatorId, seatId, allowedMaterials, recipientNames }) {
    this.operators.set(operatorId, {
      operatorId,
      seatId,
      allowedMaterials: new Set(allowedMaterials),
      recipientNames: new Set(recipientNames),
    });
  }

  // 返场后才交来轮班单：按收件名 + 材料种类找当班经办人
  findHandler(recipientName, materialKind) {
    for (const op of this.operators.values()) {
      if (op.recipientNames.has(recipientName) && op.allowedMaterials.has(materialKind)) {
        return op;
      }
    }
    return null;
  }
}

export class MailDesk {
  constructor(rosterView) {
    this.view = rosterView;
    this.parcels = new Map(); // parcelId -> parcel
  }

  setView(rosterView) {
    this.view = rosterView;
  }

  // 原发件入口：交寄一份材料。先封缄落入待发箱，再由 dispatch 决定去向。
  submit({ kind, recipientName, payload, senderId, senderSignPrivate, relatedPhotoId = null }) {
    const decision = this.view.routingDecision(recipientName, kind);
    const parcelId = randomId('parcel');
    const header = {
      parcelId,
      kind,
      senderId,
      recipientName,
      relatedPhotoId,
      createdAt: nowIso(),
    };
    let parcel = {
      ...header,
      status: 'outbox',
      routing: decision,
      envelope: null,
      senderSignature: null,
      sealRecipientEncFp: null,
      receipt: null,
      plaintextCache: payload, // 本地寄送台留存，供待重封使用，不随封件外发
      history: [{ at: nowIso(), event: 'submitted' }],
    };
    if (decision.decision === 'send') {
      const entry = decision.entry;
      parcel.envelope = sealJson(entry.encSpki, payload, header);
      parcel.sealRecipientEncFp = fingerprint(entry.encSpki);
      parcel.recipientSignSpki = entry.signSpki;
      parcel.senderSignature = signJson(senderSignPrivate, {
        kind: 'parcel-header',
        header,
        payloadDigest: digestJson(payload),
      });
    }
    this.parcels.set(parcelId, parcel);
    return parcel;
  }

  // 用原发件入口处理：能寄的投出、无依据的暂扣；轮班单给出经办人才算在途。
  dispatch(parcelId, shiftRoster) {
    const parcel = this.#mustGet(parcelId);
    const handler = shiftRoster.findHandler(parcel.recipientName, parcel.kind);
    if (parcel.routing.decision !== 'send') {
      parcel.status = 'held';
      parcel.history.push({ at: nowIso(), event: 'held', reason: parcel.routing.reason });
      return { parcel, dispatched: false, reason: parcel.routing.reason };
    }
    if (!handler) {
      parcel.status = 'held';
      const reason = `轮班单上无经办人允收 ${parcel.recipientName}/${parcel.kind}`;
      parcel.history.push({ at: nowIso(), event: 'held', reason });
      return { parcel, dispatched: false, reason };
    }
    parcel.status = 'intransit';
    parcel.handler = { operatorId: handler.operatorId, seatId: handler.seatId };
    parcel.history.push({ at: nowIso(), event: 'dispatched', handler: parcel.handler });
    return { parcel, dispatched: true, handler };
  }

  // 收件席开件：只凭实际收到的封件和手里的钥匙。
  // AAD 绑定封件头，任一字段被改都开不出来。
  open(parcelId, recipientEncPrivate) {
    const parcel = this.#mustGet(parcelId);
    if (!parcel.envelope) throw new Error('该封件未封缄（暂扣中），无件可开');
    const header = this.#headerOf(parcel);
    return openSeal(recipientEncPrivate, parcel.envelope, header);
  }

  // 收件席签收并出具回执（用收件钥签名）。
  acknowledge(parcelId, recipientEncPrivate, recipientSignPrivate) {
    const parcel = this.#mustGet(parcelId);
    const payload = this.open(parcelId, recipientEncPrivate);
    if (!parcel.recipientSignSpki) throw new Error('封件缺少收件签名钥信息');
    const receipt = {
      receiptId: randomId('rcpt'),
      parcelId: parcel.parcelId,
      recipientName: parcel.recipientName,
      kind: parcel.kind,
      relatedPhotoId: parcel.relatedPhotoId,
      payloadDigest: digestJson(payload),
      deliveredAt: nowIso(),
    };
    receipt.signature = signJson(recipientSignPrivate, { kind: 'delivery-receipt', receipt });
    parcel.receipt = receipt;
    parcel.status = 'delivered';
    parcel.history.push({ at: nowIso(), event: 'delivered' });
    return receipt;
  }

  // 已交回执对账：只认真正收件席的签名；已交到照片收件人手里的内容按回执销账。
  reconcileReceipt(receipt) {
    const parcel = this.parcels.get(receipt.parcelId);
    if (!parcel) return { ok: false, reason: '回执对应封件不存在' };
    const { signature, ...receiptBody } = receipt;
    const ok = verifySignedJson(
      parcel.recipientSignSpki,
      { kind: 'delivery-receipt', receipt: receiptBody },
      signature,
    );
    if (!ok) return { ok: false, reason: '回执签名验真失败（非真正收件席）' };
    if (receipt.payloadDigest !== digestJson(this.#safelyRead(parcel))) {
      return { ok: false, reason: '回执摘要与封件正文不符' };
    }
    parcel.status = 'delivered';
    parcel.receipt = receipt;
    parcel.history.push({ at: nowIso(), event: 'receipt-reconciled' });
    return { ok: true, parcel };
  }

  // 待重封附件：换钥后把未交付的报价改寄到当前在任钥。
  // 产生一封全新封件（新 id、新临时钥、新 AAD）；旧封件标记 superseded。
  reSealQuote(oldParcelId, senderSignPrivate) {
    const old = this.#mustGet(oldParcelId);
    if (old.kind !== MATERIALS.QUOTE) throw new Error('只有报价封件可走重封改寄');
    if (old.status === 'delivered') throw new Error('已交付封件不得重封改寄（按回执对账）');
    const decision = this.view.routingDecision(old.recipientName, MATERIALS.QUOTE);
    if (decision.decision !== 'send') {
      return { ok: false, reason: `新地址不可寄：${decision.reason}` };
    }
    const entry = decision.entry;
    if (entry.encSpki === old.sealRecipientEncFp || fingerprint(entry.encSpki) === old.sealRecipientEncFp) {
      return { ok: false, reason: '新钥与旧钥相同，无需重封' };
    }
    // 旧封件正文由寄送台在原发件时留存的明文重封（待发箱/待重封场景）
    const payload = old.plaintextCache ?? this.#safelyRead(old);
    const parcelId = randomId('parcel');
    const header = {
      parcelId,
      kind: old.kind,
      senderId: old.senderId,
      recipientName: old.recipientName,
      relatedPhotoId: old.relatedPhotoId,
      reSealedFrom: old.parcelId,
      createdAt: nowIso(),
    };
    const fresh = {
      ...header,
      status: 'outbox',
      routing: decision,
      envelope: sealJson(entry.encSpki, payload, header),
      sealRecipientEncFp: fingerprint(entry.encSpki),
      recipientSignSpki: entry.signSpki,
      senderSignature: signJson(senderSignPrivate, {
        kind: 'parcel-header',
        header,
        payloadDigest: digestJson(payload),
      }),
      receipt: null,
      plaintextCache: payload, // 重封件同样只在本地留存明文
      history: [{ at: nowIso(), event: 're-sealed', from: old.parcelId }],
    };
    old.status = 'superseded';
    old.history.push({ at: nowIso(), event: 'superseded-by', by: parcelId });
    this.parcels.set(parcelId, fresh);
    return { ok: true, parcel: fresh, oldParcel: old };
  }

  // 用一把旧私钥试探新封件：必须失败，证明旧钥打不开改寄的新报价。
  static oldKeyCannotOpen(parcel, oldEncPrivate) {
    try {
      openSeal(oldEncPrivate, parcel.envelope, {
        parcelId: parcel.parcelId,
        kind: parcel.kind,
        senderId: parcel.senderId,
        recipientName: parcel.recipientName,
        relatedPhotoId: parcel.relatedPhotoId,
        reSealedFrom: parcel.reSealedFrom,
        createdAt: parcel.createdAt,
      });
      return false; // 竟然打开了 -> 违反规则
    } catch {
      return true;
    }
  }

  #safelyRead(parcel) {
    if (parcel.plaintextCache) return parcel.plaintextCache;
    throw new Error('寄送台未留存该封件明文，无法校验摘要');
  }

  #headerOf(parcel) {
    return {
      parcelId: parcel.parcelId,
      kind: parcel.kind,
      senderId: parcel.senderId,
      recipientName: parcel.recipientName,
      relatedPhotoId: parcel.relatedPhotoId,
      ...(parcel.reSealedFrom ? { reSealedFrom: parcel.reSealedFrom } : {}),
      createdAt: parcel.createdAt,
    };
  }

  #mustGet(parcelId) {
    const p = this.parcels.get(parcelId);
    if (!p) throw new Error(`封件不存在: ${parcelId}`);
    return p;
  }

  // 按状态分箱：待发 / 在途 / 已交 / 暂扣 / 被取代
  bins() {
    const out = { outbox: [], intransit: [], delivered: [], held: [], superseded: [] };
    for (const p of this.parcels.values()) out[p.status]?.push(p);
    return out;
  }
}

export function verifySenderSignature(parcel, senderSignSpki, payload) {
  return verifySignedJson(
    senderSignSpki,
    { kind: 'parcel-header', header: stripEnvelope(parcel), payloadDigest: digestJson(payload) },
    parcel.senderSignature,
  );
}

function stripEnvelope(parcel) {
  return {
    parcelId: parcel.parcelId,
    kind: parcel.kind,
    senderId: parcel.senderId,
    recipientName: parcel.recipientName,
    relatedPhotoId: parcel.relatedPhotoId,
    ...(parcel.reSealedFrom ? { reSealedFrom: parcel.reSealedFrom } : {}),
    createdAt: parcel.createdAt,
  };
}
