import {test} from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {mkdtempSync, writeFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import * as hp from '../../tools/hit_probe.mjs';

const python = process.env.PYTHON || 'python';
export function oracle(cases) {
  const result = spawnSync(python, ['-S', 'tests/helpers/hit_probe_oracle.py'], {
    input: JSON.stringify(cases), encoding: 'utf8', maxBuffer: 32 * 1024 * 1024,
  });
  assert.equal(result.status, 0, result.stderr || result.error?.message);
  return JSON.parse(result.stdout);
}
async function evaluate(c) {
  const batches = [];
  const probe = async points => { batches.push(points); return points.map(([x, y]) => c.scores[y][x]); };
  const r = await hp.refine(c.width, c.height, probe, c.options), rank = c.rank ?? 3;
  const fact = hp.factorize(r.rows, {rank, scores: r.scores});
  return {evidence: hp.to_json(r, {rank}), rows: r.rows.map(String),
    factors: fact.factors.map(f => ({...f, rows: String(f.rows), cols: String(f.cols)})),
    uncertain: r.uncertain, pairs: hp.boundary_pairs(r.rows, c.width, {limit: c.limit ?? 8}),
    exhaustive: (await hp.exhaustive(c.width, c.height, probe, {threshold: c.options?.threshold ?? 0.5})).map(String), batches};
}
const make = (width, height, score, options = {}, rank = 3) => ({width, height,
  scores: Array.from({length: height}, (_, y) => Array.from({length: width}, (_, x) => score(x, y))), options, rank});

test('Python/Node parity: exact maps, wide masks, ties, thresholds, budgets, batches and deterministic order', async () => {
  let seed = 71;
  const random = () => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed / 2 ** 32; };
  const cases = [
    make(1, 1, () => 1), make(3, 1, x => +(x === 1), {stride: 1}), make(1, 75, (_, y) => +(y >= 31 && y < 69)),
    make(80, 4, x => +(x >= 31 && x < 72), {stride: 1}), make(64, 48, () => 0), make(64, 48, () => 1),
    make(64, 48, (x, y) => +(x >= 10 && x <= 40 && y >= 8 && y <= 30)),
    make(64, 48, (x, y) => +((x - 32) ** 2 + (y - 24) ** 2 <= 225)),
    make(64, 48, (x, y) => +(x >= 10 && x <= 50 && y >= 8 && y <= 40 && !(x >= 30 && y <= 25))),
    make(64, 8, x => Math.min(1, Math.max(0, (20.5 - x) / 2 + 0.5)), {margin: 0.25}),
    make(20, 8, () => 0.5, {margin: 0.01}),
    make(4, 4, (x, y) => +((x + y) % 2 === 0), {stride: 1}, 0),
    make(1, 1, () => 1, {max_probes: 1}),
  ];
  for (const score of [0.0000005, 0.0000015, 0.5000005, 0.5000015, 0.9999995])
    cases.push(make(1, 1, () => score, {threshold: 0}));
  for (let n = 0; n < 35; n++) {
    const a = random() * 3.14, cx = 20 + random() * 24, cy = 15 + random() * 18, hw = 8 + random() * 12, hh = 5 + random() * 7;
    cases.push(make(64, 48, (x, y) => +(Math.abs((x - cx) * Math.cos(a) + (y - cy) * Math.sin(a)) <= hw &&
      Math.abs(-(x - cx) * Math.sin(a) + (y - cy) * Math.cos(a)) <= hh)));
  }
  // Historical corner that agreeing block corners alone missed.
  cases.push(make(64, 48, (x, y) => +(Math.abs((x - 25.5) * Math.cos(2.27) + (y - 32) * Math.sin(2.27)) <= 18.8 &&
    Math.abs(-(x - 25.5) * Math.sin(2.27) + (y - 32) * Math.cos(2.27)) <= 5.2)));
  for (let n = 0; n < 30; n++) cases.push(make(9, 7, () => [0, 0.25, 0.5, 0.75, 1][Math.floor(random() * 5)],
    {stride: 1, threshold: [0, 0.5, 1][n % 3], margin: n % 2 ? 0.25 : 0}, n % 6));
  const expected = oracle(cases);
  for (const [i, c] of cases.entries()) {
    const actual = await evaluate(c);
    assert.deepEqual(actual, expected[i], `case ${i}`);
    assert.deepEqual(await evaluate(c), actual, `determinism ${i}`);
    assert.deepEqual(actual.rows, actual.exhaustive, `exhaustive ${i}`);
  }
});

