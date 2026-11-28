import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  Vault,
  Witness,
  Seat,
  MailDesk,
  ShiftRoster,
  MATERIALS,
  rosterEntry,
  pageId,
  signJson,
} from '../src/index.mjs';

function commonRoster() {
  const a = new Vault('A');
  const b = new Vault('B');
  const w = new Witness('W');
  a.publishKeyMaps([b], [w]);
  b.publishKeyMaps([a], [w]);
  const photoSeat = new Seat('照片席');
  photoSeat.generateKey(0);
  const quoteSeat = new Seat('报价席');
  quoteSeat.generateKey(0);
  const g = a.ledger.append([
    rosterEntry('add', '照片处', photoSeat, { materialsKinds: ['photo'] }),
    rosterEntry('add', '报价处', quoteSeat, { materialsKinds: ['quote'] }),
  ]);
  w.holdBase(pageId(g));
  w.attest(a.ledger, g, ['照片处', '报价处']);
  b.intake(g);
  const sender = new Seat('发件席');
  sender.generateKey(0);
  return { a, b, w, photoSeat, quoteSeat, sender, view: a.ledger.view() };
}

test('全流程：提交→在途→开件→回执对账；错误钥匙开不了', () => {
  const ctx = commonRoster();
  const desk = new MailDesk(ctx.view);
  const parcel = desk.submit({
    kind: MATERIALS.PHOTO,
    recipientName: '照片处',
    payload: { photoId: 'P1' },
    senderId: ctx.sender.seatId,
    senderSignPrivate: ctx.sender.sign.privatePkcs8,
  });
  const shifts = new ShiftRoster();
  shifts.addOperator({
    operatorId: '甲',
    seatId: ctx.photoSeat.seatId,
    allowedMaterials: ['photo'],
    recipientNames: ['照片处'],
  });
  const r = desk.dispatch(parcel.parcelId, shifts);
  assert.equal(r.dispatched, true);
  assert.equal(parcel.status, 'intransit');

  // 报价席的钥匙开不了照片封件
  assert.throws(() => desk.open(parcel.parcelId, ctx.quoteSeat.enc.privatePkcs8));
  const pt = desk.open(parcel.parcelId, ctx.photoSeat.enc.privatePkcs8);
  assert.deepEqual(pt, { photoId: 'P1' });

  const receipt = desk.acknowledge(
    parcel.parcelId,
    ctx.photoSeat.enc.privatePkcs8,
    ctx.photoSeat.sign.privatePkcs8,
  );
  assert.equal(desk.reconcileReceipt(receipt).ok, true);
  assert.equal(parcel.status, 'delivered');

  // 篡改回执正文后签名失效
  const bad = { ...receipt, deliveredAt: '1999-01-01T00:00:00.000Z' };
  assert.equal(desk.reconcileReceipt(bad).ok, false);
});

test('轮班单：无当班经办人时封件暂扣，材料种类不匹配也不派', () => {
  const ctx = commonRoster();
  const desk = new MailDesk(ctx.view);
  const parcel = desk.submit({
    kind: MATERIALS.QUOTE,
    recipientName: '报价处',
    payload: { q: 1 },
    senderId: ctx.sender.seatId,
    senderSignPrivate: ctx.sender.sign.privatePkcs8,
  });
  const emptyShifts = new ShiftRoster();
  const r1 = desk.dispatch(parcel.parcelId, emptyShifts);
  assert.equal(r1.dispatched, false);
  assert.equal(parcel.status, 'held');

  // 重置状态后再派：照片经办人不收报价
  parcel.status = 'outbox';
  const wrongShifts = new ShiftRoster();
  wrongShifts.addOperator({
    operatorId: '甲',
    seatId: ctx.photoSeat.seatId,
    allowedMaterials: ['photo'],
    recipientNames: ['报价处'],
  });
  const r2 = desk.dispatch(parcel.parcelId, wrongShifts);
  assert.equal(r2.dispatched, false);
});

test('重封：旧钥打不开新报价；已交付件不得重封', () => {
  const ctx = commonRoster();
  const desk = new MailDesk(ctx.view);
  const oldKey = ctx.quoteSeat.keyAt(0);
  const parcel = desk.submit({
    kind: MATERIALS.QUOTE,
    recipientName: '报价处',
    payload: { quoteId: 'Q1', price: 100 },
    senderId: ctx.sender.seatId,
    senderSignPrivate: ctx.sender.sign.privatePkcs8,
  });
  // 换钥并把新钥写进名册（带见证）
  ctx.quoteSeat.rotateKey();
  const p = ctx.a.ledger.append([
    rosterEntry('rotate', '报价处', ctx.quoteSeat, { materialsKinds: ['quote'] }),
  ]);
  ctx.w.attest(ctx.a.ledger, p, ['报价处']);
  desk.setView(ctx.a.ledger.view());

  const reseal = desk.reSealQuote(parcel.parcelId, ctx.sender.sign.privatePkcs8);
  assert.equal(reseal.ok, true);
  assert.equal(parcel.status, 'superseded');
  assert.equal(MailDesk.oldKeyCannotOpen(reseal.parcel, oldKey.enc.privatePkcs8), true);
  const pt = desk.open(reseal.parcel.parcelId, ctx.quoteSeat.enc.privatePkcs8);
  assert.equal(pt.price, 100);

  // 已交付件拒绝重封
  const delivered = desk.submit({
    kind: MATERIALS.QUOTE,
    recipientName: '报价处',
    payload: { q: 2 },
    senderId: ctx.sender.seatId,
    senderSignPrivate: ctx.sender.sign.privatePkcs8,
  });
  delivered.status = 'delivered';
  assert.throws(() => desk.reSealQuote(delivered.parcelId, ctx.sender.sign.privatePkcs8), /已交付/);
});

test('发件人签名可被发件公钥验真，冒用签名被拒', async () => {
  const ctx = commonRoster();
  const desk = new MailDesk(ctx.view);
  const parcel = desk.submit({
    kind: MATERIALS.PHOTO,
    recipientName: '照片处',
    payload: { photoId: 'P9' },
    senderId: ctx.sender.seatId,
    senderSignPrivate: ctx.sender.sign.privatePkcs8,
  });
  const pt = desk.open(parcel.parcelId, ctx.photoSeat.enc.privatePkcs8);
  const { verifySenderSignature } = await import('../src/mail.mjs');
  assert.equal(verifySenderSignature(parcel, ctx.sender.sign.publicSpki, pt), true);
  const impostor = new Seat('冒名席');
  impostor.generateKey(0);
  assert.equal(verifySenderSignature(parcel, impostor.sign.publicSpki, pt), false);
});
