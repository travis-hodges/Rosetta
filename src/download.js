// The download page. Two jobs: copy buttons, and rendering whatever releases.json
// says. It is deliberately separate from main.js, which drives the landing page's
// canvas artwork and scroll story and would throw on a page without them.

const status = document.querySelector('#copy-status');
let copyTimer;

function wireCopyButtons(scope) {
  scope.querySelectorAll('[data-copy]').forEach(button => button.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(button.dataset.copy);
      document.querySelectorAll('[data-copy]').forEach(item => { item.textContent = 'Copy'; });
      button.textContent = 'Copied';
      status.textContent = `Copied: ${button.dataset.copy}`;
      clearTimeout(copyTimer);
      copyTimer = setTimeout(() => { button.textContent = 'Copy'; status.textContent = ''; }, 4500);
    } catch {
      status.textContent = 'Clipboard unavailable. Select and copy the command above.';
    }
  }));
}

wireCopyButtons(document);

// The install commands must name the host actually serving this page. Hard-coding a
// domain here is how a download page ends up telling people to curl a host that
// does not resolve.
document.querySelectorAll('[data-origin-command]').forEach(node => {
  const command = node.dataset.originCommand.replace('{origin}', location.origin);
  node.textContent = command;
  const button = node.closest('.command-row')?.querySelector('[data-copy]');
  if (button) button.dataset.copy = command;
});

// This page carries the same header as the landing page, so it needs the same menu
// behaviour. main.js is not loaded here -- it drives artwork this page does not have.
const menuButton = document.querySelector('#menu-toggle');
const nav = document.querySelector('#site-nav');
const setMenu = open => {
  menuButton.setAttribute('aria-expanded', String(open));
  nav.classList.toggle('is-open', open);
  menuButton.firstChild.textContent = open ? 'Close ' : 'Menu ';
};
menuButton.addEventListener('click', () => setMenu(menuButton.getAttribute('aria-expanded') !== 'true'));
nav.querySelectorAll('a').forEach(link => link.addEventListener('click', () => setMenu(false)));
addEventListener('keydown', event => { if (event.key === 'Escape') setMenu(false); });

// ---------------------------------------------------------------- release state

const panel = document.querySelector('#release-panel');

const escapeHtml = value => String(value).replace(/[&<>"']/g, character => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[character]));

function pending(reason) {
  panel.innerHTML = `
    <div class="release-pending">
      <span class="mono"><i class="status-dot"></i> NO RELEASE PUBLISHED YET</span>
      <p>${escapeHtml(reason)}</p>
      <p class="fine-print">Nothing is hand-authored on this page. The version and
      checksum below appear only once a tagged release has been built and published,
      so this page can never show a number that no artifact matches.</p>
    </div>`;
}

function render(release) {
  const command = `curl -fsSL ${location.origin}/install.sh | sh`;
  panel.innerHTML = `
    <div class="release-head">
      <div>
        <span class="mono">CURRENT RELEASE</span>
        <h3>${escapeHtml(release.version)}</h3>
      </div>
      <dl class="release-facts mono">
        <div><dt>TAG</dt><dd>${escapeHtml(release.tag)}</dd></div>
        <div><dt>PUBLISHED</dt><dd>${escapeHtml(release.published || 'unknown')}</dd></div>
        <div><dt>SIZE</dt><dd>${escapeHtml(release.size || 'unknown')}</dd></div>
      </dl>
    </div>
    <div class="command-row">
      <div>
        <span class="mono">INSTALL</span>
        <code>${escapeHtml(command)}</code>
      </div>
      <button type="button" data-copy="${escapeHtml(command)}" aria-label="Copy the install command">Copy</button>
    </div>
    <div class="release-artifact">
      <span class="mono">ARTIFACT</span>
      <a href="${escapeHtml(release.tarball_url)}">${escapeHtml(release.tarball)}</a>
      <span class="mono">SHA-256</span>
      <code class="release-sha">${escapeHtml(release.sha256)}</code>
      <button type="button" data-copy="${escapeHtml(release.sha256)}" aria-label="Copy the SHA-256 checksum">Copy</button>
    </div>`;
  wireCopyButtons(panel);
}

fetch('/releases.json', { cache: 'no-cache' })
  .then(response => response.ok ? response.json() : Promise.reject(new Error(String(response.status))))
  .then(manifest => {
    if (!manifest.latest) {
      pending('No tagged release exists yet. Install from a source checkout using the commands below.');
      return;
    }
    render(manifest.latest);
  })
  .catch(() => pending('The release manifest could not be loaded. Install from a source checkout using the commands below.'));
