import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { Script, createContext } from 'node:vm';
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
  // serve.mjs and build.mjs both flatten public/ onto the site root, so a
  // reference resolves against public/ first and the repository root second.
  for (const reference of references) {
    const path = reference.slice(1);
    const found = await readFile(new URL(`public/${path}`, site))
      .catch(() => readFile(new URL(path, site)))
      .then(() => true, () => false);
    assert.ok(found, `index.html references ${reference}, which does not exist`);
  }
});

test('the page reaches no origin it has not declared', () => {
  const origins = new Set([...`${html}${css}`.matchAll(/https?:\/\/([^/'")\s]+)/g)].map(match => match[1]));
  for (const origin of origins) {
    assert.ok(
      ['github.com', 'www.gao.gov', 'department.va.gov', 'www.w3.org'].includes(origin),
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
  // Percentages in inline CSS (max-width:100%) are layout, not claims.
  const prose = html.replace(/<style\b[\s\S]*?<\/style>/g, '').replace(/ style="[^"]*"/g, '');
  assert.doesNotMatch(prose, /\b\d{1,3}(\.\d+)?\s*%/, 'no hand-authored performance percentages');
  // The demo is real recorded output now, so it must be labelled as recorded
  // rather than illustrative — and must still carry the coverage caveat.
  assert.match(html, /recorded verbatim/i, 'the transcript must say it is recorded verbatim');
  assert.match(html, /not a proof of all program behaviour/i, 'the coverage caveat must survive');
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
  for (const path of ['/AGENTS.md', '/package.json', '/rosetta/core/interface.py', '/.git/config']) {
    assert.equal((await fetch(base + path)).status, 404, `${path} must not be served`);
  }
});

// The hero sculpture only exists once its animation loop runs, so a static page
// check cannot see it. This drives the real module under a stub DOM with a
// hand-pumped clock, which is the only way to observe the sphere turning.
function pumpSculpture(frames, msPerFrame) {
  const stub = () => new Proxy(function () {}, {
    get(target, key) {
      if (key === 'classList') return { toggle() {}, add() {}, remove() {}, contains: () => false };
      if (key === 'dataset') return {};
      if (key === 'style') return { setProperty() {} };
      if (key === 'textContent' || key === 'innerHTML') return '';
      if (key === 'offsetHeight' || key === 'offsetTop') return 900;
      if (key === 'getBoundingClientRect') return () => ({ width: 900, height: 900, top: 0, bottom: 900, left: 0, right: 900 });
      if (key === 'matches') return () => false;
      if (key === 'getAttribute') return () => 'false';
      if (key === Symbol.toPrimitive || key === 'then') return undefined;
      return stub();
    },
    apply: () => stub(),
  });

  // Both canvases run; each records separately so the hero can be read alone.
  const drawn = {}, strokes = {};
  const makeCanvas = id => {
    const glyphs = drawn[id] = [];
    const shapes = strokes[id] = [];
    const context2d = {
      ...['clearRect', 'save', 'restore', 'translate', 'rotate', 'beginPath', 'stroke', 'fill',
        'arc', 'setTransform', 'createLinearGradient', 'createRadialGradient']
        .reduce((all, name) => ({ ...all, [name]: () => ({ addColorStop() {} }) }), {}),
      ellipse: (...args) => shapes.push(args),
      fillText: (text, x, y) => glyphs.push({ text, x, y, style: context2d.fillStyle }),
    };
    return { width: 0, height: 0, getContext: () => context2d, parentElement: stub() };
  };
  const canvases = { 'hero-canvas': makeCanvas('hero-canvas'), 'story-canvas': makeCanvas('story-canvas') };

  const frameQueue = [];
  const observers = [];
  const Observer = class { constructor(cb) { observers.push(cb); } observe() {} unobserve() {} disconnect() {} };
  const sandbox = {
    document: new Proxy({}, { get(target, key) {
      if (key === 'hidden') return false;
      if (key === 'getElementById') return id => canvases[id];
      if (key === 'querySelectorAll') return () => [];
      if (key === 'addEventListener' || key === 'removeEventListener') return () => {};
      if (key === 'documentElement') return stub();
      return stub();
    } }),
    matchMedia: () => ({ matches: false, addEventListener() {} }),
    requestAnimationFrame: cb => frameQueue.push(cb),
    cancelAnimationFrame: () => {},
    ResizeObserver: Observer,
    IntersectionObserver: Observer,
    addEventListener: () => {},
    navigator: { clipboard: { writeText: () => Promise.resolve() } },
    devicePixelRatio: 1, innerWidth: 1440, innerHeight: 900, scrollY: 0,
    Math, Date, Object, Array, String, Number, Float32Array, Int32Array, Uint8Array, Proxy, Symbol, Promise,
  };
  sandbox.window = sandbox;
  createContext(sandbox);
  new Script(script, { filename: 'src/main.js' }).runInContext(sandbox);

  // Size the canvas and mark it visible, the way the real observers would.
  observers.forEach(cb => cb([{ isIntersecting: true, target: stub() }]));
  const captured = [];
  for (let n = 0; n < frames; n++) {
    drawn['hero-canvas'].length = 0;
    const due = frameQueue.splice(0, frameQueue.length);
    due.forEach(cb => cb(n * msPerFrame));
    if (drawn['hero-canvas'].length) captured.push(drawn['hero-canvas'].slice());
  }
  return { frames: captured, ellipses: strokes['hero-canvas'] };
}

// Glyphs are emitted in an order that shifts as the sphere turns, so frames can
// only be compared by where a byte landed, never by its index in the draw list.
// A byte travels a few pixels per frame, which is why this pairs by proximity
// rather than by grid cell: at this speed a cell boundary falls between frames.
function pairByProximity(before, after, tolerance) {
  const cell = tolerance * 2;
  const buckets = new Map();
  for (const glyph of after) {
    const key = `${Math.round(glyph.x / cell)},${Math.round(glyph.y / cell)}`;
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key).push(glyph);
  }
  const pairs = [];
  for (const glyph of before) {
    const cx = Math.round(glyph.x / cell), cy = Math.round(glyph.y / cell);
    let best = null, shortest = tolerance;
    for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) {
      for (const candidate of buckets.get(`${cx + dx},${cy + dy}`) ?? []) {
        const distance = Math.hypot(candidate.x - glyph.x, candidate.y - glyph.y);
        if (distance < shortest) { best = candidate; shortest = distance; }
      }
    }
    if (best) pairs.push({ from: glyph, to: best, distance: shortest });
  }
  return pairs;
}

test('the hero sphere is a full transparent shell that turns slowly', () => {
  const { frames, ellipses } = pumpSculpture(48, 60);
  assert.ok(frames.length >= 20, `expected a run of drawn frames, got ${frames.length}`);
  const first = frames[0];
  const last = frames[frames.length - 1];

  // Dense: the whole point of the shell is that it is made of many bytes.
  assert.ok(first.length > 1200, `expected a dense shell, drew ${first.length} bytes`);
  for (const glyph of first) assert.match(glyph.text, /^[0-9A-F]{2}$/);

  // Nothing is drawn but the bytes — no orbit ellipse around the globe.
  assert.equal(ellipses.length, 0, `expected no drawn ellipse, got ${ellipses.length}`);

  // Semi-transparent throughout: the far shell is nearly vapour and even the
  // nearest bytes stay see-through rather than reading as solid ink.
  const alphas = first.map(g => Number(/([\d.]+)\)$/.exec(g.style)?.[1] ?? 1));
  const [faintest, boldest] = [Math.min(...alphas), Math.max(...alphas)];
  assert.ok(faintest < .1, `far side is not faint enough (min alpha ${faintest})`);
  assert.ok(boldest > .3, `near side has faded out (max alpha ${boldest})`);
  assert.ok(boldest < .7, `shell must stay see-through, never ink (max alpha ${boldest})`);

  // A full shell, not a facing surface: both hemispheres carry real populations.
  const far = alphas.filter(a => a < .3).length;
  assert.ok(far > first.length * .25 && far < first.length * .75, `lopsided shell (${far} far of ${first.length})`);

  // Turning, and slowly: frame to frame a byte drifts a few pixels, not a leap.
  const step = pairByProximity(frames[10], frames[11], 10);
  assert.ok(step.length > frames[10].length * .6, `frames do not correspond (${step.length} paired)`);
  const drift = step.reduce((total, pair) => total + pair.distance, 0) / step.length;
  assert.ok(drift > .2, `sphere is not turning (mean drift ${drift.toFixed(2)}px per frame)`);
  assert.ok(drift < 8, `sphere is spinning too fast (mean drift ${drift.toFixed(2)}px per frame)`);

  // Cumulative travel has to be measured frame by frame, never by comparing the
  // first frame to the last. The longitude lattice maps onto itself every column
  // step, so at some instants the point cloud looks untouched while every byte on
  // it has in fact slid a long way around.
  const perFrame = [];
  for (let n = 10; n < 30; n++) {
    const pairs = pairByProximity(frames[n], frames[n + 1], 10);
    perFrame.push(pairs.reduce((total, pair) => total + pair.distance, 0) / pairs.length);
  }
  const travel = perFrame.reduce((a, b) => a + b, 0) / perFrame.length * (frames.length - 1);
  assert.ok(travel > 30, `sphere barely moved (about ${travel.toFixed(0)}px of travel over the run)`);

  // Churning, but gently: between adjacent frames only a slice of the surface
  // rerolls. If everything flips at once the sphere strobes instead of shimmering.
  const flipped = step.filter(pair => pair.from.text !== pair.to.text).length;
  assert.ok(flipped > 0, 'bytes never change value');
  assert.ok(flipped < step.length * .45, `bytes strobe rather than shimmer (${flipped} of ${step.length} flipped in one frame)`);
});

// The demo terminal shows real `rosetta verify` output. Nothing stops someone
// editing that transcript into something the CLI would never print, so pin every
// distinctive line back to the Python that produces it.
test('the demo transcript matches what the verifier actually prints', async () => {
  const [reportSource, runtime] = await Promise.all([
    readFile(new URL('rosetta/tools/report.py', site), 'utf8'),
    readFile(new URL('rosetta/core/runtime.py', site), 'utf8'),
  ]);
  // The renderer builds long sentences from adjacent string literals, so join
  // those seams before matching or every wrapped phrase looks like a mismatch.
  const report = reportSource.replace(/"\s*\n\s*"/g, '');
  const transcript = [...html.matchAll(/<pre class="transcript[^"]*"[^>]*><code>([\s\S]*?)<\/code><\/pre>/g)]
    .map(match => match[1].replace(/<[^>]+>/g, '').replace(/&lt;/g, '<').replace(/&gt;/g, '>'));
  assert.equal(transcript.length, 2, 'expected both the diverging and the equivalent run');

  // Phrasing the renderer owns. If report.py is reworded, this fails loudly
  // rather than leaving invented text on the page.
  for (const fragment of [
    'candidate matched the baseline on all ',
    'same output, same error behaviour, and ',
    'identical global state after every run.',
    'candidate diverged on ',
    'divergence(s) across ',
    'distinct reference(s).',
    'Database state differs',
    'has no FileMan data dictionary entry; the pieces above are positional only.',
  ]) {
    assert.ok(report.includes(fragment), `report.py no longer prints ${JSON.stringify(fragment)}`);
    assert.ok(transcript.join('\n').includes(fragment.trim()), `the transcript dropped ${JSON.stringify(fragment)}`);
  }
  assert.ok(runtime.includes('"<absent>"'), 'runtime.py no longer renders <absent>');

  // Every command shown has to be one the CLI defines, pointed at files that exist.
  const commands = [...html.matchAll(/rosetta (verify|edit) /g)].map(m => m[1]);
  assert.ok(commands.includes('verify') && commands.includes('edit'), 'expected both commands');
  for (const path of new Set([...html.matchAll(/examples\/mumps\/[\w.]+/g)].map(m => m[0]))) {
    await readFile(new URL(path, site));
  }

  // The two runs must disagree on the verdict; that is the whole point of the toggle.
  assert.ok(transcript.some(t => t.includes('NOT EQUIVALENT')), 'no diverging run shown');
  assert.ok(transcript.some(t => /(^|\n)EQUIVALENT/.test(t)), 'no equivalent run shown');
});
