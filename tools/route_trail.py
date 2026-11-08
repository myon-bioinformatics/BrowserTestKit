"""Turn an emoji page trail such as '🏠 → 🗺 ↔ 🌍' into the URLs to visit, in order.

A trail names pages by their emoji identity: `A → B` goes from A to B, `A ↔ B`
goes to B and back to A (ASCII `->` and `<->` work too; spaces are ignored).
The emoji -> route registry is read from a JSON object ({route: emoji} or
{emoji: route}) or from a source file with a map literal ('/route': '🏠'), such
as a generated Dart/TS registry; `--map NAME` limits reading to one named map.

Emoji are compared without variation selectors (U+FE0E/U+FE0F), so a typed 🗺
matches a registered 🗺️; a registry whose emoji collide after that is an error.

Exit codes: 0 OK, 1 execution failure, 2 input error (unknown emoji, bad trail or registry).
"""
from __future__ import annotations

import argparse
import copy
import json
import ipaddress
import re
import sys

GO, ROUND = "→", "↔"
_PAIR = re.compile(r"""['"](/[^'"]*)['"]\s*:\s*['"]([^'"]+)['"]""")


def norm(emoji: str) -> str:
    return re.sub(r"[\s︎️]", "", emoji)


def load_registry(text: str, *, name: str | None = None, map_name: str | None = None) -> dict:
    """Return {normalized emoji: route}. JSON when it parses as an object, else map-literal pairs."""
    pairs = None
    if (name or "").endswith(".json") or text.lstrip().startswith("{"):
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise ValueError('invalid JSON registry') from exc
        if isinstance(data, dict) and ('schema' in data or 'pages' in data):
            if map_name is not None:
                raise ValueError('--map is not used with route-trail/1')
            return _catalog(data)
        if isinstance(data, dict):
            if map_name is not None:
                data = data.get(map_name)
                if not isinstance(data, dict):
                    raise ValueError(f"map not found: {map_name}")
            pairs = [(k, v) if str(k).startswith("/") else (v, k) for k, v in data.items()]
    if pairs is None:
        if map_name is not None:
            start = text.find(map_name)
            if start < 0:
                raise ValueError(f"map not found: {map_name}")
            end = text.find("}", start)
            text = text[start:end if end > 0 else None]
        # Values that are routes belong to alias maps, not to page identities.
        pairs = [(r, e) for r, e in _PAIR.findall(text) if not e.startswith("/")]
    registry = {}
    for route, emoji in pairs:
        if not isinstance(route, str) or not isinstance(emoji, str) or not route.startswith("/"):
            raise ValueError(f"registry entry must pair a /route with an emoji: {route!r}: {emoji!r}")
        key = norm(emoji)
        if not key:
            raise ValueError(f"empty emoji for {route}")
        if key in registry and registry[key] != route:
            raise ValueError(f"emoji {emoji} names two routes: {registry[key]} and {route}")
        registry[key] = route
    if not registry:
        raise ValueError("registry has no /route: emoji pairs")
    return registry


def parse_trail(trail: str) -> list[str]:
    """'A → B ↔ C' -> ['A', 'B', 'C', 'B'] (normalized emoji, in visiting order)."""
    text = re.sub(r"\s", "", trail).replace("<->", ROUND).replace("->", GO)
    tokens = re.split(f"([{GO}{ROUND}])", text)
    if not tokens[0] or any(not t for t in tokens[2::2]):
        raise ValueError("trail must alternate pages and arrows, e.g. '🏠 → 🗺 ↔ 🌍'")
    steps = [norm(tokens[0])]
    for arrow, page in zip(tokens[1::2], tokens[2::2]):
        previous = steps[-1]
        steps.append(norm(page))
        if arrow == ROUND:
            steps.append(previous)
    return steps


