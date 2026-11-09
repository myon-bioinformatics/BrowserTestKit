/** Python hit_probe.py companion. No runtime dependencies.
 * refine/exhaustive await a sync or async probe(points). Rows are BigInt bitsets;
 * scores is a Map keyed by "x,y". to_json is the portable btk-hit-probe/1 boundary.
 * Same coarse-grid assumption: features thinner than stride can be missed.
 */
import {readFileSync, realpathSync} from 'node:fs';
import {pathToFileURL} from 'node:url';

export const SCHEMA = 'btk-hit-probe/1';
const key = (x, y) => `${x},${y}`;
const bit = x => 1n << BigInt(x);
const count = n => n.toString(2).replaceAll('0', '').length;
function integer(name, n, min = 1) {
  if (!Number.isSafeInteger(n) || n < min) throw new RangeError(`${name} must be a ${min ? 'positive' : 'non-negative'} safe integer`);
}
function unit(name, n) {
  if (typeof n !== 'number' || !Number.isFinite(n) || n < 0 || n > 1) throw new RangeError(`${name} must be in 0..1`);
}
function grid(width, height) {
  integer('width', width); integer('height', height);
  integer('cells', width * height);
}
function masks(rows) {
  if (!Array.isArray(rows) || rows.some(r => typeof r !== 'bigint' || r < 0n)) throw new TypeError('rows must be non-negative BigInt bitsets');
}
async function ask(probe, points) {
  const values = Array.from(await probe(points));
  if (values.length !== points.length) throw new RangeError('probe returned a different number of scores');
  for (const value of values) unit('score', value);
  return values;
}

export async function refine(width, height, probe, {stride = 8, threshold = 0.5, margin = 0, max_probes = 100000} = {}) {
  grid(width, height); integer('stride', stride); integer('max_probes', max_probes, 0);
  unit('threshold', threshold); unit('margin', margin);
  const scores = new Map(), inferred = new Map();
  const lattice = size => {
    const out = [];
    for (let x = 0; x < size; x += stride) out.push(x);
    if (out.at(-1) !== size - 1) out.push(size - 1);
    return out;
  };
  const xs = lattice(width), ys = lattice(height);
  let blocks = [], rounds = 0;
  for (let i = 0; i < Math.max(xs.length - 1, 1); i++)
    for (let j = 0; j < Math.max(ys.length - 1, 1); j++)
      blocks.push([xs[i], xs[i + 1] ?? xs[i], ys[j], ys[j + 1] ?? ys[j]]);
  while (blocks.length) {
    rounds++;
    const points = [], pending = new Set();
    for (const [xa, xb, ya, yb] of blocks) for (const x of [xa, xb]) for (const y of [ya, yb]) {
      const k = key(x, y);
      if (!scores.has(k) && !pending.has(k)) { pending.add(k); points.push([x, y]); }
    }
    if (scores.size + points.length > max_probes) throw new RangeError(`probe budget exceeded: ${max_probes}`);
    if (points.length) {
      const values = await ask(probe, points);
      points.forEach(([x, y], i) => scores.set(key(x, y), values[i]));
    }
    const probed = [...scores].map(([k, s]) => [...k.split(',').map(Number), s >= threshold]);
    const next = [];
    for (const [xa, xb, ya, yb] of blocks) {
      const corners = [xa, xb].flatMap(x => [ya, yb].map(y => scores.get(key(x, y))));
      const labels = new Set(corners.map(c => c >= threshold));
      let unsure = !!margin && corners.some(c => Math.abs(c - threshold) <= margin);
      const label = corners[0] >= threshold, reach = Math.max(xb - xa, yb - ya);
      if (labels.size === 1 && !unsure) unsure = reach >= 4 && probed.some(([x, y, lab]) =>
        lab !== label && xa - reach <= x && x <= xb + reach && ya - reach <= y && y <= yb + reach);
      if (labels.size === 1 && !unsure) {
        for (let x = xa; x <= xb; x++) for (let y = ya; y <= yb; y++) {
          const k = key(x, y); if (!inferred.has(k)) inferred.set(k, label);
        }
        continue;
      }
      if (xb - xa <= 1 && yb - ya <= 1) continue;
      const xm = Math.floor((xa + xb) / 2), ym = Math.floor((ya + yb) / 2);
      for (const [x0, x1] of xb - xa > 1 ? [[xa, xm], [xm, xb]] : [[xa, xb]])
        for (const [y0, y1] of yb - ya > 1 ? [[ya, ym], [ym, yb]] : [[ya, yb]]) next.push([x0, x1, y0, y1]);
    }
    blocks = next;
  }
  const rows = Array.from({length: height}, (_, y) => {
    let row = 0n;
    for (let x = 0; x < width; x++) {
      const k = key(x, y), hit = scores.has(k) ? scores.get(k) >= threshold : inferred.get(k);
      if (hit) row |= bit(x);
    }
    return row;
  });
  const uncertain = [...scores].filter(([, s]) => margin && Math.abs(s - threshold) <= margin)
    .map(([k]) => k.split(',').map(Number)).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  return {schema: SCHEMA, width, height, stride, threshold, margin, cells: width * height,
    probes: scores.size, rounds, rows, scores, uncertain};
}

