// Copies the static site into dist/. No bundler: the page is plain HTML, CSS and an
// ES module the browser loads directly, so the build is a copy plus a syntax check.
import { mkdir, readFile, writeFile, cp, rm } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { Script } from 'node:vm';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const dist = join(root, 'dist');

const html = await readFile(join(root, 'index.html'), 'utf8');
const script = await readFile(join(root, 'src/main.js'), 'utf8');
// Parse-only. A syntax error must fail the build, not the first visitor's browser.
new Script(script, { filename: 'src/main.js' });

for (const reference of html.matchAll(/(?:href|src)="(\/[^"]+)"/g)) {
  await readFile(join(root, reference[1])).catch(() => {
    throw new Error(`index.html references ${reference[1]}, which does not exist`);
  });
}

await rm(dist, { recursive: true, force: true });
await mkdir(dist, { recursive: true });
await writeFile(join(dist, 'index.html'), html);
await cp(join(root, 'src'), join(dist, 'src'), { recursive: true });
// public/ flattens onto the site root, so public/og.png is served at /og.png.
await cp(join(root, 'public'), dist, { recursive: true });

console.log('Built Rosetta landing page → dist/index.html');
