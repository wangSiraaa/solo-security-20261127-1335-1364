import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  addChat,
  approveCorrection,
  createCorrection,
  createState,
  exportTranscript,
  makeCitation,
  publicRegistry,
  setPresence,
  verifySessionMarks,
} from './src/core.js';
import { buildEvidencePackage } from './src/evidence.js';
import { arbitratePackage, retrieveSignedContent } from './src/arbiter.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PUBLIC_DIR = path.join(__dirname, 'public');
const ARTIFACT_DIR = path.join(__dirname, 'artifacts');
const PORT = Number(process.env.PORT || 4173);
const HOST = process.env.HOST || '127.0.0.1';

/** @type {Map<string, ReturnType<typeof createState>>} */
const sessions = new Map();

function getState(sid = 'present') {
  const key = ['present', 'missing', 'live'].includes(sid) ? sid : 'live';
  if (!sessions.has(key)) sessions.set(key, createState({ scenario: key }));
  return sessions.get(key);
}

function publicState(state) {
  return {
    ...state,
    parties: Object.fromEntries(
      Object.entries(state.parties).map(([id, p]) => [
        id,
        {
          id: p.id,
          name: p.name,
          role: p.role,
          seal: { ...p.seal, privateKeyPem: '[redacted-in-browser; localhost terminal action signs server-side]' },
        },
      ]),
    ),
    registry: publicRegistry(state),
    pairMarkChecks: verifySessionMarks(state),
  };
}

function sendJson(res, status, data) {
  const body = JSON.stringify(data, null, 2);
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'cache-control': 'no-store',
  });
  res.end(body);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let data = '';
    req.on('data', (chunk) => {
      data += chunk;
      if (data.length > 5_000_000) reject(new Error('请求体过大'));
    });
    req.on('end', () => {
      if (!data) return resolve({});
      try {
        resolve(JSON.parse(data));
      } catch (err) {
        reject(new Error(`JSON 解析失败：${err.message}`));
      }
    });
    req.on('error', reject);
  });
}

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.md': 'text/markdown; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
};

function serveStatic(req, res, pathname) {
  let rel = pathname === '/' ? '/index.html' : pathname;
  let root = PUBLIC_DIR;
  if (pathname.startsWith('/artifacts/')) {
    root = __dirname;
    rel = pathname;
  }
  const filePath = path.normalize(path.join(root, rel));
  const allowedRoots = [PUBLIC_DIR, ARTIFACT_DIR, path.join(__dirname, 'artifacts')];
  if (!allowedRoots.some((r) => filePath === r || filePath.startsWith(`${r}${path.sep}`))) {
    res.writeHead(403);
    return res.end('Forbidden');
  }
  fs.readFile(filePath, (err, buf) => {
    if (err) {
      res.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' });
      return res.end('Not found');
    }
    res.writeHead(200, { 'content-type': MIME[path.extname(filePath)] ?? 'application/octet-stream' });
    res.end(buf);
  });
}

