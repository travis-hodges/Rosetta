/* Rosetta GUI. No framework, no build step — the page has to open on a
   machine that cannot reach a package index.

   One rule governs every function here: the front end renders what the API
   said and computes nothing. Any number this file derived on its own would be
   a number `rosetta bench report` could not reproduce, and the whole product
   is that every published figure traces back to a real run. */

'use strict';

const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

/* ------------------------------------------------------------------ api */

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  const text = await res.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch { data = { error: text }; }
  if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`);
  return data;
}

/* A job's events accumulate; `onEvent` is called once per new event, in order.
   Polling rather than streaming is deliberate: an edit loop can run for many
   minutes and a dropped connection must not lose the run, which is still
   there on the server when the page asks again. */
async function follow(jobId, onEvent, onPoll) {
  let since = 0;
  for (;;) {
    const job = await api('GET', `/api/job?id=${encodeURIComponent(jobId)}&since=${since}`);
    for (const ev of job.events) onEvent(ev, job);
    since = job.n_events;
    if (onPoll) onPoll(job);
    if (job.state !== 'running') return job;
    await new Promise(r => setTimeout(r, 700));
  }
}

/* ------------------------------------------------------------------ dom */

function el(tag, attrs = {}, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'html') node.innerHTML = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    node.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return node;
}

/* Diff colouring is the one place markup is generated from a payload, so the
   text is escaped by hand rather than trusted. */
function diffBlock(text) {
  const pre = el('pre', { class: 'diff' });
  for (const line of String(text || '').split('\n')) {
    const cls = line.startsWith('+') ? 'add'
      : line.startsWith('-') ? 'del'
      : line.startsWith('@') ? 'at' : '';
    pre.append(el('span', { class: cls }, line + '\n'));
  }
  return pre;
}

function step(cls, headKids, bodyKids) {
  return el('section', { class: `step ${cls || ''}` },
    el('header', { class: 'step-head' }, headKids),
    el('div', { class: 'step-body' }, bodyKids));
}

function divergenceList(divs) {
  if (!divs || !divs.length) return null;
  return el('ul', { class: 'divs' }, divs.map(d =>
    el('li', {}, el('details', {},
      el('summary', {}, el('b', {}, `case ${d.case_index}`), d.headline),
      el('div', { class: 'detail' },
        (d.details || []).map(x => el('span', {}, x)),
        d.ref ? el('span', {}, `ref: ${d.ref}`) : null,
        d.fileman_file ? el('span', {}, `file: ${d.fileman_file}`) : null)))));
}

function proofReceipt(receipt) {
  if (!receipt) return null;
  const live = !!receipt.live;
  const isolation = receipt.isolation || {};
  const digest = String(receipt.receipt_sha256 || 'unavailable');
  const artifact = receipt.artifact || receipt.artifact_error || 'session evidence only';
  return el('section', { class: `proof-receipt ${live ? 'live' : 'test'}` },
    el('header', {},
      el('span', { class: 'proof-beacon', 'aria-hidden': 'true' }),
      el('b', {}, 'Proof receipt'),
      el('span', { class: 'proof-provenance' }, receipt.provenance || 'UNKNOWN BACKEND')),
    el('div', { class: 'proof-metrics' },
      el('div', {}, el('small', {}, 'Runtime'), el('strong', {}, receipt.runtime || 'unknown')),
      el('div', {}, el('small', {}, 'Cases'), el('strong', {}, String(receipt.n_cases ?? '—'))),
      el('div', {}, el('small', {}, 'Diverged'), el('strong', {}, String(receipt.n_diverged ?? '—'))),
      el('div', {}, el('small', {}, 'State'), el('strong', {}, isolation.restored ? 'rolled back' : 'not asserted'))),
    el('div', { class: 'proof-chain' },
      (receipt.observables_checked || []).map((name, i) =>
        el('span', {}, el('i', {}, String(i + 1).padStart(2, '0')), name))),
    el('footer', {},
      el('span', {}, `sha256 ${digest.slice(0, 20)}…`),
      el('span', { title: artifact }, artifact)));
}

function proofStage(stage, detail) {
  return step('proof-stage active', [
    el('span', { class: 'proof-beacon', 'aria-hidden': 'true' }),
    el('span', { class: 'tag' }, stage),
    el('span', { class: 'spacer' }),
    el('span', {}, 'live verifier'),
  ], [el('p', { class: 'verdict' }, detail)]);
}

function verdictStep(v, extraHead) {
  const good = !!v.equivalent;
  return step(good ? 'good' : 'bad', [
    el('span', { class: 'tag' }, good ? 'Verifier: equivalent' : 'Verifier: divergence'),
    el('span', { class: 'spacer' }),
    el('span', {}, `${v.n_cases} cases · ${v.n_diverged} diverged${v.n_void ? ` · ${v.n_void} void` : ''}`),
    extraHead || null,
  ], [
    el('p', { class: `verdict ${good ? 'good' : 'bad'}` }, v.verdict),
    divergenceList(v.divergences),
    proofReceipt(v.proof_receipt),
  ]);
}

/* A tools-on agent calls `verify_change` itself, and each call runs the whole
   case suite against the real database, so a single proposal can legitimately
   take ten minutes. A spinner alone is indistinguishable from a hang, so the
   elapsed clock ticks -- the one number this file is allowed to compute,
   because it is about the UI and not about the result. */
function busy(label) {
  const clock = el('span', { class: 'clock' }, '0s');
  const started = Date.now();
  const timer = setInterval(() => {
    if (!clock.isConnected) { clearInterval(timer); return; }
    const s = Math.round((Date.now() - started) / 1000);
    clock.textContent = s < 90 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
  }, 1000);
  return step('', [el('span', { class: 'tag' }, label), el('span', { class: 'spacer' }),
    clock, el('span', { class: 'spin' })], []);
}

/* Keeps exactly one busy step at the end of a timeline while a job runs.
   Getting this wrong is not cosmetic: the longest wait in the whole product is
   the first proposal, and a timeline that shows nothing there looks hung. */
function trailingBusy(container) {
  let node = null;
  let label = null;
  return {
    show(text) {
      if (node && label === text && node.isConnected) return;
      this.clear();
      label = text;
      node = busy(text);
      container.append(node);
    },
    clear() {
      if (node) node.remove();
      node = null;
      label = null;
    },
  };
}

function fail(message) {
  return step('bad', [el('span', { class: 'tag' }, 'Stopped')],
    [el('p', { class: 'verdict bad' }, message)]);
}

/* ----------------------------------------------------------------- home */

async function loadHome() {
  const s = await api('GET', '/api/status');
  const facts = $('#home-facts');
  facts.replaceChildren(
    el('div', {}, el('dt', {}, 'Split lock'),
      s.split ? el('dd', {}, s.split) : el('dd', { class: 'pending' }, 'not written')),
    el('div', {}, el('dt', {}, 'Eval task set'),
      s.n_tasks ? el('dd', {}, `${s.n_tasks} tasks`) : el('dd', { class: 'pending' }, 'not built')),
    el('div', {}, el('dt', {}, 'Benchmark traces'),
      el('dd', {}, String(s.n_traces))),
    el('div', {}, el('dt', {}, 'Registered models'),
      s.models.length ? el('dd', {}, String(s.models.length))
        : el('dd', { class: 'pending' }, 'none yet')),
    el('div', {}, el('dt', {}, 'Published result'),
      s.published ? el('dd', {}, el('b', {}, 'yes'))
        : el('dd', { class: 'pending' }, 'pending — no full run')),
  );
  return s;
}

$('#doctor-btn').addEventListener('click', async (e) => {
  const btn = e.currentTarget, out = $('#doctor-out');
  btn.disabled = true; out.replaceChildren(el('div', {}, el('span', { class: 'spin' }), ''));
  try {
    const job = await api('POST', '/api/doctor');
    out.replaceChildren();
    await follow(job.id, (ev) => {
      if (ev.kind !== 'check') return;
      out.append(el('div', { class: ev.ok ? 'ok' : 'bad' },
        el('b', {}, ev.ok ? 'ok' : '--'), el('span', {}, `${ev.name}: ${ev.detail}`)));
    });
  } catch (err) {
    out.replaceChildren(el('div', { class: 'bad' }, el('b', {}, '--'), el('span', {}, err.message)));
  } finally { btn.disabled = false; }
});

/* -------------------------------------------------------------- routines */

let routinesLoaded = false;
async function loadRoutines(q) {
  const r = await api('GET', `/api/routines${q ? `?q=${encodeURIComponent(q)}` : ''}`);
  $('#routine-list').replaceChildren(...r.names.map(n => el('option', { value: n })));
  routinesLoaded = true;
  return r;
}

/* Reports whether a routine can be verified at all, because "no inputs" is
   the most common reason a fresh checkout cannot verify anything, and finding
   that out after writing a candidate is the wrong time. */
async function describeRoutine(name, noteEl, readoutEl) {
  if (!name) { noteEl.textContent = ''; if (readoutEl) readoutEl.textContent = '—'; return null; }
  try {
    const r = await api('GET', `/api/routine?name=${encodeURIComponent(name)}`);
    noteEl.className = 'note';
    noteEl.textContent = `${r.lines} lines · ${r.cases_origin}`;
    if (readoutEl) readoutEl.textContent = `${r.routine} · ${r.lines} lines · ${r.origin}`;
    return r;
  } catch (err) {
    noteEl.className = 'note bad';
    noteEl.textContent = err.message;
    if (readoutEl) readoutEl.textContent = '—';
    return null;
  }
}

/* ----------------------------------------------------------------- edit */

async function loadModelOptions() {
  const { models } = await api('GET', '/api/models');
  const sel = $('#edit-model');
  sel.replaceChildren(
    el('option', { value: '' }, 'default'),
    ...models.map(m => el('option', { value: m.name }, `${m.name} — ${m.model}`)));
  return models;
}

$('#edit-routine').addEventListener('change', (e) =>
  describeRoutine(e.target.value.trim().toUpperCase(), $('#edit-note')));

/* A run outlives the page. An edit loop can take a quarter of an hour, and a
   reload in the middle of one used to orphan the view while the job carried on
   server-side. The job id is remembered so the timeline can reattach — the
   events are all still there, because a job accumulates them. */
const REMEMBERED = {
  get: (view) => sessionStorage.getItem(`rosetta:job:${view}`),
  set: (view, id) => sessionStorage.setItem(`rosetta:job:${view}`, id),
  clear: (view) => sessionStorage.removeItem(`rosetta:job:${view}`),
};

async function driveEdit(jobId, { reattached = false } = {}) {
  const out = $('#edit-timeline'), btn = $('#edit-run');
  const spinner = trailingBusy(out);
  let routine = $('#edit-routine').value.trim().toUpperCase();
  /* Which wait we are in decides what the label should say, and the two are
     very different: minutes on a model, minutes on a case suite. */
  let phase = 'model';
  btn.disabled = true;
  out.replaceChildren();
  if (reattached) {
    out.append(step('', [el('span', { class: 'tag' }, 'Reattached to a run in progress'),
      el('span', { class: 'spacer' }), el('span', {}, jobId)], []));
  }
  spinner.show('starting the loop');

  try {
    await follow(jobId, (ev) => {
      spinner.clear();
      if (ev.kind === 'attempt' || ev.kind === 'proving') phase = 'verify';
      else if (ev.kind === 'verdict') phase = 'model';

      if (ev.kind === 'source') {
        routine = ev.routine;
        out.append(step('', [el('span', { class: 'tag' }, ev.routine),
          el('span', { class: 'spacer' }), el('span', {}, `${ev.lines} lines · ${ev.origin}`)], []));
      } else if (ev.kind === 'inputs') {
        out.append(step('', [el('span', { class: 'tag' }, 'Inputs'),
          el('span', { class: 'spacer' }), el('span', {}, ev.origin)], []));
      } else if (ev.kind === 'attempt') {
        out.append(step('', [el('span', { class: 'tag' }, `Attempt ${ev.n}`),
          el('span', { class: 'spacer' }), el('span', {}, 'model proposal')],
          [diffBlock(ev.diff), ev.explanation
            ? el('p', { class: 'verdict', style: 'color:var(--muted);margin-top:12px' }, ev.explanation)
            : null]));
      } else if (ev.kind === 'proving') {
        out.append(proofStage('PROVE',
          `${ev.n_cases ?? 'Stored'} case(s) · baseline ↔ candidate · stdout + errors + global state`));
      } else if (ev.kind === 'verdict') {
        /* The pill is the audit trail: a rejected verdict was handed back to
           the model, an accepted one ended the loop. Which attempts the model
           could see is the difference between the two benchmark conditions. */
        out.append(verdictStep(ev, el('span', { class: ev.equivalent ? 'pill blind' : 'pill saw' },
          ev.equivalent ? 'loop ended here' : 'fed back to the model')));
      } else if (ev.kind === 'accepted') {
        out.append(step('good', [el('span', { class: 'tag' }, 'Accepted'),
          el('span', { class: 'spacer' }), el('span', {}, `on attempt ${ev.n}`)],
          [el('p', { class: 'verdict good' },
            'Verified equivalent. Nothing was written to the corpus — applying a change ' +
            'to a system people depend on stays a human decision.'),
          el('div', { class: 'row end', style: 'margin-top:14px' },
            el('button', {
              class: 'ghost tiny',
              onclick: () => download(`${routine}.verified.m`, ev.candidate_src),
            }, 'Download candidate')),
          diffBlock(ev.diff)]));
      } else if (ev.kind === 'exhausted') {
        out.append(step('bad', [el('span', { class: 'tag' }, 'No verified candidate')],
          [el('p', { class: 'verdict bad' }, ev.reason),
          el('p', { style: 'color:var(--muted);margin:10px 0 0' },
            'Nothing was written. The routine on disk is untouched.')]));
      } else if (ev.kind === 'failed') {
        out.append(fail(ev.message));
      }
    }, (job) => {
      if (job.state !== 'running') { spinner.clear(); REMEMBERED.clear('edit'); return; }
      spinner.show(phase === 'verify'
        ? 'verifying — running both versions against the real database'
        : 'waiting on the model — it is calling the verifier itself');
    });
  } catch (err) {
    spinner.clear();
    REMEMBERED.clear('edit');
    out.append(fail(err.message));
  } finally { btn.disabled = false; }
}

$('#edit-run').addEventListener('click', async () => {
  const out = $('#edit-timeline');
  const routine = $('#edit-routine').value.trim().toUpperCase();
  const request = $('#edit-request').value.trim();
  if (!routine || !request) {
    out.replaceChildren(fail('A routine and a change request are both required.'));
    return;
  }
  try {
    const job = await api('POST', '/api/edit', {
      routine, request,
      model: $('#edit-model').value || null,
      attempts: Number($('#edit-attempts').value) || 3,
      timeout_s: Number($('#edit-timeout').value) || 1800,
    });
    REMEMBERED.set('edit', job.id);
    await driveEdit(job.id);
  } catch (err) {
    out.replaceChildren(fail(err.message));
  }
});

function download(filename, text) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/plain' }));
  const a = el('a', { href: url, download: filename });
  document.body.append(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}

/* --------------------------------------------------------------- verify */

$('#verify-routine').addEventListener('change', (e) =>
  describeRoutine(e.target.value.trim().toUpperCase(), $('#verify-note'), $('#verify-baseline')));

$('#verify-load').addEventListener('click', async () => {
  const name = $('#verify-routine').value.trim().toUpperCase();
  const r = await describeRoutine(name, $('#verify-note'), $('#verify-baseline'));
  if (r) $('#verify-candidate').value = r.source;
});

const dropzone = $('#verify-candidate');
['dragover', 'dragenter'].forEach(k => dropzone.addEventListener(k, (e) => {
  e.preventDefault(); dropzone.classList.add('drop');
}));
['dragleave', 'drop'].forEach(k => dropzone.addEventListener(k, () =>
  dropzone.classList.remove('drop')));
dropzone.addEventListener('drop', async (e) => {
  e.preventDefault();
  const file = e.dataTransfer.files[0];
  if (!file) return;
  dropzone.value = await file.text();
  /* A dropped `ORCRC.m` almost always means "verify ORCRC", and guessing the
     routine from the filename saves the one step people forget. */
  const guess = file.name.replace(/\.m$/i, '').toUpperCase();
  if (guess && !$('#verify-routine').value.trim()) {
    $('#verify-routine').value = guess;
    describeRoutine(guess, $('#verify-note'), $('#verify-baseline'));
  }
});

async function driveVerify(jobId, { reattached = false } = {}) {
  const out = $('#verify-out'), btn = $('#verify-run');
  const routine = $('#verify-routine').value.trim().toUpperCase() || 'the routine';
  const spinner = trailingBusy(out);
  btn.disabled = true;
  out.replaceChildren();
  if (reattached) {
    out.append(step('', [el('span', { class: 'tag' }, 'Reattached to a run in progress'),
      el('span', { class: 'spacer' }), el('span', {}, jobId)], []));
  }
  spinner.show('starting');
  let reported = false;
  try {
    await follow(jobId, (ev) => {
      spinner.clear();
      if (ev.kind === 'inputs') {
        out.append(step('', [el('span', { class: 'tag' }, 'Inputs'),
          el('span', { class: 'spacer' }), el('span', {}, ev.origin)], []));
      } else if (ev.kind === 'proof_stage') {
        out.append(proofStage(ev.stage, ev.detail));
      } else if (ev.kind === 'report') {
        $$('.proof-stage.active', out).forEach(node => node.classList.remove('active'));
        out.append(verdictStep(ev));
        reported = true;
      } else if (ev.kind === 'failed') {
        out.append(fail(ev.message));
        reported = true;
      }
    }, (job) => {
      if (job.state !== 'running') { spinner.clear(); REMEMBERED.clear('verify'); return; }
      spinner.show(`running both versions of ${routine} against the real database`);
    });
    if (!reported) out.append(fail('The job finished without producing a report.'));
  } catch (err) {
    spinner.clear();
    REMEMBERED.clear('verify');
    out.append(fail(err.message));
  } finally { btn.disabled = false; }
}

$('#verify-run').addEventListener('click', async () => {
  const out = $('#verify-out');
  const routine = $('#verify-routine').value.trim().toUpperCase();
  const candidate = $('#verify-candidate').value;
  if (!routine || !candidate.trim()) {
    out.replaceChildren(fail('A routine and a complete candidate source are both required.'));
    return;
  }
  try {
    const job = await api('POST', '/api/verify', { routine, candidate_src: candidate });
    REMEMBERED.set('verify', job.id);
    await driveVerify(job.id);
  } catch (err) {
    out.replaceChildren(fail(err.message));
  }
});

/* --------------------------------------------------------------- models */

async function loadModels() {
  const { models } = await api('GET', '/api/models');
  const list = $('#model-list');
  if (!models.length) {
    list.replaceChildren(el('div', { class: 'empty' },
      'No models registered. Rosetta does not host models — register the id your local ' +
      'agent host already reaches, for example ollama/qwen2.5-coder:32b.'));
  } else {
    list.replaceChildren(...models.map(m => el('div', { class: 'r' },
      el('span', { class: 'name' }, m.name),
      el('span', { class: 'id' }, m.model),
      m.notes ? el('span', { class: 'notes' }, m.notes) : null,
      el('button', { class: 'ghost tiny', onclick: () => testModel(m.name) }, 'Test'),
      el('button', {
        class: 'ghost tiny',
        onclick: async () => {
          await api('DELETE', `/api/models?name=${encodeURIComponent(m.name)}`);
          loadModels(); loadModelOptions();
        },
      }, 'Remove'))));
  }
  return models;
}

$('#model-add').addEventListener('click', async () => {
  const note = $('#model-note');
  note.className = 'note'; note.textContent = '';
  try {
    await api('POST', '/api/models', {
      name: $('#model-name').value.trim(),
      model: $('#model-id').value.trim(),
      notes: $('#model-notes').value.trim(),
    });
    $('#model-name').value = ''; $('#model-id').value = ''; $('#model-notes').value = '';
    await loadModels(); await loadModelOptions();
  } catch (err) {
    note.className = 'note bad';
    note.textContent = err.message;
  }
});

async function testModel(name) {
  const out = $('#model-test-out');
  out.replaceChildren();
  const spinner = trailingBusy(out);
  spinner.show(`asking ${name} for one rewrite`);
  try {
    const job = await api('POST', '/api/model-test', { name });
    const frames = [];
    await follow(job.id, (ev) => {
      spinner.clear();
      if (ev.kind === 'reachable') {
        frames.push(step('good', [el('span', { class: 'tag' }, 'Reachable'),
          el('span', { class: 'spacer' }), el('span', {}, ev.model)],
          [el('p', { class: 'verdict good' },
            `${ev.model} answered with a parseable candidate (${ev.lines} lines).`),
          el('p', { style: 'color:var(--muted);margin:10px 0 0' },
            'This proves reachability and output format — not competence. Competence is ' +
            'measured on the held-out eval set, never by a smoke test.')]));
      } else if (ev.kind === 'failed') {
        frames.push(fail(ev.message));
      }
      if (frames.length) out.replaceChildren(...frames);
    }, (job) => {
      if (job.state !== 'running') { spinner.clear(); return; }
      spinner.show(`asking ${name} for one rewrite`);
    });
  } catch (err) {
    spinner.clear();
    out.replaceChildren(fail(err.message));
  }
}

/* ----------------------------------------------------------------- runs */

async function loadRuns() {
  const out = $('#runs-out');
  out.replaceChildren(busy('reading results'));
  const r = await api('GET', '/api/runs');
  const frames = [];

  if (r.published) {
    /* Rendered verbatim. The site's report contract is the same document, and
       a second interpretation of it here is a second chance to be wrong. */
    frames.push(step('good', [el('span', { class: 'tag' }, 'Published'),
      el('span', { class: 'spacer' }), el('span', {}, r.summary_path)],
      [el('pre', { class: 'src' }, JSON.stringify(r.published, null, 2))]));
  } else {
    frames.push(el('div', { class: 'pending' },
      el('strong', {}, 'No result is published yet.'),
      el('p', { style: 'margin:0 0 10px' },
        'This view stays empty rather than showing a placeholder. Every figure it can ' +
        'display is read from results/summary.json, which only a real benchmark run writes.'),
      el('pre', { class: 'cmd' },
        'rosetta bench run --model NAME\nrosetta bench report')));
  }

  const rows = r.traces.map(t => el('tr', {},
    el('td', { class: 'mono' }, t.file),
    el('td', { class: 'mono' }, (t.models || []).join(', ') || '—'),
    el('td', { class: 'mono' }, (t.conditions || []).join(', ') || '—'),
    el('td', { class: 'mono' }, String(t.tasks ?? '—')),
    el('td', { class: 'mono' }, t.error ? `unreadable: ${t.error}` : String(t.n_records))));

  frames.push(step('', [el('span', { class: 'tag' }, 'Provenance'),
    el('span', { class: 'spacer' }),
    el('span', {}, `${r.traces.length} trace file(s)`)],
    [el('p', { style: 'color:var(--muted);margin:0 0 6px' },
      'Every number above is reproducible from these traces. Nothing on this page is ' +
      'computed in the browser.'),
    r.traces.length
      ? el('table', {}, el('thead', {}, el('tr', {},
          el('th', {}, 'File'), el('th', {}, 'Model'), el('th', {}, 'Conditions'),
          el('th', {}, 'Tasks'), el('th', {}, 'Records'))),
        el('tbody', {}, rows))
      : el('p', { style: 'color:var(--muted);margin:0' }, 'No runs recorded yet.')]));

  out.replaceChildren(...frames);
}

/* --------------------------------------------------------------- router */

/* If a run is still going when the view opens -- after a reload, or after
   navigating away and back -- pick it up instead of showing an empty page. */
async function reattach(view, drive) {
  const id = REMEMBERED.get(view);
  if (!id) return;
  try {
    const job = await api('GET', `/api/job?id=${encodeURIComponent(id)}`);
    if (job.state === 'running') await drive(id, { reattached: true });
    else REMEMBERED.clear(view);
  } catch {
    REMEMBERED.clear(view);  // pruned or restarted; nothing to rejoin
  }
}

const VIEWS = {
  home:   async () => { await loadHome(); },
  edit:   async () => {
    await loadModelOptions();
    if (!routinesLoaded) await loadRoutines();
    reattach('edit', driveEdit);
  },
  verify: async () => {
    if (!routinesLoaded) await loadRoutines();
    reattach('verify', driveVerify);
  },
  models: async () => { await loadModels(); },
  runs:   loadRuns,
  train:  async () => {},
};

async function route() {
  const name = (location.hash.replace(/^#\/?/, '') || 'home').split('?')[0];
  const view = VIEWS[name] ? name : 'home';
  $$('.view').forEach(v => v.classList.toggle('on', v.dataset.view === view));
  $$('.nav a').forEach(a => a.classList.toggle('on', a.dataset.view === view));
  document.title = view === 'home' ? 'Rosetta' : `Rosetta — ${view}`;
  window.scrollTo(0, 0);
  try { await VIEWS[view](); } catch (err) {
    console.error(err);
    const stage = $(`.view[data-view="${view}"]`);
    if (stage) stage.append(fail(err.message));
  }
}

window.addEventListener('hashchange', route);
route();
