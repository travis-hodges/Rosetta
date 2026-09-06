const slides = [...document.querySelectorAll('.slide')];
const frame = document.querySelector('.deck-frame');
const deck = document.querySelector('.deck');
const progress = document.querySelector('#deck-progress-bar');
const count = document.querySelector('#slide-count');
const name = document.querySelector('#slide-name');
const status = document.querySelector('#slide-status');
const motionButton = document.querySelector('#motion-button');
const overviewButton = document.querySelector('#overview-button');
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
let activeIndex = 0;
let paused = reducedMotion.matches;
let overview = false;
let touchStart = null;
let wheelLocked = false;
const orbControllers = [];

function indexFromHash() {
  const match = location.hash.match(/^#slide-(\d{2})$/);
  if (!match) return 0;
  return Math.max(0, Math.min(slides.length - 1, Number(match[1]) - 1));
}

function restartScene(slide) {
  slide.classList.remove('is-active');
  void slide.offsetWidth;
  slide.classList.add('is-active');
  orbControllers.forEach(controller => controller.refresh());
}

function showSlide(nextIndex, options = {}) {
  const next = Math.max(0, Math.min(slides.length - 1, nextIndex));
  activeIndex = next;
  slides.forEach((slide, index) => {
    slide.classList.toggle('is-past', index < next);
    slide.classList.toggle('is-active', index === next);
    slide.setAttribute('aria-hidden', String(index !== next && !overview));
    slide.inert = index !== next && !overview;
  });
  const slide = slides[next];
  if (!options.skipRestart && !reducedMotion.matches) restartScene(slide);
  progress.style.width = `${((next + 1) / slides.length) * 100}%`;
  count.textContent = `${String(next + 1).padStart(2, '0')} / ${String(slides.length).padStart(2, '0')}`;
  name.textContent = slide.dataset.title.toUpperCase();
  status.textContent = `Slide ${next + 1} of ${slides.length}: ${slide.dataset.title}`;
  if (!options.fromHash) history.replaceState(null, '', `#${slide.id}`);
}

function setOverview(next) {
  overview = next;
  deck.classList.toggle('is-overview', overview);
  overviewButton.setAttribute('aria-pressed', String(overview));
  overviewButton.innerHTML = overview ? 'Present <span aria-hidden="true">⊡</span>' : 'Overview <span aria-hidden="true">⊞</span>';
  slides.forEach((slide, index) => {
    slide.setAttribute('aria-hidden', String(!overview && index !== activeIndex));
    slide.inert = !overview && index !== activeIndex;
    slide.tabIndex = overview ? 0 : -1;
  });
  if (overview) deck.scrollTop = 0;
  if (!overview) showSlide(activeIndex);
}

function syncMotion() {
  document.documentElement.classList.toggle('motion-paused', paused);
  motionButton.setAttribute('aria-pressed', String(paused));
  motionButton.innerHTML = paused ? 'Resume <span aria-hidden="true">▷</span>' : 'Pause <span aria-hidden="true">Ⅱ</span>';
  orbControllers.forEach(controller => controller.refresh());
}

function move(delta) {
  if (overview) return;
  showSlide(activeIndex + delta);
}

document.querySelector('#next-button').addEventListener('click', () => move(1));
document.querySelector('#prev-button').addEventListener('click', () => move(-1));
motionButton.addEventListener('click', () => { paused = !paused; syncMotion(); });
overviewButton.addEventListener('click', () => setOverview(!overview));
slides.forEach((slide, index) => slide.addEventListener('click', () => {
  if (!overview) return;
  activeIndex = index;
  setOverview(false);
}));

addEventListener('hashchange', () => showSlide(indexFromHash(), { fromHash: true }));
addEventListener('keydown', event => {
  if (['ArrowRight', 'ArrowDown', 'PageDown', ' '].includes(event.key)) { event.preventDefault(); move(1); }
  if (['ArrowLeft', 'ArrowUp', 'PageUp'].includes(event.key)) { event.preventDefault(); move(-1); }
  if (event.key === 'Home') { event.preventDefault(); showSlide(0); }
  if (event.key === 'End') { event.preventDefault(); showSlide(slides.length - 1); }
  if (event.key.toLowerCase() === 'o' || event.key === 'Escape') { event.preventDefault(); setOverview(event.key === 'Escape' ? false : !overview); }
  if (event.key.toLowerCase() === 'm') { paused = !paused; syncMotion(); }
  if (event.key.toLowerCase() === 'r' && !overview) restartScene(slides[activeIndex]);
});

frame.addEventListener('wheel', event => {
  if (overview || wheelLocked || Math.abs(event.deltaY) < 18) return;
  wheelLocked = true;
  move(event.deltaY > 0 ? 1 : -1);
  setTimeout(() => { wheelLocked = false; }, 650);
}, { passive: true });
frame.addEventListener('touchstart', event => { touchStart = event.touches[0].clientX; }, { passive: true });
frame.addEventListener('touchend', event => {
  if (touchStart === null || overview) return;
  const delta = touchStart - event.changedTouches[0].clientX;
  touchStart = null;
  if (Math.abs(delta) > 45) move(delta > 0 ? 1 : -1);
}, { passive: true });

function createOrb(canvas) {
  const context = canvas.getContext('2d');
  if (!context) return null;
  const mode = canvas.dataset.orb;
  let width = 0;
  let height = 0;
  let frameId = 0;
  let phase = 0;
  let lastTime = 0;

  function draw() {
    if (!width || !height) return;
    context.clearRect(0, 0, width, height);
    const radius = Math.min(width * .35, height * .37);
    const spread = mode === 'open' ? .58 + Math.sin(phase * .5) * .08 : 0;
    context.save();
    context.translate(width * .5, height * .49 + Math.sin(phase * .55) * height * .008);
    context.rotate(-.31 + Math.sin(phase * .22) * .08);
    context.strokeStyle = '#77824f45';
    context.lineWidth = .7;
    context.beginPath();
    context.ellipse(0, 0, radius * 1.27, radius * .92, .7 + phase * .035, 0, Math.PI * 2);
    context.stroke();
    for (let dot = 0; dot < 3; dot += 1) {
      const angle = phase * .18 + dot * Math.PI * .67;
      context.fillStyle = dot === 1 ? '#f2ff66' : '#8da044';
      context.beginPath();
      context.arc(Math.cos(angle) * radius * 1.25, Math.sin(angle) * radius * .91, radius * .013, 0, Math.PI * 2);
      context.fill();
    }
    const slices = 64;
    const pitch = .25 + Math.sin(phase * .21) * .035;
    for (let index = 0; index < slices; index += 1) {
      const latitude = (index / (slices - 1) * 2 - 1) * .985;
      const y = latitude * radius * (1 + spread);
      const r = Math.sqrt(1 - latitude * latitude) * radius;
      const offset = Math.sin(index * .16 + phase * .35) * spread * radius * .15;
      const metal = context.createLinearGradient(-r, 0, r, 0);
      metal.addColorStop(0, '#343c2f');
      metal.addColorStop(.16, '#c4cab8');
      metal.addColorStop(.34, '#626b58');
      metal.addColorStop(.51 + Math.sin(phase * .17) * .06, '#f0f2e4');
      metal.addColorStop(.72, '#707a60');
      metal.addColorStop(.88, '#b3bba3');
      metal.addColorStop(1, '#303929');
      context.fillStyle = metal;
      context.beginPath();
      context.ellipse(offset, y, r, Math.max(.45, r * pitch), 0, 0, Math.PI * 2);
      context.fill();
      context.strokeStyle = '#283023b0';
      context.lineWidth = Math.max(.55, radius / slices * .25);
      context.beginPath();
      context.ellipse(offset, y + radius / slices * .5, r, Math.max(.45, r * pitch), 0, .08, Math.PI - .08);
      context.stroke();
    }
    if (spread > .3) {
      const glow = context.createRadialGradient(0, 0, 0, 0, 0, radius * .16);
      glow.addColorStop(0, '#f7ffaf');
      glow.addColorStop(1, '#a7bf36');
      context.fillStyle = glow;
      context.beginPath();
      context.arc(0, 0, radius * .12, 0, Math.PI * 2);
      context.fill();
    }
    context.restore();
  }

  function tick(time) {
    frameId = 0;
    if (paused || document.hidden || !canvas.closest('.slide').classList.contains('is-active')) { lastTime = 0; draw(); return; }
    phase += lastTime ? Math.min((time - lastTime) / 1000, .05) : 0;
    lastTime = time;
    draw();
    frameId = requestAnimationFrame(tick);
  }

  function refresh() {
    cancelAnimationFrame(frameId);
    frameId = 0;
    lastTime = 0;
    draw();
    if (!paused && canvas.closest('.slide').classList.contains('is-active')) frameId = requestAnimationFrame(tick);
  }

  const observer = new ResizeObserver(() => {
    const rect = canvas.getBoundingClientRect();
    width = rect.width;
    height = rect.height;
    const ratio = Math.min(devicePixelRatio || 1, 2);
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    refresh();
  });
  observer.observe(canvas);
  return { refresh, dispose() { cancelAnimationFrame(frameId); observer.disconnect(); } };
}

document.querySelectorAll('[data-orb]').forEach(canvas => {
  const controller = createOrb(canvas);
  if (controller) orbControllers.push(controller);
});
document.addEventListener('visibilitychange', () => orbControllers.forEach(controller => controller.refresh()));
addEventListener('pagehide', event => { if (!event.persisted) orbControllers.forEach(controller => controller.dispose()); });
reducedMotion.addEventListener('change', () => { paused = reducedMotion.matches; syncMotion(); });

showSlide(indexFromHash(), { fromHash: true, skipRestart: true });
syncMotion();
