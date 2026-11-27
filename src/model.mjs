import { sha256, stableStringify } from './core.mjs';

export const SCOPE = Object.freeze({
  CAPTURE_SEGMENT: 'capture.segment',
  SAVE_SEGMENT: 'clip.save_segment',
  PRELISTEN: 'monitor.prelisten',
  SCHEDULE_CAPTURE: 'capture.scheduled',
  KEEP_ORIGINAL: 'retain.original',
  SPLIT_TRACKS: 'organize.split_tracks',
});

export const LEGACY_CONSENT_ID = 'consent_legacy_record_segment';
export const OLD_BUTTON_ID = 'btn.record.old';
export const AGENT_ID = 'agent_old_button_proxy';

export const LEGACY_SCOPES = Object.freeze([
  SCOPE.CAPTURE_SEGMENT,
  SCOPE.SAVE_SEGMENT,
]);

export const LEGACY_OBLIGATIONS = Object.freeze([
  'device.input.close_on_stop',
  'agent.bind_start_stop_fault_cleanup',
]);

export function packageHash(frames) {
  const slim = frames.map((frame) => ({
    seq: frame.seq,
    text: frame.text,
    sha256: frame.sha256,
    at: frame.at,
  }));
  return sha256(stableStringify(slim));
}

function emptyState() {
  return {
    bootstrapped: false,
    users: {},
    devices: {},
    consents: {},
    grants: [],
    sessions: {},
    projects: {},
    tracks: {},
    agents: {},
    segments: {},
    clips: {},
    originals: {},
    schedules: {},
    certificates: {},
    eventTypes: [],
  };
}

