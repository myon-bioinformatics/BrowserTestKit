"""Measure how one OS x browser x device sees the same page, and compare them in tabs (platform-matrix).

    python tools/platform_probe.py collect --browser chromium --profile desktop --out DIR [--headed] [--channel chrome]
    python -S tools/platform_probe.py merge DIR... --out dataset.json      # one dataset from many runs
    python -S tools/platform_probe.py summary dataset.json                 # one line per environment; exit 1 if any fail
    python -S tools/platform_probe.py render dataset.json --out matrix.html  # tabs: OS / browser / device / mode

collect defaults to fixtures/platform_probe.html; --adapter JSON selects an app. It records `btk-platform-facts/1`:
screen, window outer/inner (frame = outer - inner; real only when headed), scrollbar
width (page / always-scrolling probe), devicePixelRatio, visualViewport, safe-area insets, pointer/hover/touch,
measured font widths, whether Meiryo actually renders (`fonts.meiryo`: true / false / null when the generic
fallbacks cannot be told apart) and the body's declared font stack (`fonts.body_family`), platform and browser version. The environment passes when
the layout holds (no horizontal overflow, no overlap, nothing clipped) and a real
click (a tap on touch profiles) at the integer centre of the button registers.
`min_ok_width` is the first sampled passing viewport width on the search grid (8 px
by default). Adapters scan in ascending order; only the monotonic local fixture uses
binary search. Step 1 resolves integer-pixel edges within the tested bounds, not
subpixel or untested widths.

Only collect needs Playwright; merge, summary and render are stdlib-only.
Exit codes: 0 OK; 1 an environment failed (collect, summary); 2 input error.
"""
from __future__ import annotations

import argparse, datetime, functools, glob, html, json, os, platform, sys, threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer; from pathlib import Path

# Tests load this module directly by path.
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import platform_adapter as adapters

FACTS_SCHEMA = "btk-platform-facts/1"
DATASET_SCHEMA = "btk-platform-matrix/1"
ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "platform_probe.html"
PROFILES = {"desktop": None, "iphone": "iPhone 13", "pixel": "Pixel 7"}
DESKTOP_VIEWPORT = {"width": 1280, "height": 800}
# Width search runs on a grid: 8 px by default so results line up across runs, tools and
# tiles (Material's 8 dp grid), or 1 px (--width-step 1) to find an exact breakpoint edge
# such as 419/420. WebKit on Windows refuses viewports below 250x240 ("minimal viewport of
# 250x240"), so the search starts at the first grid width >= 250 on every platform.
WIDTH_STEP = 8
MIN_VIEWPORT_WIDTH = 250


def search_floor(step: int) -> int:
    return -(-MIN_VIEWPORT_WIDTH // step) * step


MIN_SEARCH_WIDTH = search_floor(WIDTH_STEP)


def os_name() -> str:
    return {"Darwin": "macOS", "Windows": "Windows", "Linux": "Linux"}.get(platform.system(), platform.system())


def env_id(env: dict) -> str:
    browser = env.get("channel") or env["browser"]
    return f"{env['os']}-{browser}-{env['profile']}-{'headless' if env['headless'] else 'headed'}"


def narrowest_ok_width(ok_at, low: int, high: int) -> int | None:
    """Smallest width in [low, high] where ok_at(width) is true, assuming monotonic; None if high fails."""
    if not ok_at(high):
        return None
    if ok_at(low):
        return low
    while high - low > 1:
        mid = (low + high) // 2
        if ok_at(mid):
            high = mid
        else:
            low = mid
    return high


# js-adopt: what the adopted libraries must do in every engine (fixtures load them with SRI).
# seedrandom('platform-matrix') is an ARC4 stream: the same first value in every engine and OS.
EXPECTED_SEED = 0.18705921546491625


def libs_check(libs: dict | None) -> dict:
    """Adopted libraries loaded, behaved, and a wrong integrity was refused."""
    if libs is None:
        return {"ok": True, "failed": []}
    failed = []
    if not libs.get("purify"):
        failed.append("purify_missing")
    sanitized = libs.get("sanitized") or ""
    if "onerror" in sanitized or "<b>ok</b>" not in sanitized:
        failed.append("sanitize_unsafe")
    if libs.get("seed") is None or libs["seed"] != EXPECTED_SEED:
        failed.append("seed_mismatch")
    if libs.get("sri_refused") is not True:
        failed.append("sri_not_enforced")
    return {"ok": not failed, "failed": failed}


def verdict(record: dict) -> str:
    libs = record.get("libs", {"ok": True})
    if any(not s['layout']['ok'] or not s['click']['ok'] for s in record.get('samples', [])):
        return 'fail'
    return "pass" if record["layout"]["ok"] and record["click"]["ok"] and libs["ok"] else "fail"


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):  # keep collect output to one line per environment
        pass


