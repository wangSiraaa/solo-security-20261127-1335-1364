'use strict';

const {
  uid, token, sha256, hmac, canonical, DAY_MS, invariant, PostError,
} = require('./util');

// Case lifecycle states.
const S = {
  ACTIVE: 'active',
  FORWARDING: 'forwarding',        // migration authorised, new box not yet known
  HELD_AT_POST: 'held_at_post',    // migration revoked -> "留邮站待领"
  STOPPED: 'stopped',              // stop-delivery applied
  RELATION_DELETED: 'relation_deleted', // escrow: address gone, person link retained
  ERASED: 'erased',                // full erasure: no person link at all
  SUSPENDED: 'suspended',          // erased shell, suspended
};

const HOLD_STATES = new Set([
  S.HELD_AT_POST, S.STOPPED, S.RELATION_DELETED, S.ERASED, S.SUSPENDED,
]);

const CERT = {
  DELIVERY_RELATION_DELETED: 'DELIVERY_RELATION_DELETED',
  FULL_ERASURE: 'FULL_ERASURE',
};

class PostStation {
  constructor({ clock = () => Date.now() } = {}) {
    this.clock = clock;
    this._signKey = token('sign-');      // for certificates & vouchers
    this._salt = token('salt-');         // for non-reversible case tags
    this._citizens = new Map();          // identityKey -> citizen
    this._citizensById = new Map();      // citizenId -> citizen
    this._mailboxes = new Map();         // mailboxId -> mailbox
    this._aliases = new Map();           // alias -> { currentMailboxId, generation, free }
    this._cases = new Map();             // caseId -> case record
    this._routes = new Map();            // opaque returnRoute -> caseId (post-only)
    this._inTransit = new Map();         // transitId -> { caseId, body, at }
    this._held = new Map();              // caseId -> [ held letter ]
    this._certs = new Map();             // certId -> certificate
    this._rooms = new Map();             // name -> ReplyRoom
  }

  // ---- wiring -----------------------------------------------------------

  attachRoom(name, room) {
    this._rooms.set(name, room);
    room._postName = name;
    return this;
  }

  get roomNames() {
    return [...this._rooms.keys()];
  }

  _room(name) {
    const r = this._rooms.get(name);
    invariant(r, 'NO_SUCH_ROOM', `no reply room named ${name}`);
    return r;
  }

  // ---- citizens & mailboxes ---------------------------------------------

  registerCitizen() {
    const identityKey = token('id-');
    const citizenId = uid('CIT-');
    const rec = { citizenId, identityKey, createdAt: this.clock() };
    this._citizens.set(identityKey, rec);
    this._citizensById.set(citizenId, rec);
    return { citizenId, identityKey };
  }

  _citizenByIdentity(identityKey) {
    const c = this._citizens.get(identityKey);
    invariant(c, 'BAD_IDENTITY', 'unknown identity key');
    return c;
  }

  // Opening a box mints a fresh mailbox id and a fresh device secret. The
  // human-readable alias may be recycled from earlier boxes, but the mailbox
  // id never is, so old replies can never be re-pointed by alias reuse.
  openMailbox(identityKey) {
    const citizen = this._citizenByIdentity(identityKey);
    const mailboxId = uid('BOX-');
    const ownerSecret = token('own-');
    const alias = this._nextAlias();
    const mb = {
      mailboxId,
      citizenId: citizen.citizenId,
      ownerSecret,
      alias,
      active: true,
      retiredAt: null,
      createdAt: this.clock(),
    };
    this._mailboxes.set(mailboxId, mb);
    this._aliases.set(alias, {
      currentMailboxId: mailboxId, generation: (this._aliases.get(alias)?.generation || 0) + 1, free: false,
    });
    return { mailboxId, ownerSecret, alias };
  }

  _nextAlias() {
    // recycle the lowest-generation free alias, else mint a new one.
    let best = null;
    for (const [alias, st] of this._aliases) {
      if (st.free && (best === null || st.generation < this._aliases.get(best).generation)) best = alias;
    }
    return best || uid('alias-');
  }

