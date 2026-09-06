import assert from 'node:assert/strict';
import { mkdir, mkdtemp, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';

import {
  RosettaExperience,
  phase,
  pulseFrames,
} from '../.opencode/plugins/rosetta-experience.js';
import RosettaExperienceDefault from '../.opencode/plugins/rosetta-experience.js';
import {
  STATUS,
  aggregateStatus,
  databaseArtifactStatus,
  digestFile,
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
