/**
 * Element annotation overlay, DOM highlight CSS, and TAP element assertions.
 * Twin JavaScript ES Module for tools/element_overlay.py.
 */

export const SCHEMA = "btk-element-overlay/1";

function roundCoordinate(value) {
  // Same binary64 operations as Python, including ties toward +infinity.
  if (Math.abs(value) >= 2 ** 52) return value;
  return Math.floor(value * 100 + 0.5) / 100;
}

function svgNumber(value) {
  if (!Number.isFinite(value)) throw new Error('SVG numbers must be finite');
  if (Number.isInteger(value)) return BigInt(value).toString();
  const [mantissa, exponent] = value.toExponential(16).split('e');
  return mantissa.replace(/0+$/, '').replace(/\.$/, '') + 'e' + Number(exponent);
}

function extractElementList(data) {
  if (Array.isArray(data)) {
    return data.filter(item => item && typeof item === 'object' && !Array.isArray(item));
  }
  if (data && typeof data === 'object') {
    if (Array.isArray(data.elements)) {
      return data.elements.filter(item => item && typeof item === 'object' && !Array.isArray(item));
    }
    if (Array.isArray(data.items)) {
      return data.items.filter(item => item && typeof item === 'object' && !Array.isArray(item));
    }
    if ('x' in data || 'y' in data || 'width' in data || 'w' in data || 'height' in data || 'h' in data || 'selector' in data || 'id' in data) {
      return [data];
    }
  }
  return [];
}

function parseViewport(viewport) {
  if (viewport && typeof viewport === 'object') {
    const w = 'width' in viewport ? viewport.width : viewport.w;
    const h = 'height' in viewport ? viewport.height : viewport.h;
    if (Number.isFinite(w) && Number.isFinite(h) && w > 0 && h > 0) {
      return { width: Number(w), height: Number(h) };
    }
  } else if (typeof viewport === 'string') {
    const match = viewport.replace(/^[ \t\n\r\v\f]+|[ \t\n\r\v\f]+$/g, '').match(/^(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)$/i);
    if (match) {
      const w = Number(match[1]), h = Number(match[2]);
      if (Number.isFinite(w) && Number.isFinite(h) && w > 0 && h > 0) {
        return { width: w, height: h };
      }
    }
  }
  return null;
}

export function annotateElements(elements, options = {}) {
  const opts = options || {};
  const rawList = extractElementList(elements);
  const viewport = parseViewport(opts.viewport);
  const query = String(opts.find || '').trim().toLowerCase();

  const items = [];
  let index = 1;

  for (const raw of rawList) {
    const x = 'x' in raw ? raw.x : raw.left;
    const y = 'y' in raw ? raw.y : raw.top;
    const w = 'width' in raw ? raw.width : raw.w;
    const h = 'height' in raw ? raw.height : raw.h;

    if (![x, y, w, h].every(Number.isFinite)) {
      continue;
    }

    const fx = Number(x);
    const fy = Number(y);
    const fw = Number(w);
    const fh = Number(h);

    if (fw <= 0 || fh <= 0) {
      continue;
    }

    const name = String(raw.name || raw.label || raw.text || raw.innerText || '').trim();
    const role = String(raw.role || '').trim();
    const selector = String(raw.selector || (raw.id ? `#${raw.id}` : '')).trim();

    if (query) {
      const searchable = `${name} ${role} ${selector}`.toLowerCase();
      if (!searchable.includes(query)) {
        continue;
      }
    }

    if (![fx + fw, fy + fh, fx + fw / 2, fy + fh / 2].every(Number.isFinite)) continue;
    const cx = roundCoordinate(fx + fw / 2.0);
    const cy = roundCoordinate(fy + fh / 2.0);

    let clipped = false;
    if (viewport) {
      const { width: vw, height: vh } = viewport;
      if (fx < 0 || fy < 0 || (fx + fw) > vw || (fy + fh) > vh) {
        clipped = true;
      }
    }

    items.push({
      index,
      label: String(index),
      name,
      role,
      selector,
      x: roundCoordinate(fx),
      y: roundCoordinate(fy),
      width: roundCoordinate(fw),
      height: roundCoordinate(fh),
      cx,
      cy,
      clipped,
    });
    index++;
  }

  return {
    schema: SCHEMA,
    count: items.length,
    viewport,
    items,
  };
}