def resolve(trail: str, registry: dict, *, base: str = "http://localhost:8080/", hash_routing: bool = True) -> list[dict]:
    rows = []
    for emoji in parse_trail(trail):
        route = registry.get(emoji)
        if route is None:
            raise ValueError(f"unknown page emoji: {emoji}")
        item = copy.deepcopy(route) if isinstance(route, dict) else {'emoji': emoji, 'route': route}
        route = item['route']
        url = base + "#" + route if hash_routing else base.rstrip("/") + route
        rows.append({**item, "url": url})
        if len(rows) > MAX_STEPS:
            raise ValueError('trail exceeds 1000 steps')
    return rows


MAX_STEPS = 1000
# Shared grammar with route_trail_node.mjs (TWIN_PARSER_DRIFT). The match only
# splits the URL; _execution_url also checks host semantics and destination port.
# Deliberately supported hosts: ASCII DNS, canonical dotted IPv4, bracketed IPv6.
# IDN/ACE labels are excluded until both twins have the same IDNA implementation.
EXECUTION_URL_SOURCE = r'^(?:([Hh][Tt][Tt][Pp][Ss]?):\/\/([A-Za-z0-9.-]+|\[[0-9A-Fa-f:.]+\])(?::([0-9]{1,5}))?(?:[/?#][^\x00-\x20\x7f-\x9f\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff\\]*)?|[Ff][Ii][Ll][Ee]:\/\/(?:[Ll][Oo][Cc][Aa][Ll][Hh][Oo][Ss][Tt])?\/[^\x00-\x20\x7f-\x9f\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff\\]*)$'
EXECUTION_URL = re.compile(EXECUTION_URL_SOURCE)


def _execution_url(url):
    match = EXECUTION_URL.fullmatch(url)
    if not match:
        return False
    scheme, host, port = match.groups()
    if scheme is None:  # Local absolute file URL; no authority or port.
        return True
    if port is not None and not 1 <= int(port) <= 65535:
        return False  # Port 0 is not a destination service.
    if host.startswith('['):
        try:
            ipaddress.IPv6Address(host[1:-1])
            return True
        except ValueError:
            return False
    dns = host[:-1] if host.endswith('.') else host
    if not dns or len(dns) > 253:
        return False
    labels = dns.lower().split('.')
    if any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
           or label.startswith('xn--') for label in labels):
        return False
    # WHATWG treats a numeric/hex final label as IPv4, even in a DNS-looking name.
    # Accept only the canonical four-octet spelling, never legacy/octal/hex forms.
    if re.fullmatch(r'[0-9]+|0x[0-9a-f]*', labels[-1]):
        return (len(labels) == 4 and all(re.fullmatch(r'0|[1-9][0-9]{0,2}', label)
                                       and int(label) <= 255 for label in labels))
    return True


MAX_TIMEOUT = 120000


def _ready_spec(value):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {'selector', 'text'}:
        raise ValueError('ready requires selector and optional exact text')
    selector = value.get('selector')
    if not isinstance(selector, str) or not selector or len(selector) > 512:
        raise ValueError('ready selector must be 1..512 characters')
    if 'text' in value and (not isinstance(value['text'], str) or len(value['text']) > 2000):
        raise ValueError('ready text must be a string of at most 2000 characters')
    return dict(value)


def _catalog(data):
    if set(data) != {'schema', 'pages'} or data['schema'] != 'route-trail/1':
        raise ValueError('expected route-trail/1 with pages')
    if not isinstance(data['pages'], list) or not 1 <= len(data['pages']) <= MAX_STEPS:
        raise ValueError('pages must contain 1..1000 records')
    registry, routes = {}, set()
    for raw in data['pages']:
        if not isinstance(raw, dict) or set(raw) - {'id', 'emoji', 'title', 'route', 'ready'}:
            raise ValueError('unknown page fields')
        ident, emoji, route = raw.get('id'), raw.get('emoji'), raw.get('route')
        if not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,63}', ident):
            raise ValueError('page id must be an ASCII identifier')
        if not isinstance(emoji, str) or not norm(emoji) or emoji.startswith('@'):
            raise ValueError('page emoji must be nonempty and cannot start with @')
        if not isinstance(route, str) or not route.startswith('/') or len(route) > 2048:
            raise ValueError('page route must start with / and be at most 2048 characters')
        if route in routes:
            raise ValueError('duplicate page route: ' + route)
        title = raw.get('title', '')
        if not isinstance(title, str) or len(title) > 200:
            raise ValueError('page title must be a string of at most 200 characters')
        item = {'id': ident, 'emoji': norm(emoji), 'route': route, 'title': title,
                'ready': _ready_spec(raw.get('ready'))}
        for key in (norm(emoji), '@' + ident):
            if key in registry:
                raise ValueError('duplicate page identity: ' + key)
            registry[key] = item
        routes.add(route)
    return registry


