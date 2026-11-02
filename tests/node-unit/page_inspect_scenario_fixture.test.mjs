import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const html = readFileSync(new URL('../../fixtures/page_inspect_scenarios.html', import.meta.url), 'utf8');
const code = html.match(/<script>([\s\S]*?)<\/script>/)[1];
function fixture(overrides = {}) {
  const nodes = new Map();
  const document = {querySelector(id) {
    if (!nodes.has(id)) nodes.set(id, {
      dataset: {turn: '0'}, listeners: {},
      addEventListener(name, handler) { this.listeners[name] = handler; }
    });
    return nodes.get(id);
  }};
  const queried = [];
  const sandbox = {
    document, devicePixelRatio: 2, matchMedia: () => ({matches: true}),
    navigator: {language: 'ja-JP', permissions: {query: async ({name}) => {
      queried.push(name); return {state: 'prompt'};
    }}},
    fetch: async () => ({ok: true}), ...overrides
  };
  vm.runInNewContext(code, sandbox, {timeout: 1000});
  return {nodes, queried, sandbox, click: id => nodes.get(id).listeners.click()};
}

test('workspace selects Node 26 and this test uses a supported runtime', () => {
  assert.equal(readFileSync(new URL('../../.nvmrc', import.meta.url), 'utf8').trim(), '26');
  assert.ok(Number(process.versions.node.split('.')[0]) >= 26);
});

test('notification and location queries do not request location coordinates', async () => {
  const f = fixture();
  await f.click('#scenario-permission');
  assert.deepEqual(f.queried, ['notifications']);
  await f.click('#scenario-geolocation');
  assert.deepEqual(f.queried, ['notifications', 'geolocation']);
  assert.equal(f.nodes.get('#scenario-geolocation-result').dataset.state, 'prompt');
  assert.ok(!code.includes('getCurrentPosition'));
});

test('permission probe records each change with a new generation', async () => {
  let state = 'prompt';
  const f = fixture({navigator: {language: 'en-US', permissions: {query: async () => ({state})}}});
  const result = f.nodes.get('#scenario-permission-result') || f.sandbox.document.querySelector('#scenario-permission-result');
  for (const [index, current] of ['prompt', 'granted', 'prompt'].entries()) {
    state = current;
    await f.click('#scenario-permission');
    assert.equal(result.dataset.state, current);
    assert.equal(result.dataset.completed, String(index + 1));
  }
});

test('unsupported and broken permission APIs are distinct', async () => {
  for (const [name, expected] of [['TypeError', 'unsupported'], ['NotSupportedError', 'unsupported'], ['Error', 'invalid']]) {
    const f = fixture({navigator: {language: 'en-US', permissions: {query: async () => {throw {name};}}}});
    await f.click('#scenario-permission');
    assert.equal(f.nodes.get('#scenario-permission-result').dataset.state, expected);
  }
});

test('unknown permission values are never copied into evidence', async () => {
  const f = fixture({navigator: {language: 'en-US', permissions: {query: async () => ({state: 'SECRET'})}}});
  await f.click('#scenario-permission');
  assert.equal(f.nodes.get('#scenario-permission-result').dataset.state, 'invalid');
});

test('fetch recovery cannot reuse the previous completed generation', async () => {
  let reject;
  const f = fixture();
  await f.click('#scenario-fetch');
  const result = f.nodes.get('#scenario-result');
  assert.equal(result.dataset.completed, '1');
  f.sandbox.fetch = () => new Promise((_, no) => {reject = no;});
  const pending = f.click('#scenario-fetch');
  assert.equal(result.dataset.state, 'pending');
  assert.equal(result.dataset.completed, undefined);
  reject(new Error('private transport message'));
  await pending;
  assert.equal(result.dataset.completed, '2');
  assert.equal(result.dataset.state, 'error');
  f.sandbox.fetch = async () => ({ok: true});
  await f.click('#scenario-fetch');
  assert.equal(result.dataset.completed, '3');
  assert.equal(result.dataset.state, 'ok');
});

test('upload supports selection, rejection and reset without reading contents', () => {
  const f = fixture();
  const handler = f.nodes.get('#scenario-upload').listeners.change;
  for (const [files, expected] of [
    [[{size: 5, type: 'text/plain'}], 'accepted'],
    [[{size: 6, type: 'text/plain'}], 'rejected'], [[], 'cleared']]) {
    handler({target: {files}});
    assert.equal(f.nodes.get('#scenario-upload-result').dataset.state, expected);
  }
});
