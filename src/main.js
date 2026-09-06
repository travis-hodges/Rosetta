// No libraries or network requests: the page remains readable without JavaScript.
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
let paused = reducedMotion.matches;
let storyProgress = 0;
let activeChapter = 0;
const motionButton = document.querySelector('#motion-toggle');
const menuButton = document.querySelector('#menu-toggle');
const nav = document.querySelector('#site-nav');
const story = document.querySelector('#workflow');
const chapters = [...document.querySelectorAll('[data-chapter]')];
const jumps = [...document.querySelectorAll('[data-jump]')];
const artControllers = [];

function syncMotion() {
  document.documentElement.classList.toggle('motion-paused', paused);
  motionButton.setAttribute('aria-pressed', String(paused));
  motionButton.innerHTML = paused ? 'Resume motion <span aria-hidden="true">▷</span>' : 'Pause motion <span aria-hidden="true">Ⅱ</span>';
  artControllers.forEach(art => art.refresh());
}
motionButton.addEventListener('click', () => { paused = !paused; syncMotion(); });
reducedMotion.addEventListener('change', () => { paused = reducedMotion.matches; syncMotion(); });
syncMotion();
function setMenu(open) {
  nav.classList.toggle('is-open', open);
  menuButton.setAttribute('aria-expanded', String(open));
  menuButton.innerHTML = open ? 'Close <span aria-hidden="true">−</span>' : 'Menu <span aria-hidden="true">+</span>';
}
menuButton.addEventListener('click', () => setMenu(menuButton.getAttribute('aria-expanded') !== 'true'));
nav.querySelectorAll('a').forEach(link => link.addEventListener('click', () => setMenu(false)));
addEventListener('keydown', event => {
  if (event.key === 'Escape' && menuButton.getAttribute('aria-expanded') === 'true') { setMenu(false); menuButton.focus(); }
});
addEventListener('click', event => {
  if (!document.querySelector('.site-header').contains(event.target)) setMenu(false);
});
function setChapter(index) {
  activeChapter = index;
  chapters.forEach((chapter, i) => {
    chapter.classList.toggle('is-active', i === index);
    chapter.setAttribute('aria-hidden', String(i !== index));
    chapter.inert = i !== index;
  });
  jumps.forEach((button, i) => {
    if (i === index) button.setAttribute('aria-current', 'step');
    else button.removeAttribute('aria-current');
  });
}
setChapter(0);
let scrollQueued = false;
function updateScroll() {
  scrollQueued = false;
  const rect = story.getBoundingClientRect();
  const travel = Math.max(1, story.offsetHeight - document.querySelector('.story-sticky').offsetHeight);
  storyProgress = Math.max(0, Math.min(1, -rect.top / travel));
  const chapter = Math.min(2, Math.floor(storyProgress * 3));
  if (activeChapter !== chapter) setChapter(chapter);
  const progress = document.querySelector('.story-progress');
  progress.style.setProperty('--progress', storyProgress);
  progress.setAttribute('aria-valuenow', String(Math.round(storyProgress * 100)));
  document.querySelector('.site-header').classList.toggle('is-dark', rect.top < 70 && rect.bottom > 70);
  document.documentElement.style.setProperty('--hero-scroll', paused ? 0 : Math.min(1, scrollY / innerHeight));
}
addEventListener('scroll', () => {
  if (!scrollQueued) { scrollQueued = true; requestAnimationFrame(updateScroll); }
}, { passive: true });
addEventListener('resize', () => { if (innerWidth > 760) setMenu(false); updateScroll(); });
jumps.forEach((button, i) => button.addEventListener('click', () => {
  const top = story.getBoundingClientRect().top + scrollY;
  const travel = Math.max(1, story.offsetHeight - document.querySelector('.story-sticky').offsetHeight);
  scrollTo({ top: top + travel * (i / 3 + 0.1), behavior: paused || reducedMotion.matches ? 'instant' : 'smooth' });
}));
updateScroll();