def validate_steps(steps, timeout_ms=10000):
    """Validate the entire plan before any navigation. No Playwright import."""
    if type(timeout_ms) is not int or not 1 <= timeout_ms <= MAX_TIMEOUT:
        raise ValueError('timeout_ms must be an integer 1..120000')
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise ValueError('steps must contain 1..1000 records')
    for step in steps:
        if not isinstance(step, dict) or set(step) - {'id', 'emoji', 'route', 'url', 'title', 'ready'}:
            raise ValueError('invalid execution step')
        if any(not isinstance(step.get(k), str) for k in ('emoji', 'route', 'url')):
            raise ValueError('step requires emoji, route and url strings')
        url = step['url']
        # One explicit rule shared with route_trail_node.mjs (EXECUTION_URL): each language's own
        # URL parser accepts and repairs different inputs (TWIN_PARSER_DRIFT).
        if len(url) > 4096 or not _execution_url(url):
            raise ValueError('execution URLs must be absolute HTTP(S) or local file URLs without credentials')
        _ready_spec(step.get('ready'))
    return steps


def _receipt(steps, total, error=None):
    result = {'schema': 'route-trail-run/1', 'ok': error is None,
              'total': total, 'completed': len(steps), 'steps': steps}
    if error is not None:
        result['error'] = error
    return result


class TrailExecutionError(RuntimeError):
    """Execution stopped, with only previously verified steps marked complete."""
    def __init__(self, receipt):
        self.receipt = receipt
        super().__init__(receipt['error']['message'])


def _failed(done, steps, index, phase, exc):
    return TrailExecutionError(_receipt(list(done), len(steps), {
        'index': index, 'route': steps[index]['route'], 'phase': phase,
        'message': str(exc)[:2000], 'type': type(exc).__name__}))


def _sync_call(callback, page, step):
    import inspect
    result = callback(page, copy.deepcopy(step))
    if inspect.isawaitable(result):
        if inspect.iscoroutine(result):
            result.close()
        raise TypeError('sync hooks cannot return awaitables; use awalk')
    if result is False:
        raise AssertionError('hook returned False')


async def _async_call(callback, page, step):
    import inspect
    result = callback(page, copy.deepcopy(step))
    if inspect.isawaitable(result):
        result = await result
    if result is False:
        raise AssertionError('hook returned False')


def walk_steps(page, trail, registry, *, base='http://localhost:8080/', hash_routing=True,
               ready=None, check=None, navigate=None, timeout_ms=10000):
    """Yield verified steps. Caller owns Page/context/browser and can pause at yields.

    Navigation defaults to goto; a consumer can supply in-app clicking instead.
    ready/check hooks keep framework details outside this module.
    """
    steps = validate_steps(resolve(trail, registry, base=base, hash_routing=hash_routing), timeout_ms)
    yield from _walk_plan(page, steps, ready=ready, check=check, navigate=navigate, timeout_ms=timeout_ms)


