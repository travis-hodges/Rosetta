import { mkdir, readFile, writeFile, copyFile, rm } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { Script } from 'node:vm';
const root = dirname(dirname(fileURLToPath(import.meta.url)));
const html = await readFile(join(root, 'web/index.html'), 'utf8');
for (const id of ['rosetta-core', 'rosetta-app']) {
  const script = html.match(new RegExp(`<script id="${id}">([\\s\\S]*?)<\\/script>`));
  if (!script) throw new Error(`Missing ${id}`);
  new Script(script[1], { filename: id });
}
await mkdir(join(root, 'dist'), { recursive: true });
await writeFile(join(root, 'dist/index.html'), html);
// A report is optional. Never manufacture benchmark data to fill an empty state.
try {
  await readFile(join(root, 'results/summary.json'));
  await mkdir(join(root, 'dist/results'), { recursive: true });
  await copyFile(join(root, 'results/summary.json'), join(root, 'dist/results/summary.json'));
} catch (error) { if (error.code !== 'ENOENT') throw error; await rm(join(root, 'dist/results/summary.json'), { force: true }); }
console.log('Built standalone Rosetta website → dist/index.html');
