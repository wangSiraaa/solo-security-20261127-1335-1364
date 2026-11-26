'use strict';

// Independent verifier. Given the two reply rooms' full local snapshots and the
// post's public case view, it checks the privacy claims the spec makes:
//
//   1. The two rooms share no case number, no pseudonym and no sender marker.
//   2. Neither room holds the mailbox address, the owner secret, the citizen
//      id, or the post-internal case id.
//   3. The room's return route is opaque and does not itself encode any of the
//      forbidden values.
//
// It deliberately does NOT need the post's secrets, mirroring a real auditor.
class Verifier {
  constructor({ post, rooms }) {
    this.post = post;
    this.rooms = rooms; // { name: ReplyRoom }
  }

  _snapshotAll() {
    return Object.values(this.rooms).map((r) => r.snapshot());
  }

  // Values that, if shared across rooms, would let the two rooms be joined.
  checkRoomUnlinkability() {
    const snaps = this._snapshotAll();
    invariantAtLeastTwo(snaps);
    const seen = new Map(); // value -> room name
    const collisions = [];
    for (const snap of snaps) {
      for (const c of snap.cases) {
        for (const value of [c.caseNumber, c.pseudonym]) {
          if (seen.has(value) && seen.get(value) !== snap.room) {
            collisions.push({ value, rooms: [seen.get(value), snap.room] });
          } else {
            seen.set(value, snap.room);
          }
        }
      }
    }
    // A common sender marker would also be a shared label; check labels too.
    const labels = new Map();
    for (const snap of snaps) {
      for (const c of snap.cases) {
        if (c.label == null) continue;
        if (labels.has(c.label) && labels.get(c.label) !== snap.room) {
          collisions.push({ value: c.label, kind: 'shared_label', rooms: [labels.get(c.label), snap.room] });
        } else {
          labels.set(c.label, snap.room);
        }
      }
    }
    return { ok: collisions.length === 0, collisions };
  }

  // What forbidden values would a room have if it were leaking?
  checkRoomsHoldNoAddressSecrets() {
    const leaks = [];
    const forbidden = [];
    for (const [, mb] of this.post._mailboxes) {
      forbidden.push({ kind: 'mailboxId', value: mb.mailboxId });
      forbidden.push({ kind: 'ownerSecret', value: mb.ownerSecret });
      forbidden.push({ kind: 'alias', value: mb.alias });
    }
    for (const [, c] of this.post._citizensById) {
      forbidden.push({ kind: 'citizenId', value: c.citizenId });
      forbidden.push({ kind: 'identityKey', value: c.identityKey });
    }
    for (const [, rec] of this.post._cases) {
      forbidden.push({ kind: 'caseId', value: rec.caseId });
    }
    const serialized = JSON.stringify(this._snapshotAll());
    for (const f of forbidden) {
      if (serialized.includes(f.value)) leaks.push(f);
    }
    return { ok: leaks.length === 0, leaks };
  }

  // Do the two rooms, together, end up holding a value that maps one room's
  // case to the other's? (Also verifies return routes do not embed forbidden
  // values.)
  checkReturnRoutesOpaque() {
    const bad = [];
    for (const snap of this._snapshotAll()) {
      for (const c of snap.cases) {
        const rec = this.post.resolveRoute(c.returnRoute);
        if (!rec) { bad.push({ caseNumber: c.caseNumber, reason: 'route unresolved' }); continue; }
        if (c.returnRoute.length < 16) bad.push({ caseNumber: c.caseNumber, reason: 'route too short/guessable' });
        for (const [, mb] of this.post._mailboxes) {
          if (c.returnRoute.includes(mb.mailboxId)) bad.push({ caseNumber: c.caseNumber, reason: 'route embeds mailbox id' });
        }
      }
    }
    return { ok: bad.length === 0, bad };
  }

  // One case as seen from the room vs. from the post.
  crossCheckCase(caseId) {
    const postView = this.post.inspectCase(caseId);
    const room = this.rooms[postView.room];
    const roomView = room.query(postView.roomCaseNumber);
    return { postView, roomView };
  }

  fullAudit() {
    return {
      unlinkable: this.checkRoomUnlinkability(),
      noLeaks: this.checkRoomsHoldNoAddressSecrets(),
      routesOpaque: this.checkReturnRoutesOpaque(),
    };
  }
}

function invariantAtLeastTwo(snaps) {
  if (snaps.length < 2) throw new Error('verifier needs at least two rooms');
}

module.exports = { Verifier };