  // Retire a box (device abandoned / address handed on). The alias returns to
  // the pool; the mailbox id stays, and any case still pointing at it keeps
  // pointing at it (an alias handed to a new owner must NOT inherit old mail).
  retireMailbox(mailboxId, { ownerSecret } = {}) {
    const mb = this._mailbox(mailboxId);
    if (ownerSecret !== undefined) this._requireOwner(mb, ownerSecret);
    mb.active = false;
    mb.retiredAt = this.clock();
    const st = this._aliases.get(mb.alias);
    if (st && st.currentMailboxId === mailboxId) st.free = true;
    return { mailboxId, aliasFreed: mb.alias };
  }

  _mailbox(mailboxId) {
    const mb = this._mailboxes.get(mailboxId);
    invariant(mb, 'NO_SUCH_MAILBOX', `no mailbox ${mailboxId}`);
    return mb;
  }

  _requireOwner(mb, ownerSecret) {
    invariant(ownerSecret === mb.ownerSecret, 'BAD_OWNER_SECRET', 'owner secret does not match mailbox');
  }

  // Post-only: resolve box owner (the whole point of the trusted station).
  resolveOwner(mailboxId) {
    const mb = this._mailbox(mailboxId);
    return { mailboxId, citizenId: mb.citizenId, alias: mb.alias, active: mb.active };
  }

  // ---- cases ------------------------------------------------------------

  // Open a case in one reply room. The room is told only an opaque return
  // route; it mints its own case number and pseudonym.
  openCase(identityKey, mailboxId, roomName, { label = null } = {}) {
    const citizen = this._citizenByIdentity(identityKey);
    const mb = this._mailbox(mailboxId);
    invariant(mb.citizenId === citizen.citizenId, 'NOT_BOX_OWNER', 'mailbox does not belong to citizen');
    const room = this._room(roomName);
    const returnRoute = token('route-');
    const opened = room.openCase(returnRoute, label);
    const caseId = uid('CASE-');
    this._routes.set(returnRoute, caseId);
    const rec = {
      caseId,
      citizenId: citizen.citizenId,
      room: roomName,
      roomCaseNumber: opened.caseNumber,
      pseudonym: opened.pseudonym,
      returnRoute,
      mailboxId,
      label,
      status: S.ACTIVE,
      migration: null,
      escrow: null,
      clearKind: null,
      createdAt: this.clock(),
      erasedAt: null,
      stopAt: null,
      voucherIssuedAt: null,
    };
    this._cases.set(caseId, rec);
    this._held.set(caseId, []);
    return { caseId, room: roomName, roomCaseNumber: opened.caseNumber };
  }

  _case(caseId) {
    const rec = this._cases.get(caseId);
    invariant(rec, 'NO_SUCH_CASE', `no case ${caseId}`);
    return rec;
  }

  _caseTag(caseId) {
    return sha256(`${this._salt}:${caseId}`);
  }

  // ---- letters: submit / in-transit / deliver ---------------------------

  // A reply has arrived at the post but has not yet been handed to a room.
  submitReply(caseId, body) {
    this._case(caseId);
    const transitId = uid('TRN-');
    this._inTransit.set(transitId, { transitId, caseId, body, at: this.clock() });
    return { transitId };
  }

  // Hand every in-transit reply to its room (or hold it if the case can't
  // accept delivery right now).
  flushTransit() {
    const results = [];
    for (const item of [...this._inTransit.values()]) {
      this._inTransit.delete(item.transitId);
      results.push(this._deliverOne(item.caseId, item.body, item.transitId));
    }
    return results;
  }

  deliverReply(caseId, body) {
    this.submitReply(caseId, body);
    return this.flushTransit().find((r) => r.caseId === caseId);
  }

  _deliverOne(caseId, body, transitId) {
    const rec = this._case(caseId);
    const now = this.clock();
    if (HOLD_STATES.has(rec.status)) {
      return this._hold(caseId, { body, transitId, reason: rec.status, at: now });
    }
    if (rec.migration && rec.migration.active) {
      if (!rec.migration.target) {
        if (now <= rec.migration.until) {
          // forwarding window still open; wait for the new box address.
          return this._hold(caseId, { body, transitId, reason: 'awaiting_forward', at: now });
        }
        // window expired with no new box -> must wait at post.
        rec.status = S.HELD_AT_POST;
        return this._hold(caseId, { body, transitId, reason: 'awaiting_pickup', at: now });
      }
    }
    const room = this._room(rec.room);
    const deposited = room.deposit(rec.roomCaseNumber, body, { now });
    rec.status = rec.migration && rec.migration.active ? S.ACTIVE : rec.status;
    return {
      caseId,
      state: deposited.state,
      deliveryId: deposited.deliveryId,
      routedToMailboxId: this._currentMailboxId(rec),
      roomCaseNumber: rec.roomCaseNumber,
    };
  }