def _walk_plan(page, steps, *, ready=None, check=None, navigate=None, timeout_ms=10000):
    done = []
    for index, step in enumerate(steps):
        hook_step = {**step, 'index': index}
        phase = 'navigate'
        try:
            if navigate is None:
                response = page.goto(step['url'], wait_until='domcontentloaded', timeout=timeout_ms)
                if response is not None and response.status >= 400:
                    raise RuntimeError('HTTP status ' + str(response.status))
            else:
                _sync_call(navigate, page, hook_step)
            phase = 'ready'
            spec = step.get('ready')
            if spec:
                target = page.locator(spec['selector'])
                target.wait_for(state='visible', timeout=timeout_ms)
                if 'text' in spec:
                    from playwright.sync_api import expect
                    expect(target).to_have_text(spec['text'], timeout=timeout_ms)
            if ready is not None:
                _sync_call(ready, page, hook_step)
            phase = 'verify'
            page.wait_for_url(lambda value: str(value) == step['url'], timeout=timeout_ms)
            if check is not None:
                _sync_call(check, page, hook_step)
            record = {**step, 'index': index, 'status': 'passed',
                      'verification': 'url+marker' if spec else 'url', 'observed_url': page.url}
            done.append(record)
        except Exception as exc:
            raise _failed(done, steps, index, phase, exc) from exc
        yield copy.deepcopy(record)


def walk(page, trail, registry, *, base='http://localhost:8080/', hash_routing=True,
         ready=None, check=None, navigate=None, timeout_ms=10000):
    """One-call sync execution on an existing Page; no browser ownership transfer."""
    steps = validate_steps(resolve(trail, registry, base=base, hash_routing=hash_routing), timeout_ms)
    done = list(_walk_plan(page, steps, ready=ready, check=check, navigate=navigate, timeout_ms=timeout_ms))
    return _receipt(done, len(steps))


async def awalk_steps(page, trail, registry, *, base='http://localhost:8080/', hash_routing=True,
                      ready=None, check=None, navigate=None, timeout_ms=10000):
    steps = validate_steps(resolve(trail, registry, base=base, hash_routing=hash_routing), timeout_ms)
    async for record in _awalk_plan(page, steps, ready=ready, check=check, navigate=navigate, timeout_ms=timeout_ms):
        yield record


async def _awalk_plan(page, steps, *, ready=None, check=None, navigate=None, timeout_ms=10000):
    done = []
    for index, step in enumerate(steps):
        hook_step = {**step, 'index': index}
        phase = 'navigate'
        try:
            if navigate is None:
                response = await page.goto(step['url'], wait_until='domcontentloaded', timeout=timeout_ms)
                if response is not None and response.status >= 400:
                    raise RuntimeError('HTTP status ' + str(response.status))
            else:
                await _async_call(navigate, page, hook_step)
            phase = 'ready'
            spec = step.get('ready')
            if spec:
                target = page.locator(spec['selector'])
                await target.wait_for(state='visible', timeout=timeout_ms)
                if 'text' in spec:
                    from playwright.async_api import expect
                    await expect(target).to_have_text(spec['text'], timeout=timeout_ms)
            if ready is not None:
                await _async_call(ready, page, hook_step)
            phase = 'verify'
            await page.wait_for_url(lambda value: str(value) == step['url'], timeout=timeout_ms)
            if check is not None:
                await _async_call(check, page, hook_step)
            record = {**step, 'index': index, 'status': 'passed',
                      'verification': 'url+marker' if spec else 'url', 'observed_url': page.url}
            done.append(record)
        except Exception as exc:
            raise _failed(done, steps, index, phase, exc) from exc
        yield copy.deepcopy(record)


async def awalk(page, trail, registry, *, base='http://localhost:8080/', hash_routing=True,
                ready=None, check=None, navigate=None, timeout_ms=10000):
    steps = validate_steps(resolve(trail, registry, base=base, hash_routing=hash_routing), timeout_ms)
    done = [record async for record in _awalk_plan(page, steps, ready=ready, check=check,
                                                 navigate=navigate, timeout_ms=timeout_ms)]
    return _receipt(done, len(steps))


def _close_result(result, close):
    try:
        close()
    except Exception as exc:
        if result['ok']:
            result['ok'] = False
            result['error'] = {'phase': 'cleanup', 'message': str(exc)[:2000]}
        else:
            result['cleanup_error'] = str(exc)[:2000]
    return result