function findPackage(packageId) {
  for (const state of sessions.values()) {
    const pkg = state.packages.find((p) => p.packageId === packageId);
    if (pkg) return { pkg, state };
  }
  return null;
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`);
  const pathname = url.pathname;

  if (req.method === 'GET' && pathname === '/api/health') {
    return sendJson(res, 200, { ok: true, service: 'private-talk-arbitration-simulator', port: PORT });
  }

  try {
    if (req.method === 'GET' && pathname === '/verify-evidence.mjs') {
      const file = path.join(__dirname, 'scripts', 'verify-evidence.mjs');
      const buf = fs.readFileSync(file);
      res.writeHead(200, { 'content-type': 'text/javascript; charset=utf-8' });
      return res.end(buf);
    }

    if (pathname.startsWith('/api/')) {
      const body = req.method === 'POST' ? await readBody(req) : {};
      const sid = url.searchParams.get('sid') || body.sid || 'present';

      if (req.method === 'GET' && pathname === '/api/state') {
        return sendJson(res, 200, publicState(getState(sid)));
      }

      if (req.method === 'POST' && pathname === '/api/reset') {
        const scenario = body.scenario || sid;
        const state = createState({ scenario });
        sessions.set(sid, state);
        return sendJson(res, 200, publicState(state));
      }

      const state = getState(sid);

      if (req.method === 'POST' && pathname === '/api/presence') {
        setPresence(state, body.party, body.status, body.at);
        return sendJson(res, 200, publicState(state));
      }

      if (req.method === 'POST' && pathname === '/api/messages') {
        const message = addChat(state, body);
        return sendJson(res, 200, { message, state: publicState(state) });
      }

      if (req.method === 'POST' && pathname === '/api/citation-preview') {
        const citation = makeCitation(state, body);
        return sendJson(res, 200, citation);
      }

      if (req.method === 'POST' && pathname === '/api/corrections') {
        const correction = createCorrection(state, body);
        return sendJson(res, 200, { correction, state: publicState(state) });
      }

      if (req.method === 'POST' && pathname === '/api/approvals') {
        const result = approveCorrection(state, body);
        return sendJson(res, 200, { ...result, state: publicState(state) });
      }

      if (req.method === 'POST' && pathname === '/api/transcript') {
        const transcript = exportTranscript(state, body.party ?? sid, {
          includePairVerifier: Boolean(body.includePairVerifier),
        });
        return sendJson(res, 200, transcript);
      }

      if (req.method === 'POST' && pathname === '/api/packages') {
        const pkg = buildEvidencePackage(state, body);
        state.packages.push({ packageId: pkg.packageId, generatedAt: pkg.generatedAt, title: pkg.title });
        // Keep full package in a side map? state.packages currently summaries. Store full on global map.
        packagesById.set(pkg.packageId, pkg);
        return sendJson(res, 200, pkg);
      }

      if (req.method === 'GET' && pathname === '/api/packages') {
        return sendJson(res, 200, {
          sessionSummaries: state.packages,
          loadedPackages: [...packagesById.values()].map((p) => ({
            packageId: p.packageId,
            title: p.title,
            generatedAt: p.generatedAt,
            caseCaption: p.caseCaption,
          })),
        });
      }

      if (req.method === 'GET' && pathname.startsWith('/api/packages/')) {
        const packageId = pathname.split('/').pop();
        const found = packagesById.get(packageId) ?? findPackage(packageId)?.pkg;
        if (!found) return sendJson(res, 404, { error: '找不到证据包；若证据包是下载的 JSON，请在仲裁席直接上传。' });
        return sendJson(res, 200, found);
      }

      if (req.method === 'POST' && pathname === '/api/arbitrate') {
        let pkg = body.package;
        if (!pkg && body.packageId) {
          pkg = packagesById.get(body.packageId) ?? findPackage(body.packageId)?.pkg;
        }
        if (!pkg) return sendJson(res, 400, { error: '请提供证据包 JSON 或 packageId' });
        const award = arbitratePackage(pkg, body.options ?? {});
        const awardSummary = { awardId: award.awardId, packageId: award.packageId, renderedAt: award.renderedAt };
        state.awards.push(awardSummary);
        awardsById.set(award.awardId, award);
        return sendJson(res, 200, award);
      }

      if (req.method === 'POST' && pathname === '/api/retrieve') {
        let pkg = body.package;
        if (!pkg && body.packageId) pkg = packagesById.get(body.packageId) ?? findPackage(body.packageId)?.pkg;
        if (!pkg) return sendJson(res, 400, { error: '缺少证据包' });
        return sendJson(res, 200, retrieveSignedContent(pkg, body.query));
      }

      if (req.method === 'GET' && pathname.startsWith('/api/awards/')) {
        const awardId = pathname.split('/').pop();
        const award = awardsById.get(awardId);
        if (!award) return sendJson(res, 404, { error: '找不到本次运行中出具的裁决；可使用已导出的裁决 JSON。' });
        return sendJson(res, 200, award);
      }

      return sendJson(res, 404, { error: `未知 API：${req.method} ${pathname}` });
    }

    if (req.method === 'GET') return serveStatic(req, res, pathname);
    res.writeHead(405);
    res.end('Method Not Allowed');
  } catch (err) {
    return sendJson(res, err.code === 'PARTY_ABSENT' ? 409 : 400, {
      error: err.message,
      code: err.code ?? 'BAD_REQUEST',
    });
  }
});

const packagesById = new Map();
const awardsById = new Map();

server.listen(PORT, HOST, () => {
  fs.mkdirSync(ARTIFACT_DIR, { recursive: true });
  console.log(`谈单终端/模拟仲裁席已启动：`);
  console.log(`  总入口      http://${HOST}:${PORT}/`);
  console.log(`  甲终端      http://${HOST}:${PORT}/terminal.html?party=A&sid=present`);
  console.log(`  乙终端      http://${HOST}:${PORT}/terminal.html?party=B&sid=present`);
  console.log(`  失联分支甲  http://${HOST}:${PORT}/terminal.html?party=A&sid=missing`);
  console.log(`  失联分支乙  http://${HOST}:${PORT}/terminal.html?party=B&sid=missing`);
  console.log(`  仲裁席      http://${HOST}:${PORT}/arbiter.html`);
});
