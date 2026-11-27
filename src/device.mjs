import http from 'node:http';
import path from 'node:path';
import {
  HttpError,
  ensureDir,
  generateEd25519KeyPair,
  newId,
  nowIso,
  readJson,
  readJsonBody,
  requirePairingToken,
  sendJson,
  sha256,
  signObject,
  writeJsonAtomic,
} from './core.mjs';
import { packageHash } from './model.mjs';

export class SimulatedDevice {
  constructor({ dataDir, pairingToken, deviceId = 'dev_studio' }) {
    this.dataDir = dataDir;
    this.pairingToken = pairingToken;
    this.stateFile = path.join(dataDir, 'device-state.json');
    this.keyFile = path.join(dataDir, 'device-key.json');
    ensureDir(dataDir);

    const keys = readJson(this.keyFile, null);
    if (keys?.publicKeyPem && keys?.privateKeyPem) {
      this.keys = keys;
    } else {
      this.keys = generateEd25519KeyPair();
      writeJsonAtomic(this.keyFile, this.keys);
    }

    this.state = readJson(this.stateFile, null) || {
      deviceId,
      publicKeyPem: this.keys.publicKeyPem,
      lifecycle: 'paired',
      status: 'idle',
      activeOperationId: null,
      holdStopReceipt: false,
      operations: {},
      createdAt: nowIso(),
    };
    this.state.publicKeyPem = this.keys.publicKeyPem;
    this.save();
  }

  save() {
    writeJsonAtomic(this.stateFile, this.state);
  }

  publicState() {
    return {
      deviceId: this.state.deviceId,
      lifecycle: this.state.lifecycle,
      status: this.state.status,
      activeOperationId: this.state.activeOperationId,
      holdStopReceipt: this.state.holdStopReceipt,
      operations: Object.fromEntries(
        Object.entries(this.state.operations).map(([id, op]) => [
          id,
          {
            operationId: id,
            segmentId: op.segmentId,
            state: op.state,
            frameCount: op.frames?.length || 0,
            holdReceipt: !!op.holdReceipt,
            hasReceipt: !!op.receipt,
            receiptDelivered: !!op.receiptDelivered,
            faultReason: op.faultReason || null,
            stopReason: op.stopReason || null,
            createdAt: op.createdAt,
            endedAt: op.endedAt || null,
          },
        ]),
      ),
    };
  }

  makeReceipt(operation, type, reason) {
    const endedAt = operation.endedAt || operation.cleanedAt || nowIso();
    const payload = {
      type,
      receiptVersion: 1,
      deviceId: this.state.deviceId,
      operationId: operation.operationId,
      segmentId: operation.segmentId,
      inputClosed: true,
      finalSeq: operation.frames.length,
      packageHash: packageHash(operation.frames),
      lastFrameHash: operation.frames.at(-1)?.sha256 || null,
      startedAt: operation.createdAt,
      endedAt,
      reason: reason || operation.stopReason || operation.faultReason || null,
      revocation: operation.revocation || null,
    };
    return { ...payload, signatureB64: signObject(this.keys.privateKeyPem, payload) };
  }

  startOperation(body) {
    const { operationId = newId('op'), segmentId, sessionId } = body;
    if (!segmentId) throw new HttpError(400, 'SEGMENT_ID_REQUIRED', 'segmentId is required.');

    const active = this.state.activeOperationId
      ? this.state.operations[this.state.activeOperationId]
      : null;
    if (active && active.state === 'capturing' && active.operationId !== operationId) {
      throw new HttpError(409, 'DEVICE_BUSY', 'Device input is already capturing.', {
        activeOperationId: active.operationId,
      });
    }

    if (this.state.operations[operationId]) {
      const existing = this.state.operations[operationId];
      if (existing.state === 'capturing') {
        return { ok: true, idempotent: true, operation: this.publicOperation(operationId) };
      }
      throw new HttpError(409, 'OPERATION_TERMINAL', 'Operation already reached a terminal state.');
    }

    const operation = {
      operationId,
      segmentId,
      sessionId: sessionId || null,
      state: 'capturing',
      frames: [],
      createdAt: nowIso(),
      endedAt: null,
      receipt: null,
      receiptDelivered: false,
      holdReceipt: null,
      revocation: null,
    };
    this.state.operations[operationId] = operation;
    this.state.status = 'capturing';
    this.state.activeOperationId = operationId;
    this.save();
    return { ok: true, operation: this.publicOperation(operationId) };
  }