def _worker(request):
    steps = validate_steps(request['steps'], request['timeout_ms'])
    engine = request['browser']
    if engine not in {'chromium', 'firefox', 'webkit'}:
        raise ValueError('invalid browser')
    if request['backend'] == 'python-async':
        import asyncio
        from playwright.async_api import async_playwright
        async def run():
            async with async_playwright() as pw:
                browser = await getattr(pw, engine).launch()
                try:
                    page = await browser.new_page()
                    done = [s async for s in _awalk_plan(page, steps, timeout_ms=request['timeout_ms'])]
                    result = _receipt(done, len(steps))
                except TrailExecutionError as exc:
                    result = exc.receipt
                except Exception as exc:
                    result = _receipt([], len(steps), {'phase': 'setup', 'message': str(exc)[:2000]})
                try:
                    await browser.close()
                except Exception as exc:
                    if result['ok']:
                        result['ok'] = False
                        result['error'] = {'phase': 'cleanup', 'message': str(exc)[:2000]}
                    else:
                        result['cleanup_error'] = str(exc)[:2000]
                return result
        return asyncio.run(run())
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = getattr(pw, engine).launch()
        try:
            page = browser.new_page()
            done = list(_walk_plan(page, steps, timeout_ms=request['timeout_ms']))
            result = _receipt(done, len(steps))
        except TrailExecutionError as exc:
            result = exc.receipt
        except Exception as exc:
            result = _receipt([], len(steps), {'phase': 'setup', 'message': str(exc)[:2000]})
        return _close_result(result, browser.close)


def _worker_main():
    request = None
    try:
        raw = sys.stdin.read(2 * 1024 * 1024 + 1)
        if len(raw.encode('utf-8')) > 2 * 1024 * 1024:
            raise ValueError('execution plan exceeds 2 MiB')
        request = json.loads(raw)
        result = _worker(request)
    except TrailExecutionError as exc:
        result = exc.receipt
    except Exception as exc:
        result = _receipt([], len(request.get('steps', [])) if isinstance(request, dict) else 0,
                          {'phase': 'setup', 'message': str(exc)[:2000], 'type': type(exc).__name__})
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['ok'] else 1


def run_plan(steps, *, backend='python', browser='chromium', timeout_ms=10000, run_timeout=120, node_cwd=None):
    """Bounded subprocess bridge. Python -S planning needs no browser dependency.

    The optional runtime runs once with site packages enabled. A Node backend
    receives the same plan and uses the consumer's existing Playwright install.
    """
    import subprocess
    from pathlib import Path
    validate_steps(steps, timeout_ms)
    if backend not in {'python', 'python-async', 'node'} or browser not in {'chromium', 'firefox', 'webkit'}:
        raise ValueError('invalid backend or browser')
    if type(run_timeout) is not int or not 1 <= run_timeout <= 3600:
        raise ValueError('run_timeout must be 1..3600 seconds')
    request = {'steps': steps, 'backend': backend, 'browser': browser, 'timeout_ms': timeout_ms}
    if backend == 'node':
        asset = Path(__file__).with_name('route_trail_node.mjs')
        if not asset.exists():
            asset = Path(sys.prefix) / 'share/browser-test-kit/route_trail_node.mjs'
        if not asset.exists():
            import site
            asset = Path(site.getuserbase()) / 'share/browser-test-kit/route_trail_node.mjs'
        if not asset.exists():
            raise ValueError('Node helper is unavailable; reinstall the package including its shared asset')
        cmd = ['node', str(asset.resolve())]
    else:
        cmd = [sys.executable, str(Path(__file__).resolve()), '--worker']
    try:
        child = subprocess.run(cmd, input=json.dumps(request, ensure_ascii=False), capture_output=True,
                               text=True, encoding='utf-8', errors='replace', timeout=run_timeout, check=False,
                               cwd=node_cwd if backend == 'node' else None)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _receipt([], len(steps), {'phase': 'process', 'message': str(exc)[:2000], 'type': type(exc).__name__})
    try:
        result = json.loads(child.stdout)
        if (not isinstance(result, dict) or result.get('schema') != 'route-trail-run/1'
                or type(result.get('ok')) is not bool or result.get('total') != len(steps)
                or not isinstance(result.get('steps'), list)
                or type(result.get('completed')) is not int
                or result.get('completed') != len(result['steps'])
                or result['completed'] > len(steps)
                or (not result['ok'] and not isinstance(result.get('error'), dict))
                or (result['ok'] and result['completed'] != len(steps))):
            raise ValueError('invalid runtime receipt')
        for index, record in enumerate(result['steps']):
            if (not isinstance(record, dict) or record.get('index') != index
                    or record.get('status') != 'passed'
                    or any(record.get(k) != steps[index][k] for k in ('emoji', 'route', 'url'))
                    or record.get('observed_url') != steps[index]['url']):
                raise ValueError('receipt steps do not match the requested plan')
    except (ValueError, TypeError):
        return _receipt([], len(steps), {'phase': 'protocol', 'message': 'runtime did not return a valid receipt: ' + child.stderr[-1000:]})
    if child.returncode != 0 and result['ok']:
        result['ok'] = False
        result['error'] = {'phase': 'process', 'message': 'runtime exited ' + str(child.returncode)}
    result.update({'backend': backend, 'browser': browser, 'exit_code': child.returncode})
    return result


