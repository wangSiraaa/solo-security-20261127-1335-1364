'use strict';

const { uid, invariant, PostError } = require('./util');

// A ReplyRoom is one of the two independently-queryable offices. Each room
// knows ONLY:
//   - its own case numbers (local numbering, unrelated to the other room)
//   - a pseudonym it minted for that case, local to this room
//   - an opaque returnRoute handed to it by the post station (it cannot resolve
//     the route, cannot see the owner, cannot see the mailbox address)
//   - per-letter delivery accounting
//
// It must never be given: the other room's case number, a shared sender
// marker, the mailbox address, the owner's secret, or any post-internal id
// that links back to the citizen.
//
// This is what makes "two rooms together cannot link one sender": the only
// values the two rooms could compare are their caseNumber and pseudonym, both
// of which are minted independently and share no derivation.
class ReplyRoom {
  constructor(name) {
    this.name = name;
    this._cases = new Map(); // ownCaseNumber -> case record
  }

  // Called by the post station when a new case must be tracked in this room.
  // The room mints its OWN case number and pseudonym; the post only supplies
  // the opaque return route.
  openCase(returnRoute, label) {
    invariant(typeof returnRoute === 'string' && returnRoute.length > 0,
      'BAD_RETURN_ROUTE', 'return route is required');
    const caseNumber = uid(`${this.name.toUpperCase()}-C-`);
    const pseudonym = uid(`${this.name.toUpperCase()}-P-`);
    const rec = {
      caseNumber,
      pseudonym,
      returnRoute,
      label: label || null,
      held: false, // true once a stop-delivery order is applied here
      deliveries: new Map(), // deliveryId -> delivery record
    };
    this._cases.set(caseNumber, rec);
    return { caseNumber, pseudonym };
  }

  _require(caseNumber) {
    const rec = this._cases.get(caseNumber);
    invariant(rec, 'NO_SUCH_CASE', `room ${this.name} has no case ${caseNumber}`);
    return rec;
  }

  // Clerk successfully hands the reply into the room. This is the deposit
  // registration and it is deliberately separate from pickup.
  deposit(caseNumber, body, meta = {}) {
    const rec = this._require(caseNumber);
    invariant(!rec.held, 'CASE_HELD', 'this case is on stop-delivery; deposit refused');
    const deliveryId = uid('DLV-');
    const delivery = {
      deliveryId,
      body,
      depositedAt: meta.now || Date.now(),
      pickedUpAt: null,
      pickedUpBy: null,
      returnedToPostAt: null,
      state: 'delivered_unclaimed', // "已投未取"
    };
    rec.deliveries.set(deliveryId, delivery);
    return { deliveryId, state: delivery.state };
  }

  // Owner (or their authorised bearer token) actually collects a letter.
  // This is the pickup registration, distinct from deposit.
  pickup(caseNumber, deliveryId, { bearer, now } = {}) {
    const rec = this._require(caseNumber);
    const delivery = rec.deliveries.get(deliveryId);
    invariant(delivery, 'NO_SUCH_DELIVERY', `no delivery ${deliveryId}`);
    invariant(!delivery.pickedUpAt, 'ALREADY_PICKED_UP', 'letter already collected');
    invariant(delivery.state !== 'returned', 'LETTER_RETURNED', 'letter was returned to post');
    if (bearer !== undefined) {
      invariant(bearer === rec.pseudonym, 'BAD_BEARER', 'pickup bearer does not match case');
    }
    delivery.pickedUpAt = now || Date.now();
    delivery.pickedUpBy = 'owner';
    delivery.state = 'picked_up';
    return { deliveryId, state: delivery.state, pickedUpAt: delivery.pickedUpAt };
  }

  // Post station asks the room to hand a still-unclaimed letter back because
  // the case was stopped / migrated. Room records the change but never learns
  // where it goes.
  recall(caseNumber, deliveryId, { now } = {}) {
    const rec = this._require(caseNumber);
    const delivery = rec.deliveries.get(deliveryId);
    invariant(delivery, 'NO_SUCH_DELIVERY', `no delivery ${deliveryId}`);
    invariant(!delivery.pickedUpAt, 'ALREADY_PICKED_UP', 'cannot recall a collected letter');
    invariant(delivery.state !== 'returned', 'ALREADY_RETURNED', 'already returned');
    delivery.state = 'returned';
    delivery.returnedToPostAt = now || Date.now();
    return { deliveryId, state: delivery.state };
  }

  setHeld(caseNumber, held) {
    const rec = this._require(caseNumber);
    rec.held = Boolean(held);
    return { caseNumber, held: rec.held };
  }

  // What a verifier sees when they query THIS room for THIS room's case.
  query(caseNumber) {
    const rec = this._require(caseNumber);
    return {
      room: this.name,
      caseNumber: rec.caseNumber,
      pseudonym: rec.pseudonym,
      held: rec.held,
      deliveries: [...rec.deliveries.values()].map((d) => ({ ...d })),
    };
  }

  // Full local view, for the verifier cross-check that two rooms share nothing.
  snapshot() {
    return {
      room: this.name,
      cases: [...this._cases.values()].map((rec) => ({
        caseNumber: rec.caseNumber,
        pseudonym: rec.pseudonym,
        returnRoute: rec.returnRoute,
        label: rec.label,
        held: rec.held,
        deliveries: [...rec.deliveries.values()].map((d) => ({ ...d })),
      })),
    };
  }
}

module.exports = { ReplyRoom, PostError };
