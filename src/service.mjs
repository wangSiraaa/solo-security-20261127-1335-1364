import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import {
  HttpError,
  ensureDir,
  generateEd25519KeyPair,
  newId,
  nowIso,
  readJson,
  readJsonBody,
  sendJson,
  signObject,
  verifyObject,
  writeJsonAtomic,
} from './core.mjs';
import { EventStore } from './event-store.mjs';
import {
  AGENT_ID,
  LEGACY_CONSENT_ID,
  LEGACY_OBLIGATIONS,
  LEGACY_SCOPES,
  OLD_BUTTON_ID,
  SCOPE,
  authorize,
  packageHash,
  reduceState,
  trackView,
} from './model.mjs';

const TERMINAL_DEVICE_STATUSES = new Set(['device_closed_verified', 'fault_cleaned_verified']);

export class UpgradeService {
  constructor({ dataDir, deviceUrl, pairingToken, publicDir }) {
    this.dataDir = dataDir;
    this.deviceUrl = deviceUrl.replace(/\/$/, '');
    this.pairingToken = pairingToken;
    this.publicDir = publicDir;
    ensureDir(dataDir);

    this.store = new EventStore(path.join(dataDir, 'service-events.jsonl'));
    this.state = reduceState(this.store.events);
    this.presence = new Map();

    this.keyFile = path.join(dataDir, 'service-key.json');
    const keys = readJson(this.keyFile, null);
    if (keys?.publicKeyPem && keys?.privateKeyPem) {
      this.keys = keys;
    } else {
      this.keys = generateEd25519KeyPair();
      writeJsonAtomic(this.keyFile, this.keys);
    }
  }

  record(type, payload = {}) {
    const event = this.store.append(type, payload);
    this.state = reduceState(this.store.events);
    return event;
  }

  async waitForDevice(timeoutMs = 8000) {
    const deadline = Date.now() + timeoutMs;
    let lastError;
    while (Date.now() < deadline) {
      try {
        const res = await fetch(`${this.deviceUrl}/health`);
        if (res.ok) return await res.json();
      } catch (error) {
        lastError = error;
      }
      await new Promise((resolve) => setTimeout(resolve, 120));
    }
    throw new HttpError(502, 'DEVICE_UNREACHABLE', `Cannot reach simulated device at ${this.deviceUrl}`, {
      cause: lastError?.message,
    });
  }