export function reduceState(events) {
  const s = emptyState();

  for (const e of events) {
    s.eventTypes.push(e.type);

    switch (e.type) {
      case 'bootstrap.completed':
        s.bootstrapped = true;
        s.bootstrap = { at: e.ts, fixture: e.fixture };
        break;

      case 'user.created':
        s.users[e.userId] = { userId: e.userId, name: e.name, createdAt: e.ts };
        break;

      case 'device.registered':
        s.devices[e.deviceId] = {
          deviceId: e.deviceId,
          publicKeyPem: e.publicKeyPem,
          deviceUrl: e.deviceUrl,
          lifecycle: 'paired',
          registeredAt: e.ts,
        };
        break;

      case 'device.lifecycle_changed':
        if (s.devices[e.deviceId]) {
          s.devices[e.deviceId].lifecycle = e.lifecycle;
          s.devices[e.deviceId].lifecycleReason = e.reason;
          s.devices[e.deviceId].lifecycleAt = e.ts;
        }
        break;

      case 'consent.given':
        s.consents[e.consentId] = {
          consentId: e.consentId,
          userId: e.userId,
          deviceId: e.deviceId || null,
          policyVersion: e.policyVersion,
          policyName: e.policyName,
          source: e.source,
          agreedOperation: e.agreedOperation,
          originalScopes: [...(e.scopes || [])],
          currentScopes: [...(e.scopes || [])],
          obligations: [...(e.obligations || [])],
          status: 'active',
          givenAt: e.ts,
        };
        break;

      case 'consent.narrowed': {
        const c = s.consents[e.consentId];
        if (c) {
          c.status = e.mode || 'narrowed';
          c.currentScopes = [...(e.scopes || [])];
          c.narrowedAt = e.ts;
          c.narrowReason = e.reason;
        }
        break;
      }

      case 'consent.restored': {
        const c = s.consents[e.consentId];
        if (c) {
          c.status = 'active';
          c.currentScopes = [...c.originalScopes];
          c.restoredAt = e.ts;
          delete c.narrowReason;
        }
        break;
      }

      case 'consent.revoked': {
        if (e.all) {
          for (const c of Object.values(s.consents)) {
            if (!e.userId || c.userId === e.userId) {
              c.status = 'revoked';
              c.currentScopes = [];
            }
          }
          for (const g of s.grants) {
            if (!e.userId || g.userId === e.userId) {
              g.status = 'revoked';
            }
          }
        } else if (e.consentId && s.consents[e.consentId]) {
          s.consents[e.consentId].status = 'revoked';
          s.consents[e.consentId].currentScopes = [];
        } else if (e.grantId) {
          const g = s.grants.find((x) => x.grantId === e.grantId);
          if (g) g.status = 'revoked';
        }
        break;
      }

      case 'migration.capabilities_granted':
        s.grants.push({
          grantId: e.grantId,
          userId: e.userId,
          projectId: e.projectId || null,
          deviceId: e.deviceId || null,
          scopes: [...(e.scopes || [])],
          selections: { ...(e.selections || {}) },
          presenceId: e.presenceId,
          status: 'active',
          grantedAt: e.ts,
        });
        break;

      case 'session.opened':
        s.sessions[e.sessionId] = {
          sessionId: e.sessionId,
          userId: e.userId,
          deviceId: e.deviceId,
          segmentId: e.segmentId || null,
          kind: e.kind || 'segment',
          status: 'open',
          openedAt: e.ts,
        };
        break;

      case 'session.revoked':
        if (s.sessions[e.sessionId]) {
          s.sessions[e.sessionId].status = 'revoked';
          s.sessions[e.sessionId].revokedAt = e.ts;
          s.sessions[e.sessionId].revokeReason = e.reason;
          s.sessions[e.sessionId].discard = e.discard !== false;
        }
        break;

      case 'project.created':
        s.projects[e.projectId] = {
          projectId: e.projectId,
          name: e.name,
          kind: e.kind,
          legacyButtonId: e.legacyButtonId || null,
          userPresentRequired: !!e.userPresentRequired,
          createdAt: e.ts,
        };
        break;

      case 'project.edited':
        if (s.projects[e.projectId]) {
          Object.assign(s.projects[e.projectId], e.patch || {});
          s.projects[e.projectId].lastEditedAt = e.ts;
        }
        break;

      case 'track.created':
        s.tracks[e.trackId] = {
          trackId: e.trackId,
          projectId: e.projectId,
          name: e.name,
          kind: e.kind || 'audio',
          createdAt: e.ts,
        };
        break;

      case 'agent.installed':
        s.agents[e.agentId] = {
          agentId: e.agentId,
          kind: e.kind,
          buttonId: e.buttonId,
          binds: [...(e.binds || [])],
          userId: e.userId || null,
          projectId: e.projectId || null,
          installedAt: e.ts,
          status: 'active',
        };
        break;

      case 'agent.removed':
        if (s.agents[e.agentId]) s.agents[e.agentId].status = 'removed';
        break;

      case 'segment.opened':
        s.segments[e.segmentId] = {
          segmentId: e.segmentId,
          operationId: e.operationId,
          sessionId: e.sessionId,
          agentId: e.agentId,
          userId: e.userId,
          deviceId: e.deviceId,
          projectId: e.projectId,
          trackId: e.trackId || null,
          buttonId: e.buttonId || null,
          autoAdmit: e.autoAdmit !== false,
          status: 'opening',
          frameCount: 0,
          lastFrameHash: null,
          createdAt: e.ts,
          backendStatus: null,
          receipt: null,
          receiptVerified: false,
          admissionStatus: 'pending',
          clipId: null,
          certificateId: null,
          cleanup: null,
        };
        break;

      case 'segment.device_started':
        if (s.segments[e.segmentId]) s.segments[e.segmentId].status = 'active';
        break;

      case 'segment.frame_accepted':
        if (s.segments[e.segmentId]) {
          s.segments[e.segmentId].status = 'active';
          s.segments[e.segmentId].frameCount = Math.max(
            s.segments[e.segmentId].frameCount,
            e.seq,
          );
          s.segments[e.segmentId].lastFrameHash = e.sha256;
        }
        break;

      case 'stop.requested':
        if (s.segments[e.segmentId]) {
          s.segments[e.segmentId].status = 'stop_requested';
          s.segments[e.segmentId].stopRequestedAt = e.ts;
          s.segments[e.segmentId].stopReason = e.reason || 'user_stop';
        }
        break;

      case 'stop.receipt_held':
      case 'stop.unconfirmed':
        if (s.segments[e.segmentId]) {
          s.segments[e.segmentId].status = 'stop_unconfirmed';
          s.segments[e.segmentId].receiptHeld = e.type === 'stop.receipt_held';
          s.segments[e.segmentId].unresolvedReason = e.reason || e.transportError || 'receipt_unavailable';
        }
        break;

      case 'device.stop_receipt.recorded':
        if (s.segments[e.segmentId]) {
          s.segments[e.segmentId].status = 'device_closed_verified';
          s.segments[e.segmentId].receipt = e.receipt;
          s.segments[e.segmentId].receiptVerified = true;
          s.segments[e.segmentId].receiptHeld = false;
          s.segments[e.segmentId].closedAt = e.ts;
          if (e.receipt?.finalSeq !== undefined) {
            s.segments[e.segmentId].frameCount = e.receipt.finalSeq;
          }
        }
        break;

      case 'segment.backend_stop_written':
        if (s.segments[e.segmentId]) {
          s.segments[e.segmentId].backendStatus = 'stopped';
          s.segments[e.segmentId].backendStopNote = e.note;
          s.segments[e.segmentId].backendStopAt = e.ts;
        }
        break;

      case 'segment.admission_granted':
        if (s.segments[e.segmentId]) {
          s.segments[e.segmentId].admissionStatus = 'admitted';
          s.segments[e.segmentId].clipId = e.clipId;
          s.segments[e.segmentId].admittedAt = e.ts;
        }
        break;

      case 'segment.admission_denied':
        if (s.segments[e.segmentId]) {
          s.segments[e.segmentId].admissionStatus = 'denied';
          s.segments[e.segmentId].admissionCode = e.code;
          s.segments[e.segmentId].admissionReason = e.reason;
          s.segments[e.segmentId].deniedAt = e.ts;
        }
        break;

      case 'clip.routed':
        s.clips[e.clipId] = {
          clipId: e.clipId,
          projectId: e.projectId,
          trackId: e.trackId,
          segmentId: e.segmentId,
          packageHash: e.packageHash,
          label: e.label,
          text: e.text,
          frames: e.frames || [],
          seqRange: e.seqRange,
          createdAt: e.ts,
          origin: e.origin || 'record_segment',
        };
        break;

      case 'clip.split':
        if (s.clips[e.parentClipId]) {
          s.clips[e.parentClipId].splitAt = e.ts;
          s.clips[e.parentClipId].childClipIds = e.childClipIds || [];
        }
        for (const child of e.children || []) {
          s.clips[child.clipId] = child;
        }
        break;

      case 'original.retained':
        s.originals[e.originalId] = {
          originalId: e.originalId,
          userId: e.userId,
          segmentId: e.segmentId,
          packageHash: e.packageHash,
          package: e.package,
          retainedAt: e.ts,
        };
        break;

      case 'schedule.created':
        s.schedules[e.scheduleId] = { ...e };
        break;

      case 'certificate.issued':
        s.certificates[e.certificateId] = e.certificate;
        if (s.segments[e.certificate.segmentId]) {
          s.segments[e.certificate.segmentId].certificateId = e.certificateId;
        }
        break;

      case 'fault.injected':
        if (s.segments[e.segmentId]) {
          s.segments[e.segmentId].lastFault = { reason: e.reason, at: e.ts };
        }
        break;

      case 'fault.cleanup_completed':
        if (s.segments[e.segmentId]) {
          s.segments[e.segmentId].status = 'fault_cleaned_verified';
          s.segments[e.segmentId].cleanup = e.receipt;
          s.segments[e.segmentId].receipt = e.receipt;
          s.segments[e.segmentId].receiptVerified = true;
          s.segments[e.segmentId].cleanedAt = e.ts;
        }
        break;

      default:
        break;
    }
  }

  return s;
}

