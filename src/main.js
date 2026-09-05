import './styles.css';

const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const menuButton = document.querySelector('.menu-toggle');
const nav = document.querySelector('#site-nav');

menuButton?.addEventListener('click', () => {
  const open = menuButton.getAttribute('aria-expanded') !== 'true';
  menuButton.setAttribute('aria-expanded', String(open));
  menuButton.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
  nav?.classList.toggle('is-open', open);
  document.body.classList.toggle('menu-open', open);
});

nav?.querySelectorAll('a').forEach((link) => link.addEventListener('click', () => {
  menuButton?.setAttribute('aria-expanded', 'false');
  menuButton?.setAttribute('aria-label', 'Open menu');
  nav.classList.remove('is-open');
  document.body.classList.remove('menu-open');
}));

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && menuButton?.getAttribute('aria-expanded') === 'true') {
    menuButton.click();
    menuButton.focus();
  }
});

const makeCanvas = (canvas, isHero) => {
  if (!canvas) return;
  const ctx = canvas.getContext('2d', { alpha: true });
  let width = 0; let height = 0; let frame = 0; let raf = 0; let points = [];
  const resize = () => {
    const bounds = canvas.getBoundingClientRect();
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    width = bounds.width; height = bounds.height;
    canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio);
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    points = Array.from({ length: isHero ? Math.max(58, Math.floor(width / 18)) : Math.max(38, Math.floor(width / 26)) }, (_, index) => ({
      x: Math.random() * width, y: Math.random() * height, vx: (Math.random() - .5) * (isHero ? .25 : .1), vy: (Math.random() - .5) * .14, phase: Math.random() * Math.PI * 2, size: index % 11 === 0 ? 3 : 1,
    }));
  };
  const draw = () => {
    ctx.clearRect(0, 0, width, height);
    const grid = 52;
    ctx.strokeStyle = 'rgba(255,255,255,.055)'; ctx.lineWidth = 1;
    for (let x = 0; x < width; x += grid) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, height); ctx.stroke(); }
    for (let y = 0; y < height; y += grid) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(width, y); ctx.stroke(); }
    points.forEach((point, index) => {
      point.x += point.vx + (isHero ? 0 : .12); point.y += point.vy;
      if (point.x < -15) point.x = width + 15; if (point.x > width + 15) point.x = -15;
      if (point.y < -15) point.y = height + 15; if (point.y > height + 15) point.y = -15;
      if (isHero) {
        for (let next = index + 1; next < points.length; next += 1) {
          const peer = points[next]; const distance = Math.hypot(point.x - peer.x, point.y - peer.y);
          if (distance < 120) { ctx.strokeStyle = `rgba(242,255,102,${(1 - distance / 120) * .12})`; ctx.beginPath(); ctx.moveTo(point.x, point.y); ctx.lineTo(peer.x, peer.y); ctx.stroke(); }
        }
        const active = Math.sin(frame * .018 + point.phase) > .93;
        ctx.fillStyle = active ? '#f2ff66' : 'rgba(255,255,255,.4)'; ctx.shadowColor = active ? '#f2ff66' : 'transparent'; ctx.shadowBlur = active ? 15 : 0;
        ctx.fillRect(point.x - point.size / 2, point.y - point.size / 2, point.size, point.size); ctx.shadowBlur = 0;
      } else {
        ctx.fillStyle = `rgba(255,255,255,${.04 + ((Math.sin(frame * .01 + point.phase) + 1) / 2) * .14})`; ctx.font = '11px "Space Mono", monospace';
        ctx.fillText(['░','▒','▓','0','1','/','→','+'][index % 8], point.x, point.y);
      }
    });
    if (isHero) { const progress = (frame * .7) % (width + 250) - 125; ctx.strokeStyle = 'rgba(242,255,102,.26)'; ctx.beginPath(); ctx.moveTo(progress, height * .43); ctx.lineTo(progress + 160, height * .43); ctx.stroke(); ctx.fillStyle = '#f2ff66'; ctx.fillRect(progress + 160, height * .43 - 4, 8, 8); }
    frame += 1; if (!reducedMotion) raf = requestAnimationFrame(draw);
  };
  resize(); draw(); window.addEventListener('resize', resize);
  if (reducedMotion) ctx.clearRect(0, 0, width, height);
  return () => { cancelAnimationFrame(raf); window.removeEventListener('resize', resize); };
};

makeCanvas(document.querySelector('#hero-canvas'), true);
makeCanvas(document.querySelector('#stream-canvas'), false);

const glyphRain = document.querySelector('#glyph-rain');
if (glyphRain) { const chars = ['░','▒','▓','0','1','/','\\','→','←','+','▌','▐']; glyphRain.textContent = Array.from({ length: 2200 }, (_, index) => index % 84 === 0 ? '\n' : chars[Math.floor(Math.random() * chars.length)]).join(' '); }

const eventCopy = document.querySelector('#event-copy'); const statusCopy = document.querySelector('.status-copy');
const events = [['worktree.create → ready', '"CLAIMED"'], ['agent.delegate → builder', '"RUNNING"'], ['tests.verify → 11 passed', '"VERIFIED"'], ['pull-request.open → #108', '"COMPLETE"']]; let eventIndex = 0;
if (!reducedMotion) setInterval(() => { eventIndex = (eventIndex + 1) % events.length; if (eventCopy) eventCopy.textContent = events[eventIndex][0]; if (statusCopy) statusCopy.textContent = events[eventIndex][1]; }, 1900);

const proofSection = document.querySelector('.workflow-section'); const proofLeft = document.querySelector('.proof-left'); const proofRight = document.querySelector('.proof-right');
const updateProof = () => { if (!proofSection || !proofLeft || !proofRight || reducedMotion) return; const bounds = proofSection.getBoundingClientRect(); const progress = Math.max(0, Math.min(1, (window.innerHeight - bounds.top) / (window.innerHeight + bounds.height))); const offset = (progress - .5) * 150; proofLeft.style.transform = `translateX(${offset - 86}px)`; proofRight.style.transform = `translateX(${-offset + 86}px)`; };
window.addEventListener('scroll', updateProof, { passive: true }); updateProof();
const year = document.querySelector('#year'); if (year) year.textContent = new Date().getFullYear();
