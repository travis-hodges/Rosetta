import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { Script } from 'node:vm';
import { spawn } from 'node:child_process';
import { once } from 'node:events';

const site = new URL('../', import.meta.url);
const read = path => readFile(new URL(path, site), 'utf8');

// One entry per published page. Each page loads exactly one module: main.js drives
// the landing page's canvas artwork and scroll story and would throw on a page that
// has none of it, so /download has its own.
const PAGES = [
  { file: 'index.html', module: 'src/main.js' },
  { file: 'download.html', module: 'src/download.js' },
];
const DIRECTORY_ROUTES = new Set(['/slideshow']);
for (const page of PAGES) {
  page.html = await read(page.file);
  page.script = await read(page.module);
}
const html = PAGES[0].html;            // the landing page, where most rules apply
const script = PAGES[0].script;
const css = await read('src/styles.css');

test('every page module parses without a bundler', () => {
  for (const { module, script: source } of PAGES) {
    assert.doesNotThrow(() => new Script(source, { filename: module }));
    assert.doesNotMatch(source, /^\s*import\s+['"]/m, `${module}: bundler-only imports will 404 in the browser`);
  }
});

test('every local asset every page references exists', async () => {
  for (const { file, html: markup, module } of PAGES) {
    const references = [...markup.matchAll(/(?:href|src)="((?:\.\/|\/)[^"#?]+)"/g)].map(match => match[1]);
    const normalized = references.map(reference => reference.replace(/^\.\//, '/'));
    assert.ok(normalized.includes(`/${module}`), `${file} must load ${module}`);
    assert.ok(normalized.includes('/src/styles.css'), `${file} must load the stylesheet`);
    for (const reference of references) {
      if (DIRECTORY_ROUTES.has(reference)) continue;
      const path = reference.replace(/^(?:\.\/|\/)/, '');
      // Clean URLs: /download is a page route, not a file on disk.
      if (PAGES.some(page => page.file === `${path}.html`) || PAGES.some(page => page.file === path)) continue;
      // build.mjs flattens public/ onto the site root, so /favicon.svg is public/favicon.svg.
      // Resolve the same two ways it does, or the build passes while this fails.
      await readFile(new URL(path, site))
        .catch(() => readFile(new URL(`public/${path}`, site)))
        .catch(() => { throw new Error(`${file} references ${reference}, which does not exist`); });
    }
  }
});

const ALLOWED_ORIGINS = [
  'fonts.googleapis.com', 'fonts.gstatic.com',   // typography
  'github.com',                                  // the repository
  'www.gao.gov', 'department.va.gov',            // cited sources
  'www.w3.org', 'openapi.vercel.sh',             // schema namespaces
];

test('every external origin the page reaches is declared', () => {
  const everything = PAGES.map(page => page.html).join('') + css;
  const origins = new Set([...everything.matchAll(/https?:\/\/([^/'")\s]+)/g)].map(match => match[1]));
  // Report every offender at once. Asserting inside the loop stops at the first,
  // which turns one review into one round trip per link.
  const undeclared = [...origins].filter(origin => !ALLOWED_ORIGINS.includes(origin));
  assert.deepEqual(undeclared, [], `Undeclared external origins: ${undeclared.join(', ')}`);
  assert.doesNotMatch(everything, /analytics|gtag|googletagmanager/i);
});

test('DOM identifiers are unique and accessibility relationships resolve', () => {
  for (const { file, html: markup } of PAGES) {
    const ids = [...markup.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
    assert.equal(new Set(ids).size, ids.length, `${file}: IDs must be unique`);
    for (const match of markup.matchAll(/(?:aria-controls|aria-labelledby|for)="([^"]+)"/g)) {
      for (const id of match[1].split(' ')) assert.ok(ids.includes(id), `${file}: missing target ${id}`);
    }
  }
});

test('in-page navigation targets real sections', () => {
  for (const { file, html: markup } of PAGES) {
    for (const match of markup.matchAll(/href="#([^"]+)"/g)) {
      assert.ok(markup.includes(`id="${match[1]}"`), `${file}: dead anchor #${match[1]}`);
    }
  }
});

test('every public footer links to the slideshow', () => {
  for (const { file, html: markup } of PAGES) {
    assert.match(markup, /<footer\b[\s\S]*href="\/slideshow"[^>]*>Slideshow ↗<\/a>/,
      `${file}: footer must link to /slideshow`);
  }
});

test('every element each page script drives is present in that page', () => {
  for (const { file, html: markup, module, script: source } of PAGES) {
    for (const match of source.matchAll(/querySelector\('#([\w-]+)'\)/g)) {
      assert.ok(markup.includes(`id="${match[1]}"`), `${module} targets #${match[1]}, absent from ${file}`);
    }
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

// Visible text only: no <style>/<script> bodies and no attribute values. A guard that
// fires on `max-width:100%` teaches people to ignore it, so it has to read what a
// visitor reads.
const textContent = markup => markup
  .replace(/<(style|script)\b[^>]*>[\s\S]*?<\/\1>/gi, ' ')
  .replace(/<[^>]+>/g, ' ');

test('no page claims numbers it has not measured', () => {
  for (const { file, html: markup } of PAGES) {
    const text = textContent(markup);
    assert.doesNotMatch(text, /\b\d{1,3}(\.\d+)?\s*%/, `${file}: no hand-authored performance percentages`);
    assert.doesNotMatch(text, /\b[0-9a-f]{64}\b/i, `${file}: checksums come from releases.json, never hand-authored`);
  }
  assert.match(textContent(html), /synthetic|illustrative/i, 'the walkthrough must be labelled as illustrative');
});

test('the installer and release manifest are publishable artifacts', async () => {
  const installer = await read('public/install.sh');
  // Served to `curl | sh`, so a syntax error is a broken install for everyone.
  const check = spawn('sh', ['-n', 'public/install.sh'], { cwd: site, stdio: 'pipe' });
  const [code] = await once(check, 'exit');
  assert.equal(code, 0, 'public/install.sh must be valid POSIX sh');

  // The pinned release is substituted at publish time. If the sentinel is gone, a
  // real version must have replaced it -- never a hand-typed guess.
  const pinned = installer.match(/^PINNED_VERSION="([^"]*)"/m);
  assert.ok(pinned, 'install.sh must declare PINNED_VERSION');
  assert.ok(
    pinned[1].startsWith('__ROSETTA') || /^\d+\.\d+\.\d+$/.test(pinned[1]),
    `PINNED_VERSION is neither the sentinel nor a version: ${pinned[1]}`,
  );

  // A download page that names a host is a download page that breaks when the host
  // changes. Both the page and the installer derive it instead.
  for (const { file, html: markup } of PAGES) {
    assert.doesNotMatch(markup, /https?:\/\/rosetta\.[a-z]+/i, `${file} hard-codes a domain`);
  }

  // Strip comments first: the installer documents that it never uses sudo, and that
  // sentence must not be what trips the guard.
  const body = installer.replace(/^\s*#.*$/gm, '');
  assert.doesNotMatch(body, /\bsudo\b/, 'the installer must never invoke sudo');
  assert.doesNotMatch(body, /(^|\s)(rm\s+-rf?\s+["']?\$HOME["']?\s*$|rm\s+-rf?\s+\/\s)/m,
    'the installer must never remove a bare $HOME or /');

  const manifest = JSON.parse(await read('public/releases.json'));
  assert.ok('latest' in manifest, 'releases.json must have a latest key');
  if (manifest.latest) {
    for (const field of ['version', 'tag', 'tarball', 'tarball_url', 'sha256']) {
      assert.ok(manifest.latest[field], `releases.json latest is missing ${field}`);
    }
    assert.match(manifest.latest.sha256, /^[0-9a-f]{64}$/, 'sha256 must be a full digest');
  }
});

test('the stakes and product answer are explicit', () => {
  assert.match(html, /KNOWN EXPOSURE/);
  assert.match(html, /known cybersecurity vulnerabilities/);
  assert.match(html, /THE REQUIRED TRUST LAYER/);
  assert.match(html, /diff output and database state/i);
  assert.match(html, /air-gapped environment/i);
});

test('the landing page shows the product surface and official installer', () => {
  assert.match(html, /Rosetta terminal UI/);
  assert.match(html, /OPENCODE-DERIVED TUI/);
  assert.match(html, /RECORDED AUDIT TRACE/);
  assert.doesNotMatch(html, /ACTUAL PRODUCT UI|product-window|product-stage/);
  assert.match(html, /EXECUTION HARNESS/);
  assert.match(html, /BENCHMARKS/);
  assert.match(html, /PROOF RECEIPTS/);
  assert.match(html, /data-origin-command="curl -fsSL \{origin\}\/install\.sh \| sh"/);
  assert.doesNotMatch(html, /hero-warning/);
  assert.doesNotMatch(script, /FUTURE RUNTIME/);
});

test('the build produces a servable site and unknown paths 404', async t => {
  const build = spawn(process.execPath, ['scripts/build.mjs'], { cwd: site, stdio: 'pipe' });
  const [code] = await once(build, 'exit');
  assert.equal(code, 0);
  assert.equal(await readFile(new URL('dist/index.html', site), 'utf8'), html);
  const slideshow = await readFile(new URL('dist/slideshow.html', site), 'utf8');
  assert.match(slideshow, /id="deck"/);
  assert.match(slideshow, /href="\/slideshow\/pitch\.css"/);
  assert.match(slideshow, /src="\/slideshow\/pitch\.js"/);
  assert.equal(
    await readFile(new URL('dist/slideshow/index.html', site), 'utf8'),
    await read('pitch/index.html'),
  );

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

  // The download route, the installer and the manifest are the install path. If any
  // of them stops being served, the front page's one command breaks.
  const page = await fetch(base + '/download');
  assert.equal(page.status, 200, '/download must resolve via cleanUrls');
  assert.match(await page.text(), /id="release-panel"/);

  const slideshowPage = await fetch(base + '/slideshow');
  assert.equal(slideshowPage.status, 200, '/slideshow must resolve via cleanUrls');
  assert.match(await slideshowPage.text(), /id="deck"/);
  for (const path of ['/slideshow/pitch.css', '/slideshow/pitch.js']) {
    assert.equal((await fetch(base + path)).status, 200, `${path} must be served`);
  }

  const installer = await fetch(base + '/install.sh');
  assert.equal(installer.status, 200, '/install.sh must be served');
  assert.match(installer.headers.get('content-type'), /text\/plain/);
  assert.match(await installer.text(), /^#!\/bin\/sh/);

  const manifest = await fetch(base + '/releases.json');
  assert.equal(manifest.status, 200, '/releases.json must be served');
  assert.ok('latest' in await manifest.json());

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
  for (const path of ['/AGENTS.md', '/package.json', '/rosetta/core/interface.py', '/.git/config']) {
    assert.equal((await fetch(base + path)).status, 404, `${path} must not be served`);
  }
});
