import {test, expect} from '@playwright/test';
import {readFileSync, mkdirSync, symlinkSync, mkdtempSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {createRequire} from 'node:module';
import {spawnSync} from 'node:child_process';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
let walkSteps, walk, TrailExecutionError;
test.beforeAll(async () => {
  // Native import avoids the CommonJS test runner transforming the ESM asset.
  ({walkSteps, walk, TrailExecutionError} = await new Function('url', 'return import(url)')(
    pathToFileURL(resolve('tools/route_trail_node.mjs')).href));
});

const base = pathToFileURL(resolve('fixtures/route_trail_delayed.html')).href;
function plan(trail = '@home → @hub → @world → @home') {
  const child = spawnSync('python', ['-S', 'tools/route_trail.py', trail, '--registry',
    'fixtures/route_trail_execution.json', '--base', base, '--json'], {encoding:'utf8'});
  expect(child.status, child.stderr).toBe(0);
  return JSON.parse(child.stdout);
}

test('shared plan waits for render and resumes on the same page', async ({page}, testInfo) => {
  const steps = plan('🏠 → @hub → 🌍 → 🏠');
  expect(steps).toEqual(plan());
  const iterator = walkSteps(page, steps, {check: async (page, step) => {
    await Promise.resolve();
    expect(await page.locator('#visits').innerText()).toBe(String(step.index + 1));
    await page.screenshot({path:testInfo.outputPath(`trail-${step.index}.png`),fullPage:true});
  }});
  expect((await iterator.next()).value.index).toBe(0);
  await page.evaluate(() => { (window as any).retained = 42; });
  // Consumer changes cannot weaken the already validated remainder.
  steps[1].ready.text = 'Wrong caller mutation';
  const remaining = [];
  for await (const step of iterator) remaining.push(step);
  expect(remaining).toHaveLength(3);
  for (let index=0; index<4; index++) {
    expect([...readFileSync(testInfo.outputPath(`trail-${index}.png`)).subarray(0,8)])
      .toEqual([137,80,78,71,13,10,26,10]);
  }
  expect(await page.evaluate(() => (window as any).retained)).toBe(42);
  expect(await page.locator('#visits').innerText()).toBe('4');
  const identities = [(await page.locator('#page').innerText()).trim().split(/\s+/)[0],
    (await page.title()).trim().split(/\s+/).at(-1)];
  for (const displayedEmoji of identities) expect(plan(displayedEmoji)[0].url).toBe(page.url());
});

test('failed readiness stops with completed prefix', async ({page}) => {
  const steps = plan(); steps[1].ready.text = 'Never rendered';
  try { await walk(page, steps, {timeoutMs:250}); throw Error('expected failure'); }
  catch (error) {
    expect(error).toBeInstanceOf(TrailExecutionError);
    expect(error.receipt.completed).toBe(1);
    expect(error.receipt.error).toMatchObject({index:1,phase:'ready'});
    expect(page.url()).toBe(steps[1].url);
  }
});

test('invalid later step never starts navigation', async ({page}) => {
  const steps = plan(); steps[2].url = 'javascript:alert(1)';
  await expect(walk(page, steps)).rejects.toThrow();
  expect(page.url()).toBe('about:blank');
});

test('Python CLI executes one Node worker with the same receipt contract', async ({browserName}) => {
  const consumer = mkdtempSync(resolve(tmpdir(), 'btk-consumer-'));
  mkdirSync(resolve(consumer, 'node_modules/@playwright'), {recursive:true});
  // No top-level playwright package: emulate a non-hoisted @playwright/test install.
  symlinkSync(resolve('node_modules/@playwright/test'), resolve(consumer, 'node_modules/@playwright/test'), 'dir');
  const require = createRequire(resolve(consumer, 'package.json'));
  expect(() => require.resolve('playwright')).toThrow();
  let child;
  try { child = spawnSync('python', ['-S', 'tools/route_trail.py', '🏠 ↔ 🌍',
    '--registry', 'fixtures/route_trail_execution.json', '--base', base,
    '--run', '--backend', 'node', '--node-cwd', consumer, '--browser', browserName, '--json'],
    {encoding:'utf8',timeout:30000}); }
  finally { rmSync(consumer, {recursive:true,force:true}); }
  expect(child.status, child.stdout + child.stderr).toBe(0);
  const result = JSON.parse(child.stdout);
  expect(result).toMatchObject({ok:true,completed:3,backend:'node',browser:browserName});
  expect(result.steps.map(s=>s.url)).toEqual(plan('@home ↔ @world').map(s=>s.url));
});
