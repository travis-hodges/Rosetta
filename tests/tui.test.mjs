import assert from 'node:assert/strict';
import { mkdir, mkdtemp, readFile, readdir, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';

import { RosettaExperience } from '../.opencode/plugins/rosetta-experience.js';
import RosettaExperienceDefault from '../.opencode/plugins/rosetta-experience.js';
import { phase, pulseFrames } from '../.opencode/lib/rosetta-experience-model.js';
import {
  RECENT_PROOFS,
  STATUS,
  aggregateStatus,
  databaseArtifactStatus,
  digestFile,
  directoryTree,
  inferIdentity,
  newestBy,
  normalizeEvent,
  routineStatus,
  scanSystem,
} from '../.opencode/lib/rosetta-system-model.js';

const wait = (milliseconds) => new Promise(resolve => setTimeout(resolve, milliseconds));

test('execution tools receive an animated proof phase', () => {
  assert.equal(RosettaExperienceDefault, RosettaExperience);
  const verify = phase('rosetta_verify_change');
  assert.equal(verify.label, 'PROVE');
  assert.equal(verify.pulse, true);
  assert.match(verify.message, /output \+ globals/);

  const execute = phase('execute-routine');
  assert.equal(execute.label, 'EXECUTE');
  assert.equal(execute.pulse, true);
  assert.equal(pulseFrames.length, 8);
  assert.equal(new Set(pulseFrames).size, 5);
  assert.equal(phase('read_routine'), null);
});

test('the proof pulse moves while a tool runs and stops on its verdict', async () => {
  const toasts = [];
  const hooks = await RosettaExperience({
    client: {
      tui: {
        async showToast({ body }) {
          toasts.push(body);
        },
      },
    },
  });

  await hooks['tool.execute.before']({
    tool: 'verify_change',
    sessionID: 'session',
    callID: 'proof-call',
  });
  await wait(930);

  assert.ok(toasts.length >= 2, 'the running indicator never advanced');
  assert.notEqual(toasts[0].title, toasts[1].title);
  assert.match(toasts[0].title, /Rosetta · PROVE/);

  await hooks['tool.execute.after'](
    { tool: 'verify_change', sessionID: 'session', callID: 'proof-call', args: {} },
    { title: 'Verification', output: 'NOT EQUIVALENT', metadata: {} },
  );
  const stoppedAt = toasts.length;
  assert.match(toasts.at(-1).title, /DIVERGENCE CAUGHT/);

  await wait(930);
  assert.equal(toasts.length, stoppedAt, 'the proof pulse continued after the verdict');
});

test('the TUI declares the VA system map plugin', async () => {
  const config = JSON.parse(await readFile(new URL('../.opencode/tui.json', import.meta.url), 'utf8'));
  assert.deepEqual(config.plugin, ['./plugins/rosetta-system-map.tsx']);

  const source = await readFile(new URL('../.opencode/plugins/rosetta-system-map.tsx', import.meta.url), 'utf8');
  assert.match(source, /identity\.toUpperCase\(\).*SYSTEM/);
  assert.match(source, /sidebar_content/);
  assert.match(source, /SYSTEM DIRECTORY/);
  assert.match(source, /file\.watcher\.updated/);
  assert.match(source, /edited/);
  assert.match(source, /verified/);

  // `colors()` returns changed/verified. Reading theme names off it -- error,
  // success -- silently drew the two swatches that carry the panel's meaning
  // in no colour at all.
  assert.doesNotMatch(source, /skin\(\)\.(error|success)/);
});

// This is the test that was missing when the experience plugin went dark:
// The harness loads a plugin file by walking every export and demanding a
// function (or an object with a `.server` function), so one exported array
// takes the entire module -- and every hook in it -- down with a TypeError
// that only ever appears in the log.
test('every export of every plugin module is loadable as a plugin', async () => {
  const directory = new URL('../.opencode/plugins/', import.meta.url);
  const files = (await readdir(directory)).filter(name => /\.(js|mjs)$/.test(name));
  assert.ok(files.length > 0, 'no plugin modules found');

  for (const name of files) {
    const module = await import(new URL(name, directory).href);
    for (const [key, value] of Object.entries(module)) {
      const loadable = typeof value === 'function'
        || (value && typeof value === 'object' && typeof value.server === 'function');
      assert.ok(loadable, `${name} exports ${key} as ${typeof value}, which the harness cannot load`);
    }
  }
});

test('routine status is red after edit and green only for its exact proof hash', async () => {
  const project = await mkdtemp(path.join(tmpdir(), 'rosetta-system-'));
  const file = path.join(project, 'ROUTINE.m');
  await writeFile(file, 'ROUTINE\n Q\n');
  const item = { file, routine: 'ROUTINE' };

  assert.equal(routineStatus(item, new Map(), true), STATUS.CHANGED);
  assert.equal(routineStatus(item, new Map([['ROUTINE', {
    equivalent: true,
    candidate_sha256: digestFile(file),
  }]]), true), STATUS.VERIFIED);
  assert.equal(routineStatus(item, new Map([['ROUTINE', {
    equivalent: true,
    candidate_sha256: 'not-the-current-file',
  }]]), true), STATUS.CHANGED);
});

test('system scan exposes the complete routine directory and verified database artifacts', async () => {
  const project = await mkdtemp(path.join(tmpdir(), 'rosetta-scan-'));
  const routines = path.join(project, 'data', 'routines');
  const changes = path.join(project, '.rosetta', 'changes');
  await mkdir(routines, { recursive: true });
  await mkdir(changes, { recursive: true });
  await writeFile(path.join(routines, 'A.m'), 'A\n Q\n');
  await writeFile(path.join(routines, 'B.m'), 'B\n Q\n');
  await writeFile(path.join(changes, 'change.plan.json'), JSON.stringify({
    schema: 'rosetta-db-change-plan/v1',
    change_id: 'chg-one',
  }));
  await writeFile(path.join(changes, 'change.receipt.json'), JSON.stringify({
    schema: 'rosetta-db-change-receipt/v1',
    change_id: 'chg-one',
    verified: true,
    snapshot_id: '/snapshots/chg-one',
  }));

  const view = scanSystem({ project, corpus: project });
  assert.equal(view.identity, 'VA VistA');
  assert.deepEqual(view.routines.map(item => item.routine), ['A', 'B']);
  assert.equal(view.changes.length, 2);
  assert.ok(view.changes.every(item => item.status === STATUS.VERIFIED));
  assert.equal(view.snapshots.length, 1);
  assert.equal(view.status.globals, STATUS.VERIFIED);

  // The tree is the layout: one row per directory, carrying the true total.
  assert.deepEqual(view.tree.map(dir => [dir.label, dir.count, dir.hidden]), [
    ['data/routines/', 2, 2],
  ]);
  assert.equal(view.tree[0].status, STATUS.IDLE);
});

test('the directory tree keeps the changed files and collapses the untouched bulk', () => {
  const routines = [
    { relative: 'A.m', routine: 'A', status: STATUS.IDLE, mtimeMs: 1 },
    { relative: 'B.m', routine: 'B', status: STATUS.CHANGED, mtimeMs: 3 },
    { relative: 'C.m', routine: 'C', status: STATUS.VERIFIED, mtimeMs: 2 },
    { relative: 'nested/D.m', routine: 'D', status: STATUS.IDLE, mtimeMs: 4 },
  ];

  const tree = directoryTree(routines, { root: 'data/routines' });
  assert.deepEqual(tree.map(dir => dir.label), ['data/routines/', 'data/routines/nested/']);

  const [top, nested] = tree;
  assert.equal(top.count, 3);
  // Newest interesting file first; the single untouched file is a count.
  assert.deepEqual(top.children.map(item => item.name), ['B.m', 'C.m']);
  assert.equal(top.hidden, 1);
  assert.equal(top.status, STATUS.CHANGED);

  assert.equal(nested.count, 1);
  assert.deepEqual(nested.children, []);
  assert.equal(nested.hidden, 1);
  assert.equal(nested.status, STATUS.IDLE);
});

test('a 500-routine corpus stays inside the sidebar and the budget is per directory', () => {
  const many = Array.from({ length: 500 }, (unused, index) => ({
    relative: `R${index}.m`,
    routine: `R${index}`,
    status: index < 30 ? STATUS.CHANGED : STATUS.IDLE,
    mtimeMs: index,
  }));

  const [dir] = directoryTree(many, { root: 'data/routines', budget: 8 });
  assert.equal(dir.count, 500);
  assert.equal(dir.children.length, 8);
  // Newest first, and the hidden count still accounts for every file.
  assert.deepEqual(dir.children.map(item => item.name).slice(0, 3), ['R29.m', 'R28.m', 'R27.m']);
  assert.equal(dir.children.length + dir.hidden, 500);

  // One directory row, its shown children, and one "N unchanged" row -- the
  // whole routine section fits well inside a ~20-row pane.
  assert.ok(1 + dir.children.length + 1 <= 12);
});

test('the proofs section shows the newest few and reports the real total', async () => {
  const project = await mkdtemp(path.join(tmpdir(), 'rosetta-proofs-'));
  const proofs = path.join(project, '.rosetta', 'proofs');
  await mkdir(proofs, { recursive: true });
  for (let index = 0; index < RECENT_PROOFS + 4; index += 1) {
    await writeFile(path.join(proofs, `${index}.json`), JSON.stringify({
      schema: 'rosetta-proof/v1',
      routine: `R${index}`,
      equivalent: true,
      created_at: `2026-01-0${1}T00:0${index}:00Z`,
    }));
  }

  const view = scanSystem({ project, corpus: project });
  assert.equal(view.proofs.length, RECENT_PROOFS + 4);
  assert.equal(view.recentProofs.length, RECENT_PROOFS);
  assert.equal(view.recentProofs[0].routine, `R${RECENT_PROOFS + 3}`);
});

test('system map status and events preserve edited-over-verified priority', () => {
  assert.equal(aggregateStatus([STATUS.VERIFIED, STATUS.CHANGED]), STATUS.CHANGED);
  assert.equal(databaseArtifactStatus({ verified: true }), STATUS.VERIFIED);
  assert.equal(databaseArtifactStatus({}), STATUS.CHANGED);
  assert.deepEqual(normalizeEvent({
    type: 'file.watcher.updated',
    properties: { file: 'data/routines/A.m', event: 'add' },
  }), {
    type: 'file.watcher.updated',
    file: 'data/routines/A.m',
    action: 'add',
  });
  assert.equal(inferIdentity({ project: '/tmp/claims', corpus: '/tmp/claims', explicitName: 'CMS-2 Tactical' }), 'CMS-2 Tactical');
  assert.equal(newestBy([
    { routine: 'A', created_at: '2026-01-01' },
    { routine: 'A', created_at: '2026-01-02' },
  ], item => item.routine).get('A').created_at, '2026-01-02');
});
