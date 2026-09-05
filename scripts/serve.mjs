import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
const root = dirname(dirname(fileURLToPath(import.meta.url)));
const directory = process.argv.includes('--dist') ? 'dist' : 'web';
const port = Number(process.env.PORT || 5173);
const server = createServer(async (request, response) => {
  let path;
  try { path = new URL(request.url, 'http://localhost').pathname; }
  catch { response.writeHead(400);response.end('Bad request');return; }
  const routes = {
    '/': [join(root, directory, 'index.html'), 'text/html; charset=utf-8'],
    '/index.html': [join(root, directory, 'index.html'), 'text/html; charset=utf-8'],
    '/results/summary.json': [join(root, directory === 'dist' ? 'dist/results' : 'results', 'summary.json'), 'application/json; charset=utf-8'],
  };
  if (!routes[path]) {response.writeHead(404);response.end('Not found');return;}
  try {
    const data = await readFile(routes[path][0]);
    response.writeHead(200, { 'Content-Type':routes[path][1], 'X-Content-Type-Options':'nosniff', 'Cache-Control':'no-store' });
    response.end(data);
  } catch(error) {
    response.writeHead(error.code === 'ENOENT' ? 404 : 500);
    response.end(error.code === 'ENOENT' ? 'Not found' : 'Server error');
  }
});
server.listen(port, '127.0.0.1', () => console.log(`Rosetta: http://127.0.0.1:${server.address().port}`));
