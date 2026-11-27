import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { SimulatedDevice } from './device.mjs';
import { UpgradeService } from './service.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const publicDir = path.resolve(__dirname, '..', 'public');
const pairingToken = 'test-pairing-token';

export async function startHarness({ label = 'harness' } = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), `recorder-upgrade-${label}-`));
  const device = new SimulatedDevice({
    dataDir: path.join(root, 'device'),
    pairingToken,
  });
  const deviceServer = await device.listen(0, '127.0.0.1');
  const deviceUrl = `http://127.0.0.1:${deviceServer.port}`;

  let service = new UpgradeService({
    dataDir: path.join(root, 'service'),
    deviceUrl,
    pairingToken,
    publicDir,
  });
  const serviceServer = await service.listen(0, '127.0.0.1');
  let base = `http://127.0.0.1:${serviceServer.port}`;

  const h = {
    root,
    device,
    deviceUrl,
    service,
    base,
    pairingToken,

    async api(method, apiPath, body, headers = {}) {
      const res = await fetch(`${base}${apiPath}`, {
        method,
        headers: {
          ...(body === undefined ? {} : { 'content-type': 'application/json' }),
          ...headers,
        },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      const json = await res.json().catch(() => ({}));
      return { ...json, status: res.status, httpStatus: res.status };
    },

    async deviceApi(method, apiPath, body) {
      const res = await fetch(`${deviceUrl}${apiPath}`, {
        method,
        headers: {
          ...(body === undefined ? {} : { 'content-type': 'application/json' }),
          'x-pairing-token': pairingToken,
        },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      const json = await res.json().catch(() => ({}));
      return { ...json, status: res.status, httpStatus: res.status };
    },

    async presence(userId = 'u_legacy', projectId = null) {
      const result = await h.api('POST', '/api/presence', { userId, projectId });
      assert.equal(result.status, 200);
      return result.token;
    },

    async restartService() {
      await service.close();
      service = new UpgradeService({
        dataDir: path.join(root, 'service'),
        deviceUrl,
        pairingToken,
        publicDir,
      });
      const nextServer = await service.listen(0, '127.0.0.1');
      base = `http://127.0.0.1:${nextServer.port}`;
      h.service = service;
      h.base = base;
      await service.waitForDevice();
      await service.reconcileAll();
      return h;
    },

    async close() {
      await service.close().catch(() => {});
      await device.close().catch(() => {});
      try {
        fs.rmSync(root, { recursive: true, force: true });
      } catch {
        // Temp cleanup is best effort.
      }
    },
  };

  const health = await h.api('GET', '/health');
  assert.equal(health.status, 200);
  const bootstrap = await h.api('POST', '/api/bootstrap', {});
  assert.equal(bootstrap.status, 200);
  return h;
}

export async function happyPathStartStopQuerySplit(h, log = () => {}) {
  log('建立用户在场会话');
  const token = await h.presence();

  log('通过旧按钮兼容代理实际开一段');
  const opened = await h.api(
    'POST',
    '/api/agent/old-button/press',
    { projectId: 'proj_new_multitrack', trackId: 'trk_take', autoAdmit: true },
    { 'x-presence-id': token },
  );
  assert.equal(opened.status, 200, JSON.stringify(opened));
  assert.equal(opened.segment.status, 'active');
  const segmentId = opened.segment.segmentId;

  log('写入两条小文本帧（替代音频）');
  const frame1 = await h.api(
    'POST',
    `/api/segments/${segmentId}/frames`,
    { text: 'voice: 验收人实际开了一段' },
  );
  assert.equal(frame1.status, 200, JSON.stringify(frame1));
  const frame2 = await h.api(
    'POST',
    `/api/segments/${segmentId}/frames`,
    { text: 'room: 停段后应能在新工程轨道查到' },
  );
  assert.equal(frame2.status, 200, JSON.stringify(frame2));

  log('实际停一段，并等待设备签名停采回执');
  const stopped = await h.api('POST', `/api/segments/${segmentId}/stop`, {});
  assert.equal(stopped.status, 200, JSON.stringify(stopped));
  assert.equal(stopped.segment.receiptVerified, true);
  assert.equal(stopped.segment.admissionStatus, 'admitted');
  assert.equal(stopped.segment.frameCount, 2);

  log('以新工程查询进入轨道的片段');
  const tracks = await h.api('GET', '/api/projects/proj_new_multitrack/tracks');
  assert.equal(tracks.status, 200);
  const takeTrack = tracks.tracks.find((t) => t.trackId === 'trk_take');
  assert.ok(takeTrack);
  assert.equal(takeTrack.clips.length, 1);
  assert.match(takeTrack.clips[0].text, /验收人实际开了一段/);

  log('制作人申请完结证明');
  const certificate = await h.api('POST', `/api/segments/${segmentId}/completion-certificate`, {});
  assert.equal(certificate.status, 200, JSON.stringify(certificate));
  assert.equal(certificate.finalSeq, 2);
  assert.match(certificate.statement, /No frame/);

  log('在场逐项授予分轨能力后执行新分轨');
  const grant = await h.api(
    'POST',
    '/api/migration/capabilities',
    { projectId: 'proj_new_multitrack', splitTracks: true },
    { 'x-presence-id': token },
  );
  assert.equal(grant.status, 200, JSON.stringify(grant));
  const split = await h.api('POST', '/api/projects/proj_new_multitrack/split', {
    clipId: takeTrack.clips[0].clipId,
  });
  assert.equal(split.status, 200, JSON.stringify(split));
  const voice = split.tracks.find((t) => t.trackId === 'trk_proj_new_multitrack_voice');
  const room = split.tracks.find((t) => t.trackId === 'trk_proj_new_multitrack_room');
  assert.ok(voice, 'voice split track should exist');
  assert.ok(room, 'room split track should exist');
  assert.equal(voice.clips.length, 1);
  assert.equal(room.clips.length, 1);

  return { segmentId, clipId: takeTrack.clips[0].clipId, certificateId: certificate.certificateId };
}

export async function consentAndHeldReceiptScenario(h, log = () => {}) {
  const token = await h.presence();

  log('录制一个暂不自动准入的包 P1');
  const opened = await h.api(
    'POST',
    '/api/agent/old-button/press',
    { projectId: 'proj_new_multitrack', trackId: 'trk_take', autoAdmit: false },
    { 'x-presence-id': token },
  );
  assert.equal(opened.status, 200);
  const p1 = opened.segment.segmentId;
  await h.api('POST', `/api/segments/${p1}/frames`, { text: 'same package consent test' });
  const stopped = await h.api('POST', `/api/segments/${p1}/stop`, {});
  assert.equal(stopped.status, 200);
  assert.equal(stopped.segment.receiptVerified, true);

  log('把旧同意改成仅试听、禁止保存');
  const narrow = await h.api('POST', '/api/consent/preview-only', {});
  assert.equal(narrow.status, 200);

  log('同一个包申请准作可存录音，必须被拒绝');
  const denied = await h.api('POST', `/api/segments/${p1}/admit`, {});
  assert.equal(denied.status, 403);
  assert.equal(denied.code, 'SCOPE_NOT_GRANTED');
  const tracksWhileDenied = await h.api('GET', '/api/projects/proj_new_multitrack/tracks');
  const allClipsWhileDenied = tracksWhileDenied.tracks.flatMap((t) => t.clips);
  assert.equal(allClipsWhileDenied.some((c) => c.segmentId === p1), false);

  log('恢复旧同意；P1 可以准入');
  const restored = await h.api('POST', '/api/consent/restore', {});
  assert.equal(restored.status, 200);
  const admitted = await h.api('POST', `/api/segments/${p1}/admit`, {});
  assert.equal(admitted.status, 200, JSON.stringify(admitted));
  assert.equal(admitted.admitted, true);

  log('P2：让设备停采签名回执滞留在设备发件箱');
  const opened2 = await h.api(
    'POST',
    '/api/agent/old-button/press',
    { projectId: 'proj_new_multitrack', trackId: 'trk_take', autoAdmit: false },
    { 'x-presence-id': token },
  );
  assert.equal(opened2.status, 200);
  const p2 = opened2.segment.segmentId;
  const op2 = opened2.segment.operationId;
  await h.api('POST', `/api/segments/${p2}/frames`, { text: 'held receipt test' });
  const hold = await h.deviceApi('POST', '/device/debug/hold-receipt', { operationId: op2, hold: true });
  assert.equal(hold.status, 200);

  const stopHeld = await h.api('POST', `/api/segments/${p2}/stop`, {});
  assert.equal(stopHeld.status, 200);
  assert.equal(stopHeld.receiptHeld, true);
  assert.equal(stopHeld.segment.status, 'stop_unconfirmed');

  log('后台只写“已停”，不能代替设备回执');
  const backend = await h.api('POST', `/api/segments/${p2}/backend-stopped`, {
    note: 'manual backend stopped marker',
  });
  assert.equal(backend.status, 200);
  const certBeforeRelease = await h.api('POST', `/api/segments/${p2}/completion-certificate`, {});
  assert.equal(certBeforeRelease.status, 409);
  assert.equal(certBeforeRelease.code, 'DEVICE_STOP_RECEIPT_REQUIRED');
  assert.equal(certBeforeRelease.details.backendStatus, 'stopped');

  log('放行滞留回执并对账，随后才能签完结证明');
  const released = await h.deviceApi('POST', '/device/debug/release-receipt', { operationId: op2 });
  assert.equal(released.status, 200);
  assert.equal(released.released.length, 1);
  const reconcile = await h.api('POST', `/api/segments/${p2}/reconcile`, {});
  assert.equal(reconcile.status, 200);
  assert.equal(reconcile.segment.receiptVerified, true);
  const certAfterRelease = await h.api('POST', `/api/segments/${p2}/completion-certificate`, {});
  assert.equal(certAfterRelease.status, 200);
  assert.equal(certAfterRelease.finalSeq, 1);

  return { p1, p2, deniedCode: denied.code };
}

export async function absentNextDayScenario(h, log = () => {}) {
  const token = await h.presence();

  log('用户今天在场时，明确授予该新工程下次分轨能力');
  const grant = await h.api(
    'POST',
    '/api/migration/capabilities',
    { projectId: 'proj_new_multitrack', splitTracks: true },
    { 'x-presence-id': token },
  );
  assert.equal(grant.status, 200);

  log('下班前完成并停好一段');
  const done = await h.api(
    'POST',
    '/api/agent/old-button/press',
    { projectId: 'proj_new_multitrack', trackId: 'trk_take', autoAdmit: true },
    { 'x-presence-id': token },
  );
  const doneId = done.segment.segmentId;
  await h.api('POST', `/api/segments/${doneId}/frames`, { text: 'voice: 今天已完成' });
  await h.api('POST', `/api/segments/${doneId}/frames`, { text: 'room: 明天可按明确授权分轨' });
  const doneStop = await h.api('POST', `/api/segments/${doneId}/stop`, {});
  assert.equal(doneStop.status, 200);

  log('另一段已开录但未停；模拟服务进程在用户离场后重启到次日');
  const orphan = await h.api(
    'POST',
    '/api/agent/old-button/press',
    { projectId: 'proj_new_multitrack', trackId: 'trk_take', autoAdmit: true },
    { 'x-presence-id': token },
  );
  const orphanId = orphan.segment.segmentId;
  const orphanOp = orphan.segment.operationId;
  await h.api('POST', `/api/segments/${orphanId}/frames`, { text: 'orphan: restart cleanup must stop input' });
  assert.equal(h.device.state.status, 'capturing');

  await h.restartService();

  const orphanAfter = h.service.state.segments[orphanId];
  assert.equal(orphanAfter.receiptVerified, true);
  assert.equal(orphanAfter.status, 'device_closed_verified');
  assert.equal(orphanAfter.admissionStatus, 'admitted');
  assert.equal(h.device.state.status, 'idle');
  assert.equal(h.device.state.activeOperationId, null);

  log('设备已终态；任何向旧操作追加帧的尝试都被拒绝');
  const lateFrame = await h.api('POST', `/api/segments/${orphanId}/frames`, { text: 'after restart' });
  assert.equal(lateFrame.status, 409);
  assert.equal(lateFrame.code, 'SEGMENT_CLOSED');

  const directLateFrame = await h.deviceApi('POST', `/device/operations/${orphanOp}/frames`, {
    text: 'direct after stop',
  });
  assert.equal(directLateFrame.status, 409);
  assert.equal(directLateFrame.code, 'INPUT_CLOSED');

  log('次日缺席：不能重新编辑只有按钮标号的老工程');
  const edit = await h.api('POST', '/api/projects/proj_old_button_only/edit', { name: '被后台改名' });
  assert.equal(edit.status, 403);
  assert.equal(edit.code, 'FRESH_USER_PRESENCE_REQUIRED');

  log('次日缺席：不能补授新能力或新开录音段');
  const grantAbsent = await h.api('POST', '/api/migration/capabilities', { prelisten: true });
  assert.equal(grantAbsent.status, 403);
  assert.equal(grantAbsent.code, 'FRESH_USER_PRESENCE_REQUIRED');
  const pressAbsent = await h.api('POST', '/api/agent/old-button/press', {});
  assert.equal(pressAbsent.status, 403);
  assert.equal(pressAbsent.code, 'FRESH_USER_PRESENCE_REQUIRED');

  log('次日缺席：旧同意不含预听/定时采集');
  const prelisten = await h.api('POST', '/api/monitor/prelisten', {});
  assert.equal(prelisten.status, 403);
  assert.equal(prelisten.code, 'SCOPE_NOT_GRANTED');
  const schedule = await h.api('POST', '/api/schedules', {});
  assert.equal(schedule.status, 403);
  assert.equal(schedule.code, 'SCOPE_NOT_GRANTED');

  log('重启后明确的项目级分轨授权仍可由后台使用');
  const state = await h.api('GET', '/api/state');
  const doneClipId = state.segments[doneId].clipId;
  assert.ok(doneClipId);
  const split = await h.api('POST', '/api/projects/proj_new_multitrack/split', { clipId: doneClipId });
  assert.equal(split.status, 200, JSON.stringify(split));

  log('只有重新来到台前的本人能建立新在场会话并开新段');
  const nextDayToken = await h.presence();
  const newPress = await h.api(
    'POST',
    '/api/agent/old-button/press',
    { projectId: 'proj_new_multitrack', trackId: 'trk_take' },
    { 'x-presence-id': nextDayToken },
  );
  assert.equal(newPress.status, 200);
  const newStop = await h.api('POST', `/api/segments/${newPress.segment.segmentId}/stop`, {});
  assert.equal(newStop.status, 200);

  return { orphanId, doneId };
}

export async function midRevocationScenario(h, log = () => {}) {
  const token = await h.presence();
  const opened = await h.api(
    'POST',
    '/api/agent/old-button/press',
    { projectId: 'proj_new_multitrack', trackId: 'trk_take', autoAdmit: true },
    { 'x-presence-id': token },
  );
  assert.equal(opened.status, 200);
  const segmentId = opened.segment.segmentId;
  const sessionId = opened.segment.sessionId;
  const operationId = opened.segment.operationId;

  await h.api('POST', `/api/segments/${segmentId}/frames`, { text: 'first allowed text' });
  await h.api('POST', `/api/segments/${segmentId}/frames`, { text: 'second allowed text' });

  log('用户中途主动撤销会话；服务必须把撤销传播到设备并停采');
  const revoked = await h.api('POST', `/api/sessions/${sessionId}/revoke`, {
    reason: 'user_clicked_revoke_mid_take',
    discard: true,
  });
  assert.equal(revoked.status, 200, JSON.stringify(revoked));
  assert.equal(revoked.session.status, 'revoked');
  assert.equal(revoked.stop.segment.receiptVerified, true);
  assert.equal(revoked.stop.segment.receipt.revocation.revoked, true);
  assert.equal(h.device.state.status, 'idle');

  log('撤销后服务和设备都拒绝后续文本帧');
  const frameAfter = await h.api('POST', `/api/segments/${segmentId}/frames`, { text: 'must not enter device' });
  assert.equal(frameAfter.status, 403);
  assert.equal(frameAfter.code, 'SESSION_REVOKED');
  const directFrame = await h.deviceApi('POST', `/device/operations/${operationId}/frames`, {
    text: 'direct must not enter',
  });
  assert.equal(directFrame.status, 409);

  log('已撤销且要求丢弃的会话不能准作可存录音');
  const admit = await h.api('POST', `/api/segments/${segmentId}/admit`, {});
  assert.equal(admit.status, 403);
  assert.equal(admit.code, 'SESSION_REVOKED');
  const tracks = await h.api('GET', '/api/projects/proj_new_multitrack/tracks');
  const clips = tracks.tracks.flatMap((t) => t.clips);
  assert.equal(clips.some((c) => c.segmentId === segmentId), false);

  log('设备回执仍可为制作人签出停采完结证明');
  const certificate = await h.api('POST', `/api/segments/${segmentId}/completion-certificate`, {});
  assert.equal(certificate.status, 200, JSON.stringify(certificate));
  assert.equal(certificate.finalSeq, 2);
  assert.equal(certificate.sessionRevoked, true);

  const authorization = await h.api(
    'GET',
    `/api/authorization?userId=u_legacy&deviceId=dev_studio&sessionId=${sessionId}`,
  );
  assert.equal(authorization.scopes['capture.segment'].allowed, false);
  assert.equal(authorization.scopes['capture.segment'].code, 'SESSION_REVOKED');

  return { segmentId, sessionId, certificateId: certificate.certificateId };
}

export async function noticeAndLifecycleScenario(h, log = () => {}) {
  log('展示新版设备说明，但不作出任何新授权');
  const notice = await h.api('POST', '/api/notices/new-device-capabilities/shown', { acknowledged: true });
  assert.equal(notice.status, 200);
  assert.equal(notice.consentGranted, false);
  const prelistenAfterNotice = await h.api('POST', '/api/monitor/prelisten', {});
  assert.equal(prelistenAfterNotice.status, 403);
  assert.equal(prelistenAfterNotice.code, 'SCOPE_NOT_GRANTED');

  const token = await h.presence();
  log('设备退役/重置后，旧授权不能自动搬到该生命周期之后');
  const retire = await h.api('POST', '/api/devices/dev_studio/lifecycle', { lifecycle: 'retired' });
  assert.equal(retire.status, 200);
  const pressRetired = await h.api(
    'POST',
    '/api/agent/old-button/press',
    {},
    { 'x-presence-id': token },
  );
  assert.equal(pressRetired.status, 403);
  assert.equal(pressRetired.code, 'DEVICE_LIFECYCLE_FORBIDDEN');

  const repair = await h.api('POST', '/api/devices/dev_studio/lifecycle', { lifecycle: 'paired' });
  assert.equal(repair.status, 200);
  const press = await h.api(
    'POST',
    '/api/agent/old-button/press',
    {},
    { 'x-presence-id': token },
  );
  assert.equal(press.status, 200, JSON.stringify(press));
  const stop = await h.api('POST', `/api/segments/${press.segment.segmentId}/stop`, {});
  assert.equal(stop.status, 200);
  assert.equal(stop.segment.receiptVerified, true);
  return {};
}

export async function faultCleanupScenario(h, log = () => {}) {
  const token = await h.presence();
  const opened = await h.api(
    'POST',
    '/api/agent/old-button/press',
    { autoAdmit: false },
    { 'x-presence-id': token },
  );
  assert.equal(opened.status, 200);
  const segmentId = opened.segment.segmentId;
  await h.api('POST', `/api/segments/${segmentId}/frames`, { text: 'before simulated fault' });

  log('设备故障后，旧按钮代理执行绑定的故障清理');
  const fault = await h.api('POST', `/api/segments/${segmentId}/fault`, { reason: 'simulated_input_fault' });
  assert.equal(fault.status, 200, JSON.stringify(fault));
  assert.equal(fault.segment.status, 'fault_cleaned_verified');
  assert.equal(fault.segment.receiptVerified, true);
  assert.equal(fault.segment.receipt.type, 'fault_cleanup_receipt');
  assert.equal(h.device.state.status, 'idle');

  const frameAfter = await h.api('POST', `/api/segments/${segmentId}/frames`, { text: 'after cleanup' });
  assert.equal(frameAfter.status, 409);
  assert.equal(frameAfter.code, 'SEGMENT_CLOSED');
  return { segmentId };
}
