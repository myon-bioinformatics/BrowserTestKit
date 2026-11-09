import { test } from '@playwright/test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { spawnSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';
let workerBudget, choice;
test.beforeAll(async () => {
  ({ workerBudget, choice } = await new Function('url', 'return import(url)')(
    pathToFileURL(path.resolve('tools/browser_execution.mjs')).href));
});

test('small/container runners stay bounded; explicit diagnostics can serialize', () => {
  const gib = 1024 ** 3;
  assert.equal(workerBudget({ cpus: 1, memory: gib, override: undefined }), 1);
  assert.equal(workerBudget({ cpus: 8, memory: 4 * gib, override: undefined }), 2);
  assert.equal(workerBudget({ cpus: 2, memory: 7 * gib, override: undefined }), 2);
  assert.equal(workerBudget({ cpus: 64, memory: 128 * gib, override: undefined }), 4);
  assert.equal(workerBudget({ cpus: 8, memory: 16 * gib, override: '1' }), 1);
  for (const override of ['0', '-1', 'Infinity', '2.5', '33', ''])
    assert.throws(() => workerBudget({ override }));
});

test('seed/route choices replay across scheduling orders and vary across seeds', () => {
  const plan = (seed, route) => Array.from({ length: 30 }, (_, i) => choice(seed, route, i, 11));
  const a = plan('run-1', '/screen1');
  plan('run-2', '/screen2');
  assert.deepEqual(plan('run-1', '/screen1'), a);
  assert.notDeepEqual(plan('run-2', '/screen1'), a);
  assert.notDeepEqual(plan('run-1', '/screen2'), a);
  assert.throws(() => choice('seed', '/', 0, 0));
});