  _currentMailboxId(rec) {
    if (rec.migration && rec.migration.active && rec.migration.target) return rec.migration.target;
    return rec.mailboxId;
  }

  _hold(caseId, item) {
    const held = this._held.get(caseId);
    held.push(item);
    return { caseId, state: 'held_at_post', reason: item.reason, heldCount: held.length };
  }

  // ---- collection -------------------------------------------------------

  // Normal device-based collection: must present the current box's secret.
  // This is what makes "reply delivered to the NEW box" observable: after
  // migration the old secret no longer opens the case.
  collect(caseId, deliveryId, { ownerSecret } = {}) {
    const rec = this._case(caseId);
    invariant(!HOLD_STATES.has(rec.status), 'CASE_HELD', `case is ${rec.status}`);
    const mb = this._mailbox(this._currentMailboxId(rec));
    this._requireOwner(mb, ownerSecret);
    const room = this._room(rec.room);
    const res = room.pickup(rec.roomCaseNumber, deliveryId, { bearer: rec.pseudonym, now: this.clock() });
    return { caseId, deliveryId, state: res.state, pickedUpAt: res.pickedUpAt };
  }

  // Device-loss recovery by identity — ONLY allowed while an escrow link is
  // alive, or for a case explicitly held at post. This is the deliberate
  // difference from the voucher method, which leaves nothing to recover by.
  collectRecovered(caseId, deliveryId, { identityKey } = {}) {
    const citizen = this._citizenByIdentity(identityKey);
    const rec = this._case(caseId);
    const escrowAlive = rec.escrow && rec.escrow.active && this.clock() < rec.escrow.until;
    invariant(escrowAlive || rec.status === S.HELD_AT_POST,
      'NOT_RECOVERABLE', 'case is not recoverable by identity (no live escrow / not held)');
    const ownerCitizenId = escrowAlive ? rec.escrow.citizenId : rec.citizenId;
    invariant(ownerCitizenId === citizen.citizenId, 'NOT_CASE_OWNER', 'identity does not own case');
    const room = this._room(rec.room);
    const res = room.pickup(rec.roomCaseNumber, deliveryId, { bearer: rec.pseudonym, now: this.clock() });
    return { caseId, deliveryId, state: res.state, via: 'identity_recovery' };
  }

  // Collect one of the letters the post is holding for the case.
  collectHeld(caseId, heldIndex, { ownerSecret, identityKey } = {}) {
    const rec = this._case(caseId);
    const held = this._held.get(caseId);
    invariant(held && held[heldIndex], 'NO_SUCH_HELD_LETTER', 'no such held letter');
    let ok = false;
    if (identityKey) ok = this._citizenByIdentity(identityKey).citizenId === rec.citizenId;
    if (!ok && ownerSecret !== undefined) {
      try { this._requireOwner(this._mailbox(this._currentMailboxId(rec)), ownerSecret); ok = true; } catch { ok = false; }
    }
    invariant(ok, 'BAD_PROOF', 'cannot prove ownership of held letter');
    const [letter] = held.splice(heldIndex, 1);
    return { caseId, state: 'released_to_owner', body: letter.body };
  }

  // Voucher pickup works with no help from the post at all: the room only
  // needs the pseudonym it minted. A bearer of a valid voucher can therefore
  // still collect even after the post has erased every person link.
  listVoucherDeliveries(voucher) {
    const payload = this.verifyVoucher(voucher);
    const room = this._room(payload.room);
    const view = room.query(payload.roomCaseNumber);
    return view.deliveries.filter((d) => d.state === 'delivered_unclaimed');
  }

  collectWithVoucher(voucher, deliveryId) {
    const payload = this.verifyVoucher(voucher);
    const room = this._room(payload.room);
    const res = room.pickup(payload.roomCaseNumber, deliveryId, { bearer: payload.pseudonym, now: this.clock() });
    return { state: res.state, deliveryId, via: 'voucher', room: payload.room };
  }

  // ---- stop-delivery ----------------------------------------------------