// Two recorded `rosetta verify` runs. Both are stacked in the markup so the
// terminal is already the height of the taller one and swapping cannot make the
// page jump; the inactive one is hidden from the accessibility tree too.
const patchButtons = [...document.querySelectorAll('[data-patch]')];
const transcripts = [...document.querySelectorAll('[data-transcript]')];
patchButtons.forEach(button => button.addEventListener('click', () => {
  patchButtons.forEach(item => item.setAttribute('aria-pressed', String(item === button)));
  transcripts.forEach(pane => {
    const shown = pane.dataset.transcript === button.dataset.patch;
    pane.classList.toggle('is-active', shown);
    pane.setAttribute('aria-hidden', String(!shown));
  });
}));
const languages = {
  mumps: { engine: 'MUMPS / YOTTADB', title: 'Start where the code\nmeets the record.', description: 'MUMPS is Rosetta’s first executable language. Inspect routines, evaluate changes, and compare captured global state using YottaDB.', boundary: 'Connect a model of your choice. No trained specialist model weights are bundled today.' },
  cobol: { engine: 'COBOL / FUTURE RUNTIME', title: 'A longer horizon\nfor business logic.', description: 'COBOL is part of the legacy-code challenge. GAO’s 2025 review identified Treasury systems using COBOL and assembly, with a shrinking pool of maintainers.', boundary: 'A future direction. Rosetta does not currently provide a COBOL execution adapter, benchmark suite, or trained COBOL model.' },
  jovial: { engine: 'JOVIAL / FUTURE RUNTIME', title: 'Make the unfamiliar\napproachable.', description: 'JOVIAL is part of Rosetta’s long-term language vision. Meaningful support will require a runtime, representative code, and executable evaluation cases.', boundary: 'A future direction. No JOVIAL runtime integration, measured results, or trained JOVIAL model is available today.' },
  cms: { engine: 'CMS-2 / FUTURE RUNTIME', title: 'More languages.\nThe same standard.', description: 'CMS-2 is another language in the long-term vision. Each new language must earn its place through runtime integration and inspectable evaluation evidence.', boundary: 'A future direction. No CMS-2 runtime integration, measured results, or trained CMS-2 model is available today.' },
};
const languageTabs = [...document.querySelectorAll('[data-language]')];
function selectLanguage(button, focus = false) {
  const name = button.dataset.language;
  const language = languages[name];
  languageTabs.forEach(tab => {
    tab.setAttribute('aria-selected', String(tab === button));
    tab.tabIndex = tab === button ? 0 : -1;
  });
  document.querySelector('#language-panel').setAttribute('aria-labelledby', button.id);
  const status = document.querySelector('#language-status');
  status.textContent = name === 'mumps' ? 'RUNTIME IMPLEMENTED' : 'FUTURE DIRECTION';
  status.classList.toggle('future', name !== 'mumps');
  document.querySelector('#language-engine').textContent = language.engine;
  document.querySelector('#language-title').textContent = language.title;
  document.querySelector('#language-description').textContent = language.description;
  document.querySelector('#language-boundary').textContent = language.boundary;
  document.querySelector('#corpus-note').hidden = name !== 'mumps';
  if (focus) button.focus();
}
languageTabs.forEach((button, i) => {
  button.addEventListener('click', () => selectLanguage(button));
  button.addEventListener('keydown', event => {
    let next;
    if (event.key === 'ArrowDown' || event.key === 'ArrowRight') next = (i + 1) % languageTabs.length;
    if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') next = (i - 1 + languageTabs.length) % languageTabs.length;
    if (event.key === 'Home') next = 0;
    if (event.key === 'End') next = languageTabs.length - 1;
    if (next !== undefined) { event.preventDefault(); selectLanguage(languageTabs[next], true); }
  });
});
const tabOrientation = matchMedia('(max-width: 760px)');
function syncTabOrientation() { document.querySelector('.language-tabs').setAttribute('aria-orientation', tabOrientation.matches ? 'horizontal' : 'vertical'); }
tabOrientation.addEventListener('change', syncTabOrientation);
syncTabOrientation();
let copyTimer;
document.querySelectorAll('[data-copy]').forEach(button => button.addEventListener('click', async () => {
  const status = document.querySelector('#copy-status');
  try {
    await navigator.clipboard.writeText(button.dataset.copy);
    document.querySelectorAll('[data-copy]').forEach(item => { item.textContent = 'Copy'; });
    button.textContent = 'Copied';
    status.textContent = `Copied: ${button.dataset.copy}`;
    clearTimeout(copyTimer);
    copyTimer = setTimeout(() => { button.textContent = 'Copy'; status.textContent = ''; }, 4500);
  } catch {
    status.textContent = 'Clipboard unavailable. Select and copy the command above.';
    const range = document.createRange();
    range.selectNodeContents(button.parentElement.querySelector('code'));
    const selection = getSelection();
    selection.removeAllRanges(); selection.addRange(range);
  }
}));
if ('IntersectionObserver' in window) {
  const reveals = new IntersectionObserver(entries => entries.forEach(entry => {
    if (entry.isIntersecting) { entry.target.classList.add('is-visible'); reveals.unobserve(entry.target); }
  }), { threshold: 0.12 });
  document.querySelectorAll('.reveal').forEach(element => reveals.observe(element));
  document.documentElement.classList.add('js-ready');
}

