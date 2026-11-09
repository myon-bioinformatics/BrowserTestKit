import {test, expect} from '@playwright/test';
import {spawnSync} from 'node:child_process';
import {writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';

let hp;
test.beforeAll(async () => {
  hp = await new Function('url', 'return import(url)')(pathToFileURL(resolve('tools/hit_probe.mjs')).href);
});
const fixture = pathToFileURL(resolve('fixtures/hit_probe.html')).href;
const sub = [[0, 0], [1, 0], [0, 1], [1, 1]], cell = 2, pad = 8;
const scoreJS = ([id, points, offsets]) => {
  const target = document.getElementById(id);
  return points.map(([x, y]) => offsets.reduce((hits, [dx, dy]) => {
    const el = document.elementFromPoint(x + dx, y + dy);
    return hits + +(!!el && (el === target || target.contains(el)));
  }, 0) / offsets.length);
};

test('adaptive hit maps match Python and exhaustive observations for plain, rounded, covered and rotated targets', async ({page}, info) => {
  test.setTimeout(90000);
  await page.setViewportSize({width: 480, height: 320});
  await page.goto(fixture);
  const observed = {};
  for (const target of ['plain', 'rounded', 'covered', 'rotated']) {
    const box = await page.locator(`#${target}`).boundingBox();
    const x0 = Math.trunc(box.x - pad), y0 = Math.trunc(box.y - pad);
    const width = Math.floor((box.width + 2 * pad) / cell), height = Math.floor((box.height + 2 * pad) / cell);
    const probe = points => page.evaluate(scoreJS, [target, points.map(([x, y]) => [x0 + x * cell, y0 + y * cell]), sub]);
    const result = await hp.refine(width, height, probe, {stride: 8, margin: 0.25});
    const points = Array.from({length: width * height}, (_, i) => [i % width, Math.floor(i / width)]);
    const values = await probe(points);
    const scores = Array.from({length: height}, (_, y) => values.slice(y * width, (y + 1) * width));
    const py = spawnSync(process.env.PYTHON || 'python', ['-S', 'tests/helpers/hit_probe_oracle.py'], {
      input: JSON.stringify([{width, height, scores, options: {stride: 8, margin: 0.25}}]), encoding: 'utf8', maxBuffer: 16 * 1024 * 1024,
    });
    expect(py.status, py.stderr).toBe(0);
    const oracle = JSON.parse(py.stdout)[0], evidence = hp.to_json(result);
    expect(evidence).toEqual(oracle.evidence);
    expect(result.rows.map(String)).toEqual(oracle.exhaustive);
    expect(result.probes).toBeLessThan(result.cells * 0.5);
    const clicks = [];
    for (const pair of hp.boundary_pairs(result.rows, width, {limit: 6})) {
      const centres = pair.map(([x, y]) => [x0 + x * cell + 1, y0 + y * cell + 1]);
      if (JSON.stringify(await page.evaluate(scoreJS, [target, centres, [[0, 0]]])) !== '[1,0]') continue;
      for (const [index, [x, y]] of centres.entries()) {
        const before = Number(await page.locator(`#${target}`).getAttribute('data-clicks'));
        await page.mouse.click(x, y);
        const clicked = Number(await page.locator(`#${target}`).getAttribute('data-clicks')) - before;
        clicks.push({x, y, predicted: index === 0 ? 1 : 0, clicked});
      }
    }
    observed[target] = {evidence, clicks};
    expect(clicks.length).toBeGreaterThan(0);
    expect(clicks.every(c => c.predicted === c.clicked)).toBe(true);
  }
  expect(observed.covered.evidence.ones).toBeLessThan(observed.plain.evidence.ones);
  expect(observed.covered.evidence.map[pad / cell + 2][pad / cell + 2]).toBe('1');
  const rank1 = target => hp.factorize(hp.rows_from_map(observed[target].evidence.map)[0], {rank: 1}).residual;
  expect(rank1('plain')).toBe(0);
  expect(rank1('rounded')).toBeGreaterThan(0);
  expect(rank1('rotated')).toBeGreaterThan(0);
  writeFileSync(info.outputPath('hit-probe.json'), JSON.stringify(observed, null, 1));
});

test('integer hit edges agree with real clicks; fractional differences are recorded', async ({page}, info) => {
  await page.setViewportSize({width: 480, height: 320}); await page.goto(fixture);
  const box = await page.locator('#plain').boundingBox(), edge = Math.trunc(box.x), y = Math.trunc(box.y + box.height / 2);
  const observed = [];
  for (const x of [edge - 1, edge - 0.75, edge - 0.5, edge - 0.25, edge]) {
    const hit = (await page.evaluate(scoreJS, ['plain', [[x, y]], [[0, 0]]]))[0];
    const before = Number(await page.locator('#plain').getAttribute('data-clicks'));
    await page.mouse.click(x, y);
    observed.push({x, hit, clicked: Number(await page.locator('#plain').getAttribute('data-clicks')) - before});
  }
  writeFileSync(info.outputPath('fractional-edge.json'), JSON.stringify(observed, null, 1));
  expect(observed.filter(r => Number.isInteger(r.x)).map(r => [r.hit, r.clicked])).toEqual([[0, 0], [1, 1]]);
});