export function buildHighlightCss(selectorsOrElements, color = '#ff0055', outlineWidth = 2) {
  const rawList = Array.isArray(selectorsOrElements) ? selectorsOrElements : [selectorsOrElements];
  const selectors = [];

  for (const item of rawList) {
    if (typeof item === 'string') {
      const s = item.trim();
      if (s) selectors.push(s);
    } else if (item && typeof item === 'object') {
      const sel = item.selector ?? (item.id ? `#${item.id}` : '');
      if (sel) selectors.push(String(sel).trim());
    }
  }

  if (selectors.length === 0) {
    return '';
  }

  const joined = selectors.join(', ');
  const w = typeof outlineWidth === 'number' ? `${outlineWidth}px` : String(outlineWidth);

  return `${joined} {\n` +
    `  outline: ${w} solid ${color} !important;\n` +
    `  outline-offset: 2px !important;\n` +
    `  box-shadow: 0 0 8px ${color} !important;\n` +
    `  transition: outline 0.2s ease-in-out !important;\n` +
    `}`;
}

export function renderSvgOverlay(annotationData, options = {}) {
  const opts = options || {};
  const data = (annotationData && typeof annotationData === 'object' && annotationData.schema)
    ? annotationData
    : annotateElements(annotationData, opts);

  const items = Array.isArray(data.items) ? data.items : [];

  let vw = 800.0;
  let vh = 600.0;

  if (data && data.viewport) {
    vw = Number(data.viewport.width);
    vh = Number(data.viewport.height);
  } else if (items.length > 0) {
    const maxX = Math.max(...items.map(i => i.x + i.width));
    const maxY = Math.max(...items.map(i => i.y + i.height));
    vw = Math.max(800.0, maxX + 20.0);
    vh = Math.max(600.0, maxY + 20.0);
  }

  const width = Number(opts.width ?? vw);
  const height = Number(opts.height ?? vh);
  const color = String(opts.color ?? opts.stroke_color ?? '#ff0055').trim();
  const bgOpacity = Number(opts.fill_opacity ?? 0.15);

  const lines = [
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${svgNumber(width)} ${svgNumber(height)}" width="${svgNumber(width)}" height="${svgNumber(height)}">`,
    `  <style>.btk-badge { font-family: sans-serif; font-size: 12px; font-weight: bold; fill: #ffffff; }</style>`,
  ];

  for (const item of items) {
    const { x, y, width: w, height: h, cx, cy, index: idx } = item;
    const badgeW = Math.max(20.0, 10.0 + String(idx).length * 8.0);
    const badgeX = Math.max(0.0, x);
    const badgeY = y >= 18.0 ? y - 18.0 : y;

    lines.push(`  <rect x="${svgNumber(x)}" y="${svgNumber(y)}" width="${svgNumber(w)}" height="${svgNumber(h)}" fill="${color}" fill-opacity="${svgNumber(bgOpacity)}" stroke="${color}" stroke-width="2" rx="3" />`);
    lines.push(`  <circle cx="${svgNumber(cx)}" cy="${svgNumber(cy)}" r="3" fill="${color}" />`);
    lines.push(`  <rect x="${svgNumber(badgeX)}" y="${svgNumber(badgeY)}" width="${svgNumber(badgeW)}" height="16" fill="${color}" rx="3" />`);
    lines.push(`  <text x="${svgNumber(badgeX + 5)}" y="${svgNumber(badgeY + 12)}" class="btk-badge">${idx}</text>`);
  }

  lines.push('</svg>');
  return lines.join('\n');
}

export function assertElements(elements, conditions = {}) {
  const conds = conditions || {};
  const ann = annotateElements(elements, conds);
  const items = ann.items;

  const minCount = Number(conds.min_count ?? conds.assert_min_count ?? 1);
  const requiredRole = String(conds.assert_role ?? conds.role ?? '').trim().toLowerCase();
  const requiredFind = String(conds.find ?? '').trim().toLowerCase();

  const assertions = [];

  // Check 1: min_count
  const c1Ok = items.length >= minCount;
  assertions.push({
    ok: c1Ok,
    description: `element count (${items.length}) >= min_count (${minCount})`
  });

  // Check 2: required_role if specified
  if (requiredRole) {
    const foundRole = items.some(item => (item.role || '').toLowerCase() === requiredRole);
    assertions.push({
      ok: foundRole,
      description: `at least one element has role '${requiredRole}'`
    });
  }

  // Check 3: search query match if specified
  if (requiredFind) {
    const foundMatch = items.length > 0;
    assertions.push({
      ok: foundMatch,
      description: `found elements matching search query '${requiredFind}'`
    });
  }

  const passed = assertions.filter(a => a.ok).length;
  const total = assertions.length;
  const allOk = (passed === total);

  const tapLines = ['TAP version 13', `1..${total}`];
  assertions.forEach((a, idx) => {
    const status = a.ok ? 'ok' : 'not ok';
    tapLines.push(`${status} ${idx + 1} - ${a.description}`);
  });

  return {
    ok: allOk,
    total,
    passed,
    failed: total - passed,
    count: items.length,
    items,
    tap: tapLines.join('\n') + '\n'
  };
}
