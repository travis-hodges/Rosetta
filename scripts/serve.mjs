// Static server for local review. `--dist` serves the built output; otherwise the
// sources are served in place, so public/og.png resolves at /og.png either way.
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { dirname, join, normalize, extname } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const dist = process.argv.includes('--dist');
// .claude/launch.json passes --port; PORT covers npm scripts and CI.
const flag = process.argv.indexOf('--port');
const port = Number((flag === -1 ? undefined : process.argv[flag + 1]) || process.env.PORT || 5173);

const types = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
};

// Ordered candidate roots. Serving sources must not expose the rest of the
// repository, so only the site's own directories are reachable.
const roots = dist ? [join(root, 'dist')] : [join(root, 'public'), root];
const servable = relative => dist
  || relative === 'index.html'
  || relative.startsWith('src/');

const server = createServer(async (request, response) => {
  let path;
  try { path = decodeURIComponent(new URL(request.url, 'http://localhost').pathname); }
  catch { response.writeHead(400); response.end('Bad request'); return; }
  if (path.endsWith('/')) path += 'index.html';

  // Reject traversal before touching the filesystem.
  const relative = normalize(path).replace(/^(\.\.[/\\])+/, '').replace(/^[/\\]+/, '');
  if (relative.split(/[/\\]/).includes('..')) { response.writeHead(400); response.end('Bad request'); return; }

  for (const base of roots) {
    // public/ is always fair game; outside it, only the site's own files are.
    if (base === root && !servable(relative)) continue;
    let data;
    try { data = await readFile(join(base, relative)); }
    catch (error) {
      if (error.code === 'ENOENT' || error.code === 'EISDIR') continue;
      response.writeHead(500); response.end('Server error'); return;
    }
    response.writeHead(200, {
      'Content-Type': types[extname(relative)] || 'application/octet-stream',
      'X-Content-Type-Options': 'nosniff',
      'Cache-Control': 'no-store',
    });
    response.end(data);
    return;
  }
  response.writeHead(404); response.end('Not found');
});

server.listen(port, '127.0.0.1', () => console.log(`Rosetta: http://127.0.0.1:${server.address().port}`));
