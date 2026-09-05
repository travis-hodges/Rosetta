import './styles.css';

document.documentElement.classList.add('js');

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

const agentContent = {
  builder: {
    profile: 'BUILDER',
    prompt: 'Implement the requested change, satisfy every acceptance criterion, and verify the complete flow.',
    mode: 'Implementation',
    returns: 'Tested source changes',
    best: 'Features, fixes, refactors',
    route: 'BUILD',
  },
  researcher: {
    profile: 'RESEARCHER',
    prompt: 'Map the system, gather reliable evidence, and return a decision-ready technical recommendation.',
    mode: 'Investigation',
    returns: 'Evidence and architecture',
    best: 'Spikes, design, unknowns',
    route: 'LEARN',
  },
  reviewer: {
    profile: 'REVIEWER',
    prompt: 'Independently test the change, identify regressions, and report risk with precise supporting evidence.',
    mode: 'Verification',
    returns: 'Findings and confidence',
    best: 'QA, security, regressions',
    route: 'CHECK',
  },
};

const tabs = [...document.querySelectorAll('[data-agent]')];
const fields = [...document.querySelectorAll('[data-agent-field]')];

tabs.forEach((tab, tabIndex) => {
  const selectTab = () => {
    tabs.forEach((item) => item.setAttribute('aria-selected', String(item === tab)));
    const selected = agentContent[tab.dataset.agent];
    fields.forEach((field) => {
      field.textContent = selected[field.dataset.agentField];
    });
  };

  tab.addEventListener('click', selectTab);
  tab.addEventListener('keydown', (event) => {
    if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    event.preventDefault();
    const direction = event.key === 'ArrowRight' ? 1 : -1;
    const next = tabs[(tabIndex + direction + tabs.length) % tabs.length];
    next.focus();
    next.click();
  });
});

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
