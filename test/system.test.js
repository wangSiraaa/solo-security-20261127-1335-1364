'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { createSystem, S, CERT, PostError } = require('../src');

// A controllable clock so the three-day forwarding window / escrow deadline can
// be crossed deterministically.
function makeClock(start = 1_700_000_000_000) {
  let t = start;
  return {
    now: () => t,
    advance: (ms) => { t += ms; },
    days: (n) => { t += n * 24 * 60 * 60 * 1000; },
  };
}

function setup() {
  const clock = makeClock();
  const sys = createSystem({ clock: clock.now });
  const alice = sys.post.registerCitizen();
  return { ...sys, clock, alice };
}

// ---------------------------------------------------------------------------
// Foundational privacy properties
// ---------------------------------------------------------------------------

test('two rooms cannot be joined into a common sender marker', () => {
  const { post, verifier, alice } = setup();
  const box = post.openMailbox(alice.identityKey);
  post.openCase(alice.identityKey, box.mailboxId, 'roomA', { label: 'alpha' });
  post.openCase(alice.identityKey, box.mailboxId, 'roomB', { label: 'beta' });

  const audit = verifier.fullAudit();
  assert.equal(audit.unlinkable.ok, true, JSON.stringify(audit.unlinkable.collisions));
  assert.equal(audit.noLeaks.ok, true, JSON.stringify(audit.noLeaks.leaks));
  assert.equal(audit.routesOpaque.ok, true, JSON.stringify(audit.routesOpaque.bad));
});

test('a room never receives the mailbox address, owner secret or other room case number', () => {
  const { post, rooms, alice } = setup();
  const box = post.openMailbox(alice.identityKey);
  const a = post.openCase(alice.identityKey, box.mailboxId, 'roomA');
  const b = post.openCase(alice.identityKey, box.mailboxId, 'roomB');

  const roomASnap = JSON.stringify(rooms.roomA.snapshot());
  const roomBSnap = JSON.stringify(rooms.roomB.snapshot());

  assert.ok(!roomASnap.includes(box.mailboxId));
  assert.ok(!roomASnap.includes(box.ownerSecret));
  assert.ok(!roomASnap.includes(alice.citizenId));
  assert.ok(!roomASnap.includes(b.roomCaseNumber));
  // and the mirror for room B
  assert.ok(!roomBSnap.includes(box.mailboxId));
  assert.ok(!roomBSnap.includes(a.roomCaseNumber));
});

test('recycled alias does not re-route old replies to the new owner', () => {
  const { post, alice } = setup();
  const box1 = post.openMailbox(alice.identityKey);
  const c1 = post.openCase(alice.identityKey, box1.mailboxId, 'roomA');
  const dlv = post.deliverReply(c1.caseId, 'old secret reply');

  // retire the box: the alias goes back to the pool.
  post.retireMailbox(box1.mailboxId, { ownerSecret: box1.ownerSecret });

  // a totally different citizen reopens a box and happens to receive the alias.
  const bob = post.registerCitizen();
  const box2 = post.openMailbox(bob.identityKey);
  assert.equal(box2.alias, box1.alias, 'alias should be recycled');
  assert.notEqual(box2.mailboxId, box1.mailboxId);

  // the old case still points at the OLD mailbox id, not the recycled alias.
  const view = post.inspectCase(c1.caseId);
  assert.equal(view.retainsAddress, true);
  assert.equal(post._case(c1.caseId).mailboxId, box1.mailboxId);

  // Bob cannot collect Alice's reply with his new secret.
  assert.throws(
    () => post.collect(c1.caseId, dlv.deliveryId, { ownerSecret: box2.ownerSecret }),
    (e) => e instanceof PostError && e.code === 'BAD_OWNER_SECRET',
  );
});

// ---------------------------------------------------------------------------
// Deposit vs pickup accounting
// ---------------------------------------------------------------------------

test('deposit and pickup are registered separately; unclaimed shows 已投未取', () => {
  const { post, alice } = setup();
  const box = post.openMailbox(alice.identityKey);
  const c = post.openCase(alice.identityKey, box.mailboxId, 'roomA');
  const d = post.deliverReply(c.caseId, 'hello');

  let view = post.inspectCase(c.caseId);
  assert.equal(view.roomState.deliveries[0].state, 'delivered_unclaimed');

  post.collect(c.caseId, d.deliveryId, { ownerSecret: box.ownerSecret });
  view = post.inspectCase(c.caseId);
  assert.equal(view.roomState.deliveries[0].state, 'picked_up');
});

