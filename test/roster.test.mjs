import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  Vault,
  Witness,
  Seat,
  Ledger,
  RosterView,
  rosterEntry,
  revokeEntry,
  pageId,
  pageBody,
  verifySignedJson,
} from '../src/index.mjs';

function setup() {
  const a = new Vault('A');
  const b = new Vault('B');
  const w = new Witness('W');
  a.publishKeyMaps([b], [w]);
  b.publishKeyMaps([a], [w]);
  const seat = new Seat('收件席');
  seat.generateKey(0);
  return { a, b, w, seat };
}

test('仅追加：篡改旧页内容后哈希与签名双双失效', () => {
  const { a, seat } = setup();
  const g = a.ledger.append([rosterEntry('add', 'n', seat)]);
  const p1 = a.ledger.append([rosterEntry('rotate', 'n', seat)]);
  const tampered = JSON.parse(JSON.stringify(g));
  tampered.entries[0].name = '被改名';
  assert.notEqual(pageId(tampered), pageId(g));
  assert.equal(verifySignedJson(a.sign.publicSpki, pageBody(tampered), tampered.signature), false);
  // 后页 prev 仍指向旧哈希，断链可被发现
  assert.equal(p1.prev, pageId(g));
});

test('离线分叉：相同 seq、不同内容 → fork，且共有最后一页正确', () => {
  const { a, b, w, seat } = setup();
  const g = a.ledger.append([rosterEntry('add', 'n', seat)]);
  w.holdBase(pageId(g));
  w.attest(a.ledger, g, ['n']);
  b.intake(g);

  const pa = a.ledger.append([rosterEntry('rotate', 'n', seat)]);
  const otherSeat = new Seat('另一席');
  otherSeat.generateKey(0);
  const pb = b.ledger.append([rosterEntry('add', 'n', otherSeat)]);
  assert.equal(pa.seq, pb.seq);
  assert.notEqual(pageId(pa), pageId(pb));

  a.bringBackLoose([pb]);
  b.bringBackLoose([pa]);
  const r = a.ledger.reconcile(b.ledger);
  assert.equal(r.kind, 'fork');
  assert.equal(r.commonBaseId, pageId(g));
  assert.deepEqual(r.mineOnly.map(pageId), [pageId(pa)]);
  assert.deepEqual(r.theirsOnly.map(pageId), [pageId(pb)]);
  // 对方页可用对方分库公钥独立验真
  assert.equal(
    verifySignedJson(b.sign.publicSpki, pageBody(pb), pb.signature),
    true,
  );
});

test('见证席拒签接不到自己旧页的异链延长', () => {
  const { a, w, seat } = setup();
  const g = a.ledger.append([rosterEntry('add', 'n', seat)]);
  w.holdBase(pageId(g));
  const alien = new Vault('alien');
  alien.publishKeyMaps([], [w]);
  const alienSeat = new Seat('alien-seat');
  alienSeat.generateKey(0);
  const alienPage = alien.ledger.append([rosterEntry('add', 'x', alienSeat)]);
  assert.throws(() => w.attest(alien.ledger, alienPage, ['x']), /接不到旧页/);
});

test('并账：撤销并集永久黏住，重新 add 不能复活', () => {
  const { a, b, w, seat } = setup();
  const backup = new Seat('备用席');
  backup.generateKey(0);
  const g = a.ledger.append([
    rosterEntry('add', 'n', seat),
    rosterEntry('add', '备用', backup),
  ]);
  w.holdBase(pageId(g));
  w.attest(a.ledger, g, ['n', '备用']);
  b.intake(g);

  a.ledger.append([revokeEntry('备用', backup)]);
  b.ledger.append([rosterEntry('add', '新增', (() => { const s = new Seat('新'); s.generateKey(0); return s; })())]);

  a.bringBackLoose(b.ledger.pages);
  b.bringBackLoose(a.ledger.pages);
  const { mergedPage } = a.ledger.merge(b.ledger, 'B', b.sign.publicSpki);
  a.ledger.countersignMerge(mergedPage, b.sign.privatePkcs8);
  assert.equal(a.ledger.view().lookup('备用').status, 'revoked');

  // 即便再追加 add，撤销状态黏住
  a.ledger.append([rosterEntry('add', '备用', backup)]);
  assert.equal(a.ledger.view().lookup('备用').status, 'revoked');
});