export async function exhaustive(width, height, probe, {threshold = 0.5} = {}) {
  grid(width, height); unit('threshold', threshold);
  const points = Array.from({length: width * height}, (_, i) => [i % width, Math.floor(i / width)]);
  const values = await ask(probe, points), rows = Array(height).fill(0n);
  points.forEach(([x, y], i) => { if (values[i] >= threshold) rows[y] |= bit(x); });
  return rows;
}

// Python round(value, 6): round the exact binary float to decimal, ties to even.
function round6(value) {
  if (!value) return 0;
  const view = new DataView(new ArrayBuffer(8)); view.setFloat64(0, value);
  const bits = view.getBigUint64(0), exponent = Number((bits >> 52n) & 2047n);
  let numerator = ((bits & ((1n << 52n) - 1n)) | (exponent ? 1n << 52n : 0n)) * 1000000n;
  const shift = (exponent || 1) - 1023 - 52;
  if (shift >= 0) return Number(numerator << BigInt(shift)) / 1e6;
  const denominator = 1n << BigInt(-shift), q = numerator / denominator, r = numerator % denominator;
  return Number(q + (r * 2n > denominator || (r * 2n === denominator && q % 2n) ? 1n : 0n)) / 1e6;
}

// CPython 3.12+ float sum (Neumaier compensated summation), as used by CI.
function sum(values) {
  let hi = 0, lo = 0;
  for (const x of values) {
    const t = hi + x;
    lo += Math.abs(hi) >= Math.abs(x) ? (hi - t) + x : (x - t) + hi;
    hi = t;
  }
  return hi + lo;
}

export function factorize(rows, {rank = 3, scores = null} = {}) {
  masks(rows); integer('rank', rank, 0);
  const residual = [...rows], factors = [];
  for (let i = 0; i < rank && residual.some(Boolean); i++) {
    const candidates = new Set(residual.filter(Boolean));
    for (const a of [...candidates]) for (const b of rows) if (a & b) candidates.add(a & b);
    let best = null;
    for (const cols of candidates) {
      let rowMask = 0n, gain = 0;
      rows.forEach((row, y) => { if ((row & cols) === cols) { rowMask |= bit(y); gain += count(residual[y] & cols); } });
      const area = count(cols) * count(rowMask);
      if (gain && (!best || gain > best.gain || (gain === best.gain && (area > best.area || (area === best.area && cols < best.cols)))))
        best = {gain, area, cols, rowMask};
    }
    if (!best) break;
    const {cols, rowMask} = best, known = [];
    for (let y = 0; y < rows.length; y++) if (rowMask & bit(y)) {
      residual[y] &= ~cols;
      for (let x = 0; bit(x) <= cols; x++) if ((cols & bit(x)) && scores?.has(key(x, y))) {
        const value = scores.get(key(x, y)); unit('score', value); known.push(value);
      }
    }
    factors.push({rows: rowMask, cols, scale: known.length ? round6(sum(known) / known.length) : null});
  }
  return {factors, residual: residual.reduce((n, r) => n + count(r), 0), ones: rows.reduce((n, r) => n + count(r), 0)};
}