// ---------------------------------------------------------------------------
// Migration: the central scenario
// ---------------------------------------------------------------------------

test('migration: only case A follows the new box; case B stays behind', () => {
  const { post, clock, alice } = setup();
  const oldBox = post.openMailbox(alice.identityKey);
  const newBox = post.openMailbox(alice.identityKey);
  const A = post.openCase(alice.identityKey, oldBox.mailboxId, 'roomA');
  const B = post.openCase(alice.identityKey, oldBox.mailboxId, 'roomB');

  // old box owner authorises migrating ONLY case A, to a not-yet-known box.
  const m = post.beginMigration(A.caseId, { ownerSecret: oldBox.ownerSecret, days: 3 });
  assert.equal(m.status, S.FORWARDING);

  // within the window the new address becomes known.
  clock.days(1);
  post.completeForwarding(A.caseId, newBox.mailboxId, { ownerSecret: oldBox.ownerSecret });

  // a reply to A must land in the NEW box.
  const dA = post.deliverReply(A.caseId, 'reply to A');
  assert.equal(dA.routedToMailboxId, newBox.mailboxId);
  // only the new box secret collects it; the old box secret cannot.
  assert.throws(
    () => post.collect(A.caseId, dA.deliveryId, { ownerSecret: oldBox.ownerSecret }),
    (e) => e.code === 'BAD_OWNER_SECRET',
  );
  post.collect(A.caseId, dA.deliveryId, { ownerSecret: newBox.ownerSecret });

  // case B is untouched: still on the old box.
  const dB = post.deliverReply(B.caseId, 'reply to B');
  assert.equal(dB.routedToMailboxId, oldBox.mailboxId);
  post.collect(B.caseId, dB.deliveryId, { ownerSecret: oldBox.ownerSecret });
});

test('migration revoked alone: case must stay at the post awaiting pickup', () => {
  const { post, clock, alice } = setup();
  const oldBox = post.openMailbox(alice.identityKey);
  const newBox = post.openMailbox(alice.identityKey);
  const A = post.openCase(alice.identityKey, oldBox.mailboxId, 'roomA');

  post.beginMigration(A.caseId, { ownerSecret: oldBox.ownerSecret, days: 3 });
  // a reply arrives during the forwarding window -> held awaiting forward
  const held = post.deliverReply(A.caseId, 'during window');
  assert.equal(held.state, 'held_at_post');

  // the owner withdraws only this authorisation, never giving a new address.
  const rev = post.revokeMigration(A.caseId, { ownerSecret: oldBox.ownerSecret });
  assert.equal(rev.status, S.HELD_AT_POST);

  // further replies are held, not deposited into the room.
  const after = post.deliverReply(A.caseId, 'after revoke');
  assert.equal(after.state, 'held_at_post');
  const view = post.inspectCase(A.caseId);
  assert.equal(view.status, S.HELD_AT_POST);
  assert.equal(view.roomState.deliveries.filter((d) => d.state === 'delivered_unclaimed').length, 0);

  // the owner can still pick the held letters up in person.
  const out = post.collectHeld(A.caseId, 0, { ownerSecret: oldBox.ownerSecret });
  assert.equal(out.state, 'released_to_owner');
});

test('keep migration, take only case B: case A still shows delivered-unclaimed', () => {
  const { post, clock, alice } = setup();
  const oldBox = post.openMailbox(alice.identityKey);
  const newBox = post.openMailbox(alice.identityKey);
  const A = post.openCase(alice.identityKey, oldBox.mailboxId, 'roomA');
  const B = post.openCase(alice.identityKey, oldBox.mailboxId, 'roomB');

  post.beginMigration(A.caseId, { ownerSecret: oldBox.ownerSecret, days: 3 });
  clock.days(1);
  post.completeForwarding(A.caseId, newBox.mailboxId, { ownerSecret: oldBox.ownerSecret });

  const dA = post.deliverReply(A.caseId, 'A reply');
  const dB = post.deliverReply(B.caseId, 'B reply');

  // collect only B (still on the old box); A remains delivered-unclaimed.
  post.collect(B.caseId, dB.deliveryId, { ownerSecret: oldBox.ownerSecret });

  const viewA = post.inspectCase(A.caseId);
  assert.equal(viewA.roomState.deliveries[0].state, 'delivered_unclaimed');
  assert.equal(viewA.migration.target, newBox.mailboxId, 'migration authorisation retained');
  assert.equal(dA.state, 'delivered_unclaimed');
});