  publicOperation(operationId) {
    const op = this.state.operations[operationId];
    if (!op) return null;
    return {
      operationId: op.operationId,
      segmentId: op.segmentId,
      state: op.state,
      frameCount: op.frames.length,
      hasReceipt: !!op.receipt,
      receiptDelivered: op.receiptDelivered,
    };
  }

  appendFrame(operationId, body) {
    const op = this.state.operations[operationId];
    if (!op) throw new HttpError(404, 'OPERATION_NOT_FOUND', 'Unknown device operation.');
    if (op.state !== 'capturing' || this.state.status !== 'capturing') {
      throw new HttpError(409, 'INPUT_CLOSED', 'Device input is not accepting frames.', {
        operationState: op.state,
        deviceStatus: this.state.status,
      });
    }
    if (typeof body.text !== 'string' || body.text.length > 20_000) {
      throw new HttpError(400, 'TEXT_FRAME_REQUIRED', 'A small text frame is required (max 20k chars).');
    }
    const seq = op.frames.length + 1;
    const at = nowIso();
    const frame = { seq, text: body.text, at, sha256: sha256(`${seq}:${body.text}`) };
    op.frames.push(frame);
    this.save();
    return { ok: true, seq: frame.seq, sha256: frame.sha256, at };
  }

  terminalize(operationId, { kind = 'stop', reason = 'user_stop', revocation = null } = {}) {
    const op = this.state.operations[operationId];
    if (!op) throw new HttpError(404, 'OPERATION_NOT_FOUND', 'Unknown device operation.');

    if (op.receipt) {
      return this.receiptResponse(op);
    }

    op.state = kind === 'cleanup' ? 'cleaned' : revocation ? 'revoked' : 'stopped';
    op.stopReason = reason;
    op.revocation = revocation;
    op.endedAt = nowIso();
    if (this.state.activeOperationId === op.operationId) {
      this.state.activeOperationId = null;
      this.state.status = 'idle';
    }

    const receiptType = kind === 'cleanup' ? 'fault_cleanup_receipt' : 'stop_receipt';
    op.receipt = this.makeReceipt(op, receiptType, reason);
    const hold = op.holdReceipt === null ? this.state.holdStopReceipt : op.holdReceipt;
    op.receiptDelivered = !hold;
    this.save();
    return this.receiptResponse(op);
  }

  receiptResponse(op) {
    const hold = op.holdReceipt === null ? this.state.holdStopReceipt : op.holdReceipt;
    if (hold && !op.forceReleased) {
      return {
        status: 202,
        body: { ok: true, receiptHeld: true, operationId: op.operationId, deviceState: this.state.status },
      };
    }
    op.receiptDelivered = true;
    this.save();
    return { status: 200, body: { ok: true, receipt: op.receipt } };
  }

  getReceipt(operationId) {
    const op = this.state.operations[operationId];
    if (!op?.receipt) throw new HttpError(404, 'RECEIPT_NOT_READY', 'No signed terminal receipt yet.');
    const hold = op.holdReceipt === null ? this.state.holdStopReceipt : op.holdReceipt;
    if (hold && !op.forceReleased) {
      return {
        status: 202,
        body: { ok: true, receiptHeld: true, operationId, deviceState: this.state.status },
      };
    }
    op.receiptDelivered = true;
    this.save();
    return { status: 200, body: { ok: true, receipt: op.receipt } };
  }

  releaseReceipt(body = {}) {
    const ids = body.operationId ? [body.operationId] : Object.keys(this.state.operations);
    const released = [];
    for (const id of ids) {
      const op = this.state.operations[id];
      if (op?.receipt) {
        op.holdReceipt = false;
        op.forceReleased = true;
        op.receiptDelivered = true;
        released.push(op.receipt);
      }
    }
    this.save();
    return { ok: true, released };
  }

  getPackage(operationId) {
    const op = this.state.operations[operationId];
    if (!op) throw new HttpError(404, 'OPERATION_NOT_FOUND', 'Unknown device operation.');
    return {
      packageId: `pkg_${op.segmentId}`,
      segmentId: op.segmentId,
      operationId: op.operationId,
      deviceId: this.state.deviceId,
      frameCount: op.frames.length,
      packageHash: packageHash(op.frames),
      frames: op.frames,
    };
  }

  injectFault(body = {}) {
    const operationId = body.operationId || this.state.activeOperationId;
    const op = operationId ? this.state.operations[operationId] : null;
    if (!op) throw new HttpError(404, 'OPERATION_NOT_FOUND', 'No operation to fault.');
    op.state = 'fault';
    op.faultReason = body.reason || 'simulated_input_fault';
    op.faultAt = nowIso();
    this.state.status = 'fault';
    this.state.activeOperationId = op.operationId;
    this.save();
    return { ok: true, deviceState: this.publicState() };
  }

