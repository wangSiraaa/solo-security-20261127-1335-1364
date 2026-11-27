import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, join, normalize } from 'node:path';
import { fileURLToPath } from 'node:url';
import { dirname } from 'node:path';
import { createSystem } from './src/system.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const publicDir = join(__dirname, 'public');
const system = createSystem();
const port = Number(process.env.PORT || 4318);
const host = process.env.HOST || '127.0.0.1';

const mime = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
};

function sendJson(res, status, body, headers = {}) {
  const data = JSON.stringify(body, null, 2);
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'cache-control': 'no-store',
    ...headers,
  });
  res.end(data);
}

async function readBody(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  if (!chunks.length) return {};
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}

async function serveStatic(req, res, pathname) {
  let relative = pathname === '/' ? 'index.html' : pathname.replace(/^\/static\//, '');
  const filePath = normalize(join(publicDir, relative));
  if (!filePath.startsWith(publicDir)) {
    res.writeHead(403);
    return res.end('Forbidden');
  }
  try {
    const content = await readFile(filePath);
    res.writeHead(200, { 'content-type': mime[extname(filePath)] || 'application/octet-stream' });
    return res.end(content);
  } catch {
    res.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' });
    return res.end('Not found');
  }
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`);
  const pathname = url.pathname;

  try {
    if (req.method === 'GET' && pathname === '/api/overview') {
      return sendJson(res, 200, system.overviewForUi());
    }

    const roleMatch = pathname.match(/^\/api\/role\/([a-z-]+)$/);
    if (req.method === 'GET' && roleMatch) {
      return sendJson(res, 200, system.roleExport(roleMatch[1]));
    }

    const exportMatch = pathname.match(/^\/api\/export\/([a-z-]+)$/);
    if (req.method === 'GET' && exportMatch) {
      const body = system.roleExport(exportMatch[1]);
      return sendJson(res, 200, body, {
        'content-disposition': `attachment; filename="${exportMatch[1]}-export.json"`,
      });
    }

    if (req.method === 'POST' && pathname === '/api/demo/reset') {
      return sendJson(res, 200, system.reset());
    }

    const importCatalogMatch = pathname.match(/^\/api\/archives\/(a|b)\/import-catalog$/);
    if (req.method === 'POST' && importCatalogMatch) {
      const body = await readBody(req);
      return sendJson(res, 200, system.importCatalog(importCatalogMatch[1], body.catalog || body));
    }

    const importSampleMatch = pathname.match(/^\/api\/archives\/(a|b)\/import-sample$/);
    if (req.method === 'POST' && importSampleMatch) {
      const side = importSampleMatch[1];
      const filePath = join(__dirname, 'imports', `archive-${side}-catalog.json`);
      const catalog = JSON.parse(await readFile(filePath, 'utf8'));
      return sendJson(res, 200, system.importCatalog(side, catalog));
    }

    if (req.method === 'POST' && pathname === '/api/demo/reset-and-prepare') {
      system.reset();
      return sendJson(res, 200, system.prepareInitialCollision());
    }

    if (req.method === 'POST' && pathname === '/api/sessions/open') {
      const body = await readBody(req);
      return sendJson(res, 200, system.openSession(body));
    }

    const submitMatch = pathname.match(/^\/api\/sessions\/([^/]+)\/submit$/);
    if (req.method === 'POST' && submitMatch) {
      const body = await readBody(req);
      return sendJson(res, 200, system.submitSide(submitMatch[1], body.pairId, body.side));
    }

    const confirmMatch = pathname.match(/^\/api\/sessions\/([^/]+)\/confirm$/);
    if (req.method === 'POST' && confirmMatch) {
      const body = await readBody(req);
      return sendJson(res, 200, system.confirmHit(confirmMatch[1], body.pairId, body.side));
    }

    const ackMatch = pathname.match(/^\/api\/sessions\/([^/]+)\/acknowledge$/);
    if (req.method === 'POST' && ackMatch) {
      const body = await readBody(req);
      return sendJson(res, 200, system.acknowledgeMismatch(ackMatch[1], body.pairId, body.side));
    }

    const closeMatch = pathname.match(/^\/api\/sessions\/([^/]+)\/close$/);
    if (req.method === 'POST' && closeMatch) {
      return sendJson(res, 200, system.closeSession(closeMatch[1]));
    }

    if (req.method === 'POST' && pathname === '/api/demo/comparison-round') {
      return sendJson(res, 200, system.runFullComparisonRound());
    }

    if (req.method === 'POST' && pathname === '/api/desk/dispatch') {
      return sendJson(res, 200, system.dispatchToDesk());
    }

    if (req.method === 'POST' && pathname === '/api/desk/adjudicate') {
      return sendJson(res, 200, { results: system.adjudicateAll() });
    }

    if (req.method === 'POST' && pathname === '/api/demo/correct-cut-and-replay') {
      return sendJson(res, 200, system.runCorrectionAndReplayHb());
    }

    if (req.method === 'POST' && pathname === '/api/demo/shorten-sp-and-replay') {
      return sendJson(res, 200, system.shortenSpExclusivityToBeforeEvening());
    }

    const replayMatch = pathname.match(/^\/api\/work-orders\/([^/]+)\/replay$/);
    if (req.method === 'POST' && replayMatch) {
      const body = await readBody(req);
      return sendJson(res, 200, system.replayWorkOrder(decodeURIComponent(replayMatch[1]), body));
    }

    const aloneMatch = pathname.match(/^\/api\/work-orders\/([^/]+)\/recheck-alone$/);
    if (req.method === 'POST' && aloneMatch) {
      try {
        const result = system.attemptCaseNumberOnlyRecheck(decodeURIComponent(aloneMatch[1]));
        return sendJson(res, 409, result);
      } catch (error) {
        return sendJson(res, 404, { ok: false, error: error.message });
      }
    }

    const reviewMatch = pathname.match(/^\/api\/review\/([^/]+)$/);
    if (req.method === 'GET' && reviewMatch) {
      try {
        return sendJson(res, 200, system.reviewPackage(decodeURIComponent(reviewMatch[1])));
      } catch (error) {
        return sendJson(res, 404, { ok: false, error: error.message });
      }
    }

    if (pathname.startsWith('/api/')) {
      return sendJson(res, 404, { ok: false, error: 'API not found' });
    }
    return serveStatic(req, res, pathname);
  } catch (error) {
    return sendJson(res, error.code === 'SESSION_OPEN' ? 409 : 400, {
      ok: false,
      error: error.message,
      code: error.code || 'BAD_REQUEST',
    });
  }
});

server.listen(port, host, () => {
  console.log(`两馆版权比对与联合排映台已启动：http://${host}:${port}`);
  console.log('零外部依赖；数据为内存样例，重启后回到初始种子。');
});