  // Apply per-case stop-delivery. Disposition (implementation's choice,
  // documented):
  //   * already-delivered-unclaimed replies are recalled from the room and
  //     held at the post;
  //   * in-transit replies are diverted to the post hold instead of being
  //     deposited;
  //   * already-collected replies are left with the owner;
  //   * the room is told to refuse any further deposit for the case.
  stopCase(caseId, proof = {}) {
    const rec = this._case(caseId);
    this._proveCaseOwner(rec, proof);
    invariant(rec.status === S.ACTIVE || rec.status === S.FORWARDING,
      'CANNOT_STOP', `cannot stop a case in state ${rec.status}`);
    const room = this._room(rec.room);
    room.setHeld(rec.roomCaseNumber, true);
    const recalled = [];
    for (const d of room.query(rec.roomCaseNumber).deliveries) {
      if (d.state === 'delivered_unclaimed') {
        room.recall(rec.roomCaseNumber, d.deliveryId, { now: this.clock() });
        recalled.push(d.deliveryId);
        this._hold(caseId, { deliveryId: d.deliveryId, body: d.body, reason: 'recalled_on_stop', at: this.clock() });
      }
    }
    const inTransitHeld = [];
    for (const item of [...this._inTransit.values()]) {
      if (item.caseId === caseId) {
        this._inTransit.delete(item.transitId);
        this._hold(caseId, { body: item.body, transitId: item.transitId, reason: 'in_transit_on_stop', at: this.clock() });
        inTransitHeld.push(item.transitId);
      }
    }
    rec.status = S.STOPPED;
    rec.stopAt = this.clock();
    return { caseId, status: rec.status, recalled, inTransitHeld };
  }

  _proveCaseOwner(rec, { ownerSecret, identityKey } = {}) {
    if (identityKey) {
      invariant(this._citizenByIdentity(identityKey).citizenId === rec.citizenId, 'BAD_IDENTITY', 'identity does not own case');
      return;
    }
    const mb = this._mailbox(this._currentMailboxId(rec));
    this._requireOwner(mb, ownerSecret);
  }

  // ---- migration --------------------------------------------------------

  // The old box owner authorises migrating ONLY this case, optionally to a
  // new box whose address is not yet known (forwarding window of `days`).
  beginMigration(caseId, { ownerSecret, newMailboxId = null, days = 3 } = {}) {
    const rec = this._case(caseId);
    invariant(!HOLD_STATES.has(rec.status), 'CASE_HELD', `case is ${rec.status}`);
    const origin = this._currentMailboxId(rec);
    const mb = this._mailbox(origin);
    this._requireOwner(mb, ownerSecret);
    if (newMailboxId) {
      const target = this._mailbox(newMailboxId);
      invariant(target.active, 'TARGET_INACTIVE', 'target mailbox is not active');
    }
    const until = this.clock() + days * DAY_MS;
    rec.migration = { active: true, origin, target: newMailboxId, until, startedAt: this.clock() };
    if (newMailboxId) {
      rec.mailboxId = newMailboxId;
      rec.status = S.ACTIVE;
      // deliver anything already waiting on the forwarding window.
      this._releaseAwaitingForward(rec);
    } else {
      rec.status = S.FORWARDING;
    }
    return {
      caseId, migration: { target: rec.migration.target, until: rec.migration.until }, status: rec.status,
    };
  }

  // The new box address became known inside the window.
  completeForwarding(caseId, newMailboxId, { ownerSecret } = {}) {
    const rec = this._case(caseId);
    invariant(rec.migration && rec.migration.active, 'NO_MIGRATION', 'no active migration');
    invariant(!rec.migration.target, 'ALREADY_TARGETED', 'migration already has a target');
    invariant(this.clock() <= rec.migration.until, 'FORWARD_WINDOW_EXPIRED',
      'the forwarding window has closed; the case must be picked up at the post');
    const origin = this._mailbox(rec.migration.origin);
    this._requireOwner(origin, ownerSecret);
    const target = this._mailbox(newMailboxId);
    invariant(target.active, 'TARGET_INACTIVE', 'target mailbox is not active');
    rec.migration.target = newMailboxId;
    rec.mailboxId = newMailboxId;
    rec.status = S.ACTIVE;
    this._releaseAwaitingForward(rec);
    return { caseId, target: newMailboxId, status: rec.status };
  }

  _releaseAwaitingForward(rec) {
    const held = this._held.get(rec.caseId) || [];
    for (const item of [...held]) {
      if (item.reason === 'awaiting_forward' && item.body !== undefined) {
        const idx = held.indexOf(item);
        held.splice(idx, 1);
        const room = this._room(rec.room);
        room.deposit(rec.roomCaseNumber, item.body, { now: this.clock() });
      }
    }
  }

