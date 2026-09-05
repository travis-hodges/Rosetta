import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { Script, runInNewContext } from 'node:vm';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
const html = await readFile(new URL('../web/index.html', import.meta.url), 'utf8');
const source = id => html.match(new RegExp(`<script id="${id}">([\\s\\S]*?)<\\/script>`))[1];
const api = runInNewContext(`${source('rosetta-core')}\nRosettaUI`, {URL});
// Synthetic fixture for validation only. Never shipped as benchmark results.
const report = () => ({schema_version:1, generated_at:'2026-09-05T12:00:00Z', task_count:20, split_hash:'a'.repeat(64), conditions:[{id:'baseline',pass_at_1:0,false_confidence_rate:1},{id:'scaffolded',pass_at_1:1,false_confidence_rate:0}]});

test('inline scripts parse without a bundler', () => {
  for(const id of ['rosetta-core','rosetta-app']) assert.doesNotThrow(()=>new Script(source(id)));
});
test('standalone document has no external runtime dependencies', () => {
  assert.doesNotMatch(html, /<script\b[^>]*\bsrc=/i);
  assert.doesNotMatch(html, /<link\b[^>]*rel="stylesheet"/i);
  assert.doesNotMatch(html, /@import|@font-face|import\s+.*from\s+/);
});
test('DOM identifiers and accessibility relationships resolve', () => {
  const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(x=>x[1]);
  assert.equal(new Set(ids).size,ids.length,'IDs must be unique');
  for(const m of html.matchAll(/(?:aria-controls|aria-labelledby|data-copy|for)="([^"]+)"/g)) {
    for(const id of m[1].split(' ')) assert.ok(ids.includes(id),`Missing target ${id}`);
  }
  assert.match(html,/prefers-reduced-motion:reduce/);
  assert.match(html,/role="status"/);
});
test('all internal routes resolve to actual views or sections', () => {
  for(const [,target] of html.matchAll(/href="#\/([^"]*)"/g)) {
    const route=api.route('#/'+target);
    assert.ok(['home','account','download'].includes(route.view));
    if(target && !['account','download'].includes(target)) assert.ok(route.section,`Unknown route ${target}`);
    if(route.section) assert.ok(html.includes(`id="${route.section}"`));
  }
  assert.equal(api.route('#/account').view,'account');
  assert.equal(api.route('#/download').view,'download');
  assert.equal(api.route('#/unknown').view,'home');
});
test('auth and release URLs require HTTPS and reject credentials', () => {
  for(const value of [null,'','javascript:alert(1)','data:text/html,x','http://example.com','https://user:pass@example.com','//example.com']) assert.equal(api.httpsURL(value),null);
  assert.equal(api.httpsURL('https://example.com/login'),'https://example.com/login');
});
test('unconfigured downloads stay unavailable', () => {
  for(const product of ['cli','gui']) for(const platform of ['mac-arm','mac-intel','linux','windows']) assert.equal(api.releaseFor({},product,platform),null);
});
test('configured release is platform-specific and needs a version', () => {
  const config={downloads:{cli:{'mac-arm':{url:'https://example.com/rosetta.zip',version:'0.1.0'}}}};
  assert.equal(api.releaseFor(config,'cli','mac-arm').version,'0.1.0');
  assert.equal(api.releaseFor(config,'gui','mac-arm'),null);
  assert.equal(api.releaseFor(config,'cli','linux'),null);
  config.downloads.cli['mac-arm'].version='';
  assert.equal(api.releaseFor(config,'cli','mac-arm'),null);
});
test('site config accepts only explicit HTTPS integrations', () => {
  const config=JSON.parse(html.match(/<script id="rosetta-config" type="application\/json">([\s\S]*?)<\/script>/)[1]);
  if(config.authUrl !== null) assert.ok(api.httpsURL(config.authUrl));
  for(const [product,platforms] of Object.entries(config.downloads)) {
    for(const [platform,release] of Object.entries(platforms)) if(release !== null) assert.ok(api.releaseFor(config,product,platform));
  }
});
test('measured report accepts zero and one without truthiness errors', () => {
  const parsed=api.validateReport(report());
  assert.equal(parsed.conditions[0].pass_at_1,0);
  assert.equal(parsed.conditions[1].pass_at_1,1);
});
test('rejects non-finite, string, percentage, and out-of-range rates', () => {
  for(const value of [NaN,Infinity,-.1,1.1,31,'0.3',null]) {
    const candidate=report();candidate.conditions[0].pass_at_1=value;
    assert.throws(()=>api.validateReport(candidate),/finite numbers/);
  }
});
test('rejects duplicate or missing comparison conditions', () => {
  const candidate=report();candidate.conditions[1].id='baseline';
  assert.throws(()=>api.validateReport(candidate),/duplicated/);
  candidate.conditions=[candidate.conditions[0]];
  assert.throws(()=>api.validateReport(candidate),/two or three/);
});
test('requires provenance fields and a valid split hash', () => {
  for(const [key,value] of [['schema_version',2],['task_count',0],['split_hash','missing'],['generated_at','bad date']]) {
    const candidate=report();candidate[key]=value;
    assert.throws(()=>api.validateReport(candidate));
  }
});
test('never renders report strings as HTML', () => {
  assert.doesNotMatch(source('rosetta-app'),/innerHTML|insertAdjacentHTML|document\.write/);
});
test('build serves the standalone artifact and missing reports return 404', async t => {
  const build=spawn(process.execPath,['scripts/build.mjs'],{cwd:new URL('..',import.meta.url),stdio:'pipe'});
  const [code]=await once(build,'exit');assert.equal(code,0);
  const built=await readFile(new URL('../dist/index.html',import.meta.url),'utf8');assert.equal(built,html);
  const server=spawn(process.execPath,['scripts/serve.mjs','--dist'],{cwd:new URL('..',import.meta.url),env:{...process.env,PORT:'0'},stdio:'pipe'});
  t.after(()=>server.kill());
  const ready=await new Promise((resolve,reject)=>{
    let output='';const timeout=setTimeout(()=>reject(new Error('Server startup timed out')),5000);
    server.stdout.on('data',chunk=>{output+=chunk;const match=output.match(/http:\/\/127\.0\.0\.1:(\d+)/);if(match){clearTimeout(timeout);resolve(match[0]);}});
    server.on('error',error=>{clearTimeout(timeout);reject(error);});
  });
  const response=await fetch(ready);assert.equal(response.status,200);assert.equal(await response.text(),html);
  assert.equal(response.headers.get('x-content-type-options'),'nosniff');
  assert.equal((await fetch(ready+'/private.txt')).status,404);
  let published;
  try {published=await readFile(new URL('../results/summary.json',import.meta.url),'utf8');} catch(error) {if(error.code!=='ENOENT')throw error;}
  const result=await fetch(ready+'/results/summary.json');
  assert.equal(result.status,published===undefined?404:200);
  if(published!==undefined)assert.equal(await result.text(),published);
});