def main(argv=None) -> int:
    if argv == ['--worker'] or argv is None and sys.argv[1:] == ['--worker']:
        return _worker_main()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("trail", help="e.g. '🏠 → 🗺 ↔ 🌍' (or ASCII -> and <->)")
    parser.add_argument("--registry", required=True, help="JSON registry or a source file with '/route': 'emoji' pairs")
    parser.add_argument("--map", dest="map_name", help="read only this named map (e.g. pageEmojis)")
    parser.add_argument("--base", default="http://localhost:8080/")
    parser.add_argument("--path-routing", action="store_true", help="BASE/route instead of BASE#route")
    parser.add_argument("--json", action="store_true", help="emoji, route and url per step")
    parser.add_argument('--run', action='store_true', help='execute the whole trail once in an optional Playwright runtime')
    parser.add_argument('--backend', choices=('python', 'python-async', 'node'), default='python')
    parser.add_argument('--browser', choices=('chromium', 'firefox', 'webkit'), default='chromium')
    parser.add_argument('--timeout-ms', type=int, default=10000, help='per navigation/wait timeout')
    parser.add_argument('--node-cwd', help='consumer directory containing the existing Node Playwright install')
    parser.add_argument('--run-timeout', type=int, default=120, help='overall subprocess deadline in seconds')
    parser.add_argument('--ready-selector', help='default visible marker for pages without a ready record')
    args = parser.parse_args(argv)
    try:
        with open(args.registry, encoding="utf-8") as handle:
            registry = load_registry(handle.read(), name=args.registry, map_name=args.map_name)
        rows = resolve(args.trail, registry, base=args.base, hash_routing=not args.path_routing)
        if args.run:
            if args.ready_selector:
                for row in rows:
                    if not row.get('ready'):
                        row['ready'] = _ready_spec({'selector': args.ready_selector})
            result = run_plan(rows, backend=args.backend, browser=args.browser,
                              timeout_ms=args.timeout_ms, run_timeout=args.run_timeout, node_cwd=args.node_cwd)
            if args.json:
                print(json.dumps(result, ensure_ascii=False))
            else:
                print(('PASS' if result['ok'] else 'FAIL') + f" {result['completed']}/{result['total']} {args.backend}/{args.browser}")
                if not result['ok']:
                    print(json.dumps(result['error'], ensure_ascii=False), file=sys.stderr)
            return 0 if result['ok'] else 1
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(rows, ensure_ascii=False) if args.json else "\n".join(r["url"] for r in rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