export function boundary_pairs(rows, width, {limit = 8} = {}) {
  masks(rows); integer('width', width); integer('limit', limit, 0);
  const pairs = [];
  rows.forEach((row, y) => { for (let x = 0; x < width; x++) if (row & bit(x))
    for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
      const nx = x + dx, ny = y + dy;
      if (0 <= nx && nx < width && 0 <= ny && ny < rows.length && !(rows[ny] & bit(nx))) pairs.push([[x, y], [nx, ny]]);
    }
  });
  return pairs.length <= limit ? pairs : Array.from({length: limit}, (_, i) => pairs[Math.floor(i * (pairs.length / limit))]);
}
export function ranges(mask) {
  masks([mask]); const out = [];
  for (let x = 0; mask >> BigInt(x); x++) if (mask & bit(x)) {
    const start = x; while (mask & bit(x + 1)) x++;
    out.push(start === x ? `${start}` : `${start}-${x}`);
  }
  return out.join(',');
}
export function render(rows, width) {
  masks(rows); integer('width', width, 0);
  return rows.map(row => Array.from({length: width}, (_, x) => row & bit(x) ? '1' : '0').join(''));
}
export function to_json(result, {rank = 3} = {}) {
  const fact = factorize(result.rows, {rank, scores: result.scores});
  return {...Object.fromEntries(['schema', 'width', 'height', 'stride', 'threshold', 'margin', 'cells', 'probes', 'rounds'].map(k => [k, result[k]])),
    map: render(result.rows, result.width), uncertain: (result.uncertain ?? []).slice(0, 50),
    factors: fact.factors.map(f => ({rows: ranges(f.rows), cols: ranges(f.cols), scale: f.scale})), residual: fact.residual, ones: fact.ones};
}
export function rows_from_map(lines) {
  if (!Array.isArray(lines) || !lines.length || lines.some(s => typeof s !== 'string' || /[^01]/.test(s)))
    throw new TypeError("map must be a non-empty list of '0'/'1' strings");
  const width = lines[0].length;
  if (lines.some(s => s.length !== width)) throw new RangeError('map rows must have equal length');
  return [lines.map(s => [...s].reduce((row, c, x) => c === '1' ? row | bit(x) : row, 0n)), width];
}
export function main(argv = process.argv.slice(2)) {
  if (argv.length === 1 && ['--help', '-h'].includes(argv[0])) {
    console.log('usage: hit_probe.mjs factorize PATH [--rank N]'); return 0;
  }
  try {
    if (argv[0] !== 'factorize' || ![2, 4].includes(argv.length) || (argv.length === 4 && (argv[2] !== '--rank' || !/^[+-]?\d+$/.test(argv[3]))))
      throw new Error('usage: hit_probe.mjs factorize PATH [--rank N]');
    const rank = argv.length === 4 ? Number(argv[3]) : 3; integer('--rank', rank);
    const data = JSON.parse(readFileSync(argv[1], 'utf8'));
    const [rows, width] = rows_from_map(Array.isArray(data) ? data : data?.map), fact = factorize(rows, {rank});
    console.log(`cells=${width * rows.length} ones=${fact.ones} factors=${fact.factors.length} residual=${fact.residual}`);
    for (const f of fact.factors) console.log(`rows=${ranges(f.rows)} cols=${ranges(f.cols)}`);
    return 0;
  } catch (error) { console.error(`error: ${error.message}`); return 2; }
}
let entryURL;
try { if (process.argv[1]) entryURL = pathToFileURL(realpathSync(process.argv[1])).href; } catch { /* stdin, eval or a removed caller path */ }
if (import.meta.url === entryURL) process.exitCode = main();
