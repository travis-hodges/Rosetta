import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { Script } from 'node:vm';
import { spawn } from 'node:child_process';
import { once } from 'node:events';

const site = new URL('../', import.meta.url);
const html = await readFile(new URL('index.html', site), 'utf8');
const script = await readFile(new URL('src/main.js', site), 'utf8');
const css = await readFile(new URL('src/styles.css', site), 'utf8');

test('the page module parses without a bundler', () => {
  assert.doesNotThrow(() => new Script(script, { filename: 'src/main.js' }));
  assert.doesNotMatch(script, /^\s*import\s+['"]/m, 'bundler-only imports will 404 in the browser');
});

test('every local asset the page references exists', async () => {
  const references = [...html.matchAll(/(?:href|src)="(\/[^"]+)"/g)].map(match => match[1]);
  assert.ok(references.includes('/src/main.js'));
  assert.ok(references.includes('/src/styles.css'));
  for (const reference of references) {
    await readFile(new URL(reference.slice(1), site));
  }
});

test('the only external origin is the declared font CDN', () => {
  const origins = new Set([...`${html}${css}`.matchAll(/https?:\/\/([^/'")\s]+)/g)].map(match => match[1]));
  for (const origin of origins) {
    assert.ok(
      ['fonts.googleapis.com', 'fonts.gstatic.com', 'github.com', 'www.w3.org', 'openapi.vercel.sh'].includes(origin),
      `Unexpected external origin ${origin}`,
    );
  }
  assert.doesNotMatch(html, /analytics|gtag|googletagmanager/i);
});

test('DOM identifiers are unique and accessibility relationships resolve', () => {
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
  assert.equal(new Set(ids).size, ids.length, 'IDs must be unique');
  for (const match of html.matchAll(/(?:aria-controls|aria-labelledby|for)="([^"]+)"/g)) {
    for (const id of match[1].split(' ')) assert.ok(ids.includes(id), `Missing target ${id}`);
  }
});

test('in-page navigation targets real sections', () => {
  for (const match of html.matchAll(/href="#([^"]+)"/g)) {
    assert.ok(html.includes(`id="${match[1]}"`), `Dead anchor #${match[1]}`);
  }
});

test('every element the script drives is present in the markup', () => {
  for (const match of script.matchAll(/querySelector\('#([\w-]+)'\)/g)) {
    assert.ok(html.includes(`id="${match[1]}"`), `Script targets missing #${match[1]}`);
  }
});

test('motion respects the reduced-motion preference', () => {
  assert.match(script, /prefers-reduced-motion:\s*reduce/);
  assert.match(css, /prefers-reduced-motion:\s*reduce/);
});

test('external links cannot reach back into the opener', () => {
  for (const match of html.matchAll(/<a\b[^>]*target="_blank"[^>]*>/g)) {
    assert.match(match[0], /rel="[^"]*noreferrer/, `Missing rel on ${match[0]}`);
  }
});

test('the page never claims benchmark numbers it has not measured', () => {
  assert.doesNotMatch(html, /\b\d{1,3}(\.\d+)?\s*%/, 'no hand-authored performance percentages');
  assert.match(html, /synthetic|illustrative/i, 'the walkthrough must be labelled as illustrative');
});

test('the build produces a servable site and unknown paths 404', async t => {
  const build = spawn(process.execPath, ['scripts/build.mjs'], { cwd: site, stdio: 'pipe' });
  const [code] = await once(build, 'exit');
  assert.equal(code, 0);
  assert.equal(await readFile(new URL('dist/index.html', site), 'utf8'), html);

  const server = spawn(process.execPath, ['scripts/serve.mjs', '--dist'], {
    cwd: site, env: { ...process.env, PORT: '0' }, stdio: 'pipe',
  });
  t.after(() => server.kill());
  const base = await new Promise((resolve, reject) => {
    let output = '';
    const timeout = setTimeout(() => reject(new Error('Server startup timed out')), 5000);
    server.stdout.on('data', chunk => {
      output += chunk;
      const match = output.match(/http:\/\/127\.0\.0\.1:(\d+)/);
      if (match) { clearTimeout(timeout); resolve(match[0]); }
    });
    server.on('error', error => { clearTimeout(timeout); reject(error); });
  });

  const root = await fetch(base);
  assert.equal(root.status, 200);
  assert.equal(await root.text(), html);
  assert.equal(root.headers.get('x-content-type-options'), 'nosniff');

  for (const [path, type] of [['/src/main.js', 'text/javascript'], ['/src/styles.css', 'text/css'], ['/og.png', 'image/png']]) {
    const asset = await fetch(base + path);
    assert.equal(asset.status, 200, `${path} must be served`);
    assert.match(asset.headers.get('content-type'), new RegExp(type));
  }

  assert.equal((await fetch(base + '/private.txt')).status, 404);
  assert.equal((await fetch(base + '/../AGENTS.md')).status, 404);
});

test('the source server refuses paths outside the site', async t => {
  const server = spawn(process.execPath, ['scripts/serve.mjs'], {
    cwd: site, env: { ...process.env, PORT: '0' }, stdio: 'pipe',
  });
  t.after(() => server.kill());
  const base = await new Promise((resolve, reject) => {
    let output = '';
    const timeout = setTimeout(() => reject(new Error('Server startup timed out')), 5000);
    server.stdout.on('data', chunk => {
      output += chunk;
      const match = output.match(/http:\/\/127\.0\.0\.1:(\d+)/);
      if (match) { clearTimeout(timeout); resolve(match[0]); }
    });
    server.on('error', error => { clearTimeout(timeout); reject(error); });
  });

  assert.equal((await fetch(base)).status, 200);
  assert.equal((await fetch(base + '/src/main.js')).status, 200);
  assert.equal((await fetch(base + '/og.png')).status, 200);
  for (const path of ['/AGENTS.md', '/package.json', '/orchestration/orchestrator.py', '/.git/config']) {
    assert.equal((await fetch(base + path)).status, 404, `${path} must not be served`);
  }
});