export function authorize(state, query) {
  const {
    userId,
    deviceId = null,
    projectId = null,
    scope,
    sessionId = null,
  } = query;

  if (!state.users[userId]) {
    return { allowed: false, code: 'UNKNOWN_USER', reason: `Unknown user ${userId}.` };
  }

  if (deviceId) {
    const device = state.devices[deviceId];
    if (!device) {
      return { allowed: false, code: 'UNREGISTERED_DEVICE', reason: `Device ${deviceId} is not paired.` };
    }
    if (['retired', 'reset', 'unpaired'].includes(device.lifecycle)) {
      return {
        allowed: false,
        code: 'DEVICE_LIFECYCLE_FORBIDDEN',
        reason: `Device lifecycle is ${device.lifecycle}; old authorization cannot be transferred automatically.`,
      };
    }
  }

  if (sessionId) {
    const session = state.sessions[sessionId];
    if (!session) {
      return { allowed: false, code: 'UNKNOWN_SESSION', reason: `Session ${sessionId} does not exist.` };
    }
    if (session.userId !== userId) {
      return { allowed: false, code: 'SESSION_USER_MISMATCH', reason: 'Session belongs to another user.' };
    }
    if (session.status === 'revoked') {
      return {
        allowed: false,
        code: 'SESSION_REVOKED',
        reason: session.revokeReason || 'The recording session was withdrawn.',
      };
    }
  }

  const sources = [];

  for (const consent of Object.values(state.consents)) {
    if (consent.userId !== userId || consent.status !== 'active') continue;
    if (consent.deviceId && deviceId && consent.deviceId !== deviceId) continue;
    if (consent.currentScopes.includes(scope)) {
      sources.push({
        type: 'legacy_consent',
        id: consent.consentId,
        policyVersion: consent.policyVersion,
        obligations: consent.obligations,
      });
    }
  }

  for (const grant of state.grants) {
    if (grant.userId !== userId || grant.status !== 'active') continue;
    if (!grant.scopes.includes(scope)) continue;
    if (grant.deviceId && deviceId && grant.deviceId !== deviceId) continue;
    if (grant.projectId && projectId && grant.projectId !== projectId) continue;
    sources.push({
      type: 'explicit_capability_grant',
      id: grant.grantId,
      projectId: grant.projectId,
      deviceId: grant.deviceId,
    });
  }

  if (sources.length === 0) {
    return {
      allowed: false,
      code: 'SCOPE_NOT_GRANTED',
      reason: `No actual, active consent/grant covers ${scope}. A new-device notice is not consent.`,
      scope,
    };
  }

  const obligations = [...new Set(sources.flatMap((x) => x.obligations || []))];
  return { allowed: true, scope, sources, obligations };
}

export function clipsForProject(state, projectId) {
  return Object.values(state.clips)
    .filter((clip) => clip.projectId === projectId)
    .sort((a, b) => a.createdAt.localeCompare(b.createdAt));
}

export function trackView(state, projectId) {
  const tracks = Object.values(state.tracks)
    .filter((track) => track.projectId === projectId)
    .sort((a, b) => a.name.localeCompare(b.name));
  for (const track of tracks) {
    track.clips = clipsForProject(state, projectId).filter((clip) => clip.trackId === track.trackId);
  }
  return tracks;
}
