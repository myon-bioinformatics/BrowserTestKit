"""App-owned selectors and postconditions; browser/OS facts stay in platform_facts.js.

JSON adapters are trusted test configuration, not code supplied by the target page.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import urljoin, urlsplit


LAYOUT_JS = r"""cfg => {
  const failed = [], boxes = [];
  const de = document.documentElement;
  if (de.scrollWidth > de.clientWidth + .5) failed.push('horizontal_overflow');
  for (const selector of cfg.elements) {
    const found = document.querySelectorAll(selector);
    if (found.length !== 1) { failed.push('missing_or_ambiguous:' + selector); continue; }
    const e = found[0], r = e.getBoundingClientRect(), style = getComputedStyle(e);
    if (r.width <= 0 || r.height <= 0 || style.visibility === 'hidden' || style.display === 'none')
      failed.push('invisible:' + selector);
    if (r.left < -.5 || r.right > innerWidth + .5 || r.top < -.5 || r.bottom > innerHeight + .5)
      failed.push('outside:' + selector);
    // Invisible Flutter accessibility labels do not share the painted glyph geometry.
    if (!cfg.flutter_semantics && e.scrollWidth > e.clientWidth + .5) failed.push('clipped:' + selector);
    boxes.push({selector, x:r.x, y:r.y, width:r.width, height:r.height});
  }
  for (let i=0;i<boxes.length;i++) for(let j=i+1;j<boxes.length;j++) {
    const a=boxes[i], b=boxes[j];
    if (Math.min(a.x+a.width,b.x+b.width)-Math.max(a.x,b.x) > .5 &&
        Math.min(a.y+a.height,b.y+b.height)-Math.max(a.y,b.y) > .5)
      failed.push('overlap:' + a.selector + ':' + b.selector);
  }
  for (const selector of cfg.targets) {
    const e=document.querySelector(selector);
    if (!e) continue;
    const r=e.getBoundingClientRect(), top=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
    // Flutter semantics have pointer-events:none and forward events to the canvas below.
    // Real coordinate input plus an app postcondition remains the deciding check there.
    if (!cfg.flutter_semantics && (!top || !(e===top || e.contains(top))))
      failed.push('occluded:' + selector);
  }
  return {ok:failed.length===0, failed, boxes};
}"""


def load_adapter(path: str | Path) -> dict:
    config = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(config, dict):
        raise ValueError('adapter must be a JSON object')
    if config.get('schema') != 'btk-platform-adapter/1':
        raise ValueError('adapter needs schema btk-platform-adapter/1')
    if not isinstance(config.get('id'), str) or not config['id'].replace('-', '').isalnum():
        raise ValueError('adapter id must be alphanumeric with optional hyphens')
    if not config.get('elements') or not all(isinstance(s, str) and s for s in config['elements']):
        raise ValueError('adapter elements must be nonempty selectors')
    click = config.get('click', {})
    if not isinstance(click, dict) or not all(isinstance(click.get(k), str) and click[k] for k in ('target', 'observe', 'attribute')):
        raise ValueError('click needs target, observe and attribute strings')
    if any(k not in click or (click[k] is not None and not isinstance(click[k], str)) for k in ('before', 'after')):
        raise ValueError('click before/after must be strings or null (absent attribute)')
    if click['before'] == click['after']:
        raise ValueError('click must have distinct before and after states')
    bindings = config.get('bindings', {})
    if not isinstance(bindings, dict) or any(not isinstance(name, str) or not name.isalnum() or
        not isinstance(binding, dict) or not all(isinstance(binding.get(k), str) for k in ('selector', 'text'))
        for name, binding in bindings.items()):
        raise ValueError('bindings need alphanumeric names, CSS selectors and exact text')
    if click['target'] not in config['elements']:
        raise ValueError('click target must be a layout element')
    validate_url(config.get('url', ''))
    provenance = config.get('provenance')
    if provenance is not None:
        source, field = (provenance.get('path'), provenance.get('field')) if isinstance(provenance, dict) else (None, None)
        if (not isinstance(source, str) or not source or source.startswith('/') or '..' in source.split('/')
                or urlsplit(source).scheme or urlsplit(source).netloc):
            raise ValueError('provenance path must be a relative same-site path')
        if not isinstance(field, list) or not field or not all(isinstance(k, str) and k for k in field):
            raise ValueError('provenance field must be a nonempty list of keys')
    return config


def validate_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
        raise ValueError('target URL must be HTTP(S), without credentials')
    return url


def target_identity(config: dict, url: str) -> dict:
    digest = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    return {'adapter': config['id'], 'url': url, 'config_sha256': digest,
            'key': config['id'] + '-' + hashlib.sha256((digest + url).encode()).hexdigest()[:12]}


def read_provenance(page, config: dict, url: str) -> dict | None:
    """Which build the page came from, read from a same-site JSON file the app publishes.

    Reported, never gating: a missing file or field is ``value: None`` with the reason,
    so a deployment without metadata still yields its layout and click observations.
    """
    spec = config.get('provenance')
    if not spec:
        return None
    target = urljoin(url.split('#', 1)[0], spec['path'])
    out = {'url': target, 'field': spec['field'], 'status': None, 'value': None, 'error': None}
    try:
        response = page.request.get(target, timeout=15000)
        out['status'] = response.status
        if not response.ok:
            out['error'] = 'http_status'
            return out
        value = json.loads(response.body())
        for key in spec['field']:
            value = value.get(key) if isinstance(value, dict) else None
        out['value'] = value if isinstance(value, (str, int, float, bool)) else None
        if out['value'] is None:
            out['error'] = 'field_missing'
    except Exception as exc:  # network, JSON or Playwright errors are part of the observation
        out['error'] = type(exc).__name__
    return out


def prepare(page, config: dict):
    if config.get('flutter_semantics'):
        page.wait_for_selector('flt-glass-pane', state='attached', timeout=60000)
        # Android Flutter uses a full-screen activation placeholder and accepts
        # only an input event at its centre. HTMLElement.click() has offset 0,0;
        # repeatedly polling with it eventually exhausts Flutter's attempt budget.
        page.wait_for_function("""() => !!document.querySelector('flt-semantics') ||
          !!document.querySelector('flt-semantics-placeholder[aria-label="Enable accessibility"]')""", timeout=60000)
        if not page.locator('flt-semantics').count():
            placeholder = page.locator('flt-semantics-placeholder[aria-label="Enable accessibility"]')
            box = placeholder.bounding_box()
            if box and box['width'] > 1 and box['height'] > 1:
                x, y = box['x'] + box['width']/2, box['y'] + box['height']/2
                touch = page.evaluate('navigator.maxTouchPoints > 0')
                (page.touchscreen.tap if touch else page.mouse.click)(x, y)
            else:
                # Desktop Flutter puts a 1px accessibility placeholder off-screen.
                placeholder.evaluate('(e) => e.click()')
        page.wait_for_selector('flt-semantics', state='attached', timeout=60000)
    bind_elements(page, config)
    for selector in config['elements']:
        page.locator(selector).wait_for(state='visible')
    settle(page)


def bind_elements(page, config):
    # Declarative exact text binding for Flutter semantics which lacks stable ids.
    for name, binding in config.get('bindings', {}).items():
        page.wait_for_function("""b => [...document.querySelectorAll(b.selector)]
          .filter(e => e.textContent.trim() === b.text).length === 1""", arg=binding)
        page.evaluate("""b => {
          document.querySelectorAll('[data-btk-probe="' + b.name + '"]').forEach(e => e.removeAttribute('data-btk-probe'));
          [...document.querySelectorAll(b.selector)].filter(e => e.textContent.trim() === b.text)[0]
            .setAttribute('data-btk-probe', b.name);
        }""", {**binding, 'name': name})


def settle(page):
    # Flutter resizes asynchronously; wait for frames as well as its semantics rebuild.
    page.evaluate('() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))')
    page.wait_for_timeout(80)


def layout(page, config: dict) -> dict:
    bind_elements(page, config)
    return page.evaluate(LAYOUT_JS, {**config, 'targets': [config['click']['target']]})


def click_check(page, config: dict, touch: bool) -> dict:
    click = config['click']
    target, observed = page.locator(click['target']), page.locator(click['observe'])
    before = observed.inner_text() if click['attribute'] == 'textContent' else observed.get_attribute(click['attribute'])
    box = target.bounding_box()
    if not box:
        return {'ok': False, 'count': 0, 'input': 'touch' if touch else 'mouse', 'point': [], 'before': before}
    x, y = int(box['x'] + box['width']/2), int(box['y'] + box['height']/2)
    (page.touchscreen.tap if touch else page.mouse.click)(x, y)
    from playwright.sync_api import TimeoutError
    try:
        page.wait_for_function("c => {const e=document.querySelector(c.observe); return e && (c.attribute==='textContent' ? e.textContent : e.getAttribute(c.attribute)) === c.after;}",
                               arg=click, timeout=3000)
    except TimeoutError:
        pass
    after = observed.inner_text() if click['attribute'] == 'textContent' else observed.get_attribute(click['attribute'])
    return {'ok': before == click['before'] and after == click['after'], 'count': int(after == click['after']),
            'before': before, 'after': after, 'input': 'touch' if touch else 'mouse', 'point': [x, y]}


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('adapter', nargs='?', help='validate a btk-platform-adapter/1 JSON file')
    args = parser.parse_args(argv)
    if args.adapter:
        config = load_adapter(args.adapter)
        print(config['id'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
