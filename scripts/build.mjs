// Copies the static site into dist/. No bundler: the pages are plain HTML, CSS and
// ES modules the browser loads directly, so the build is a copy plus a syntax check.
import { mkdir, readFile, writeFile, cp, rm, readdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { Script } from 'node:vm';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const dist = join(root, 'dist');

// Every top-level .html file is a page. Discovered rather than listed, so adding a
// page cannot silently fail to publish -- and so a stray copy cannot silently start.
const pages = (await readdir(root)).filter(name => name.endsWith('.html')).sort();
if (!pages.includes('index.html')) throw new Error('no index.html to publish');

// Parse-only. A syntax error must fail the build, not the first visitor's browser.
for (const name of await readdir(join(root, 'src'))) {
  if (!name.endsWith('.js')) continue;
  const source = await readFile(join(root, 'src', name), 'utf8');
  new Script(source, { filename: `src/${name}` });
}
for (const name of await readdir(join(root, 'pitch'))) {
  if (!name.endsWith('.js')) continue;
  const source = await readFile(join(root, 'pitch', name), 'utf8');
  new Script(source, { filename: `pitch/${name}` });
}

for (const page of pages) {
  const html = await readFile(join(root, page), 'utf8');
  for (const reference of html.matchAll(/(?:href|src)="(\/[^"#?]+)"/g)) {
    const path = reference[1];
    // Clean URLs: /download is served by download.html, so it is a page, not a file.
    if (pages.includes(`${path.slice(1)}.html`)) continue;
    // public/ flattens onto the site root, so /og.png is public/og.png.
    await readFile(join(root, path))
      .catch(() => readFile(join(root, 'public', path)))
      .catch(() => {
        throw new Error(`${page} references ${path}, which does not exist`);
      });
  }
}

await rm(dist, { recursive: true, force: true });
await mkdir(dist, { recursive: true });
for (const page of pages) {
  await writeFile(join(dist, page), await readFile(join(root, page), 'utf8'));
}
await cp(join(root, 'src'), join(dist, 'src'), { recursive: true });
await cp(join(root, 'pitch'), join(dist, 'pitch'), { recursive: true });
await cp(join(root, 'public'), dist, { recursive: true });

console.log(`Built Rosetta site → dist/ (${pages.join(', ')})`);