test('invalid dimensions, options, callback responses, masks and JSON fail promptly', async () => {
  for (const bad of [0, -1, 1.5, true, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1]) {
    for (const dim of [0, 1]) await assert.rejects(hp.refine(dim ? 2 : bad, dim ? bad : 2, p => p.map(() => 1)));
    await assert.rejects(hp.refine(2, 2, () => [], {stride: bad}));
  }
  for (const opts of [{threshold: NaN}, {threshold: 2}, {threshold: '0.5'}, {margin: -1}, {max_probes: -1}, {max_probes: 0}])
    await assert.rejects(hp.refine(2, 2, p => p.map(() => 1), opts));
  for (const probe of [() => [], () => null, p => p.map(() => NaN), p => p.map(() => Infinity), p => p.map(() => '1'),
    p => p.map(() => true), p => p.map(() => -0.1), () => { throw new Error('probe failure'); }]) {
    await assert.rejects(hp.refine(2, 2, probe)); await assert.rejects(hp.exhaustive(2, 2, probe));
  }
  await assert.rejects(hp.refine(64, 48, p => p.map(([x]) => +(x > 20)), {max_probes: 10}), /budget/);
  for (const rows of [[-1n], [1], null]) assert.throws(() => hp.factorize(rows));
  for (const bad of [-1, 1.5, NaN]) assert.throws(() => hp.factorize([1n], {rank: bad}));
  for (const bad of [null, [], ['01', '1'], ['x'], [12]]) assert.throws(() => hp.rows_from_map(bad));
  assert.deepEqual(hp.rows_from_map(['', '']), [[0n, 0n], 0]);
  assert.deepEqual(hp.boundary_pairs([1n], 2, {limit: 0}), []);
});

test('scale uses modern Python compensated sum and imports have no CLI side effects', async () => {
  const values = [0.1859062658947177, 0.9925434121760651, 0.35391282192921714];
  const result = await hp.refine(3, 1, p => p.map(([x]) => values[x]), {stride: 1, threshold: 0});
  assert.equal(hp.to_json(result).factors[0].scale, 0.510788);
  const version = spawnSync(python, ['-c', 'import sys; print(int(sys.version_info >= (3,12)))'], {encoding: 'utf8'});
  assert.equal(version.status, 0, version.stderr);
  if (version.stdout.trim() === '1') assert.deepEqual(await evaluate(make(3, 1, x => values[x], {stride: 1, threshold: 0})),
    oracle([make(3, 1, x => values[x], {stride: 1, threshold: 0})])[0]);
  for (const args of [['--input-type=module', '-'], ['--input-type=module', '-e', 'await import("./tools/hit_probe.mjs")']]) {
    const run = spawnSync(process.execPath, args, {input: 'await import("./tools/hit_probe.mjs")', encoding: 'utf8'});
    assert.equal(run.status, 0, run.stderr); assert.equal(run.stdout, '');
  }
});

test('CLI output and error exits agree with Python', () => {
  const dir = mkdtempSync(join(tmpdir(), 'hit-probe-')), path = join(dir, 'map.json');
  const run = args => spawnSync(process.execPath, ['tools/hit_probe.mjs', ...args], {encoding: 'utf8'});
  try {
    for (const data of [{map: ['1100', '1110', '0011']}, ['1'.repeat(80), '0'.repeat(80)], {map: ['', '']}]) {
      writeFileSync(path, JSON.stringify(data));
      for (const rank of [1, 2, 5]) {
        const args = ['factorize', path, '--rank', String(rank)], node = run(args);
        const py = spawnSync(python, ['-S', 'tools/hit_probe.py', ...args], {encoding: 'utf8'});
        assert.equal(node.status, 0, node.stderr); assert.equal(node.stdout, py.stdout);
      }
    }
    for (const body of ['bad json', 'null', '{"map":["1","00"]}', '{"map":["x"]}']) {
      writeFileSync(path, body); const r = run(['factorize', path]); assert.equal(r.status, 2); assert.match(r.stderr, /^error:/m);
    }
    for (const args of [[], ['wrong'], ['factorize', path, '--rank', '0'], ['factorize', path, '--rank', '1.5'], ['factorize', join(dir, 'missing')]])
      assert.equal(run(args).status, 2);
    assert.equal(run(['--help']).status, 0);
  } finally { rmSync(dir, {recursive: true}); }
});