  // Withdraw ONLY this case's migration authorisation. Per spec the case must
  // then stay at the post awaiting pickup: unclaimed replies are recalled and
  // held, routing reverts to the origin box.
  revokeMigration(caseId, { ownerSecret } = {}) {
    const rec = this._case(caseId);
    invariant(rec.migration && rec.migration.active, 'NO_MIGRATION', 'no active migration');
    const origin = this._mailbox(rec.migration.origin);
    this._requireOwner(origin, ownerSecret);
    rec.mailboxId = rec.migration.origin;
    rec.migration = null;
    rec.status = S.HELD_AT_POST;
    const room = this._room(rec.room);
    for (const d of room.query(rec.roomCaseNumber).deliveries) {
      if (d.state === 'delivered_unclaimed') {
        room.recall(rec.roomCaseNumber, d.deliveryId, { now: this.clock() });
        this._hold(caseId, { deliveryId: d.deliveryId, body: d.body, reason: 'recalled_on_revoke', at: this.clock() });
      }
    }
    // move any awaiting-forward letters into the general hold too.
    const held = this._held.get(caseId) || [];
    for (const item of held) if (item.reason === 'awaiting_forward') item.reason = 'awaiting_pickup';
    return { caseId, status: rec.status };
  }

  // ---- erasure / certificates ------------------------------------------

  // Method V: hand the per-case pickup voucher back to the citizen and clear
  // the case->person mapping immediately. The post can now honestly certify
  // FULL_ERASURE, and device/voucher loss is unrecoverable.
  eraseByVoucher(caseId, { identityKey } = {}) {
    const rec = this._case(caseId);
    const citizen = this._citizenByIdentity(identityKey);
    invariant(rec.citizenId === citizen.citizenId, 'NOT_CASE_OWNER', 'identity does not own case');
    invariant(!rec.voucherIssuedAt, 'VOUCHER_ALREADY_ISSUED', 'voucher already issued for this case');
    const now = this.clock();
    const voucher = this._issueVoucher(rec);
    rec.voucherIssuedAt = now;
    // clear the person link at the post.
    rec.citizenId = null;
    rec.mailboxId = null;
    rec.escrow = null;
    rec.clearKind = 'voucher';
    rec.status = S.ERASED;
    rec.erasedAt = now;
    const certificate = this._issueCertificate(CERT.FULL_ERASURE, rec, {
      method: 'voucher',
      note: 'post-to-person mapping erased; voucher is the only remaining bearer credential',
      recoverableByPost: false,
    });
    return { voucher, certificate };
  }

  // Method E: by the citizen's delegation the post keeps the case->person
  // relation in a private escrow until `until`, drops the address now, and
  // certifies DELIVERY_RELATION_DELETED. At the deadline a second call to
  // finalizeEscrow yields FULL_ERASURE.
  eraseByEscrow(caseId, { identityKey, days = 3 } = {}) {
    const rec = this._case(caseId);
    const citizen = this._citizenByIdentity(identityKey);
    invariant(rec.citizenId === citizen.citizenId, 'NOT_CASE_OWNER', 'identity does not own case');
    invariant(!rec.escrow, 'ESCROW_EXISTS', 'escrow already established');
    const now = this.clock();
    rec.escrow = { active: true, until: now + days * DAY_MS, citizenId: citizen.citizenId, startedAt: now };
    rec.citizenId = null;       // direct link cleared; a private escrow copy remains
    rec.mailboxId = null;       // address / delivery route forgotten
    rec.migration = null;
    rec.clearKind = 'escrow';
    rec.status = S.RELATION_DELETED;
    // The owner also receives a room-pickup voucher so they can collect their
    // replies without the post resolving any person link.
    const voucher = this._issueVoucher(rec);
    rec.voucherIssuedAt = now;
    const certificate = this._issueCertificate(CERT.DELIVERY_RELATION_DELETED, rec, {
      method: 'escrow',
      note: 'delivery relation removed; owner may still recover this case by identity until deadline',
      recoverableByPost: true,
      recoverableUntil: rec.escrow.until,
    });
    return { certificate, voucher, recoverableUntil: rec.escrow.until };
  }

