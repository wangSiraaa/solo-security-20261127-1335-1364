'use strict';

// End-to-end walkthrough of the scenarios in the task. Run with: node demo/run.js
// It narrates each step so a reviewer can follow the privacy and lifecycle
// decisions without reading the tests.

const { createSystem, S, CERT } = require('../src');

function makeClock(start = 1_700_000_000_000) {
  let t = start;
  return { now: () => t, days: (n) => { t += n * 24 * 3600 * 1000; } };
}

const section = (title) => console.log(`\n== ${title} ==`);
const show = (label, value) => console.log(
  `${label}: ${typeof value === 'string' ? value : JSON.stringify(value)}`,
);

function main() {
  const clock = makeClock();
  const { post, verifier } = createSystem({ clock: clock.now });
  const citizen = post.registerCitizen();

  const E = post.openMailbox(citizen.identityKey); // will be retired & handed on
  const F = post.openMailbox(citizen.identityKey); // the new box, address to be discovered
  const G = post.openMailbox(citizen.identityKey); // a later replacement

  section('Setup: one citizen, three boxes, one case in each room');
  const A = post.openCase(citizen.identityKey, E.mailboxId, 'roomA', { label: 'case-A' });
  const B = post.openCase(citizen.identityKey, E.mailboxId, 'roomB', { label: 'case-B' });
  show('old box alias', E.alias);
  show('case A room case no.', A.roomCaseNumber);
  show('case B room case no.', B.roomCaseNumber);

  section('Privacy audit: rooms cannot be joined; no address / owner secrets leak');
  const audit = verifier.fullAudit();
  show('rooms unlinkable', audit.unlinkable.ok);
  show('rooms hold no address/secret/citizen id', audit.noLeaks.ok);
  show('return routes opaque', audit.routesOpaque.ok);

  section('Migration: old owner authorises moving ONLY case A to a not-yet-known box');
  post.beginMigration(A.caseId, { ownerSecret: E.ownerSecret, days: 3 });
  show('A status after authorisation', post.inspectCase(A.caseId).status);
  const earlyA = post.deliverReply(A.caseId, 'early reply to A (window)');
  show('early A reply disposition while address unknown', earlyA.state);

  clock.days(1);
  post.completeForwarding(A.caseId, F.mailboxId, { ownerSecret: E.ownerSecret });
  const aAfter = post.deliverReply(A.caseId, 'reply to A after forwarding');
  show('A routes to new box?', aAfter.routedToMailboxId === F.mailboxId);

  post.retireMailbox(E.mailboxId, { ownerSecret: E.ownerSecret });
  const other = post.registerCitizen();
  const H = post.openMailbox(other.identityKey);
  show('recycled alias equals old alias', H.alias === E.alias);
  show('recycled mailbox id differs from old id', H.mailboxId !== E.mailboxId);

  section('Revoke migration AUTHORISATION ONLY: case must stay at post awaiting pickup');
  const C = post.openCase(citizen.identityKey, G.mailboxId, 'roomA', { label: 'case-C' });
  post.beginMigration(C.caseId, { ownerSecret: G.ownerSecret, days: 3 });
  show('C reply during window', post.deliverReply(C.caseId, 'C during window').state);
  post.revokeMigration(C.caseId, { ownerSecret: G.ownerSecret });
  show('C status after revoke', post.inspectCase(C.caseId).status);
  show('C reply after revoke', post.deliverReply(C.caseId, 'C after revoke').state);
  show('C unclaimed-in-room count',
    post.inspectCase(C.caseId).roomState.deliveries.filter((d) => d.state === 'delivered_unclaimed').length);
  show('C held-at-post letters', post.inspectCase(C.caseId).heldLetters.length);

  section('Keep migration, take ONLY case B: case A still shows delivered-unclaimed');
  const dA = post.deliverReply(A.caseId, 'A reply awaiting pickup');
  const dB = post.deliverReply(B.caseId, 'B reply (old box)');
  post.collect(B.caseId, dB.deliveryId, { ownerSecret: E.ownerSecret });
  const viewA = post.inspectCase(A.caseId);
  show('A delivery state', viewA.roomState.deliveries.find((d) => d.deliveryId === dA.deliveryId).state);
  show('A migration authorisation retained', Boolean(viewA.migration));
  show('A migration target is new box', viewA.migration.target === F.mailboxId);

  // -------------------------------------------------------------------
  section('Same single case ticket, Scenario 1: device lost AND original voucher lost');
  const V = post.openCase(citizen.identityKey, F.mailboxId, 'roomB', { label: 'case-V' });
  post.deliverReply(V.caseId, 'voucher-case reply');
  const { voucher: lostVoucher, certificate: eraseCert } = post.eraseByVoucher(V.caseId, { identityKey: citizen.identityKey });
  show('erase certificate kind', eraseCert.kind);
  show('post retains citizen link?', post.inspectCase(V.caseId).retainsCitizenLink);
  show('post retains address?', post.inspectCase(V.caseId).retainsAddress);
  show('recoverable by identity after voucher+device loss?',
    post.recoverByEscrow(citizen.identityKey).some((r) => r.caseId === V.caseId));
  // Pretend the original voucher is gone: cannot use it.
  show('can a forger reap it?', (() => {
    try { post.requestEarlyErasure({ ...lostVoucher, sig: 'x' }); return false; } catch { return 'rejected'; }
  })());
  show('can a genuine (still-held) voucher holder collect?',
    post.listVoucherDeliveries(lostVoucher).length === 1);

  // -------------------------------------------------------------------
  section('Same single case ticket, Scenario 2: owner brings original voucher, asks to clear early');
  const W = post.openCase(citizen.identityKey, F.mailboxId, 'roomA', { label: 'case-W' });
  const wReply = post.deliverReply(W.caseId, 'escrow reply');
  // delegate escrow retention (address dropped now, person link kept privately)
  const { certificate: relCert, voucher: wVoucher } = post.eraseByEscrow(W.caseId, { identityKey: citizen.identityKey, days: 3 });
  show('escrow certificate kind', relCert.kind);
  show('address retained after escrow?', post.inspectCase(W.caseId).retainsAddress);
  show('device+voucher lost recoverable within window?',
    post.recoverByEscrow(citizen.identityKey).some((r) => r.caseId === W.caseId));
  // owner arrives with the original voucher and demands early full clearing
  const early = post.requestEarlyErasure(wVoucher);
  show('early erasure certificate kind', early.certificate.kind);
  show('early erasure kept owner letter for the true bearer?', early.certificate.purgedDeliveries === 0);
  show('true bearer can still collect the un-purged letter?',
    post.listVoucherDeliveries(wVoucher).length === 1);
  show('identity recovery still open after early clearing?',
    post.recoverByEscrow(citizen.identityKey).some((r) => r.caseId === W.caseId));

  section('Post-internal inspection: what actually remains per case');
  for (const c of [A, C, V, W]) {
    const v = post.inspectCase(c.caseId);
    show(`case ${c.caseId}`, {
      status: v.status,
      citizenLink: v.retainsCitizenLink,
      address: v.retainsAddress,
      escrow: v.escrow ? 'active' : null,
      heldLetters: v.heldLetters.length,
      clearKind: v.clearKind,
    });
  }

  section('Verifier cross-check for case A (post view vs room view)');
  const { postView, roomView } = verifier.crossCheckCase(A.caseId);
  show('room sees own case number', roomView.caseNumber);
  show('room exposes address/citizen?', { mailboxId: roomView.mailboxId, citizenId: roomView.citizenId });
}

main();