test('migration window expires with no address: case falls back to post pickup', () => {
  const { post, clock, alice } = setup();
  const oldBox = post.openMailbox(alice.identityKey);
  const newBox = post.openMailbox(alice.identityKey);
  const A = post.openCase(alice.identityKey, oldBox.mailboxId, 'roomA');
  post.beginMigration(A.caseId, { ownerSecret: oldBox.ownerSecret, days: 3 });
  clock.days(3.5); // past the window, address never supplied
  assert.throws(
    () => post.completeForwarding(A.caseId, newBox.mailboxId, { ownerSecret: oldBox.ownerSecret }),
    (e) => e.code === 'FORWARD_WINDOW_EXPIRED',
  );
  const r = post.deliverReply(A.caseId, 'late letter');
  assert.equal(r.state, 'held_at_post');
  assert.equal(post.inspectCase(A.caseId).status, S.HELD_AT_POST);
});

test('identity-escrow and voucher can each migrate a single case without restoring a durable link', () => {
  const { post, clock, alice } = setup();
  const box = post.openMailbox(alice.identityKey);
  const target = post.openMailbox(alice.identityKey);

  // escrow-based migration by identity within the window
  const A = post.openCase(alice.identityKey, box.mailboxId, 'roomA');
  const { voucher: vA } = post.eraseByEscrow(A.caseId, { identityKey: alice.identityKey, days: 3 });
  post.migrateByEscrowIdentity(alice.identityKey, A.caseId, target.mailboxId);
  post.collect(A.caseId, post.deliverReply(A.caseId, 'x').deliveryId, { ownerSecret: target.ownerSecret });
  assert.ok(vA);

  // voucher-based migration keeps the post's citizen link null
  const B = post.openCase(alice.identityKey, box.mailboxId, 'roomB');
  const { voucher: vB } = post.eraseByVoucher(B.caseId, { identityKey: alice.identityKey });
  post.migrateByVoucher(vB, target.mailboxId);
  assert.equal(post.inspectCase(B.caseId).retainsCitizenLink, false);
  post.collect(B.caseId, post.deliverReply(B.caseId, 'y').deliveryId, { ownerSecret: target.ownerSecret });
});

// ---------------------------------------------------------------------------
// Erasure methods: voucher vs escrow
// ---------------------------------------------------------------------------

test('voucher method: losing device AND voucher is unrecoverable; FULL_ERASURE certified', () => {
  const { post, alice } = setup();
  const box = post.openMailbox(alice.identityKey);
  const A = post.openCase(alice.identityKey, box.mailboxId, 'roomA');
  post.deliverReply(A.caseId, 'only copy');

  const { voucher, certificate } = post.eraseByVoucher(A.caseId, { identityKey: alice.identityKey });
  assert.equal(certificate.kind, CERT.FULL_ERASURE);
  assert.equal(post.verifyCertificate(certificate), true);

  const after = post.inspectCase(A.caseId);
  assert.equal(after.retainsCitizenLink, false);
  assert.equal(after.retainsAddress, false);

  // device + voucher gone: identity recovery finds nothing.
  assert.deepEqual(post.recoverByEscrow(alice.identityKey), []);
  assert.throws(
    () => post.collectRecovered(A.caseId, 'x', { identityKey: alice.identityKey }),
    (e) => e.code === 'NOT_RECOVERABLE',
  );
});

test('voucher method: a genuine voucher holder can still collect after erasure', () => {
  const { post, alice } = setup();
  const box = post.openMailbox(alice.identityKey);
  const A = post.openCase(alice.identityKey, box.mailboxId, 'roomA');
  const d = post.deliverReply(A.caseId, 'pickup by voucher');
  const { voucher } = post.eraseByVoucher(A.caseId, { identityKey: alice.identityKey });

  const pending = post.listVoucherDeliveries(voucher);
  assert.equal(pending.length, 1);
  const got = post.collectWithVoucher(voucher, pending[0].deliveryId);
  assert.equal(got.state, 'picked_up');
  assert.equal(got.deliveryId, d.deliveryId);
});

