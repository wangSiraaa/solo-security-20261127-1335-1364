import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { SimulatedDevice } from './device.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const workspaceRoot = path.resolve(__dirname, '..');

const port = Number(process.env.DEVICE_PORT || 4400);
const host = process.env.HOST || '127.0.0.1';
const dataDir = process.env.DEVICE_DATA || path.join(workspaceRoot, 'data', 'device');
const pairingToken = process.env.PAIRING_TOKEN || 'local-demo-pairing-token';

const device = new SimulatedDevice({ dataDir, pairingToken });
await device.listen(port, host);
console.log(`[device] simulated recorder on http://${host}:${port} (data: ${dataDir})`);