  finalizeEscrow(caseId, { identityKey } = {}) {
    const rec = this._case(caseId);
    invariant(rec.escrow, 'NO_ESCROW', 'no escrow established');
    invariant(this.clock() >= rec.escrow.until, 'ESCROW_NOT_EXPIRED', 'escrow deadline not reached');
    if (identityKey) {
      const citizen = this._citizenByIdentity(identityKey);
      invariant(rec.escrow.citizenId === citizen.citizenId || rec.citizenId === citizen.citizenId, 'NOT_CASE_OWNER', 'identity does not own case');
    }
    rec.citizenId = null;
    rec.escrow = null;
    rec.status = S.ERASED;
    rec.erasedAt = this.clock();
    const certificate = this._issueCertificate(CERT.FULL_ERASURE, rec, {
      method: 'escrow-expired',
      note: 'escrow deadline reached; case-to-person relation fully cleared',
      recoverableByPost: false,
    });
    return { certificate };
  }

  // Early erasure on presentation of the original voucher. In escrow mode this
  // accelerates the deadline to now; in voucher mode the relation was already
  // fully erased at handover, so this only confirms it. By default unclaimed
  // room letters are NOT purged: a genuine voucher holder must always be able
  // to collect their own reply, and the room never held an address anyway.
  requestEarlyErasure(voucher, { purge = false } = {}) {
    const payload = this.verifyVoucher(voucher);
    const rec = this._case(payload.caseId);
    const room = this._room(rec.room);
    let purged = 0;
    for (const d of room.query(rec.roomCaseNumber).deliveries) {
      if (d.state === 'delivered_unclaimed' && purge) {
        room.recall(rec.roomCaseNumber, d.deliveryId, { now: this.clock() });
        purged += 1;
      }
    }
    const alreadyErased = Boolean(rec.erasedAt);
    if (!alreadyErased) {
      rec.citizenId = null;
      rec.mailboxId = null;
      rec.escrow = null;
      rec.migration = null;
      rec.clearKind = 'early_voucher';
      rec.status = S.ERASED;
      rec.erasedAt = this.clock();
    }
    rec.stopAt = rec.stopAt || this.clock();
    const certificate = this._issueCertificate(CERT.FULL_ERASURE, rec, {
      method: alreadyErased ? 'confirmed' : 'early_voucher',
      note: alreadyErased
        ? 'relation was already fully erased at voucher handover; confirmed on request'
        : 'early erasure requested with original voucher; escrow deadline accelerated to now',
      recoverableByPost: false,
      purgedDeliveries: purged,
    });
    return { certificate, purged, alreadyErased };
  }

  // Recoverability query used to answer "can a lost device still be found?".
  recoverByEscrow(identityKey) {
    const citizen = this._citizenByIdentity(identityKey);
    const now = this.clock();
    const found = [];
    for (const rec of this._cases.values()) {
      const linked = (rec.escrow && rec.escrow.citizenId === citizen.citizenId)
        || (rec.citizenId === citizen.citizenId);
      if (!linked) continue;
      const escrowAlive = rec.escrow && rec.escrow.active && now < rec.escrow.until;
      if (!escrowAlive) continue;
      const room = this._room(rec.room);
      found.push({
        caseId: rec.caseId,
        room: rec.room,
        roomCaseNumber: rec.roomCaseNumber,
        recoverableUntil: rec.escrow.until,
        unclaimed: room.query(rec.roomCaseNumber).deliveries.filter((d) => d.state === 'delivered_unclaimed').length,
      });
    }
    return found;
  }

  // Escrow-authorised single-case migration by identity.
  migrateByEscrowIdentity(identityKey, caseId, newMailboxId) {
    const citizen = this._citizenByIdentity(identityKey);
    const rec = this._case(caseId);
    invariant(rec.escrow && rec.escrow.active && this.clock() < rec.escrow.until,
      'ESCROW_EXPIRED', 'escrow link is not alive');
    invariant(rec.escrow.citizenId === citizen.citizenId, 'NOT_CASE_OWNER', 'identity does not own escrow');
    const target = this._mailbox(newMailboxId);
    invariant(target.active, 'TARGET_INACTIVE', 'target mailbox is not active');
    rec.mailboxId = newMailboxId;
    rec.status = S.ACTIVE;
    return { caseId, migratedTo: newMailboxId };
  }

