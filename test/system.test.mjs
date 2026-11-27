import test from 'node:test';
import {
  absentNextDayScenario,
  consentAndHeldReceiptScenario,
  faultCleanupScenario,
  happyPathStartStopQuerySplit,
  midRevocationScenario,
  noticeAndLifecycleScenario,
  startHarness,
} from '../src/scenarios.mjs';

test('happy path: press, stop, query new project tracks, split', async () => {
  const h = await startHarness({ label: 'test-happy' });
  try {
    await happyPathStartStopQuerySplit(h);
  } finally {
    await h.close();
  }
});

test('preview-only consent rejects same package; held receipt blocks certificate', async () => {
  const h = await startHarness({ label: 'test-consent' });
  try {
    await consentAndHeldReceiptScenario(h);
  } finally {
    await h.close();
  }
});

test('absent next-day restart only reconciles/stops; new grants require presence', async () => {
  const h = await startHarness({ label: 'test-absent' });
  try {
    await absentNextDayScenario(h);
  } finally {
    await h.close();
  }
});

test('mid-session revocation stops device and blocks save but permits closure proof', async () => {
  const h = await startHarness({ label: 'test-revoke' });
  try {
    await midRevocationScenario(h);
  } finally {
    await h.close();
  }
});

test('new notice grants nothing and retired device blocks old scope', async () => {
  const h = await startHarness({ label: 'test-notice-lifecycle' });
  try {
    await noticeAndLifecycleScenario(h);
  } finally {
    await h.close();
  }
});

test('fault path is bound to cleanup and terminal device receipt', async () => {
  const h = await startHarness({ label: 'test-fault' });
  try {
    await faultCleanupScenario(h);
  } finally {
    await h.close();
  }
});
