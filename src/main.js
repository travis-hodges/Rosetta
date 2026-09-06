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

// These outcomes are deliberately authored illustrations, never runtime results.
const patchButtons = [...document.querySelectorAll('[data-patch]')];
patchButtons.forEach(button => button.addEventListener('click', () => {
  const fixed = button.dataset.patch === 'fixed';
  patchButtons.forEach(item => item.setAttribute('aria-pressed', String(item === button)));
  const line = document.querySelector('#patch-line');
  line.textContent = fixed ? ' S ^VISITS(ID)=$G(^VISITS(ID))+1' : ' ; visit counter update removed';
  line.className = fixed ? 'code-added' : 'code-removed';
  document.querySelector('#demo-report').innerHTML = `<p><span class="report-key">OUTPUT</span><span class="pass">MATCH</span><span>"RECORDED"</span></p><p><span class="report-key">GLOBAL STATE</span><span class="${fixed ? 'pass' : 'fail'}">${fixed ? 'MATCH' : 'DIVERGED'}</span><span>^VISITS("demo")</span></p><p class="state-detail">${fixed ? 'Expected 4 → observed 4. The write is preserved.' : 'Expected 4 → observed 3. The write disappeared.'}</p><p class="verdict ${fixed ? 'pass' : 'fail'}">${fixed ? '✓ This illustrated case agrees.' : '× Regression found in this example.'}</p>`;
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
// The install command must name the host actually serving this page, so it is
// templated in the markup and filled in here. Hard-coding a domain is how a page
// ends up telling people to curl a host that does not resolve.
document.querySelectorAll('[data-origin-command]').forEach(node => {
  const command = node.dataset.originCommand.replace('{origin}', location.origin);
  node.textContent = command;
  const button = node.closest('.command-row')?.querySelector('[data-copy]');
  if (button) button.dataset.copy = command;
});

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

// A sliced, metallic sphere projected into Canvas 2D. Each latitude is a solid
// thin disc: metallic gradients give volume without a WebGL/Three dependency.
function createSculpture(canvas, dark) {
  const context = canvas.getContext('2d');
  if (!context) return null;
  let width = 0, height = 0, visible = false, frame = 0, lastTime = 0, phase = 0;
  let mouseX = 0, mouseY = 0;
  const surface = canvas.parentElement;
  function draw() {
    if (!width || !height) return;
    context.clearRect(0, 0, width, height);
    const progress = dark ? storyProgress : 0;
    const spread = dark ? Math.sin(progress * Math.PI) * 0.7 : 0;
    const radius = Math.min(width * .33, height * .345);
    const tilt = -0.34 + (dark ? progress * .68 : 0) + (paused ? 0 : mouseX * .035);
    context.save();
    context.translate(width * .5, height * .5 + (paused ? 0 : Math.sin(phase * .6) * 5));
    context.rotate(tilt);
    // Quiet orbit around the sculpture.
    context.strokeStyle = dark ? '#c1c9a22b' : '#74805a38';
    context.lineWidth = .7;
    context.beginPath();
    context.ellipse(0, 0, radius * 1.22, radius * .94, .6 + progress, 0, Math.PI * 2);
    context.stroke();
    const count = 66;
    const pitch = .27 + Math.sin(phase * .18) * .08 + (paused ? 0 : mouseY * .025);
    for (let i = 0; i < count; i++) {
      const latitude = (i / (count - 1) * 2 - 1) * .985;
      const y = latitude * radius * (1 + spread);
      const r = Math.sqrt(1 - latitude * latitude) * radius;
      const thickness = radius / count * .74;
      const offset = Math.sin(i * .13 + progress * 4) * spread * radius * .16;
      context.save();
      context.translate(offset, y);
      const metal = context.createLinearGradient(-r, -r * pitch, r, r * pitch);
      const shine = .47 + Math.sin(phase * .24 + i * .022) * .1;
      metal.addColorStop(0, dark ? '#4e5748' : '#3a4134');
      metal.addColorStop(.16, '#c7cdbb');
      metal.addColorStop(.31, '#626c58');
      metal.addColorStop(shine, '#f0f2e4');
      metal.addColorStop(.68, '#727c61');
      metal.addColorStop(.83, '#b4bca4');
      metal.addColorStop(1, '#333e2b');
      context.fillStyle = metal;
      context.beginPath();
      context.ellipse(0, 0, r, Math.max(.5, r * pitch), 0, 0, Math.PI * 2);
      context.fill();
      // A narrow dark edge separates the stacked discs.
      context.strokeStyle = i % 9 === 0 ? '#242c20' : (dark ? '#10160ac4' : '#30392ab8');
      context.lineWidth = Math.max(.7, thickness * .38);
      context.beginPath();
      context.ellipse(0, thickness, r, Math.max(.5, r * pitch), 0, 0, Math.PI);
      context.stroke();
      context.restore();
    }
    // Re-establish the front edges after stacking the discs, so the sphere
    // reads as separate metallic slices all the way across its face.
    for (let i = 1; i < count - 1; i++) {
      const latitude = (i / (count - 1) * 2 - 1) * .985;
      const r = Math.sqrt(1 - latitude * latitude) * radius;
      const y = latitude * radius * (1 + spread);
      const offset = Math.sin(i * .13 + progress * 4) * spread * radius * .16;
      context.strokeStyle = dark ? '#131b0ea6' : '#34402bab';
      context.lineWidth = Math.max(.7, radius / count * .3);
      context.beginPath();
      context.ellipse(offset, y, r, Math.max(.5, r * pitch), 0, .09, Math.PI - .09);
      context.stroke();
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