  // Voucher-authorised migration: no durable person link is restored at the
  // post, we simply re-bind this one case's delivery route for the voucher
  // bearer, then forget again.
  migrateByVoucher(voucher, newMailboxId) {
    const payload = this.verifyVoucher(voucher);
    const target = this._mailbox(newMailboxId);
    invariant(target.active, 'TARGET_INACTIVE', 'target mailbox is not active');
    const rec = this._case(payload.caseId);
    rec.mailboxId = newMailboxId;       // durable person/citizen link stays null
    // the route is usable again under the new box; the citizen link is NOT
    // restored, so this case remains unrecoverable by identity.
    rec.status = S.ACTIVE;
    return { caseId: payload.caseId, migratedTo: newMailboxId, citizenLink: rec.citizenId };
  }

  // Suspend an already-erased case whose voucher is lost. This only changes a
  // shell flag; a voucher-holder can still collect at the room.
  suspendCase(caseId) {
    const rec = this._case(caseId);
    invariant(rec.status === S.ERASED, 'NOT_ERASED', 'only an erased case can be suspended');
    rec.status = S.SUSPENDED;
    return { caseId, status: rec.status };
  }

  // ---- vouchers & certificates -----------------------------------------

  _issueVoucher(rec) {
    const payload = {
      v: 1,
      kind: 'pickup-voucher',
      caseId: rec.caseId,
      room: rec.room,
      roomCaseNumber: rec.roomCaseNumber,
      pseudonym: rec.pseudonym,
      issuedAt: this.clock(),
    };
    const sig = hmac(this._signKey, canonical(payload));
    return { ...payload, sig };
  }

  verifyVoucher(voucher) {
    invariant(voucher && typeof voucher === 'object', 'BAD_VOUCHER', 'voucher missing');
    const { sig, ...payload } = voucher;
    const expect = hmac(this._signKey, canonical(payload));
    invariant(sig === expect, 'BAD_VOUCHER_SIG', 'voucher signature invalid');
    invariant(this._rooms.has(payload.room), 'BAD_VOUCHER', 'voucher names unknown room');
    return payload;
  }

  _issueCertificate(kind, rec, extra) {
    const certId = uid('CERT-');
    const body = {
      certId,
      kind,
      caseTag: this._caseTag(rec.caseId), // non-reversible, no person link
      issuedAt: this.clock(),
      post: 'local-post-station',
      ...extra,
    };
    const sig = hmac(this._signKey, canonical(body));
    const certificate = { ...body, sig };
    this._certs.set(certId, certificate);
    return certificate;
  }

  verifyCertificate(certificate) {
    const { sig, ...body } = certificate;
    return sig === hmac(this._signKey, canonical(body));
  }

  getCertificate(certId) {
    return this._certs.get(certId) || null;
  }

  // ---- verification / inspection ---------------------------------------

  // What the post ACTUALLY retains for this case right now (no secrets, but
  // enough for an auditor to see whether a person link / address survives).
  inspectCase(caseId) {
    const rec = this._case(caseId);
    const room = this._room(rec.room);
    return {
      caseId: rec.caseId,
      status: rec.status,
      room: rec.room,
      roomCaseNumber: rec.roomCaseNumber,
      retainsCitizenLink: rec.citizenId !== null && rec.citizenId !== undefined,
      retainsAddress: rec.mailboxId !== null && rec.mailboxId !== undefined,
      // true if ANY person link survives — direct or inside a live escrow.
      retainsPersonLinkAnywhere: (rec.citizenId !== null && rec.citizenId !== undefined)
        || Boolean(rec.escrow && rec.escrow.active),
      escrow: rec.escrow ? { active: rec.escrow.active, until: rec.escrow.until } : null,
      migration: rec.migration ? { ...rec.migration } : null,
      heldLetters: (this._held.get(caseId) || []).map((h) => ({ reason: h.reason, deliveryId: h.deliveryId })),
      clearKind: rec.clearKind,
      roomState: {
        caseNumber: rec.roomCaseNumber,
        held: room.query(rec.roomCaseNumber).held,
        deliveries: room.query(rec.roomCaseNumber).deliveries.map((d) => ({ deliveryId: d.deliveryId, state: d.state })),
      },
    };
  }

  // Return-route resolution stays private to the post; rooms never get this.
  resolveRoute(returnRoute) {
    const caseId = this._routes.get(returnRoute);
    return caseId ? this._case(caseId) : null;
  }
}

module.exports = { PostStation, S, CERT, PostError };