def collect(browser_name: str, profile: str, *, headed: bool = False, channel: str | None = None,
            adapter: str | None = None, url: str | None = None,
            widths: tuple[int, ...] = (320, 384), evidence_dir: str | None = None,
            width_step: int = WIDTH_STEP) -> dict:
    if profile not in PROFILES:
        raise ValueError(f"profile must be one of {sorted(PROFILES)}")
    if type(width_step) is not int or not 1 <= width_step <= 64:
        raise ValueError('width_step must be an integer from 1 to 64')
    config = adapters.load_adapter(adapter) if adapter else None
    if url and not config:
        raise ValueError('--url needs --adapter')
    if config:
        target_url = adapters.validate_url(url or config['url'])
        return _collect(browser_name, profile, headed=headed, channel=channel, url=target_url,
                        config=config, widths=widths, evidence_dir=evidence_dir, width_step=width_step)
    # Served over HTTP: SRI (crossorigin) does not work for file:// pages in every engine.
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_QuietHandler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/{FIXTURE.relative_to(ROOT).as_posix()}"
    try:
        return _collect(browser_name, profile, headed=headed, channel=channel, url=url, width_step=width_step)
    finally:
        server.shutdown()
        server.server_close()


def _collect(browser_name: str, profile: str, *, headed: bool, channel: str | None, url: str, config: dict | None = None,
             widths: tuple[int, ...] = (), evidence_dir: str | None = None, width_step: int = WIDTH_STEP) -> dict:
    floor = search_floor(width_step)
    from playwright.sync_api import sync_playwright  # only collect needs a browser

    with sync_playwright() as pw:
        engine = getattr(pw, browser_name)
        browser = engine.launch(headless=not headed, **({"channel": channel} if channel else {}))
        try:
            device = PROFILES[profile]
            if device:
                args = dict(pw.devices[device])
            elif headed:
                # A real window: an emulated viewport would make inner larger than outer (negative frame).
                args = {"no_viewport": True}
            else:
                args = {"viewport": dict(DESKTOP_VIEWPORT)}
            args.pop("default_browser_type", None)
            if browser_name == "firefox":
                args.pop("is_mobile", None)  # DEVICE_DESCRIPTOR_CROSS_ENGINE
            context = browser.new_context(**args)
            page = context.new_page()
            page.set_default_timeout(15000)
            response = page.goto(url, wait_until='domcontentloaded', timeout=60000)
            if response and not response.ok:
                raise ValueError(f'target returned HTTP {response.status}')
            target = adapters.target_identity(config, url) if config else None
            provenance = adapters.read_provenance(page, config, url) if config else None
            if config:
                adapters.prepare(page, config)
                page.add_script_tag(path=str(ROOT / 'tools' / 'platform_facts.js'))
                facts = page.locator(config['click']['target']).evaluate('(e) => window.__btkPlatformFacts(e)')
                layout = adapters.layout(page, config)
            else:
                facts = page.evaluate("window.__platformFacts()")
                layout = page.evaluate("window.__layoutChecks()")
            size = page.viewport_size or {"width": facts["window"]["inner_width"],
                                          "height": facts["window"]["inner_height"]}
            touch = bool(args.get("has_touch"))
            samples = []
            observations = []
            def fresh_page(width):
                nonlocal context, page
                context.close()
                options = dict(args)
                options.pop('no_viewport', None)
                options['viewport'] = {'width': width, 'height': size['height']}
                context = browser.new_context(**options)
                page = context.new_page()
                page.set_default_timeout(15000)
                response = page.goto(url, wait_until='domcontentloaded', timeout=60000)
                if response and not response.ok:
                    raise ValueError(f'target returned HTTP {response.status}')
                adapters.prepare(page, config)

            def measure(width):
                page.set_viewport_size({"width": width, "height": size["height"]})
                if config:
                    adapters.settle(page)
                return adapters.layout(page, config) if config else page.evaluate("window.__layoutChecks()")
            def capture(label):
                if evidence_dir:
                    folder = Path(evidence_dir)
                    folder.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(folder / (label + '.png')))
            if config:
                capture('initial')
                # Ascending exhaustive search: responsive apps need not be monotonic.
                min_width = None
                for width in range(floor, size['width'] + 1, width_step):
                    result = measure(width)
                    observations.append({'width': width, 'ok': result['ok'], 'failed': result['failed']})
                    if result['ok']:
                        min_width = width
                        break
                for width in sorted(set(widths) | ({min_width} if min_width else set())):
                    fresh_page(width)
                    result = adapters.layout(page, config)
                    click = adapters.click_check(page, config, touch)
                    samples.append({'width': width, 'layout': result, 'click': click})
                    capture(f'width-{width}')
                fresh_page(size['width'])
                click_result = adapters.click_check(page, config, touch)
            else:
                box = page.locator("#action").bounding_box()
                x, y = int(box["x"] + box["width"] / 2), int(box["y"] + box["height"] / 2)
                (page.touchscreen.tap if touch else page.mouse.click)(x, y)
                clicks = int(page.locator("#action").get_attribute("data-clicks"))
                click_result = {"ok": clicks == 1, "count": clicks, "input": "touch" if touch else "mouse", "point": [x, y]}
                step = narrowest_ok_width(lambda k: measure(k * width_step)['ok'],
                                          floor // width_step, size['width'] // width_step)
                min_width = None if step is None else step * width_step
            resolved_url = page.url
            version = browser.version
        except Exception:
            if config and evidence_dir and 'page' in locals():
                try:
                    Path(evidence_dir).mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(Path(evidence_dir) / 'failure.png'), timeout=5000)
                except Exception:
                    pass  # preserve the original navigation/adapter failure
            raise
        finally:
            browser.close()
    record = {
        "schema": FACTS_SCHEMA,
        "env": {"os": os_name(), "os_release": platform.release(), "runner_image": os.environ.get("ImageOS"),
                "browser": browser_name, "channel": channel, "browser_version": version,
                "profile": profile, "device": PROFILES[profile] or "Desktop", "headless": not headed,
                "device_validation": "emulation" if profile != "desktop" else "desktop-browser"},
        "facts": facts, "layout": layout, "libs": libs_check(facts.get("libs")),
        "click": click_result,
        "min_ok_width": min_width,
    }
    if target:
        record['target'] = {**target, 'resolved_url': resolved_url, 'config': config}
        if provenance:
            record['target']['provenance'] = provenance
        record['samples'] = samples
        record['width_search'] = {'method': 'ascending-exhaustive', 'step': width_step, 'low': floor,
                                  'high': size['width'],
                                  'observations': observations, 'scope': 'layout-only'}
        record['observed_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    record["env"]["id"] = env_id(record["env"])
    record["verdict"] = verdict(record)
    return record


def record_key(record: dict) -> str:
    return record['env']['id'] + ('--' + record['target']['key'] if record.get('target') else '')


def load_records(paths: list[str]) -> list[dict]:
    records = {}
    for pattern in paths:
        files = [Path(p) for p in glob.glob(str(Path(pattern) / "**" / "*.json"), recursive=True)] \
            if Path(pattern).is_dir() else [Path(p) for p in glob.glob(pattern)]
        for path in files:
            data = json.loads(path.read_text(encoding="utf-8"))
            items = data["records"] if isinstance(data, dict) and data.get("schema") == DATASET_SCHEMA else [data]
            for item in items:
                if not isinstance(item, dict) or item.get("schema") != FACTS_SCHEMA:
                    continue
                records[record_key(item)] = item
    if not records:
        raise ValueError("no btk-platform-facts/1 records found")
    return [records[k] for k in sorted(records)]


def _build_suffix(record: dict) -> str:
    provenance = record.get('target', {}).get('provenance')
    if not provenance:
        return ''
    value = provenance.get('value')
    return f" build={str(value)[:12]}" if value is not None else f" build=unknown({provenance.get('error')})"


def _meiryo(record: dict) -> str:
    """Whether Meiryo actually renders on this environment: reported, never gating (most Linux/macOS lack it)."""
    fonts = record['facts'].get('fonts', {})
    return {True: 'yes', False: 'no', None: 'unknown'}[fonts['meiryo']] if 'meiryo' in fonts else '-'


def summary_lines(records: list[dict]) -> list[str]:
    out = []
    for r in records:
        w, v = r["facts"]["window"], r["facts"]["viewport"]
        failures = r["layout"]["failed"] + r.get("libs", {}).get("failed", [])
        if not r['click']['ok']:
            failures = failures + ['initial_click']
        for sample in r.get('samples', []):
            failures += [str(sample['width']) + 'px:' + why for why in sample['layout']['failed']]
            if not sample['click']['ok']:
                failures.append(str(sample['width']) + 'px:click')
        failed = ','.join(failures) or '-'
        out.append(f"{r['verdict']:4} {r['env']['id']:38} v{r['env']['browser_version']} "
                   f"frame={w['frame_width']}x{w['frame_height']} scrollbar={v['scrollbar_width']}/{v.get('scrollbar_classic')} "
                   f"dpr={v['device_pixel_ratio']} sans={r['facts'].get('fonts', {}).get('sans')} meiryo={_meiryo(r)} "
                   f"min_ok_width={r['min_ok_width']} failed={failed} target={r.get('target', {}).get('adapter', 'fixture')}"
                   + _build_suffix(r))
    return out


_PAGE_CSS = """
.pp-row{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:6px 0}.pp-row b{width:72px;color:var(--ui-muted);font-weight:500}
.pp-row .ui-button{padding:4px 10px;cursor:pointer}.pp-row .ui-button[aria-pressed=true]{background:var(--ui-accent);border-color:var(--ui-accent);color:var(--ui-bg)}
.pp-row .ui-button.none{opacity:.45}
#head{display:flex;flex-wrap:wrap;gap:12px;align-items:center;margin:12px 0}
.pp-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:16px}@media(max-width:760px){.pp-grid{grid-template-columns:1fr}}
#facts th{width:42%}#facts tr.group th{color:var(--ui-fg);font-weight:600;padding-top:10px}
#facts td.ui-changed{transition:background .2s}#facts td.varies::after{content:" *";color:var(--ui-muted)}
#diagram{width:100%;height:auto;border:1px solid var(--ui-border);border-radius:var(--ui-radius)}#diagram text{font:11px var(--ui-font);fill:var(--ui-fg)}
"""

_PAGE_BODY = """<main class="ui-page">
<h1 class="ui-title">Platform matrix</h1>
<p class="ui-muted">対象ごとの OS × ブラウザ × 端末の実測値（スマホはエミュレーション、実機ではありません）。タブを切り替えると、直前と値が変わった行が色付きになります（* は環境によって値が違う項目）。</p>
<div id="tabs"></div>
<div id="head"></div>
<div class="pp-grid"><div><table id="facts" class="ui-table"></table></div><div><svg id="diagram" viewBox="0 0 420 300" role="img" aria-label="screen, window and viewport"></svg>
<p class="ui-muted">外枠 = screen、点線 = avail、枠線色 = window（outer）、アクセント色 = viewport（inner）、危険色 = scrollbar。headless では枠が 0 になります。</p></div></div>
</main>
<script type="application/json" id="data">__DATA__</script>"""

_PAGE_JS = """const records = JSON.parse(document.getElementById('data').textContent).records;
const axes = [['Target', r => r.target?.key || 'fixture'], ['OS', r => r.env.os], ['Browser', r => r.env.channel || r.env.browser], ['Device', r => r.env.device],
              ['Mode', r => r.env.headless ? 'headless' : 'headed']];
const sel = axes.map(([, f]) => f(records[0]));
let previous = null;
const flat = (o, p = '') => Object.entries(o).flatMap(([k, v]) =>
  v && typeof v === 'object' && !Array.isArray(v) ? flat(v, p + k + '.') : [[p + k, v]]);
const varies = new Set();
const allFlat = records.map(r => new Map(flat({facts: r.facts, min_ok_width: r.min_ok_width, browser_version: r.env.browser_version})));
for (const key of allFlat[0].keys()) if (new Set(allFlat.map(m => JSON.stringify(m.get(key)))).size > 1) varies.add(key);
function match() { return records.find(r => axes.every(([, f], i) => f(r) === sel[i])); }
function tabs() {
  const box = document.getElementById('tabs'); box.textContent = '';
  axes.forEach(([name, f], i) => {
    const row = document.createElement('div'); row.className = 'pp-row';
    row.append(Object.assign(document.createElement('b'), {textContent: name}));
    for (const value of [...new Set(records.map(f))].sort()) {
      const b = document.createElement('button'); b.type = 'button'; b.className = 'ui-button'; b.textContent = value;
      b.setAttribute('aria-pressed', String(sel[i] === value));
      const probe = sel.slice(); probe[i] = value;
      if (!records.some(r => axes.every(([, g], j) => g(r) === probe[j]))) b.classList.add('none');
      b.onclick = () => { sel[i] = value; show(); };
      row.append(b);
    }
    box.append(row);
  });
}
function rect(svg, x, y, w, h, stroke, dash, label, line = 0) {
  const ns = 'http://www.w3.org/2000/svg', r = document.createElementNS(ns, 'rect');
  Object.entries({x, y, width: Math.max(w, 0), height: Math.max(h, 0), fill: 'none', stroke, 'stroke-width': 2,
    'stroke-dasharray': dash || ''}).forEach(([k, v]) => r.setAttribute(k, v));
  svg.append(r);
  if (label) { const t = document.createElementNS(ns, 'text'); t.setAttribute('x', x + 4); t.setAttribute('y', y + 13 + 14 * line);
    if (stroke !== 'currentColor') { t.style.fill = stroke; t.style.fontWeight = '600'; }
    t.textContent = label; svg.append(t); }
}
function diagram(r) {
  const svg = document.getElementById('diagram'); svg.textContent = '';
  if (!r) return;
  const s = r.facts.screen, w = r.facts.window, v = r.facts.viewport;
  const k = Math.min(400 / Math.max(s.width, w.outer_width), 280 / Math.max(s.height, w.outer_height));
  const ox = 10, oy = 10;
  rect(svg, ox, oy, s.width * k, s.height * k, 'currentColor', '', `screen ${s.width}×${s.height}`);
  rect(svg, ox + (s.avail_left || 0) * k, oy + (s.avail_top || 0) * k, s.avail_width * k, s.avail_height * k, 'currentColor', '4 3');
  const wx = ox + Math.max(0, w.screen_x) * k, wy = oy + Math.max(0, w.screen_y) * k;
  rect(svg, wx, wy, w.outer_width * k, w.outer_height * k, 'var(--ui-border)', '', `window ${w.outer_width}×${w.outer_height}（枠 ${w.frame_width}×${w.frame_height}）`, 1);
  const side = Math.max(0, w.frame_width) / 2;
  const ix = wx + side * k, iy = wy + Math.max(0, w.frame_height - side) * k;
  rect(svg, ix, iy, w.inner_width * k, w.inner_height * k, 'var(--ui-accent)', '', `viewport ${w.inner_width}×${w.inner_height}`, 2);
  if (v.scrollbar_width > 0) rect(svg, ix + v.client_width * k, iy, v.scrollbar_width * k, w.inner_height * k, 'var(--ui-danger)', '');
}
function show() {
  tabs();
  const r = match(), head = document.getElementById('head'), table = document.getElementById('facts');
  head.textContent = ''; table.textContent = '';
  if (!r) { head.textContent = 'この組み合わせは未測定です（薄いタブは測定がありません）'; diagram(null); previous = null; return; }
  const badge = Object.assign(document.createElement('span'), {className: 'ui-tag ui-' + r.verdict, textContent: r.verdict});
  head.append(badge, ` ${r.env.id}  v${r.env.browser_version}  OS ${r.env.os_release}`,
    `  ・対象 ${r.target?.url || 'fixture'} / ${r.env.device_validation || 'legacy'}  `,
    `  ・最小幅 ${r.min_ok_width ?? '—'}px`, r.layout.failed.length ? `  ・崩れ: ${r.layout.failed.join(', ')}` : '',
    `  ・クリック ${r.click.ok ? 'OK' : 'NG'}（${r.click.input} @ ${r.click.point.join(',')}）`);
  const now = new Map(flat({facts: r.facts, min_ok_width: r.min_ok_width, browser_version: r.env.browser_version, samples: JSON.stringify(r.samples || [])}));
  let group = '';
  for (const [key, value] of now) {
    const top = key.split('.').slice(0, 2).join('.');
    if (top !== group) { group = top; const g = table.insertRow(); g.className = 'group';
      g.append(Object.assign(document.createElement('th'), {colSpan: 2, textContent: top.replace('facts.', '')})); }
    const row = table.insertRow();
    row.append(Object.assign(document.createElement('th'), {textContent: key.split('.').slice(2).join('.') || key}));
    const td = row.insertCell(); td.textContent = value === null ? '—' : String(value);
    if (varies.has(key)) td.classList.add('varies');
    if (previous && JSON.stringify(previous.get(key)) !== JSON.stringify(value)) td.classList.add('ui-changed');
  }
  diagram(r); previous = now;
}
show();
"""


def render(records: list[dict]) -> str:
    """One self-contained review page from PySaiyanWeBoo (pysaiyanweboo) (core CSS + the matrix script); needs the
    ``pages`` dependency group (``uv run --group pages``), unlike collect/merge/summary which stay stdlib."""
    import pysaiyanweboo as ui  # PySaiyanWeBoo, pinned in pyproject's pages group

    data = json.dumps({"schema": DATASET_SCHEMA, "records": records}, ensure_ascii=False).replace("<", "\\u003c")
    body = _PAGE_BODY.replace("__DATA__", data) + ui.classic_script(_PAGE_JS)
    return ui.render_document(trusted_html=body, title="Platform matrix", css=ui.core_css() + _PAGE_CSS)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("collect", help="measure one environment (needs Playwright)")
    c.add_argument("--browser", choices=("chromium", "firefox", "webkit"), required=True)
    c.add_argument("--channel", help="installed branded browser, e.g. chrome or msedge (chromium only)")
    c.add_argument("--profile", choices=sorted(PROFILES), default="desktop")
    c.add_argument("--headed", action="store_true", help="a real window, so the frame size is measured")
    c.add_argument('--adapter', help='JSON adapter for an application (default: original local fixture)')
    c.add_argument('--url', help='override adapter HTTP(S) target URL')
    c.add_argument('--widths', nargs='+', type=int, default=[320, 384], help='additional real-click widths; any width from 250, e.g. device widths 390 412')
    c.add_argument('--width-step', type=int, default=WIDTH_STEP,
                   help='width search grid in px: 8 (default, aligns results) or 1 (exact breakpoint edges)')
    c.add_argument('--evidence-dir', help='screenshots; use a separate directory per environment')
    c.add_argument("--out", required=True, help="directory for <env-id>.json")
    m = sub.add_parser("merge", help="combine records into one dataset")
    m.add_argument("inputs", nargs="+", help="directories or JSON files")
    m.add_argument("--out", required=True)
    s = sub.add_parser("summary", help="one line per environment; exit 1 if any fail")
    s.add_argument("inputs", nargs="+")
    r = sub.add_parser("render", help="self-contained HTML with OS / browser / device / mode tabs")
    r.add_argument("inputs", nargs="+")
    r.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "collect":
            if args.channel and args.browser != "chromium":
                raise ValueError("--channel needs --browser chromium")
            try:
                if any(w < MIN_VIEWPORT_WIDTH or w > 3840 for w in args.widths):
                    raise ValueError('widths must be between 250 and 3840')
                record = collect(args.browser, args.profile, headed=args.headed, channel=args.channel,
                                 adapter=args.adapter, url=args.url, widths=tuple(args.widths), evidence_dir=args.evidence_dir,
                                 width_step=args.width_step)
            except Exception as exc:  # a browser that cannot launch or navigate is a finding, not a crash
                where = f"{args.browser}{'/' + args.channel if args.channel else ''} {args.profile}" \
                        f"{' headed' if args.headed else ''}"
                first = str(exc).strip().splitlines()[0][:300] if str(exc).strip() else ""
                Path(args.out).mkdir(parents=True, exist_ok=True)
                tag = Path(args.adapter).stem if args.adapter else 'fixture'
                error_path = Path(args.out) / f"error-{args.browser}-{args.profile}-{tag}.json"
                error_path.write_text(json.dumps({'schema': 'btk-platform-error/1', 'environment': where,
                                     'adapter': tag, 'error_type': type(exc).__name__, 'message': first}) + '\n', encoding='utf-8')
                print(f"error: {where}: {type(exc).__name__}: {first}", file=sys.stderr)
                if os.environ.get("GITHUB_ACTIONS"):
                    print(f"::error title=platform_probe collect::{where}: {type(exc).__name__}: {first}")
                return 2
            Path(args.out).mkdir(parents=True, exist_ok=True)
            path = Path(args.out) / f"{record_key(record)}.json"
            path.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            print(summary_lines([record])[0])
            return 0 if record["verdict"] == "pass" else 1
        records = load_records(args.inputs)
        if args.command in ("merge", "render"):
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        if args.command == "merge":
            Path(args.out).write_text(json.dumps({"schema": DATASET_SCHEMA, "records": records}, ensure_ascii=False,
                                                 indent=1) + "\n", encoding="utf-8")
            print(f"{len(records)} environments -> {args.out}")
            return 0
        if args.command == "summary":
            lines = summary_lines(records)
            print("\n".join(lines))
            return 0 if all(r["verdict"] == "pass" for r in records) else 1
        Path(args.out).write_text(render(records), encoding="utf-8")
        print(f"{len(records)} environments -> {args.out}")
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        if os.environ.get("GITHUB_ACTIONS"):
            print(f"::error title=platform_probe {args.command}::{type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