  setHold(body = {}) {
    if (body.operationId) {
      const op = this.state.operations[body.operationId];
      if (!op) throw new HttpError(404, 'OPERATION_NOT_FOUND', 'Unknown device operation.');
      op.holdReceipt = !!body.hold;
    } else {
      this.state.holdStopReceipt = !!body.hold;
    }
    this.save();
    return { ok: true, hold: !!body.hold, operationId: body.operationId || null };
  }

  async handle(req, res) {
    const url = new URL(req.url, 'http://device.local');
    const p = url.pathname;
    const method = req.method;

    try {
      if (method === 'GET' && p === '/health') {
        return sendJson(res, 200, { ok: true, service: 'simulated-recording-device', ...this.publicState() });
      }

      requirePairingToken(req, this.pairingToken);

      if (method === 'GET' && p === '/device/identity') {
        return sendJson(res, 200, {
          deviceId: this.state.deviceId,
          publicKeyPem: this.keys.publicKeyPem,
        });
      }
      if (method === 'GET' && p === '/device/state') {
        return sendJson(res, 200, this.publicState());
      }
      if (method === 'POST' && p === '/device/lifecycle') {
        const body = await readJsonBody(req);
        if (!['paired', 'online', 'offline', 'retired', 'reset'].includes(body.lifecycle)) {
          throw new HttpError(400, 'BAD_LIFECYCLE', 'Invalid lifecycle.');
        }
        this.state.lifecycle = body.lifecycle;
        this.save();
        return sendJson(res, 200, this.publicState());
      }
      if (method === 'POST' && p === '/device/operations/start') {
        const body = await readJsonBody(req);
        return sendJson(res, 200, this.startOperation(body));
      }

      let match = p.match(/^\/device\/operations\/([^/]+)\/frames$/);
      if (method === 'POST' && match) {
        const body = await readJsonBody(req);
        return sendJson(res, 200, this.appendFrame(decodeURIComponent(match[1]), body));
      }

      match = p.match(/^\/device\/operations\/([^/]+)\/stop$/);
      if (method === 'POST' && match) {
        const body = await readJsonBody(req);
        const result = this.terminalize(decodeURIComponent(match[1]), {
          kind: 'stop',
          reason: body.reason || 'user_stop',
          revocation: body.revocation || null,
        });
        return sendJson(res, result.status, result.body);
      }

      match = p.match(/^\/device\/operations\/([^/]+)\/revoke$/);
      if (method === 'POST' && match) {
        const body = await readJsonBody(req);
        const result = this.terminalize(decodeURIComponent(match[1]), {
          kind: 'stop',
          reason: body.reason || 'user_revoked',
          revocation: { revoked: true, reason: body.reason || 'user_revoked', at: nowIso() },
        });
        return sendJson(res, result.status, result.body);
      }

      match = p.match(/^\/device\/operations\/([^/]+)\/cleanup$/);
      if (method === 'POST' && match) {
        const body = await readJsonBody(req);
        const result = this.terminalize(decodeURIComponent(match[1]), {
          kind: 'cleanup',
          reason: body.reason || 'agent_fault_cleanup',
        });
        return sendJson(res, result.status, result.body);
      }

      match = p.match(/^\/device\/operations\/([^/]+)\/receipt$/);
      if (method === 'GET' && match) {
        const result = this.getReceipt(decodeURIComponent(match[1]));
        return sendJson(res, result.status, result.body);
      }

      match = p.match(/^\/device\/operations\/([^/]+)\/package$/);
      if (method === 'GET' && match) {
        return sendJson(res, 200, this.getPackage(decodeURIComponent(match[1])));
      }

      if (method === 'POST' && p === '/device/debug/hold-receipt') {
        const body = await readJsonBody(req);
        return sendJson(res, 200, this.setHold(body));
      }
      if (method === 'POST' && p === '/device/debug/release-receipt') {
        const body = await readJsonBody(req);
        return sendJson(res, 200, this.releaseReceipt(body));
      }
      if (method === 'POST' && p === '/device/debug/fault') {
        const body = await readJsonBody(req);
        return sendJson(res, 200, this.injectFault(body));
      }

      throw new HttpError(404, 'NOT_FOUND', `No device route for ${method} ${p}`);
    } catch (error) {
      const status = error.status || 500;
      return sendJson(res, status, {
        ok: false,
        code: error.code || 'DEVICE_INTERNAL',
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
