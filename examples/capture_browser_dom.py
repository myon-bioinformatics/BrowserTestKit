#!/usr/bin/env python3
"""Open one URL in optional Playwright Chromium and save a selected DOM snapshot.

Exit 0: captured (not site/content validation); 1: ambiguous selector; 2: error.
"""
import argparse, hashlib, json, sys
from datetime import datetime, timezone; from pathlib import Path; from urllib.parse import urlsplit



_DEFAULT_PORTS = {'http': 80, 'https': 443}


def same_origin_key(url):
    """(scheme, host, effective port) for origin comparison; None when the URL has no host.

    Host names compare case-insensitively and an explicit default port equals the
    omitted one, so http://LOCALHOST:80/ and http://localhost/ are one origin.
    """
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    try:
        port = parts.port
    except ValueError:
        return None
    if not parts.hostname:
        return None
    return (scheme, parts.hostname.lower(), port if port is not None else _DEFAULT_PORTS.get(scheme))

def capture(page, url, selector, timeout_ms, max_bytes, *, aria_snapshot=False):
    response = page.goto(url, wait_until='domcontentloaded', timeout=timeout_ms)
    if response is not None and response.status >= 400:
        raise ValueError('HTTP status ' + str(response.status))
    target = page.locator(selector)
    target.first.wait_for(state='attached', timeout=timeout_ms)
    if target.count() != 1:
        return None
    data = target.evaluate('e => ({html:e.outerHTML, text:e.textContent, visible:e.innerText ?? null})', timeout=timeout_ms)
    if aria_snapshot:
        # The browser's own role/name computation, for comparing static accessible-name readers.
        data['aria_snapshot'] = target.aria_snapshot(timeout=timeout_ms)
    encoded = json.dumps(data, ensure_ascii=False).encode('utf-8')
    if len(encoded) > max_bytes:
        raise ValueError('captured data exceeds byte limit')
    return {'schema':'btk-browser-dom/1', 'requested_url':url, 'url':page.url,
            'title':page.title(), 'selector':selector,
            'captured_at':datetime.now(timezone.utc).isoformat(),
            'source_kind':'dom', 'html_sha256':hashlib.sha256(data['html'].encode()).hexdigest(),
            'content_verified':False, **data}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url')
    parser.add_argument('--selector', default='body')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--timeout-ms', type=int, default=30000)
    parser.add_argument('--max-bytes', type=int, default=2000000)
    parser.add_argument('--headed', action='store_true')
    parser.add_argument('--aria-snapshot', action='store_true',
                        help="also store the selected element's Playwright aria snapshot (roles and accessible names)")
    parser.add_argument('--same-origin-only', action='store_true',
                        help='abort requests to other origins and list them in blocked_requests')
    args = parser.parse_args(argv)
    parsed = urlsplit(args.url)
    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:
        parser.error('HTTP(S) URL without embedded credentials required')
    if args.timeout_ms < 1 or args.max_bytes < 1:
        parser.error('limits must be positive')
    try:
        # Reject known-unusable destinations before importing/launching a browser.
        # is_symlink also catches dangling links, which exists() does not.
        if args.output.exists() or args.output.is_symlink():
            raise FileExistsError('output already exists: ' + str(args.output))
        if not args.output.parent.is_dir():
            raise NotADirectoryError('output parent is not a directory: ' + str(args.output.parent))
        from playwright.sync_api import sync_playwright
        with sync_playwright() as runtime:
            browser = runtime.chromium.launch(headless=not args.headed, timeout=args.timeout_ms)
            try:
                context = browser.new_context(accept_downloads=False)
                page = context.new_page()
                page.set_default_timeout(args.timeout_ms)
                blocked = []
                if args.same_origin_only:
                    origin = same_origin_key(args.url)
                    def route(request_route):
                        if same_origin_key(request_route.request.url) == origin:
                            request_route.continue_()
                        else:
                            blocked.append(request_route.request.url)
                            request_route.abort()
                    page.route('**/*', route)
                result = capture(page,args.url,args.selector,args.timeout_ms,args.max_bytes,
                                 aria_snapshot=args.aria_snapshot)
                if result is not None:
                    result['browser_version'] = browser.version
                    if args.same_origin_only:
                        result['blocked_requests'] = sorted(set(blocked))
            finally:
                browser.close()
        if result is None:
            print(json.dumps({'error':'ambiguous_selector'}),file=sys.stderr)
            return 1
        # Exclusive create: never silently overwrite earlier evidence.
        with args.output.open('x',encoding='utf-8') as stream:
            json.dump(result,stream,ensure_ascii=False,indent=2)
            stream.write('\n')
        print(json.dumps({'status':'captured','output':str(args.output),'content_verified':False}))
        return 0
    except ImportError:
        print('Install optional playwright and Chromium; see README.',file=sys.stderr)
        return 2
    except Exception as error:
        print(json.dumps({'error':str(error)}),file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
