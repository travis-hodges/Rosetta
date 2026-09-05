import './styles.css';

document.documentElement.classList.add('js');

/* --- mobile navigation --- */
const menuButton = document.querySelector('.menu-toggle');
const nav = document.querySelector('#site-nav');

menuButton?.addEventListener('click', () => {
  const isOpen = menuButton.getAttribute('aria-expanded') === 'true';
  menuButton.setAttribute('aria-expanded', String(!isOpen));
  nav?.classList.toggle('is-open', !isOpen);
});

nav?.querySelectorAll('a').forEach((link) => {
  link.addEventListener('click', () => {
    menuButton?.setAttribute('aria-expanded', 'false');
    nav.classList.remove('is-open');
  });
});

/* --- copy buttons on install commands --- */
document.querySelectorAll('.copy').forEach((button) => {
  button.addEventListener('click', async () => {
    const text = button.dataset.copy ?? '';
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const field = document.createElement('textarea');
      field.value = text;
      field.setAttribute('readonly', '');
      field.style.position = 'fixed';
      field.style.opacity = '0';
      document.body.append(field);
      field.select();
      document.execCommand('copy');
      field.remove();
    }
    const original = button.textContent;
    button.textContent = 'Copied';
    button.dataset.copied = 'true';
    setTimeout(() => {
      button.textContent = original;
      delete button.dataset.copied;
    }, 1600);
  });
});

/* --- benchmark results ---
 * Populated only from results/summary.json. Nothing on this page is hand-typed:
 * if no run has been published, the table stays empty and says so. */
const percent = (value) =>
  typeof value === 'number' && Number.isFinite(value)
    ? `${(value <= 1 ? value * 100 : value).toFixed(1)}%`
    : '—';

async function loadResults() {
  const panel = document.querySelector('#results');
  if (!panel) return;

  const status = document.querySelector('#results-status');
  const empty = document.querySelector('#results-empty');
  const table = document.querySelector('#results-table');
  const rows = document.querySelector('#results-rows');

  const setEmpty = (message) => {
    panel.dataset.state = 'empty';
    if (status) status.textContent = 'NO RUN PUBLISHED';
    if (empty) {
      empty.hidden = false;
      if (message) empty.textContent = message;
    }
    if (table) table.hidden = true;
  };

  let summary;
  try {
    const response = await fetch('/results/summary.json', { cache: 'no-store' });
    if (!response.ok) throw new Error(String(response.status));
    summary = await response.json();
  } catch {
    setEmpty();
    return;
  }

  const conditions = Array.isArray(summary?.conditions) ? summary.conditions : [];
  if (conditions.length === 0) {
    setEmpty();
    return;
  }

  rows.replaceChildren(
    ...conditions.map((condition) => {
      const row = document.createElement('tr');
      [
        condition.name ?? '—',
        percent(condition.pass_at_1),
        percent(condition.pass_at_3),
        percent(condition.false_confidence_rate),
      ].forEach((value) => {
        const cell = document.createElement('td');
        cell.textContent = value;
        row.append(cell);
      });
      return row;
    }),
  );

  panel.dataset.state = 'ready';
  if (status) status.textContent = summary.generated_at ? `RUN ${summary.generated_at}` : 'PUBLISHED';
  if (empty) empty.hidden = true;
  if (table) table.hidden = false;
}

loadResults();

/* --- scroll reveals --- */
const observer = new IntersectionObserver(
  (entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add('is-visible');
        observer.unobserve(entry.target);
      }
    });
  },
  { threshold: 0.12 },
);

document.querySelectorAll('.reveal').forEach((element) => observer.observe(element));
document.querySelector('#year').textContent = new Date().getFullYear();
