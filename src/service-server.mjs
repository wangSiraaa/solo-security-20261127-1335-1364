import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { UpgradeService } from './service.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const workspaceRoot = path.resolve(__dirname, '..');

const port = Number(process.env.SERVICE_PORT || process.env.PORT || 4399);
const host = process.env.HOST || '127.0.0.1';
const dataDir = process.env.SERVICE_DATA || path.join(workspaceRoot, 'data', 'service');
const deviceUrl = process.env.DEVICE_URL || 'http://127.0.0.1:4400';
const pairingToken = process.env.PAIRING_TOKEN || 'local-demo-pairing-token';
const publicDir = path.join(workspaceRoot, 'public');

const service = new UpgradeService({ dataDir, deviceUrl, pairingToken, publicDir });
await service.listen(port, host);
console.log(`[service] upgrade console on http://${host}:${port} (data: ${dataDir})`);

service
  .waitForDevice(10_000)
  .then(async () => {
    await service.bootstrap();
    const results = await service.reconcileAll();
    if (results.length) console.log('[service] startup reconciliation:', results);
  })
  .catch((error) => console.warn('[service] device/bootstrap not ready yet:', error.message));
