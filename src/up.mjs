import { spawn } from 'node:child_process';
import path from 'node:path';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, '..');

if (process.argv.includes('--reset')) {
  fs.rmSync(path.join(root, 'data'), { recursive: true, force: true });
  console.log('[up] removed previous ./data');
}

const env = {
  ...process.env,
  HOST: process.env.HOST || '127.0.0.1',
  SERVICE_PORT: process.env.SERVICE_PORT || '4399',
  DEVICE_PORT: process.env.DEVICE_PORT || '4400',
  PAIRING_TOKEN: process.env.PAIRING_TOKEN || 'local-demo-pairing-token',
  SERVICE_DATA: process.env.SERVICE_DATA || path.join(root, 'data', 'service'),
  DEVICE_DATA: process.env.DEVICE_DATA || path.join(root, 'data', 'device'),
  DEVICE_URL: process.env.DEVICE_URL || 'http://127.0.0.1:4400',
};

const children = [
  ['device', path.join(root, 'src', 'device-server.mjs'), { ...env }],
  ['service', path.join(root, 'src', 'service-server.mjs'), { ...env, DEVICE_URL: `http://${env.HOST}:${env.DEVICE_PORT}` }],
].map(([label, script, childEnv]) => {
  const child = spawn(process.execPath, [script], { env: childEnv, stdio: ['ignore', 'pipe', 'pipe'] });
  child.stdout.on('data', (chunk) => process.stdout.write(`[${label}] ${chunk}`));
  child.stderr.on('data', (chunk) => process.stderr.write(`[${label}!] ${chunk}`));
  child.on('exit', (code, signal) => {
    console.log(`[up] ${label} exited code=${code} signal=${signal}`);
    shutdown(code === 0 ? 0 : 1);
  });
  return child;
});

let shuttingDown = false;
function shutdown(code = 0) {
  if (shuttingDown) return;
  shuttingDown = true;
  for (const child of children) {
    if (!child.killed) child.kill('SIGTERM');
  }
  setTimeout(() => process.exit(code), 250).unref();
}

process.on('SIGINT', () => shutdown(0));
process.on('SIGTERM', () => shutdown(0));

console.log(`[up] console: http://${env.HOST}:${env.SERVICE_PORT}/`);
console.log('[up] press Ctrl+C to stop both service and device');