test('escrow method: relation erased early but identity recovery works until deadline', () => {
  const { post, clock, alice } = setup();
  const box = post.openMailbox(alice.identityKey);
  const A = post.openCase(alice.identityKey, box.mailboxId, 'roomA');
  const d = post.deliverReply(A.caseId, 'recover me');

  const { certificate, recoverableUntil } = post.eraseByEscrow(A.caseId, { identityKey: alice.identityKey, days: 3 });
  assert.equal(certificate.kind, CERT.DELIVERY_RELATION_DELETED);
  assert.equal(post.verifyCertificate(certificate), true);

  // address gone, person link retained under escrow.
  const view = post.inspectCase(A.caseId);
  assert.equal(view.retainsAddress, false);
  assert.equal(view.retainsCitizenLink, false); // citizenId nulled, escrow holds it
  assert.ok(view.escrow && view.escrow.active);

  // device lost, voucher lost: recovery by identity still works within window.
  const found = post.recoverByEscrow(alice.identityKey);
  assert.equal(found.length, 1);
  const got = post.collectRecovered(A.caseId, d.deliveryId, { identityKey: alice.identityKey });
  assert.equal(got.state, 'picked_up');

  // after the deadline the case is genuinely erased and unrecoverable.
  clock.days(3);
  assert.deepEqual(post.recoverByEscrow(alice.identityKey), []);
  const fin = post.finalizeEscrow(A.caseId, { identityKey: alice.identityKey });
  assert.equal(fin.certificate.kind, CERT.FULL_ERASURE);
  assert.equal(post.inspectCase(A.caseId).escrow, null);
});

test('escrow: cannot recover after deadline even before finalize is called', () => {
  const { post, clock, alice } = setup();
  const box = post.openMailbox(alice.identityKey);
  const A = post.openCase(alice.identityKey, box.mailboxId, 'roomA');
  const d = post.deliverReply(A.caseId, 'x');
  post.eraseByEscrow(A.caseId, { identityKey: alice.identityKey, days: 3 });
  clock.days(4);
  assert.throws(
    () => post.collectRecovered(A.caseId, d.deliveryId, { identityKey: alice.identityKey }),
    (e) => e.code === 'NOT_RECOVERABLE',
  );
  // finalize is allowed at/after the deadline and yields the stronger cert.
  const fin = post.finalizeEscrow(A.caseId, {});
  assert.equal(fin.certificate.kind, CERT.FULL_ERASURE);
});

// ---------------------------------------------------------------------------
// "本人拿着原凭条来要求提前清除" and the suspended shell
// ---------------------------------------------------------------------------

test('early erasure with original voucher; non-bearer cannot reap it', () => {
  const { post, alice } = setup();
  const box = post.openMailbox(alice.identityKey);
  const A = post.openCase(alice.identityKey, box.mailboxId, 'roomA');
  const d = post.deliverReply(A.caseId, 'still collectable');
  const { voucher } = post.eraseByVoucher(A.caseId, { identityKey: alice.identityKey });

  const res = post.requestEarlyErasure(voucher);
  assert.equal(res.certificate.kind, CERT.FULL_ERASURE);
  assert.equal(res.alreadyErased, true);

  // a non-bearer (random voucher) cannot trigger it.
  const forged = { ...voucher, sig: 'deadbeef' };
  assert.throws(() => post.requestEarlyErasure(forged), (e) => e.code === 'BAD_VOUCHER_SIG');

  // the genuine holder can still collect the reply that was not purged.
  const pending = post.listVoucherDeliveries(voucher);
  assert.equal(pending.length, 1);
  post.collectWithVoucher(voucher, pending[0].deliveryId);
});

test('suspended case with lost voucher: owner collects at the room', () => {
  const { post, alice } = setup();
  const box = post.openMailbox(alice.identityKey);
  const A = post.openCase(alice.identityKey, box.mailboxId, 'roomA');
  const d = post.deliverReply(A.caseId, 'for the true holder');
  const { voucher } = post.eraseByVoucher(A.caseId, { identityKey: alice.identityKey });

  // the owner loses the voucher: without it the relation stays cleared and the
  // case is suspended at the post...
  post.suspendCase(A.caseId);
  assert.equal(post.inspectCase(A.caseId).status, S.SUSPENDED);

  // ...but the true holder who still has the voucher can collect their reply.
  const pending = post.listVoucherDeliveries(voucher);
  const got = post.collectWithVoucher(voucher, pending[0].deliveryId);
  assert.equal(got.state, 'picked_up');
});

