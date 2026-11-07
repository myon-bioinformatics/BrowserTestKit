"""One small, self-contained page for reviewing a published-app run: every page x environment.

The full ``platform-app-matrix`` artifact (screenshots, tiles, viewer) can be too large to
download and read in one go. This digest keeps what a review needs on one page: a thumbnail
per page and environment (embedded as data URIs), its pass/fail, the deployed build and the
failure reasons. Thumbnails start at 64x128 px and are halved in each direction (a quarter
of the pixels) until the page fits ``--max-bytes``; the chosen size is printed on the page.
It also writes a short Markdown summary for a pull-request comment.

Uses the same trusted, pinned Ironmate checkout as app_comparison.py for PNG work.

    python -S scripts/app_digest.py app-results --ironmate .ironmate --runners-json '["ubuntu-24.04"]' \
        --out app-digest [--run-url URL] [--max-bytes 1000000]
"""
from __future__ import annotations

import argparse, base64, html, importlib, json, sys
from pathlib import Path

import app_comparison as comparison
import platform_probe as probe

SCHEMA = 'btk-app-digest/1'
START_TILE = (64, 128)
MIN_TILE = (16, 32)


def _cells(records):
    cells = {}
    for record in records:
        target, env = record.get('target') or {}, record['env']
        if not target.get('adapter'):
            continue
        key = (target['adapter'], f"{env['os']}-{env.get('channel') or env['browser']}-{env['profile']}")
        if key in cells:
            raise ValueError(f'ambiguous observations for {key}')
        failures = list(record['layout']['failed'])
        if not record['click']['ok']:
            failures.append('initial_click')
        for sample in record.get('samples', []):
            failures += [f"{sample['width']}px:{why}" for why in sample['layout']['failed']]
            if not sample['click']['ok']:
                failures.append(f"{sample['width']}px:click")
        cells[key] = {'verdict': record['verdict'], 'failures': failures[:6],
                      'build': ((target.get('provenance') or {}).get('value'))}
    return cells


def _page(pages, envs, cells, tiles, encode, tile, run_url):
    head = ''.join(f'<th>{html.escape(e)}</th>' for e in envs)
    rows = []
    for page in pages:
        row = [f'<th>{html.escape(page)}</th>']
        for env in envs:
            cell, image = cells.get((page, env)), tiles.get((page, env))
            if cell is None and image is None:
                row.append('<td class="missing">not run</td>')
                continue
            img = ''
            if image is not None:
                data = base64.b64encode(encode(*image)).decode('ascii')
                img = f'<img src="data:image/png;base64,{data}" width="{tile[0]}" height="{tile[1]}" alt="">'
            verdict = cell['verdict'] if cell else 'no record'
            why = '<br>'.join(html.escape(f) for f in (cell or {}).get('failures', []))
            build = (cell or {}).get('build')
            build_note = f'<br><small>build {html.escape(build[:12])}</small>' if build else ''
            row.append(f'<td class="{html.escape(verdict)}">{img}<br>{html.escape(verdict)}'
                       + build_note + (f'<br><small>{why}</small>' if why else '') + '</td>')
        rows.append('<tr>' + ''.join(row) + '</tr>')
    builds = sorted({c['build'][:12] for c in cells.values() if c.get('build')})
    link = f' · <a href="{html.escape(run_url, quote=True)}">run</a>' if run_url else ''
    return ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Published app digest</title><style>body{font:13px system-ui,sans-serif;margin:8px}'
            'table{border-collapse:collapse}th,td{border:1px solid #ccc;padding:4px;vertical-align:top;text-align:center}'
            'td.fail{background:#ffebe9}td.missing{color:#666}small{color:#555}</style>'
            f'<p>Thumbnails {tile[0]}x{tile[1]} px · builds {html.escape(", ".join(builds) or "unknown")}{link}. '
            'Mobile profiles are emulation; thumbnails are for orientation, not pixel review.</p>'
            f'<table><tr><th></th>{head}</tr>{"".join(rows)}</table>\n')