test('合并页缺少对方双签时，对库受理被拒', () => {
  const { a, b, seat } = setup();
  const g = a.ledger.append([rosterEntry('add', 'n', seat)]);
  b.intake(g);
  const s2 = new Seat('s2');
  s2.generateKey(0);
  a.ledger.append([rosterEntry('add', 'm', s2)]);
  const s3 = new Seat('s3');
  s3.generateKey(0);
  b.ledger.append([rosterEntry('add', 'k', s3)]);
  a.bringBackLoose(b.ledger.pages);
  b.bringBackLoose(a.ledger.pages);
  const { mergedPage } = a.ledger.merge(b.ledger, 'B', b.sign.publicSpki);
  // 不双签直接让 B 受理
  assert.throws(() => b.intake(mergedPage), /双签/);
});

test('路由：无见证的单侧地址暂扣；冲突名裁决前暂扣；裁决后放行', () => {
  const { a, b, w, seat } = setup();
  const g = a.ledger.append([rosterEntry('add', '共同名', seat)]);
  w.holdBase(pageId(g));
  w.attest(a.ledger, g, ['共同名']);
  b.intake(g);

  const sideSeat = new Seat('单侧席');
  sideSeat.generateKey(0);
  const pb = b.ledger.append([rosterEntry('add', '单侧名', sideSeat)]);
  b.ledger.markIntake(pb, 'B', b.sign.privatePkcs8); // 仅本地签收

  a.bringBackLoose(b.ledger.pages);
  b.bringBackLoose(a.ledger.pages);
  const { mergedPage } = a.ledger.merge(b.ledger, 'B', b.sign.publicSpki);
  a.ledger.countersignMerge(mergedPage, b.sign.privatePkcs8);
  const view = a.ledger.view();
  assert.equal(view.routingDecision('单侧名', 'photo').decision, 'hold');
  assert.equal(view.routingDecision('共同名', 'photo').decision, 'send');
  assert.equal(view.routingDecision('不存在', 'photo').decision, 'hold');

  // 同名冲突
  seat.rotateKey();
  const forkSeat = new Seat('分叉席');
  forkSeat.generateKey(0);
  const pa2 = a.ledger.append([rosterEntry('rotate', '共同名', seat)]);
  const pb2 = b.ledger.append([rosterEntry('add', '共同名', forkSeat)]);
  a.bringBackLoose([pb2]);
  b.bringBackLoose([pa2]);
  const m2 = a.ledger.merge(b.ledger, 'B', b.sign.publicSpki);
  a.ledger.countersignMerge(m2.mergedPage, b.sign.privatePkcs8);
  const conflicted = a.ledger.view();
  assert.equal(conflicted.routingDecision('共同名', 'photo').decision, 'hold');
  w.holdBase(pageId(m2.mergedPage));
  const resolve = a.ledger.resolveConflict('共同名', rosterEntry('rotate', '共同名', seat));
  w.attest(a.ledger, resolve, ['共同名']);
  assert.equal(a.ledger.view().routingDecision('共同名', 'photo').decision, 'send');
});

test('RosterView 只采纳覆盖本收件名的见证', () => {
  const { a, w, seat } = setup();
  const s2 = new Seat('s2');
  s2.generateKey(0);
  const g = a.ledger.append([
    rosterEntry('add', '甲', seat),
    rosterEntry('add', '乙', s2),
  ]);
  w.holdBase(pageId(g));
  w.attest(a.ledger, g, ['甲']); // 只见证甲
  const view = a.ledger.view();
  assert.equal(view.lookup('甲').attestations.length, 1);
  assert.equal(view.lookup('乙').attestations.length, 0);
});

test('从 JSON 重建 Ledger 后链与视图一致', () => {
  const { a, seat } = setup();
  a.ledger.append([rosterEntry('add', 'n', seat)]);
  seat.rotateKey();
  a.ledger.append([rosterEntry('rotate', 'n', seat)]);
  const json = a.ledger.toJSON();
  const restored = new Ledger(json.vaultId, a.sign.privatePkcs8, json.pages);
  assert.equal(restored.chainPages().length, 2);
  assert.equal(restored.view().lookup('n').lastEntry.encSpki, seat.activeKey().enc.publicSpki);
});