test('same ticket, both experiences: device+voucher lost, then early clear by voucher', () => {
  const { post, clock, alice } = setup();
  const box = post.openMailbox(alice.identityKey);
  const A = post.openCase(alice.identityKey, box.mailboxId, 'roomA');
  const d = post.deliverReply(A.caseId, 'the only letter');

  // delegation given: escrow keeps the case-to-person relation privately.
  const { certificate: relCert, voucher } = post.eraseByEscrow(A.caseId, { identityKey: alice.identityKey, days: 3 });
  assert.equal(relCert.kind, CERT.DELIVERY_RELATION_DELETED);

  // Experience 1: device lost AND the original voucher lost. Still findable?
  const foundByEscrow = post.recoverByEscrow(alice.identityKey);
  assert.equal(foundByEscrow.length, 1, 'escrow keeps it findable');
  // and the owner can still recover the letter by identity alone.
  const got = post.collectRecovered(A.caseId, d.deliveryId, { identityKey: alice.identityKey });
  assert.equal(got.state, 'picked_up');

  // Experience 2: the owner turns up with the original voucher and asks the
  // post to clear this case EARLY. Only FULL_ERASURE can be signed now.
  const early = post.requestEarlyErasure(voucher);
  assert.equal(early.certificate.kind, CERT.FULL_ERASURE);
  // the voucher had already collected the letter, so nothing is purged on the
  // true bearer's head; identity recovery is now closed.
  assert.equal(post.recoverByEscrow(alice.identityKey).length, 0);
  assert.equal(post.inspectCase(A.caseId).retainsCitizenLink, false);
});

test('contrast: voucher method leaves nothing to find when device+voucher are both lost', () => {
  const { post, alice } = setup();
  const box = post.openMailbox(alice.identityKey);
  const A = post.openCase(alice.identityKey, box.mailboxId, 'roomA');
  const d = post.deliverReply(A.caseId, 'x');
  post.eraseByVoucher(A.caseId, { identityKey: alice.identityKey });
  assert.equal(post.recoverByEscrow(alice.identityKey).length, 0);
  assert.throws(() => post.collectRecovered(A.caseId, d.deliveryId, { identityKey: alice.identityKey }),
    (e) => e.code === 'NOT_RECOVERABLE');
});

// ---------------------------------------------------------------------------
// Stop-delivery disposition
// ---------------------------------------------------------------------------

test('stop-delivery recalls unclaimed and diverts in-transit, keeps collected', () => {
  const { post, alice } = setup();
  const box = post.openMailbox(alice.identityKey);
  const A = post.openCase(alice.identityKey, box.mailboxId, 'roomA');
  const d1 = post.deliverReply(A.caseId, 'one');
  const d2 = post.deliverReply(A.caseId, 'two');
  post.collect(A.caseId, d1.deliveryId, { ownerSecret: box.ownerSecret });
  // leave one currently in-transit, not yet flushed
  post.submitReply(A.caseId, 'three');

  const stop = post.stopCase(A.caseId, { ownerSecret: box.ownerSecret });
  assert.equal(stop.status, S.STOPPED);
  assert.deepEqual(stop.recalled, [d2.deliveryId]);
  assert.equal(stop.inTransitHeld.length, 1);

  const view = post.inspectCase(A.caseId);
  // collected reply stays picked_up; unclaimed was recalled.
  const picked = view.roomState.deliveries.find((d) => d.deliveryId === d1.deliveryId);
  const recalled = view.roomState.deliveries.find((d) => d.deliveryId === d2.deliveryId);
  assert.equal(picked.state, 'picked_up');
  assert.equal(recalled.state, 'returned');

  // room refuses new deposits while stopped.
  const r = post.deliverReply(A.caseId, 'later');
  assert.equal(r.state, 'held_at_post');
});

// ---------------------------------------------------------------------------
// Verifier: joint query must not link rooms
// ---------------------------------------------------------------------------

test('verifier cross-check: a case viewed from post and room matches on room-owned ids only', () => {
  const { post, verifier, alice } = setup();
  const box = post.openMailbox(alice.identityKey);
  const A = post.openCase(alice.identityKey, box.mailboxId, 'roomA');
  post.deliverReply(A.caseId, 'x');

  const { postView, roomView } = verifier.crossCheckCase(A.caseId);
  assert.equal(postView.roomCaseNumber, roomView.caseNumber);
  assert.equal(roomView.deliveries.length, 1);
  // room view carries no address / citizen.
  assert.equal(roomView.mailboxId, undefined);
  assert.equal(roomView.citizenId, undefined);
});

test('verifier detects a deliberately leaked shared marker', () => {
  const { post, rooms, verifier, alice } = setup();
  const box = post.openMailbox(alice.identityKey);
  post.openCase(alice.identityKey, box.mailboxId, 'roomA', { label: 'shared' });
  post.openCase(alice.identityKey, box.mailboxId, 'roomB', { label: 'shared' });
  const audit = verifier.checkRoomUnlinkability();
  assert.equal(audit.ok, false);
  assert.ok(audit.collisions.some((c) => c.kind === 'shared_label'));
});