  async deviceRequest(method, apiPath, body = undefined) {
    let res;
    try {
      res = await fetch(`${this.deviceUrl}${apiPath}`, {
        method,
        headers: {
          'content-type': 'application/json',
          'x-pairing-token': this.pairingToken,
        },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (error) {
      throw new HttpError(502, 'DEVICE_UNREACHABLE', error.message);
    }

    const raw = await res.text();
    let json = {};
    if (raw) {
      try {
        json = JSON.parse(raw);
      } catch {
        json = { raw };
      }
    }
    if (!res.ok) {
      throw new HttpError(res.status, json.code || 'DEVICE_ERROR', json.message || `Device returned ${res.status}`, json.details);
    }
    return json;
  }

  auth(query) {
    const result = authorize(this.state, query);
    if (!result.allowed) {
      throw new HttpError(403, result.code, result.reason, { scope: result.scope });
    }
    return result;
  }

  requirePresence(req, body = {}) {
    const id = req.headers['x-presence-id'] || body.presenceId;
    const presence = id ? this.presence.get(id) : null;
    if (!presence || presence.expiresAt < Date.now()) {
      if (id) this.presence.delete(id);
      throw new HttpError(
        403,
        'FRESH_USER_PRESENCE_REQUIRED',
        'A real user must be present at the offline console. Restart/replay does not restore presence.',
      );
    }
    const userId = body.userId || req.headers['x-user-id'];
    if (userId && presence.userId !== userId) {
      throw new HttpError(403, 'PRESENCE_USER_MISMATCH', 'Presence belongs to another user.');
    }
    const projectId = body.projectId || null;
    if (presence.projectId && projectId && presence.projectId !== projectId) {
      throw new HttpError(403, 'PRESENCE_PROJECT_MISMATCH', 'Presence is scoped to another project.');
    }
    return presence;
  }

  assertPresence(body) {
    if (!body.userId || !this.state.users[body.userId]) {
      throw new HttpError(400, 'KNOWN_USER_REQUIRED', 'A known userId is required to assert presence.');
    }
    const id = newId('pres');
    const ttlMs = Number(body.ttlMs || 15 * 60 * 1000);
    const presence = {
      id,
      userId: body.userId,
      projectId: body.projectId || null,
      createdAt: Date.now(),
      expiresAt: Date.now() + ttlMs,
      source: body.source || 'local_console',
    };
    this.presence.set(id, presence);
    this.record('presence.asserted', {
      presenceId: id,
      userId: presence.userId,
      projectId: presence.projectId,
      expiresAt: new Date(presence.expiresAt).toISOString(),
    });
    return { ...presence, token: id };
  }

  presentNewNotice(body = {}) {
    const userId = body.userId || 'u_legacy';
    this.record('notice.presented', {
      userId,
      policyVersion: body.policyVersion || 'device-v2-granular-capabilities',
      shownCapabilities: body.shownCapabilities || Object.values(SCOPE),
      acknowledgedByViewer: !!body.acknowledged,
    });
    return {
      ok: true,
      consentGranted: false,
      message: '新版设备说明仅被展示/阅读；它不会把旧“录一段”同意扩张为常驻监听或任何新能力。',
    };
  }

  async setDeviceLifecycle(deviceId, body = {}) {
    const device = this.state.devices[deviceId];
    if (!device) throw new HttpError(404, 'DEVICE_NOT_FOUND', 'Device is not paired.');
    const lifecycle = body.lifecycle;
    if (!['paired', 'online', 'offline', 'retired', 'reset', 'unpaired'].includes(lifecycle)) {
      throw new HttpError(400, 'BAD_LIFECYCLE', 'Invalid device lifecycle.');
    }
    this.record('device.lifecycle_changed', { deviceId, lifecycle, reason: body.reason || null });
    try {
      await this.deviceRequest('POST', '/device/lifecycle', { lifecycle, reason: body.reason });
    } catch (error) {
      if (!body.allowUnreachable) throw error;
    }
    return { ok: true, device: this.state.devices[deviceId] };
  }

  async bootstrap() {
    if (this.state.bootstrapped) {
      return { ok: true, idempotent: true, fixture: this.state.bootstrap };
    }

    const identity = await this.deviceRequest('GET', '/device/identity');

    this.record('user.created', { userId: 'u_legacy', name: '老用户 / Legacy Station User' });
    this.record('device.registered', {
      deviceId: identity.deviceId,
      publicKeyPem: identity.publicKeyPem,
      deviceUrl: this.deviceUrl,
    });
    this.record('consent.given', {
      consentId: LEGACY_CONSENT_ID,
      userId: 'u_legacy',
      deviceId: identity.deviceId,
      policyVersion: 'classic-recorder-v1',
      policyName: '录一段（旧按钮一体化说明）',
      source: 'legacy_import_from_actual_operation_log',
      agreedOperation: 'record_segment',
      scopes: LEGACY_SCOPES,
      obligations: LEGACY_OBLIGATIONS,
    });

    this.record('project.created', {
      projectId: 'proj_old_button_only',
      name: '只有原按钮标号的老工程',
      kind: 'legacy_button_only',
      legacyButtonId: OLD_BUTTON_ID,
      userPresentRequired: true,
    });
    this.record('track.created', {
      trackId: 'trk_old_marker',
      projectId: 'proj_old_button_only',
      name: '旧工程占位轨',
    });

    this.record('project.created', {
      projectId: 'proj_new_multitrack',
      name: '新分轨工程',
      kind: 'multitrack',
      userPresentRequired: false,
    });
    this.record('track.created', { trackId: 'trk_take', projectId: 'proj_new_multitrack', name: '录段主轨' });
    this.record('track.created', { trackId: 'trk_voice', projectId: 'proj_new_multitrack', name: '人声分轨' });
    this.record('track.created', { trackId: 'trk_room', projectId: 'proj_new_multitrack', name: '环境分轨' });

    this.record('agent.installed', {
      agentId: AGENT_ID,
      kind: 'old-button-semantics-proxy',
      buttonId: OLD_BUTTON_ID,
      userId: 'u_legacy',
      binds: ['start', 'stop', 'fault-cleanup'],
      guarantee: 'One press starts one segment; stop or fault compensation closes the device input.',
      doesNotGrant: [SCOPE.PRELISTEN, SCOPE.SCHEDULE_CAPTURE, SCOPE.KEEP_ORIGINAL, SCOPE.SPLIT_TRACKS],
    });
    this.record('bootstrap.completed', { fixture: 'offline-radio-1399' });

    return { ok: true, idempotent: false };
  }

  setPreviewOnly(body = {}) {
    const userId = body.userId || 'u_legacy';
    const consentId = body.consentId || LEGACY_CONSENT_ID;
    const consent = this.state.consents[consentId];
    if (!consent || consent.userId !== userId) {
      throw new HttpError(404, 'CONSENT_NOT_FOUND', 'Legacy consent was not found.');
    }
    this.record('consent.narrowed', {
      consentId,
      userId,
      mode: 'preview_only_save_forbidden',
      scopes: [SCOPE.PRELISTEN],
      reason: body.reason || 'acceptance: old consent changed to preview-only/no-save',
    });
    return { ok: true, authorization: this.authorizationSummary({ userId, deviceId: consent.deviceId }) };
  }

  restoreConsent(body = {}) {
    const consentId = body.consentId || LEGACY_CONSENT_ID;
    const consent = this.state.consents[consentId];
    if (!consent) throw new HttpError(404, 'CONSENT_NOT_FOUND', 'Legacy consent was not found.');
    this.record('consent.restored', {
      consentId,
      userId: body.userId || consent.userId,
      reason: body.reason || 'acceptance: restore actual legacy consent',
    });
    return { ok: true, authorization: this.authorizationSummary({ userId: consent.userId, deviceId: consent.deviceId }) };
  }

  authorizationSummary(query) {
    const scopes = Object.values(SCOPE).reduce((out, scope) => {
      const result = authorize(this.state, { ...query, scope });
      out[scope] = result.allowed
        ? { allowed: true, sources: result.sources, obligations: result.obligations }
        : { allowed: false, code: result.code, reason: result.reason };
      return out;
    }, {});
    return { query, scopes };
  }

  upgradeAssessment(query = {}) {
    const userId = query.userId || 'u_legacy';
    const deviceId = query.deviceId || 'dev_studio';
    const authorization = this.authorizationSummary({ userId, deviceId });
    const projects = Object.values(this.state.projects).map((project) => {
      const unattendedGrants = this.state.grants
        .filter((grant) => grant.status === 'active' && (!grant.projectId || grant.projectId === project.projectId))
        .map((grant) => ({ grantId: grant.grantId, scopes: grant.scopes, projectId: grant.projectId }));
      return {
        projectId: project.projectId,
        name: project.name,
        kind: project.kind,
        legacyButtonId: project.legacyButtonId,
        userPresentRequired: project.userPresentRequired,
        oldButtonPath: project.legacyButtonId
          ? 'compat-agent:start/stop/fault-cleanup as one operation'
          : null,
        unattendedGrants,
        rule: project.userPresentRequired
          ? 'Only old button semantics are imported; editing or migration requires a fresh present user.'
          : 'Explicit persisted project grants may run after restart; otherwise require a present user.',
      };
    });
    const incompleteSegments = Object.values(this.state.segments)
      .filter((segment) => !segment.receiptVerified)
      .map((segment) => ({
        segmentId: segment.segmentId,
        status: segment.status,
        deviceId: segment.deviceId,
        operationId: segment.operationId,
        required: 'idempotent device stop/cleanup and signed receipt; never resume capture',
      }));

    return {
      chosenMigrationPath: 'keep_old_button_semantics_compatibility_agent',
      answerToPersistentMonitoring:
        'No. record_segment does not imply monitor.prelisten, capture.scheduled, retain.original, or organize.split_tracks.',
      safeHandoffBoundary: 'verified_device_terminal_receipt_plus_matching_package_hash',
      restartPolicy: {
        absentSafetyReconcilerMay: [
          'stop_or_cleanup_orphan_operation',
          'receive_held_receipt',
          'admit_under_current_consent',
        ],
        absentPersistedGrantsMay: ['use_explicit_project_scoped_new_capability'],
        freshPresenceRequiredFor: [
          'start_new_segment',
          'edit_legacy_button_only_project',
          'grant_new_capability',
        ],
        resumeSameSegment: false,
      },
      authorization,
      agent: this.state.agents[AGENT_ID] || null,
      projects,
      incompleteSegments,
    };
  }

  async pressOldButton(body, req) {
    this.requirePresence(req, body);
    const userId = body.userId || 'u_legacy';
    const deviceId = body.deviceId || 'dev_studio';
    const projectId = body.projectId || 'proj_new_multitrack';
    const trackId = body.trackId || 'trk_take';

    this.auth({ userId, deviceId, projectId, scope: SCOPE.CAPTURE_SEGMENT });
    const project = this.state.projects[projectId];
    if (!project) throw new HttpError(404, 'PROJECT_NOT_FOUND', 'Project not found.');
    const track = this.state.tracks[trackId];
    if (!track || track.projectId !== projectId) {
      throw new HttpError(400, 'TRACK_PROJECT_MISMATCH', 'trackId does not belong to projectId.');
    }

    const segmentId = newId('seg');
    const operationId = newId('devop');
    const sessionId = newId('ses');
    const buttonId = body.buttonId || project.legacyButtonId || OLD_BUTTON_ID;

    this.record('session.opened', { sessionId, userId, deviceId, segmentId, kind: 'old_button_segment' });
    this.record('segment.opened', {
      segmentId,
      operationId,
      sessionId,
      agentId: AGENT_ID,
      userId,
      deviceId,
      projectId,
      trackId,
      buttonId,
      autoAdmit: body.autoAdmit !== false,
    });

    try {
      const started = await this.deviceRequest('POST', '/device/operations/start', {
        operationId,
        segmentId,
        sessionId,
      });
      this.record('segment.device_started', { segmentId, deviceStatus: started.operation?.state || 'capturing' });
    } catch (error) {
      this.record('segment.open_failed', { segmentId, code: error.code, message: error.message });
      await this.safeCleanup(segmentId, 'start_failed_compensation');
      throw error;
    }

    return { ok: true, segment: this.state.segments[segmentId], agent: this.state.agents[AGENT_ID] };
  }

  async appendFrame(segmentId, body, req) {
    const segment = this.requireSegment(segmentId);
    const userId = body.userId || segment.userId;

    try {
      this.auth({
        userId,
        deviceId: segment.deviceId,
        projectId: segment.projectId,
        sessionId: segment.sessionId,
        scope: SCOPE.CAPTURE_SEGMENT,
      });
    } catch (error) {
      await this.safeStop(segmentId, { reason: 'authorization_changed_stop_input' }).catch(() => {});
      throw error;
    }

    if (segment.receiptVerified || TERMINAL_DEVICE_STATUSES.has(segment.status)) {
      throw new HttpError(
        409,
        'SEGMENT_CLOSED',
        'This segment has a terminal device receipt. Press the button again to start a separately authorized segment.',
      );
    }

    let frameAck;
    try {
      frameAck = await this.deviceRequest(
        'POST',
        `/device/operations/${encodeURIComponent(segment.operationId)}/frames`,
        { text: body.text },
      );
    } catch (error) {
      if (error.status === 409) {
        await this.safeCleanup(segmentId, 'frame_reported_input_fault').catch(() => {});
      }
      throw error;
    }

    this.record('segment.frame_accepted', {
      segmentId,
      seq: frameAck.seq,
      sha256: frameAck.sha256,
    });
    return { ok: true, ...frameAck, segment: this.state.segments[segmentId] };
  }

  requireSegment(segmentId) {
    const segment = this.state.segments[segmentId];
    if (!segment) throw new HttpError(404, 'SEGMENT_NOT_FOUND', 'Segment not found.');
    return segment;
  }

  verifyReceiptObject(segment, receipt) {
    const device = this.state.devices[segment.deviceId];
    if (!device?.publicKeyPem) {
      throw new HttpError(502, 'DEVICE_KEY_UNKNOWN', 'No trusted public key for device.');
    }
    if (!receipt?.signatureB64) {
      throw new HttpError(502, 'RECEIPT_SIGNATURE_MISSING', 'Device receipt has no signature.');
    }
    const { signatureB64, ...payload } = receipt;
    const valid = verifyObject(device.publicKeyPem, payload, signatureB64);
    if (!valid) throw new HttpError(502, 'INVALID_DEVICE_RECEIPT', 'Device receipt signature is invalid.');
    if (receipt.deviceId !== segment.deviceId) {
      throw new HttpError(409, 'RECEIPT_DEVICE_MISMATCH', 'Receipt was signed by another device.');
    }
    if (receipt.operationId !== segment.operationId || receipt.segmentId !== segment.segmentId) {
      throw new HttpError(409, 'RECEIPT_SCOPE_MISMATCH', 'Receipt does not belong to this segment operation.');
    }
    return true;
  }

  async storeReceipt(segmentId, receipt) {
    const segment = this.requireSegment(segmentId);
    this.verifyReceiptObject(segment, receipt);
    this.record('device.stop_receipt.recorded', { segmentId, receipt });
    await this.maybeAutoAdmit(segmentId);
    return this.state.segments[segmentId];
  }

  async storeCleanupReceipt(segmentId, receipt) {
    const segment = this.requireSegment(segmentId);
    this.verifyReceiptObject(segment, receipt);
    this.record('fault.cleanup_completed', { segmentId, receipt });
    await this.maybeAutoAdmit(segmentId);
    return this.state.segments[segmentId];
  }

  async maybeAutoAdmit(segmentId) {
    const segment = this.state.segments[segmentId];
    if (segment?.autoAdmit && segment.admissionStatus === 'pending') {
      try {
        await this.admitSegment(segmentId, { system: true });
      } catch (error) {
        if (error.status >= 500) throw error;
      }
    }
  }

  async safeStop(segmentId, options = {}) {
    try {
      return await this.stopSegment(segmentId, options);
    } catch (error) {
      this.record('stop.unconfirmed', {
        segmentId,
        reason: options.reason || 'safe_stop_failed',
        transportError: error.message,
      });
      return this.state.segments[segmentId];
    }
  }

  async safeCleanup(segmentId, reason) {
    const segment = this.state.segments[segmentId];
    if (!segment) return null;
    try {
      const result = await this.deviceRequest(
        'POST',
        `/device/operations/${encodeURIComponent(segment.operationId)}/cleanup`,
        { reason },
      );
      if (result.receipt) await this.storeCleanupReceipt(segmentId, result.receipt);
      return this.state.segments[segmentId];
    } catch (error) {
      this.record('stop.unconfirmed', { segmentId, reason: 'cleanup_unreachable', transportError: error.message });
      return this.state.segments[segmentId];
    }
  }

  async stopSegment(segmentId, body = {}) {
    let segment = this.requireSegment(segmentId);
    if (segment.receiptVerified) return { ok: true, idempotent: true, segment };

    if (!['stop_requested', 'stop_unconfirmed'].includes(segment.status)) {
      this.record('stop.requested', { segmentId, reason: body.reason || 'user_stop' });
    }

    const path = body.revocation
      ? `/device/operations/${encodeURIComponent(segment.operationId)}/revoke`
      : `/device/operations/${encodeURIComponent(segment.operationId)}/stop`;

    try {
      const result = await this.deviceRequest('POST', path, {
        reason: body.reason || 'user_stop',
        revocation: body.revocation || null,
      });

      if (result.receipt) {
        segment = body.cleanupReceipt
          ? await this.storeCleanupReceipt(segmentId, result.receipt)
          : await this.storeReceipt(segmentId, result.receipt);
        return { ok: true, segment };
      }

      this.record('stop.receipt_held', {
        segmentId,
        reason: 'Device closed input but signed receipt is held in its outbox.',
      });
      return { ok: true, receiptHeld: true, status: 'stop_unconfirmed', segment: this.state.segments[segmentId] };
    } catch (error) {
      this.record('stop.unconfirmed', { segmentId, reason: 'stop_call_failed', transportError: error.message });
      if (error instanceof HttpError && error.status < 500 && error.code !== 'DEVICE_UNREACHABLE') throw error;
      return { ok: false, status: 'stop_unconfirmed', warning: error.message, segment: this.state.segments[segmentId] };
    }
  }

  async reconcileSegment(segmentId) {
    let segment = this.requireSegment(segmentId);
    if (segment.receiptVerified) {
      await this.maybeAutoAdmit(segmentId);
      return this.state.segments[segmentId];
    }

    try {
      const receiptResult = await this.deviceRequest(
        'GET',
        `/device/operations/${encodeURIComponent(segment.operationId)}/receipt`,
      );
      if (receiptResult.receipt) {
        await this.storeReceipt(segmentId, receiptResult.receipt);
        return this.state.segments[segmentId];
      }
    } catch (error) {
      if (error.code !== 'RECEIPT_NOT_READY' && error.status !== 404 && error.status !== 202) {
        // Fall through to idempotent stop; the receipt endpoint may be unavailable.
      }
    }

    const stopped = await this.safeStop(segmentId, { reason: 'startup_or_manual_reconciliation' });
    if (stopped?.receipt) {
      // safeStop returns stopSegment result; state is updated inside.
    }
    segment = this.state.segments[segmentId];
    if (segment.receiptVerified) await this.maybeAutoAdmit(segmentId);
    return this.state.segments[segmentId];
  }

  async reconcileAll() {
    const results = [];
    for (const segment of Object.values(this.state.segments)) {
      if (!segment.receiptVerified) {
        try {
          const updated = await this.reconcileSegment(segment.segmentId);
          results.push({ segmentId: segment.segmentId, status: updated.status });
        } catch (error) {
          this.record('stop.unconfirmed', {
            segmentId: segment.segmentId,
            reason: 'startup_reconcile_failed',
            transportError: error.message,
          });
          results.push({ segmentId: segment.segmentId, status: 'stop_unconfirmed', error: error.message });
        }
      } else if (segment.autoAdmit && segment.admissionStatus === 'pending') {
        await this.maybeAutoAdmit(segment.segmentId);
        results.push({ segmentId: segment.segmentId, status: segment.status, admitted: true });
      }
    }
    return results;
  }

  async admitSegment(segmentId, body = {}) {
    const segment = this.requireSegment(segmentId);

    if (segment.admissionStatus === 'admitted' && segment.clipId) {
      return { ok: true, idempotent: true, clip: this.state.clips[segment.clipId] };
    }

    if (!segment.receiptVerified || !segment.receipt) {
      throw new HttpError(
        409,
        'DEVICE_STOP_RECEIPT_REQUIRED',
        'A verified signed device stop/cleanup receipt is required before a package becomes a saved take.',
        { backendStatus: segment.backendStatus, serviceStatus: segment.status },
      );
    }

    // Reconstruct current authorization. A later preview-only consent or a
    // revoked/discard session overrides the historical capture permission.
    this.auth({
      userId: segment.userId,
      deviceId: segment.deviceId,
      projectId: segment.projectId,
      sessionId: segment.sessionId,
      scope: SCOPE.SAVE_SEGMENT,
    });

    let pkg;
    try {
      pkg = await this.deviceRequest(
        'GET',
        `/device/operations/${encodeURIComponent(segment.operationId)}/package`,
      );
    } catch (error) {
      this.record('segment.admission_denied', {
        segmentId,
        code: 'PACKAGE_UNAVAILABLE',
        reason: error.message,
      });
      throw error;
    }

    const recomputed = packageHash(pkg.frames || []);
    if (pkg.packageHash !== recomputed || recomputed !== segment.receipt.packageHash) {
      const error = new HttpError(409, 'PACKAGE_HASH_MISMATCH', 'Package does not match the signed device receipt.');
      this.record('segment.admission_denied', { segmentId, code: error.code, reason: error.message });
      throw error;
    }

    const projectId = body.projectId || segment.projectId;
    const trackId = body.trackId || segment.trackId;
    const project = this.state.projects[projectId];
    const track = this.state.tracks[trackId];
    if (!project || !track || track.projectId !== projectId) {
      throw new HttpError(400, 'DESTINATION_TRACK_INVALID', 'Choose a track that belongs to the destination project.');
    }

    const clipId = newId('clip');
    const frames = pkg.frames || [];
    const text = frames.map((frame) => frame.text).join('\n');
    this.record('clip.routed', {
      clipId,
      projectId,
      trackId,
      segmentId,
      packageHash: pkg.packageHash,
      label: body.label || `文本片段 ${segment.segmentId.slice(-6)}`,
      text,
      frames,
      seqRange: { from: frames[0]?.seq || 0, to: frames.at(-1)?.seq || 0 },
    });
    this.record('segment.admission_granted', { segmentId, clipId });

    return { ok: true, admitted: true, clip: this.state.clips[clipId], tracks: trackView(this.state, projectId) };
  }

  writeBackendStopped(segmentId, body = {}) {
    this.requireSegment(segmentId);
    this.record('segment.backend_stop_written', {
      segmentId,
      note: body.note || '后台状态被手工写成“已停”',
    });
    return { ok: true, warning: 'This is only a database/backend marker. It is not device stop proof.' };
  }

  async revokeSession(sessionId, body = {}) {
    const session = this.state.sessions[sessionId];
    if (!session) throw new HttpError(404, 'SESSION_NOT_FOUND', 'Session not found.');
    if (session.status !== 'revoked') {
      this.record('session.revoked', {
        sessionId,
        reason: body.reason || 'user_revoked_mid_segment',
        discard: body.discard !== false,
      });
    }

    const segment = Object.values(this.state.segments).find((x) => x.sessionId === sessionId);
    let stop = null;
    if (segment && !segment.receiptVerified) {
      stop = await this.stopSegment(segment.segmentId, {
        reason: body.reason || 'user_revoked_mid_segment',
        revocation: { revoked: true, reason: body.reason || 'user_revoked_mid_segment', at: nowIso() },
      });
    }
    return { ok: true, session: this.state.sessions[sessionId], stop };
  }

  revokeAllConsent(body = {}) {
    const userId = body.userId || 'u_legacy';
    this.record('consent.revoked', { userId, all: true, reason: body.reason || 'user_revoked_all' });
    return { ok: true };
  }

  grantCapabilities(body, req) {
    this.requirePresence(req, body);
    const userId = body.userId || 'u_legacy';
    const projectId = body.projectId || null;
    const deviceId = body.deviceId || 'dev_studio';
    if (!this.state.users[userId]) throw new HttpError(404, 'USER_NOT_FOUND', 'User not found.');

    const selectionToScope = {
      prelisten: SCOPE.PRELISTEN,
      scheduledCapture: SCOPE.SCHEDULE_CAPTURE,
      retainOriginal: SCOPE.KEEP_ORIGINAL,
      splitTracks: SCOPE.SPLIT_TRACKS,
    };
    const scopes = new Set(body.scopes || []);
    const selections = {};
    for (const [key, scope] of Object.entries(selectionToScope)) {
      selections[key] = !!body[key];
      if (body[key]) scopes.add(scope);
    }
    const known = new Set(Object.values(SCOPE));
    for (const scope of scopes) {
      if (!known.has(scope) || scope === SCOPE.CAPTURE_SEGMENT || scope === SCOPE.SAVE_SEGMENT) {
        throw new HttpError(400, 'INVALID_NEW_SCOPE', `Itemized handoff can only add new capabilities: ${scope}`);
      }
    }
    if (scopes.size === 0) {
      throw new HttpError(400, 'NO_CAPABILITY_SELECTED', 'Select at least one new capability to grant.');
    }

    const grantId = newId('grant');
    this.record('migration.capabilities_granted', {
      grantId,
      userId,
      projectId,
      deviceId,
      scopes: [...scopes],
      selections,
      presenceId: req.headers['x-presence-id'] || body.presenceId,
    });
    return { ok: true, grant: this.state.grants.find((g) => g.grantId === grantId) };
  }

  editProject(projectId, body, req) {
    const project = this.state.projects[projectId];
    if (!project) throw new HttpError(404, 'PROJECT_NOT_FOUND', 'Project not found.');
    this.requirePresence(req, { ...body, projectId });
    const patch = {};
    if (typeof body.name === 'string' && body.name.trim()) patch.name = body.name.trim();
    if (typeof body.legacyButtonId === 'string' && body.legacyButtonId.trim()) {
      patch.legacyButtonId = body.legacyButtonId.trim();
    }
    if (Object.keys(patch).length === 0) throw new HttpError(400, 'NO_EDITS', 'Provide name or legacyButtonId.');
    this.record('project.edited', { projectId, patch });
    return { ok: true, project: this.state.projects[projectId] };
  }

  createProject(body, req) {
    this.requirePresence(req, body);
    if (!body.projectId || !/^[a-zA-Z0-9_-]{3,80}$/.test(body.projectId)) {
      throw new HttpError(400, 'BAD_PROJECT_ID', 'projectId must be 3-80 letters, numbers, _ or -.');
    }
    if (this.state.projects[body.projectId]) throw new HttpError(409, 'PROJECT_EXISTS', 'projectId already exists.');
    this.record('project.created', {
      projectId: body.projectId,
      name: body.name || body.projectId,
      kind: 'multitrack',
      userPresentRequired: false,
    });
    const tracks = Array.isArray(body.tracks) && body.tracks.length ? body.tracks : ['主轨'];
    for (const [index, name] of tracks.entries()) {
      const trackId = body.trackIds?.[index] || `trk_${body.projectId}_${index + 1}`;
      this.record('track.created', { trackId, projectId: body.projectId, name });
    }
    return { ok: true, project: this.state.projects[body.projectId], tracks: trackView(this.state, body.projectId) };
  }

  createSchedule(body) {
    const userId = body.userId || 'u_legacy';
    const deviceId = body.deviceId || 'dev_studio';
    this.auth({ userId, deviceId, projectId: body.projectId || null, scope: SCOPE.SCHEDULE_CAPTURE });
    const scheduleId = newId('sched');
    const schedule = {
      scheduleId,
      userId,
      deviceId,
      projectId: body.projectId || null,
      startsAt: body.startsAt || new Date(Date.now() + 60 * 60 * 1000).toISOString(),
      durationSec: Number(body.durationSec || 300),
      createdAt: nowIso(),
      status: 'created_but_requires_explicit_timer_grant',
    };
    this.record('schedule.created', schedule);
    return { ok: true, schedule };
  }

  prelisten(body) {
    const userId = body.userId || 'u_legacy';
    const deviceId = body.deviceId || 'dev_studio';
    this.auth({ userId, deviceId, projectId: body.projectId || null, scope: SCOPE.PRELISTEN });
    return {
      ok: true,
      prelisten: {
        channelId: newId('listen'),
        mode: 'ephemeral_preview_only',
        persists: false,
        note: 'Prelisten is separately consented and does not authorize capture or saving.',
      },
    };
  }

  async retainOriginal(segmentId, body = {}) {
    const segment = this.requireSegment(segmentId);
    this.auth({
      userId: body.userId || segment.userId,
      deviceId: segment.deviceId,
      projectId: segment.projectId,
      scope: SCOPE.KEEP_ORIGINAL,
    });
    const pkg = await this.deviceRequest(
      'GET',
      `/device/operations/${encodeURIComponent(segment.operationId)}/package`,
    );
    const originalId = newId('orig');
    this.record('original.retained', {
      originalId,
      userId: segment.userId,
      segmentId,
      packageHash: pkg.packageHash,
      package: pkg,
    });
    return { ok: true, originalId, packageHash: pkg.packageHash };
  }

  splitProject(projectId, body = {}) {
    const project = this.state.projects[projectId];
    if (!project) throw new HttpError(404, 'PROJECT_NOT_FOUND', 'Project not found.');
    this.auth({
      userId: body.userId || 'u_legacy',
      deviceId: body.deviceId || 'dev_studio',
      projectId,
      scope: SCOPE.SPLIT_TRACKS,
    });

    const projectClips = Object.values(this.state.clips).filter((c) => c.projectId === projectId);
    const clip = body.clipId ? this.state.clips[body.clipId] : projectClips.at(-1);
    if (!clip || clip.projectId !== projectId) throw new HttpError(404, 'CLIP_NOT_FOUND', 'No clip to split.');

    const lines = String(clip.text || '').split('\n').filter((line) => line.trim());
    /** @type {Map<string, string>} */
    const labelToTrackId = new Map();
    const children = [];
    let fallbackIndex = 0;

    for (const line of lines) {
      const match = line.match(/^\s*(?:\[([^\]]+)\]|([A-Za-z0-9_一-龥-]+)\s*[:：])\s*(.*)$/);
      let label;
      let text;
      if (match) {
        label = (match[1] || match[2] || 'split').trim();
        text = match[3] || '';
      } else {
        fallbackIndex += 1;
        label = fallbackIndex % 2 === 1 ? 'voice' : 'room';
        text = line;
      }
      const slug = label.toLowerCase().replace(/[^a-z0-9一-龥]+/g, '_').slice(0, 30) || 'part';
      let trackId = labelToTrackId.get(label);
      if (!trackId) {
        trackId = body.trackMap?.[label] || `trk_${projectId}_${slug}`;
        labelToTrackId.set(label, trackId);
        if (!this.state.tracks[trackId]) {
          this.record('track.created', { trackId, projectId, name: label });
        }
      }
      const childClipId = newId('clip');
      children.push({
        clipId: childClipId,
        projectId,
        trackId,
        parentClipId: clip.clipId,
        segmentId: clip.segmentId,
        packageHash: clip.packageHash,
        label: `${label} 分轨`,
        text,
        frames: [],
        seqRange: clip.seqRange,
        createdAt: nowIso(),
        origin: 'split_child',
      });
    }

    if (children.length === 0) throw new HttpError(400, 'NOTHING_TO_SPLIT', 'Clip contains no text lines.');
    this.record('clip.split', { projectId, parentClipId: clip.clipId, childClipIds: children.map((c) => c.clipId), children });
    return { ok: true, tracks: trackView(this.state, projectId) };
  }

  async injectFault(segmentId, body = {}) {
    const segment = this.requireSegment(segmentId);
    await this.deviceRequest('POST', `/device/debug/fault`, {
      operationId: segment.operationId,
      reason: body.reason || 'simulated_fault',
    });
    this.record('fault.injected', { segmentId, reason: body.reason || 'simulated_fault' });
    await this.safeCleanup(segmentId, body.cleanupReason || 'old_button_agent_fault_cleanup');
    return { ok: true, segment: this.state.segments[segmentId] };
  }

  issueCertificate(segmentId) {
    const segment = this.requireSegment(segmentId);
    if (!segment.receiptVerified || !segment.receipt) {
      throw new HttpError(
        409,
        'DEVICE_STOP_RECEIPT_REQUIRED',
        'Cannot sign a completion/no-continued-capture certificate without a verified device stop receipt. Restoring consent or writing backend "stopped" does not provide this proof.',
        {
          segmentId,
          serviceStatus: segment.status,
          backendStatus: segment.backendStatus,
          receiptPresent: !!segment.receipt,
        },
      );
    }

    const session = this.state.sessions[segment.sessionId];
    const certificateId = newId('cert');
    const unsigned = {
      certificateId,
      certificateVersion: 1,
      statement:
        'The signed device terminal receipt closes this operation/input. No frame belonging to this segment exists after finalSeq.',
      noPersistentMonitoringGranted: true,
      segmentId,
      userId: segment.userId,
      deviceId: segment.deviceId,
      operationId: segment.operationId,
      sessionId: segment.sessionId,
      finalSeq: segment.receipt.finalSeq,
      packageHash: segment.receipt.packageHash,
      lastFrameHash: segment.receipt.lastFrameHash,
      deviceReceiptType: segment.receipt.type,
      deviceReceiptSignatureB64: segment.receipt.signatureB64,
      admissionStatus: segment.admissionStatus,
      admissionCode: segment.admissionCode || null,
      sessionRevoked: session?.status === 'revoked',
      issuedAt: nowIso(),
    };
    const signatureB64 = signObject(this.keys.privateKeyPem, unsigned);
    const certificate = { ...unsigned, serviceSignatureB64: signatureB64 };
    this.record('certificate.issued', { certificateId, certificate });
    return certificate;
  }

  async handle(req, res) {
    const url = new URL(req.url, 'http://service.local');
    const p = url.pathname;
    const method = req.method;

    try {
      if (method === 'GET' && (p === '/' || p === '/index.html')) {
        const file = path.join(this.publicDir, 'index.html');
        const html = fs.readFileSync(file);
        res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' });
        return res.end(html);
      }

      if (method === 'GET' && p === '/health') {
        return sendJson(res, 200, { ok: true, service: 'recording-upgrade-service', bootstrapped: this.state.bootstrapped });
      }
      if (method === 'GET' && p === '/service-public-key') {
        return sendJson(res, 200, { publicKeyPem: this.keys.publicKeyPem });
      }

      if (method === 'POST' && p === '/api/bootstrap') {
        const body = await readJsonBody(req);
        return sendJson(res, 200, await this.bootstrap(body));
      }
      if (method === 'POST' && p === '/api/notices/new-device-capabilities/shown') {
        const body = await readJsonBody(req);
        return sendJson(res, 200, this.presentNewNotice(body));
      }

      let deviceLifecycleMatch = p.match(/^\/api\/devices\/([^/]+)\/lifecycle$/);
      if (method === 'POST' && deviceLifecycleMatch) {
        const body = await readJsonBody(req);
        return sendJson(
          res,
          200,
          await this.setDeviceLifecycle(decodeURIComponent(deviceLifecycleMatch[1]), body),
        );
      }
      if (method === 'GET' && p === '/api/state') {
        return sendJson(res, 200, this.state);
      }
      if (method === 'GET' && p === '/api/authorization') {
        const query = {
          userId: url.searchParams.get('userId') || 'u_legacy',
          deviceId: url.searchParams.get('deviceId') || 'dev_studio',
          projectId: url.searchParams.get('projectId') || undefined,
          sessionId: url.searchParams.get('sessionId') || undefined,
        };
        return sendJson(res, 200, this.authorizationSummary(query));
      }

      if (method === 'GET' && p === '/api/upgrade/assessment') {
        const query = {
          userId: url.searchParams.get('userId') || 'u_legacy',
          deviceId: url.searchParams.get('deviceId') || 'dev_studio',
        };
        return sendJson(res, 200, this.upgradeAssessment(query));
      }

      if (method === 'POST' && p === '/api/presence') {
        const body = await readJsonBody(req);
        return sendJson(res, 200, this.assertPresence(body));
      }

      if (method === 'POST' && p === '/api/consent/preview-only') {
        const body = await readJsonBody(req);
        return sendJson(res, 200, this.setPreviewOnly(body));
      }
      if (method === 'POST' && p === '/api/consent/restore') {
        const body = await readJsonBody(req);
        return sendJson(res, 200, this.restoreConsent(body));
      }
      if (method === 'POST' && p === '/api/consent/revoke-all') {
        const body = await readJsonBody(req);
        return sendJson(res, 200, this.revokeAllConsent(body));
      }

      if (method === 'POST' && p === '/api/migration/capabilities') {
        const body = await readJsonBody(req);
        return sendJson(res, 200, this.grantCapabilities(body, req));
      }
      if (method === 'POST' && p === '/api/projects') {
        const body = await readJsonBody(req);
        return sendJson(res, 200, this.createProject(body, req));
      }

      let match = p.match(/^\/api\/projects\/([^/]+)\/edit$/);
      if (method === 'POST' && match) {
        const body = await readJsonBody(req);
        return sendJson(res, 200, this.editProject(decodeURIComponent(match[1]), body, req));
      }

      match = p.match(/^\/api\/projects\/([^/]+)\/split$/);
      if (method === 'POST' && match) {
        const body = await readJsonBody(req);
        return sendJson(res, 200, this.splitProject(decodeURIComponent(match[1]), body));
      }

      match = p.match(/^\/api\/projects\/([^/]+)\/tracks$/);
      if (method === 'GET' && match) {
        const projectId = decodeURIComponent(match[1]);
        if (!this.state.projects[projectId]) throw new HttpError(404, 'PROJECT_NOT_FOUND', 'Project not found.');
        return sendJson(res, 200, { projectId, tracks: trackView(this.state, projectId) });
      }

      if (method === 'POST' && p === '/api/agent/old-button/press') {
        const body = await readJsonBody(req);
        return sendJson(res, 200, await this.pressOldButton(body, req));
      }

      if (method === 'POST' && p === '/api/segments') {
        const body = await readJsonBody(req);
        return sendJson(res, 200, await this.pressOldButton(body, req));
      }

      match = p.match(/^\/api\/segments\/([^/]+)\/frames$/);
      if (method === 'POST' && match) {
        const body = await readJsonBody(req);
        return sendJson(res, 200, await this.appendFrame(decodeURIComponent(match[1]), body, req));
      }

      match = p.match(/^\/api\/segments\/([^/]+)\/stop$/);
      if (method === 'POST' && match) {
        const body = await readJsonBody(req);
        return sendJson(res, 200, await this.stopSegment(decodeURIComponent(match[1]), body));
      }

      match = p.match(/^\/api\/segments\/([^/]+)\/reconcile$/);
      if (method === 'POST' && match) {
        const body = await readJsonBody(req).catch(() => ({}));
        return sendJson(res, 200, { ok: true, segment: await this.reconcileSegment(decodeURIComponent(match[1])), ...body });
      }

      match = p.match(/^\/api\/segments\/([^/]+)\/admit$/);
      if (method === 'POST' && match) {
        const body = await readJsonBody(req);
        return sendJson(res, 200, await this.admitSegment(decodeURIComponent(match[1]), body));
      }

      match = p.match(/^\/api\/segments\/([^/]+)\/backend-stopped$/);
      if (method === 'POST' && match) {
        const body = await readJsonBody(req);
        return sendJson(res, 200, this.writeBackendStopped(decodeURIComponent(match[1]), body));
      }

      match = p.match(/^\/api\/segments\/([^/]+)\/completion-certificate$/);
      if (method === 'POST' && match) {
        const body = await readJsonBody(req).catch(() => ({}));
        return sendJson(res, 200, this.issueCertificate(decodeURIComponent(match[1]), body));
      }

      match = p.match(/^\/api\/segments\/([^/]+)\/retain-original$/);
      if (method === 'POST' && match) {
        const body = await readJsonBody(req);
        return sendJson(res, 200, await this.retainOriginal(decodeURIComponent(match[1]), body));
      }

      match = p.match(/^\/api\/segments\/([^/]+)\/fault$/);
      if (method === 'POST' && match) {
        const body = await readJsonBody(req);
        return sendJson(res, 200, await this.injectFault(decodeURIComponent(match[1]), body));
      }

      match = p.match(/^\/api\/sessions\/([^/]+)\/revoke$/);
      if (method === 'POST' && match) {
        const body = await readJsonBody(req);
        return sendJson(res, 200, await this.revokeSession(decodeURIComponent(match[1]), body));
      }

      if (method === 'POST' && p === '/api/schedules') {
        const body = await readJsonBody(req);
        return sendJson(res, 200, this.createSchedule(body));
      }
      if (method === 'POST' && p === '/api/monitor/prelisten') {
        const body = await readJsonBody(req);
        return sendJson(res, 200, this.prelisten(body));
      }

      if (method === 'GET' && p === '/api/device/state') {
        return sendJson(res, 200, await this.deviceRequest('GET', '/device/state'));
      }
      if (method === 'POST' && p === '/api/device/hold-receipt') {
        const body = await readJsonBody(req);
        return sendJson(res, 200, await this.deviceRequest('POST', '/device/debug/hold-receipt', body));
      }
      if (method === 'POST' && p === '/api/device/release-receipt') {
        const body = await readJsonBody(req);
        return sendJson(res, 200, await this.deviceRequest('POST', '/device/debug/release-receipt', body));
      }

      match = p.match(/^\/api\/certificates\/([^/]+)$/);
      if (method === 'GET' && match) {
        const certificate = this.state.certificates[decodeURIComponent(match[1])];
        if (!certificate) throw new HttpError(404, 'CERTIFICATE_NOT_FOUND', 'Certificate not found.');
        return sendJson(res, 200, certificate);
      }

      throw new HttpError(404, 'NOT_FOUND', `No service route for ${method} ${p}`);
    } catch (error) {
      const status = error.status || 500;
      if (status >= 500) console.error('[service]', error);
      return sendJson(res, status, {
        ok: false,
        code: error.code || 'SERVICE_INTERNAL',
        message: error.message,
        details: error.details,
      });
    }
  }

  listen(port, host = '127.0.0.1') {
    this.server = http.createServer((req, res) => this.handle(req, res));
    return new Promise((resolve) => {
      this.server.listen(port, host, () => {
        const address = this.server.address();
        resolve({ server: this.server, port: address.port, host });
      });
    });
  }

  close() {
    return new Promise((resolve, reject) => {
      if (!this.server) return resolve();
      this.server.close((err) => (err ? reject(err) : resolve()));
    });
  }
}