test('actual Playwright workers overlap, replace a failed worker, and finish remaining tests', () => {
  const dir = mkdtempSync(path.join(tmpdir(), 'btk-worker-check-'));
  const require = createRequire(path.resolve('package.json'));
  const entry = require.resolve('@playwright/test');
  const cli = path.join(path.dirname(entry), 'cli.js');
  const events = path.join(dir, 'events.jsonl');
  try {
    writeFileSync(path.join(dir, 'playwright.config.cjs'), `module.exports = {
      testDir: ${JSON.stringify(dir)}, fullyParallel: false, workers: 2, retries: 0,
      reporter: [['junit', {outputFile: ${JSON.stringify(path.join(dir, 'junit.xml'))}}]],
      outputDir: ${JSON.stringify(path.join(dir, 'results'))},
    };`);
    const header = `const {test,expect}=require(${JSON.stringify(entry)});
      const fs=require('node:fs');
      function event(kind, info) { fs.appendFileSync(${JSON.stringify(events)}, JSON.stringify({
        kind, title:info.title, worker:info.workerIndex, at:Date.now()})+'\\n'); }
      test.beforeEach(async({},info)=>event('start',info));
      test.afterEach(async({},info)=>event('end',info));`;
    writeFileSync(path.join(dir, 'a.spec.cjs'), header + `
      test('intentional failure',async()=>{await new Promise(r=>setTimeout(r,500));expect(1).toBe(2)});
      test('after replacement',async()=>{});`);
    writeFileSync(path.join(dir, 'b.spec.cjs'), header + `
      test('concurrent work',async()=>{await new Promise(r=>setTimeout(r,1200))});
      test('remaining work',async({},info)=>{info.annotations.push({type:'exploration_seed',description:'replay-42'})});`);
    const result = spawnSync(process.execPath, [cli, 'test', '-c', path.join(dir, 'playwright.config.cjs')],
      { encoding: 'utf8', timeout: 30_000, env: { ...process.env, PLAYWRIGHT_HTML_OPEN: 'never' } });
    assert.equal(result.status, 1, result.stderr);
    const rows = readFileSync(events, 'utf8').trim().split('\n').map(JSON.parse);
    const start = title => rows.find(row => row.title === title && row.kind === 'start');
    const end = title => rows.find(row => row.title === title && row.kind === 'end');
    assert.notEqual(start('intentional failure').worker, start('after replacement').worker);
    assert.ok(end('remaining work'));
    assert.ok(Math.max(start('intentional failure').at, start('concurrent work').at)
      < Math.min(end('intentional failure').at, end('concurrent work').at));
    let active = 0, peak = 0;
    for (const row of rows) { active += row.kind === 'start' ? 1 : -1; peak = Math.max(peak, active); }
    assert.equal(peak, 2);
    assert.equal(active, 0);
    const xml = readFileSync(path.join(dir, 'junit.xml'), 'utf8');
    assert.match(xml, /tests="4"/);
    assert.match(xml, /failures="1"/);
    assert.match(xml, /name="exploration_seed" value="replay-42"/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

// A controlled Page adapter verifies scheduling/error ownership without an engine.
// Pointer geometry and native input are exercised by tests/node/browser_execution.spec.ts.
test('bounded exploration preserves receipts, original failures and listener cleanup', async () => {
  const { EventEmitter } = await import('node:events');
  const { explore, ExplorationError } = await new Function('url', 'return import(url)')(
    pathToFileURL(path.resolve('tools/browser_execution.mjs')).href);
  let inputs = 0;
  const page = Object.assign(new EventEmitter(), {
    locator: () => ({ count: async () => 0 }),
    keyboard: { press: async () => { inputs++; } },
    mouse: { wheel: async () => { inputs++; } },
    url: () => 'https://example.test/#/page',
  });
  const result = await explore(page, { route: '👩🏽‍💻', seed: 'test', steps: 3 });
  assert.equal(result.ok, true);
  assert.equal(result.actions.length, 3);
  assert.equal(inputs, 3);
  assert.deepEqual(result.inventories.map(row => [row.fields, row.buttons]), [[0, 0], [0, 0], [0, 0]]);
  assert.equal(page.listenerCount('pageerror'), 0);
  const cause = new Error('application readiness failed');
  await assert.rejects(explore(page, { route: '/', seed: 'test', steps: 3,
    ready: async () => { throw cause; } }), error => {
      assert.ok(error instanceof ExplorationError);
      assert.equal(error.cause, cause);
      assert.equal(error.receipt.ok, false);
      assert.equal(error.receipt.actions.length, 1);
      assert.match(error.receipt.actions[0].error, /application readiness failed/);
      return true;
    });
  assert.equal(page.listenerCount('pageerror'), 0);
  await assert.rejects(explore(page, { route: '/', seed: 'test', steps: 1,
    ready: async () => { page.emit('pageerror', new Error('app crash')); } }), error => {
      assert.deepEqual(error.receipt.errors, ['app crash']);
      return true;
    });
  assert.equal(page.listenerCount('pageerror'), 0);
  await assert.rejects(explore(page, { route: '/', seed: 'test', steps: 21 }), TypeError);
});

test('a candidate that vanishes after enumeration is recorded without a long wait', async () => {
  const { EventEmitter } = await import('node:events');
  const { explore, ExplorationError } = await new Function('url', 'return import(url)')(
    pathToFileURL(path.resolve('tools/browser_execution.mjs')).href);
  const timeout = Object.assign(new Error('candidate is no longer present'), { name: 'TimeoutError' });
  let checks = 0;
  const candidate = { isVisible: async () => true,
    isEditable: async ({ timeout: bound }) => { checks++; assert.equal(bound, 250); throw timeout; },
    isEnabled: async ({ timeout: bound }) => { checks++; assert.equal(bound, 250); throw timeout; } };
  const locator = { count: async () => 1, nth: () => candidate };
  const page = Object.assign(new EventEmitter(), {
    locator: () => locator, getByRole: () => locator,
    keyboard: { press: async () => {} }, mouse: { wheel: async () => {} }, url: () => 'https://example.test/',
  });
  const result = await explore(page, { route: '/', seed: 'gone', steps: 1, buttonName: /Add/ });
  assert.equal(checks, 2);
  assert.equal(result.actions.length, 1);
  assert.ok(['tab', 'escape', 'scroll'].includes(result.actions[0].kind));
  assert.deepEqual(result.inventories[0].checks.map(row => row.outcome),
    ['unavailable_after_inventory', 'unavailable_after_inventory']);
  assert.equal(result.inventories[0].fields, 1);  // different from absent at initial inventory
  candidate.isEditable = async () => { throw new Error('browser closed'); };
  await assert.rejects(explore(page, { route: '/', seed: 'gone', steps: 1 }), error => {
    assert.ok(error instanceof ExplorationError);
    assert.match(error.cause.message, /browser closed/);
    assert.equal(error.receipt.inventories[0].checks[0].outcome, 'error');
    return true;
  });
  assert.equal(page.listenerCount('pageerror'), 0);
});

test('a selected action timeout remains a failure instead of being reclassified as absent', async () => {
  const { EventEmitter } = await import('node:events');
  const { explore } = await new Function('url', 'return import(url)')(
    pathToFileURL(path.resolve('tools/browser_execution.mjs')).href);
  const cause = Object.assign(new Error('selected input disappeared'), { name: 'TimeoutError' });
  const item = { isVisible: async () => true, isEditable: async () => true,
    getAttribute: async () => { throw cause; } };
  const page = Object.assign(new EventEmitter(), {
    locator: () => ({ count: async () => 1, nth: () => item }),
    url: () => 'https://example.test/',
  });
  let seed = 0;
  while (choice(String(seed), '/', 0, 4) !== 3) seed++;
  await assert.rejects(explore(page, { route: '/', seed: String(seed), steps: 1 }), error => {
    assert.equal(error.cause, cause);
    assert.equal(error.receipt.inventories[0].checks[0].outcome, 'eligible');
    assert.equal(error.receipt.actions[0].kind, 'fill');
    assert.match(error.receipt.actions[0].error, /selected input disappeared/);
    return true;
  });
});

test('repeated availability races cannot exhaust the whole page test budget', async () => {
  const { EventEmitter } = await import('node:events');
  const { explore } = await new Function('url', 'return import(url)')(
    pathToFileURL(path.resolve('tools/browser_execution.mjs')).href);
  const item = { isVisible: async () => true, isEditable: async () => {
    await new Promise(resolve => setTimeout(resolve, 260));
    throw Object.assign(new Error('gone'), { name: 'TimeoutError' });
  } };
  const page = Object.assign(new EventEmitter(), {
    locator: () => ({ count: async () => 20, nth: () => item }),
    url: () => 'https://example.test/', keyboard: { press: async () => {} }, mouse: { wheel: async () => {} },
  });
  const result = await explore(page, { route: '/', seed: 'bounded', steps: 1 });
  assert.equal(result.inventories[0].budget_exhausted, true);
  assert.ok(result.inventories[0].checks.length > 0 && result.inventories[0].checks.length <= 4);
  assert.equal(result.actions.length, 1);
});