def summary_markdown(pages, envs, cells, run_url=None):
    """A few lines for a PR comment: per page, passing environments, and each failure."""
    lines = ['<!-- platform-apps-digest -->', '### Published app digest', '']
    total = sum(1 for p in pages for e in envs)
    passed = sum(1 for p in pages for e in envs if cells.get((p, e), {}).get('verdict') == 'pass')
    builds = sorted({c['build'][:12] for c in cells.values() if c.get('build')})
    lines.append(f"{passed}/{total} page x environment checks passed; build {', '.join(builds) or 'unknown'}."
                 + (f' [Run]({run_url})' if run_url else ''))
    lines += ['', '| Page | Passed | Needs a look |', '| --- | --- | --- |']
    for page in pages:
        ok = [e for e in envs if cells.get((page, e), {}).get('verdict') == 'pass']
        bad = [f"{e} ({', '.join(cells[(page, e)]['failures'][:2]) or 'fail'})" if (page, e) in cells else f'{e} (not run)'
               for e in envs if e not in ok]
        lines.append(f"| {page} | {len(ok)}/{len(envs)} | {'; '.join(bad) or '-'} |")
    return '\n'.join(lines) + '\n'


def generate(root, out, *, ironmate, runners, adapter_dir, shot='width-384', max_bytes=1_000_000, run_url=None, pages=None, environments=('*',)):
    if type(max_bytes) is not int or not 50_000 <= max_bytes <= 20_000_000:
        raise ValueError('max_bytes must be 50000..20000000')
    sys.path.insert(0, str(Path(ironmate).resolve()))
    matrix = importlib.import_module('browser_matrix')
    pages, envs = comparison.select_targets(adapter_dir, runners, pages=pages, environments=environments)
    records = comparison.select_records(probe.load_records([str(root)]), pages, envs)
    cells = _cells(records)
    shots = matrix.find_shots(root, shot, pages=pages, environments=envs)
    tile = START_TILE
    while True:
        tiles = matrix.build(shots, tile_w=tile[0], tile_h=tile[1], expected_pages=pages, expected_envs=envs)[2] if shots else {}
        page = _page(pages, envs, cells, tiles, matrix.encode_png, tile, run_url)
        if len(page.encode('utf-8')) <= max_bytes or tile == MIN_TILE:
            break
        tile = (max(MIN_TILE[0], tile[0] // 2), max(MIN_TILE[1], tile[1] // 2))  # a quarter of the pixels
    out = Path(out)
    if out.is_symlink() or (out.exists() and any(out.iterdir())):
        raise ValueError(f'{out} must be a new or empty directory')
    out.mkdir(parents=True, exist_ok=True)
    (out / 'digest.html').write_text(page, encoding='utf-8')
    (out / 'summary.md').write_text(summary_markdown(pages, envs, cells, run_url), encoding='utf-8')
    info = {'schema': SCHEMA, 'tile': list(tile), 'bytes': len(page.encode('utf-8')), 'pages': len(pages),
            'envs': len(envs), 'passed': sum(1 for c in cells.values() if c['verdict'] == 'pass'),
            'fits': len(page.encode('utf-8')) <= max_bytes}
    (out / 'digest.json').write_text(json.dumps(info) + '\n', encoding='utf-8')
    return info


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('root', type=Path)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--ironmate', type=Path)
    parser.add_argument('--runners-json', required=True)
    parser.add_argument('--adapters', type=Path, default=probe.ROOT / 'adapters')
    parser.add_argument('--shot', default='width-384')
    parser.add_argument('--max-bytes', type=int, default=1_000_000)
    parser.add_argument('--run-url')
    comparison.selection_arguments(parser)
    args = parser.parse_args(argv)
    try:
        if comparison.selected_plan(args, parser):
            return 0
        info = generate(args.root, args.out, ironmate=args.ironmate, runners=json.loads(args.runners_json),
                        adapter_dir=args.adapters, shot=args.shot, max_bytes=args.max_bytes, run_url=args.run_url, pages=args.page, environments=args.env or ('*',))
    except (OSError, ValueError, ImportError, TypeError, KeyError) as error:
        print(f'error: {error}', file=sys.stderr)
        return 2
    print(json.dumps(info))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