// The sphere is written rather than drawn: a full lat/long shell of hex bytes
// spun about its axis and projected by hand. Every byte on the far side is drawn
// too, at low opacity, so the volume reads as a transparent globe rather than a
// disc. No WebGL and no font download — the page's own monospace stack.
const HEX = '0123456789ABCDEF';
// View-space light: up, to the left, and toward the camera.
const LIGHT = [-.44, .56, .70].map((component, _, all) => component / Math.hypot(...all));
// On cream, a lit byte is dark ink and shadow fades out; on the near-black story
// surface that inverts. Each entry is [shadow rgb, lit rgb].
const INK = {
  light: [[173, 181, 155], [26, 33, 21]],
  dark: [[57, 67, 45], [242, 244, 230]],
};
const SHADES = 24, DEPTHS = 12;
// Bytes churn fast, but each one keeps its own offset into the clock so the
// surface never flips over all at once.
function byteAt(column, row, tick) {
  let h = (column * 73856093) ^ (row * 19349663) ^ (tick * 83492791);
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  h = (h ^ (h >>> 16)) >>> 0;
  return HEX[h & 15] + HEX[(h >>> 8) & 15];
}
function createSculpture(canvas, dark) {
  const context = canvas.getContext('2d');
  if (!context) return null;
  let width = 0, height = 0, visible = false, frame = 0, lastTime = 0, phase = 0;
  let mouseX = 0, mouseY = 0;
  const surface = canvas.parentElement;
  // Thousands of glyphs a frame: project into reused buffers, then draw far side
  // first. Reallocating these every tick would hand the GC the whole sphere.
  let screenX = new Float32Array(0), screenY = new Float32Array(0);
  let paint = new Int32Array(0), onFront = new Uint8Array(0), labels = [];
  function reserve(capacity) {
    if (screenX.length >= capacity) return;
    const size = 1 << (32 - Math.clz32(capacity - 1));
    screenX = new Float32Array(size); screenY = new Float32Array(size);
    paint = new Int32Array(size); onFront = new Uint8Array(size); labels = new Array(size);
  }
  function draw() {
    if (!width || !height) return;
    context.clearRect(0, 0, width, height);
    const progress = dark ? storyProgress : 0;
    const spread = dark ? Math.sin(progress * Math.PI) * 0.7 : 0;
    const radius = Math.min(width * .33, height * .345);
    const tilt = -0.06 + (dark ? progress * .5 : 0) + (paused ? 0 : mouseX * .035);
    context.save();
    context.translate(width * .5, height * .5 + (paused ? 0 : Math.sin(phase * .6) * 5));
    context.rotate(tilt);
    const [shadow, lit] = dark ? INK.dark : INK.light;
    const size = Math.max(6, radius * .047);
    const advance = size * 1.36;
    const rows = Math.max(9, Math.round(radius * 2 / (size * 1.04)));
    // Latitude tilt: how far the poles lean toward the camera.
    const lean = .23 + (paused ? 0 : mouseY * .05);
    const sinLean = Math.sin(lean), cosLean = Math.cos(lean);
    const spin = phase * .22;
    // Slow churn, so the offset has to sit inside the floor: added outside it,
    // every byte on the sphere flips on the same tick and the surface strobes.
    const churn = phase * 3;
    context.font = `600 ${size.toFixed(2)}px ui-monospace, SFMono-Regular, Menlo, monospace`;
    context.textAlign = 'center';
    context.textBaseline = 'middle';
    // Shade x opacity, rebuilt per frame. Composing an rgba() string per glyph
    // costs more than the whole projection once there are thousands of them.
    const palette = new Array(SHADES * DEPTHS);
    for (let shade = 0; shade < SHADES; shade++) {
      const k = shade / (SHADES - 1);
      const channel = index => Math.round(shadow[index] + (lit[index] - shadow[index]) * k);
      const rgb = `${channel(0)},${channel(1)},${channel(2)}`;
      for (let step = 0; step < DEPTHS; step++) {
        palette[shade * DEPTHS + step] = `rgba(${rgb},${(.04 + step / (DEPTHS - 1) * .54).toFixed(3)})`;
      }
    }
    reserve(rows * Math.ceil(radius * 2 * Math.PI / advance) + rows);
    let count = 0;
    for (let i = 0; i < rows; i++) {
      const latitude = ((i + .5) / rows * 2 - 1) * .995;
      const ring = Math.sqrt(1 - latitude * latitude) * radius;
      const columns = Math.max(3, Math.round(ring * 2 * Math.PI / advance));
      const arc = Math.PI * 2 / columns;
      const offset = Math.sin(i * .13 + progress * 4) * spread * radius * .16;
      const poleY = latitude * radius;
      for (let j = 0; j < columns; j++) {
        const longitude = j * arc + spin;
        const sinLong = Math.sin(longitude), cosLong = Math.cos(longitude);
        // Where the surface turns away from the camera the bytes stack into mush;
        // thin them once their projected gap closes to a fraction of a glyph.
        if (ring * Math.abs(sinLong) * arc < size * .26) continue;
        const z = ring * sinLong;
        const x = ring * cosLong;
        const y = poleY * cosLean - z * sinLean;
        const depth = poleY * sinLean + z * cosLean;
        const front = depth > 0;
        // Lambert against the rotated normal, so the terminator travels with the spin.
        // The ambient floor matters: with pure lambert the unlit side drops out
        // entirely and the globe reads as a crescent instead of a ball.
        const lambert = Math.max(0, (x * LIGHT[0] + y * LIGHT[1] + depth * LIGHT[2]) / radius);
        const shade = Math.min(SHADES - 1, Math.round((.3 + lambert ** .8 * .7) * (SHADES - 1)));
        // The far shell stays faint; the near shell carries the image.
        const reach = depth / radius;
        const step = front
          ? Math.min(DEPTHS - 1, 5 + Math.round(reach * (DEPTHS - 6)))
          : Math.max(0, Math.round((1 + reach) * 3));
        screenX[count] = offset + x;
        screenY[count] = y * (1 + spread);
        paint[count] = shade * DEPTHS + step;
        onFront[count] = front ? 1 : 0;
        labels[count] = byteAt(j, i, Math.floor(churn + (i * 7 + j * 13) % 6 / 6));
        count++;
      }
    }
    // Far shell first, so the near bytes read on top of it.
    for (const pass of [0, 1]) {
      for (let k = 0; k < count; k++) {
        if (onFront[k] !== pass) continue;
        context.fillStyle = palette[paint[k]];
        context.fillText(labels[k], screenX[k], screenY[k]);
      }
    }
    if (spread > .15) {
      context.globalAlpha = Math.min(.9, spread);
      const core = context.createRadialGradient(-5, -5, 0, 0, 0, radius * .12);
      core.addColorStop(0, '#efffb1'); core.addColorStop(1, '#aec739');
      context.fillStyle = core; context.beginPath(); context.arc(0, 0, radius * .11, 0, Math.PI * 2); context.fill();
    }
    context.restore();
  }
  function tick(time) {
    frame = 0;
    if (!visible || document.hidden || paused) { lastTime = 0; return; }
    if (!lastTime || time - lastTime >= 30) {
      phase += lastTime ? Math.min((time - lastTime) / 1000, .05) : 0;
      lastTime = time; draw();
    }
    frame = requestAnimationFrame(tick);
  }
  function refresh() {
    cancelAnimationFrame(frame); frame = 0; lastTime = 0;
    if (visible && !document.hidden) { draw(); if (!paused) frame = requestAnimationFrame(tick); }
  }
  const resize = new ResizeObserver(() => {
    const rect = surface.getBoundingClientRect();
    width = rect.width; height = rect.height;
    const dpr = Math.min(devicePixelRatio || 1, 1.75);
    canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr);
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
    draw();
  });
  resize.observe(surface);
  const observer = new IntersectionObserver(([entry]) => { visible = entry.isIntersecting; refresh(); }, { rootMargin: '80px' });
  observer.observe(surface);
  const pointer = event => { if (!paused) { mouseX = event.clientX / innerWidth - .5; mouseY = event.clientY / innerHeight - .5; } };
  addEventListener('pointermove', pointer, { passive: true });
  document.addEventListener('visibilitychange', refresh);
  surface.classList.add('art-ready');
  return { refresh, dispose() { cancelAnimationFrame(frame); resize.disconnect(); observer.disconnect(); removeEventListener('pointermove', pointer); document.removeEventListener('visibilitychange', refresh); } };
}
for (const [id, dark] of [['hero-canvas', false], ['story-canvas', true]]) {
  const art = createSculpture(document.getElementById(id), dark);
  if (art) artControllers.push(art);
}
addEventListener('pagehide', event => { if (!event.persisted) artControllers.forEach(art => art.dispose()); });
