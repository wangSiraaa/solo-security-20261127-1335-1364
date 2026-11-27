import http from 'node:http';
import { createReadStream } from 'node:fs';
import { stat } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const port = Number(process.env.PORT ?? 4173);
const host = process.env.HOST ?? '127.0.0.1';

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
};

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, `http://${host}`);
    let pathname = decodeURIComponent(url.pathname);
    if (pathname === '/') pathname = '/web/index.html';

    // 只开放界面与浏览器需要的核心模块；没有任何执行规则的后端 API。
    const allowedPrefixes = ['/web/', '/src/'];
    if (pathname !== '/web/index.html' && !allowedPrefixes.some((p) => pathname.startsWith(p))) {
      res.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' });
      res.end('Not found');
      return;
    }

    const filePath = path.normalize(path.join(__dirname, pathname));
    if (!filePath.startsWith(path.join(__dirname, 'web')) && !filePath.startsWith(path.join(__dirname, 'src'))) {
      res.writeHead(403);
      res.end('Forbidden');
      return;
    }

    const info = await stat(filePath);
    if (!info.isFile()) throw new Error('not file');
    res.writeHead(200, { 'content-type': MIME[path.extname(filePath)] ?? 'application/octet-stream' });
    createReadStream(filePath).pipe(res);
  } catch {
    res.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' });
    res.end('Not found');
  }
});

server.listen(port, host, () => {
  console.log(`规则发布器界面： http://${host}:${port}`);
  console.log('按 Ctrl+C 停止；界面加载后，站点底册/兑现记录可在页面内离线使用。');
});
