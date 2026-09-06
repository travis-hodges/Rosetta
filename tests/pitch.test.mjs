import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { Script } from 'node:vm';

const root = new URL('../', import.meta.url);
const html = await readFile(new URL('pitch/index.html', root), 'utf8');
const script = await readFile(new URL('pitch/pitch.js', root), 'utf8');
const css = await readFile(new URL('pitch/pitch.css', root), 'utf8');

test('pitch prototype parses and stays dependency free', () => {
  assert.doesNotThrow(() => new Script(script, { filename: 'pitch/pitch.js' }));
  assert.doesNotMatch(script, /^\s*import\s/m);
  assert.doesNotMatch(html, /https?:\/\//);
});

test('pitch has eight uniquely identified slides', () => {
  const ids = [...html.matchAll(/<section\b[^>]*\bid="(slide-\d{2})"/g)].map(match => match[1]);
  assert.equal(ids.length, 8);
  assert.equal(new Set(ids).size, 8);
  ids.forEach((id, index) => assert.equal(id, `slide-${String(index + 1).padStart(2, '0')}`));
});

test('pitch includes navigation, overview, replay and motion controls', () => {
  assert.match(script, /ArrowRight/);
  assert.match(script, /setOverview/);
  assert.match(script, /restartScene/);
  assert.match(html, /id="motion-button"/);
  assert.match(html, /aria-live="polite"/);
});

test('motion has a reduced-motion path and no fabricated percentages', () => {
  assert.match(css, /prefers-reduced-motion:\s*reduce/);
  const visibleCopy = html.replace(/<[^>]+>/g, ' ');
  assert.doesNotMatch(visibleCopy, /\b\d{1,3}(?:\.\d+)?\s*%/);
  assert.match(html, /no sample claims/i);
});
