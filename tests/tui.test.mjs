import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
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
  inferIdentity,
  normalizeEvent,
  defaultExpandedDirectories,
  projectDirectoryTree,
  projectExplorerTree,
  projectFileStatus,
  referenceSummary,
  scanProjectDirectory,
  scanSystem,
  walkProject,
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

test('reference toasts describe only actual tool calls', async () => {
  const toasts = [];
  const hooks = await RosettaExperience({
    client: { tui: { async showToast({ body }) { toasts.push(body); } } },
  });
  await hooks['tool.execute.before'](
    { tool: 'references_reference_search', callID: 'lookup' },
    { args: { query: 'error trapping' } },
  );
  assert.equal(toasts.length, 1);
  assert.equal(toasts[0].title, 'Searching references');
  assert.equal(toasts[0].message, 'error trapping');
  await hooks['tool.execute.before']({ tool: 'bash' }, { args: { command: 'test' } });
  assert.equal(toasts.length, 1, 'unobserved phases must not be invented');
  assert.equal(hooks.tool, undefined, 'no canned success tool in the generic agent');
});

test('the shipped and generic TUI presets both declare the live project map', async () => {
  const config = JSON.parse(await readFile(new URL('../.opencode/tui.json', import.meta.url), 'utf8'));
  assert.deepEqual(config.plugin, ['./plugins/rosetta-system-map.tsx']);
  const generic = JSON.parse(await readFile(new URL('../.opencode/generic/tui.json', import.meta.url), 'utf8'));
  assert.deepEqual(generic.plugin, ['../plugins/rosetta-system-map.tsx']);

  const source = await readFile(new URL('../.opencode/plugins/rosetta-system-map.tsx', import.meta.url), 'utf8');
  assert.match(source, /sidebar_content/);
  assert.match(source, /sidebar_title/);
  assert.match(source, /sidebar_footer/);
  assert.match(source, /home_logo/);
  assert.match(source, /██████╗/);
  assert.match(source, /UNDERSTAND/);
  assert.match(source, /CHANGE/);
  assert.match(source, /PROVE/);
  assert.match(source, /ROSETTA SESSION:/);
  assert.match(source, /ROSETTA_VERSION/);
  assert.match(source, /onMouseDown/);
  assert.match(source, /onKeyDown/);
  assert.doesNotMatch(source, /<scrollbox/);
  assert.doesNotMatch(source, /more directories/);
  assert.match(source, /PROJECT/);
  assert.match(source, /REFERENCES/);
  assert.match(source, /file\.watcher\.updated/);
  assert.match(source, /edited/);
  assert.match(source, /verified/);
  assert.doesNotMatch(source, /MUMPS ROUTINES/);
  assert.doesNotMatch(source, /ROSETTA_CORPUS_DIR/);
  assert.doesNotMatch(source, /setInterval/);
  assert.doesNotMatch(source, /watch\(project/);
  assert.doesNotMatch(source, /ROSETTA_RUNTIME \|\| "YottaDB"/);
  assert.doesNotMatch(source, /ROSETTA_SYSTEM_LANGUAGE \|\| "MUMPS"/);

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

test('system scan treats source files generically and exposes verified database artifacts', async () => {
  const project = await mkdtemp(path.join(tmpdir(), 'rosetta-scan-'));
  const source = path.join(project, 'src');
  const changes = path.join(project, '.rosetta', 'changes');
  await mkdir(source, { recursive: true });
  await mkdir(changes, { recursive: true });
  await writeFile(path.join(source, 'legacy.m'), 'LEGACY\n Q\n');
  await writeFile(path.join(source, 'main.ts'), 'export {}\n');
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

  const view = scanSystem({ project });
  assert.equal(view.identity, path.basename(project));
  assert.ok(view.project.files.some(item => item.relative === 'src/legacy.m'));
  assert.equal(view.routines, undefined, 'the TUI must not infer or inventory a language');
  assert.equal(view.changes.length, 2);
  assert.ok(view.changes.every(item => item.status === STATUS.VERIFIED));
  assert.equal(view.snapshots.length, 1);
  assert.equal(view.status.globals, STATUS.VERIFIED);
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

  const view = scanSystem({ project });
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
  assert.equal(inferIdentity({ project: '/tmp/claims', explicitName: 'CMS-2 Tactical' }), 'CMS-2 Tactical');
});

test('project directory scan walks the working tree and ignores build noise', async () => {
  const project = await mkdtemp(path.join(tmpdir(), 'rosetta-project-'));
  await mkdir(path.join(project, 'src'), { recursive: true });
  await mkdir(path.join(project, 'node_modules'), { recursive: true });
  await mkdir(path.join(project, '.next'), { recursive: true });
  await mkdir(path.join(project, '.git'), { recursive: true });
  await writeFile(path.join(project, 'README.md'), '# hi\n');
  await writeFile(path.join(project, 'src', 'main.js'), 'console.log(1)\n');
  await writeFile(path.join(project, 'node_modules', 'dep.js'), 'x\n');
  await writeFile(path.join(project, '.next', 'bundle.js'), 'x\n');
  await writeFile(path.join(project, '.git', 'config'), 'x\n');

  const view = scanProjectDirectory({ project });
  const names = view.files.map(item => item.relative).sort();
  assert.deepEqual(names, ['README.md', 'src/main.js']);
  assert.equal(view.status, STATUS.IDLE);
  assert.equal(view.tree.length, 2);
});

test('project directory marks edited files as CHANGED and rolls up the tree', async () => {
  const project = await mkdtemp(path.join(tmpdir(), 'rosetta-project-edit-'));
  await mkdir(path.join(project, 'src'), { recursive: true });
  await writeFile(path.join(project, 'README.md'), '# hi\n');
  await writeFile(path.join(project, 'src', 'main.js'), 'console.log(1)\n');

  const modified = new Set([path.join(project, 'src', 'main.js')]);
  const view = scanProjectDirectory({ project, modifiedFiles: modified });
  assert.equal(view.status, STATUS.CHANGED);
  const src = view.tree.find(dir => dir.label === 'src/');
  assert.ok(src, 'src/ directory row missing');
  assert.equal(src.status, STATUS.CHANGED);
  assert.deepEqual(src.children.map(item => item.name), ['main.js']);
  assert.equal(src.children[0].status, STATUS.CHANGED);
});

test('projectFileStatus is CHANGED only when modified', () => {
  assert.equal(projectFileStatus({}, false), STATUS.IDLE);
  assert.equal(projectFileStatus({}, true), STATUS.CHANGED);
});

test('scanSystem exposes all project files without language-specific partitioning', async () => {
  const project = await mkdtemp(path.join(tmpdir(), 'rosetta-scan-project-'));
  const routines = path.join(project, 'data', 'routines');
  await mkdir(routines, { recursive: true });
  await writeFile(path.join(routines, 'A.m'), 'A\n Q\n');
  await writeFile(path.join(project, 'README.md'), '# hi\n');

  const view = scanSystem({ project });
  assert.ok(view.project, 'project view missing from scanSystem');
  assert.ok(view.project.files.some(item => item.relative === 'README.md'));
  assert.ok(view.project.files.some(item => item.relative === 'data/routines/A.m'),
    'all source belongs in the generic project tree');
});

test('project directory summaries are globally bounded and put edited files first', () => {
  const files = Array.from({ length: 30 }, (_, index) => ({
    file: `/project/dir-${index}/file-${index}.ts`,
    relative: `dir-${index}/file-${index}.ts`,
    name: `file-${index}.ts`,
    status: index === 29 ? STATUS.CHANGED : STATUS.IDLE,
    change: index === 29 ? 'edited' : '',
  }));
  const view = projectDirectoryTree(files, { directoryBudget: 5, fileBudget: 4, perDirectory: 2 });
  assert.equal(view.tree.length, 5);
  assert.equal(view.hiddenDirectories, 25);
  assert.equal(view.tree.flatMap(directory => directory.children).length, 4);
  assert.equal(view.tree[0].children[0].name, 'file-29.ts');
  assert.equal(view.tree[0].status, STATUS.CHANGED);
});

test('project directory rows prefer working files over licenses and lock files', () => {
  const files = ['project.md', 'vendor-manifest.json', 'LICENSE-YottaDB.txt', 'package-lock.json'].map(name => ({
    file: `/project/references/${name}`,
    relative: `references/${name}`,
    name,
    status: STATUS.IDLE,
    change: '',
  }));
  const [references] = projectDirectoryTree(files, { fileBudget: 4, perDirectory: 4 }).tree;
  assert.deepEqual(references.children.map(item => item.name), ['project.md', 'vendor-manifest.json']);
  assert.equal(references.hidden, 2);
});

test('project explorer is a complete nested tree with changed status rolled up', () => {
  const files = [
    { file: '/project/README.md', relative: 'README.md', name: 'README.md', status: STATUS.IDLE, change: '' },
    { file: '/project/src/app/page.tsx', relative: 'src/app/page.tsx', name: 'page.tsx', status: STATUS.CHANGED, change: 'edited' },
    { file: '/project/src/lib/store.ts', relative: 'src/lib/store.ts', name: 'store.ts', status: STATUS.IDLE, change: '' },
  ];
  const tree = projectExplorerTree(files);
  assert.equal(tree.length, 2);
  const src = tree.find(node => node.key === 'src');
  assert.equal(src.type, 'directory');
  assert.equal(src.count, 2);
  assert.equal(src.status, STATUS.CHANGED);
  const app = src.children.find(node => node.key === 'src/app');
  assert.equal(app.count, 1);
  assert.equal(app.children[0].key, 'src/app/page.tsx');
  assert.deepEqual(defaultExpandedDirectories(files).sort(), ['src', 'src/app']);
});

test('Git-backed directory scans show changes that predate the TUI session', async () => {
  const project = await mkdtemp(path.join(tmpdir(), 'rosetta-git-project-'));
  execFileSync('git', ['init', '-q'], { cwd: project });
  await mkdir(path.join(project, 'src'), { recursive: true });
  await writeFile(path.join(project, 'src', 'main.js'), 'before\n');
  execFileSync('git', ['add', 'src/main.js'], { cwd: project });
  await writeFile(path.join(project, 'src', 'main.js'), 'after\n');
  await writeFile(path.join(project, 'src', 'new.js'), 'new\n');

  const view = scanProjectDirectory({ project });
  assert.equal(view.changed, 2);
  assert.equal(view.files.find(item => item.relative === 'src/main.js').change, 'edited');
  assert.equal(view.files.find(item => item.relative === 'src/new.js').change, 'new');
});

test('reference summary reports only repository-declared sources and commands', async () => {
  const project = await mkdtemp(path.join(tmpdir(), 'rosetta-reference-panel-'));
  await writeFile(path.join(project, 'manual.md'), '# Local manual\n');
  await writeFile(path.join(project, 'rosetta.json'), JSON.stringify({
    version: 1,
    sources: [{ id: 'local', title: 'Local manual', path: 'manual.md', kind: 'internal' }],
    commands: { test: ['npm', 'test'] },
  }));
  const summary = referenceSummary(project);
  assert.deepEqual(summary.sources, [{ id: 'local', title: 'Local manual', kind: 'internal', language: '', available: true }]);
  assert.deepEqual(summary.commands, [{ name: 'test', command: 'npm test' }]);
});

test('reference summary exposes only an active missing-language request', async () => {
  const project = await mkdtemp(path.join(tmpdir(), 'rosetta-reference-needed-'));
  await mkdir(path.join(project, '.git'));
  await mkdir(path.join(project, '.rosetta'));
  await writeFile(path.join(project, '.rosetta', 'reference-requests.json'), JSON.stringify({
    version: 1,
    requests: [{ language: 'JOVIAL', reason: 'task edits a procedure' }],
  }));
  const summary = referenceSummary(project);
  assert.equal(summary.config, '');
  assert.deepEqual(summary.pending, [{ language: 'JOVIAL', reason: 'task edits a procedure' }]);
  assert.equal(summary.error, '');
});
