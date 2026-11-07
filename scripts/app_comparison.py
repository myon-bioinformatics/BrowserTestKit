"""Join published-app screenshots, planned coverage and measured observations.

Requires an explicit, trusted Ironmate checkout; does not download dependencies.
Pixel differences are diagnostic only and never override platform-probe verdicts.
"""
from __future__ import annotations

import argparse, fnmatch, html, importlib, json, sys
from pathlib import Path

import platform_probe as probe

LANES = [('chromium', 'desktop'), ('chromium', 'pixel'), ('firefox', 'desktop'),
         ('webkit', 'desktop'), ('webkit', 'iphone')]
OS_NAMES = {'ubuntu-24.04': 'Linux', 'macos-latest': 'macOS', 'windows-latest': 'Windows'}
GUIDANCE = ('MAE/RMSE describe raster differences, not UI defects. OS fonts, browser rendering, DPR, '
            'viewport height, animations and capture time can differ. Cropped content is not evaluated. '
            'Use the independent layout/click observations and inspect original screenshots. '
            'Mobile profiles are emulation; Playwright WebKit is not Safari.')


def planned_envs(runners):
    if not runners or any(r not in OS_NAMES for r in runners):
        raise ValueError('expected known runner OS names')
    return sorted(f'{OS_NAMES[r]}-{browser}-{profile}' for r in runners for browser, profile in LANES)



def select_targets(adapter_dir, runners, *, pages=None, environments=('*',)):
    """Select adapter filename stems and planned environments; OR within each group."""
    pages = ('flutter-*',) if pages is None else pages
    if isinstance(pages, str) or isinstance(environments, str):
        raise TypeError('selection rules must be sequences')
    pages, environments = tuple(pages), tuple(environments)
    if any(not isinstance(rule, str) or not rule for rule in pages + environments):
        raise ValueError('selection rules must be nonempty strings')
    directory = Path(adapter_dir)
    if not directory.is_dir():
        raise ValueError('adapter directory must exist')
    selected = []
    for path in sorted(directory.glob('*.json')):
        if path.is_symlink() or not path.is_file() or not any(fnmatch.fnmatchcase(path.stem, rule) for rule in pages):
            continue
        identifier = probe.adapters.load_adapter(path)['id']
        if identifier in selected:
            raise ValueError(f'duplicate selected adapter id: {identifier}')
        selected.append(identifier)
    envs = sorted({env for env in planned_envs(runners)
                   if any(fnmatch.fnmatchcase(env, rule) for rule in environments)})
    if not selected or not envs:
        raise ValueError('no selected adapters or planned environments')
    return selected, envs


def select_records(records, pages, envs):
    return [r for r in records if (r.get('target') or {}).get('adapter') in pages
            and f"{r['env']['os']}-{r['env'].get('channel') or r['env']['browser']}-{r['env']['profile']}" in envs]


def selection_arguments(parser):
    parser.add_argument('--page', action='append', help='adapter filename-stem glob; repeat for OR (default flutter-*)')
    parser.add_argument('--env', action='append', help='planned environment glob; repeat for OR')
    parser.add_argument('--list', action='store_true', help='list selected plan without loading records or images')


def selected_plan(args, parser):
    if args.list and (args.out is not None or args.ironmate is not None):
        parser.error('--list cannot be combined with --out or --ironmate')
    if not args.list and (args.out is None or args.ironmate is None):
        parser.error('--out and --ironmate are required unless --list is used')
    if args.list:
        if not args.root.is_dir():
            raise ValueError('results root must be a directory')
        pages, envs = select_targets(args.adapters, json.loads(args.runners_json),
                                    pages=args.page, environments=args.env or ('*',))
        print(json.dumps({'pages': pages, 'envs': envs}))
        return True
    return False


def observation(record, shot):
    samples = [s for s in record.get('samples', []) if shot == f"width-{s['width']}"]
    sample = samples[0] if samples else (record if shot == 'initial' else None)
    return {'verdict': record['verdict'], 'shot_layout': sample.get('layout') if sample else None,
            'shot_click': sample.get('click') if sample else None,
            'device_pixel_ratio': record['facts']['viewport']['device_pixel_ratio'],
            'initial_css_viewport': record['facts']['viewport'],
            'build': record.get('target', {}).get('provenance', {}).get('value'),
            'provenance': record.get('target', {}).get('provenance'),
            'browser_version': record['env']['browser_version'], 'observed_at': record.get('observed_at'),
            'url': record['target'].get('resolved_url', record['target']['url'])}


def annotate(report, records, shot):
    observations = {}
    for record in records:
        env = record['env']
        key = (record['target']['adapter'], f"{env['os']}-{env.get('channel') or env['browser']}-{env['profile']}")
        if key in observations:
            raise ValueError(f'ambiguous observations for {key}')
        observations[key] = observation(record, shot)
    report['guidance'] = GUIDANCE
    report['shot'] = shot
    report['observed_builds'] = sorted({o['build'] for o in observations.values() if o['build']})
    for cell in report['cells']:
        obs = observations.get((cell['page'], cell['env']))
        base = observations.get((cell['page'], report['reference']))
        cell['observation'] = obs
        cell['build_comparison'] = ('unknown' if not obs or not base or not obs['build'] or not base['build']
                                    else 'same' if obs['build'] == base['build'] else 'different')
    report['coverage'] = {'expected': len(report['pages']) * len(report['envs']),
                          'captured': len(report['cells']), 'missing': len(report['missing'])}
    return report


def evidence_html(report):
    esc = lambda x: html.escape(str(x))
    rows = []
    for cell in report['cells']:
        obs = cell['observation'] or {}
        layout, click = obs.get('shot_layout'), obs.get('shot_click')
        values = [cell['page'], cell['env'], obs.get('device_pixel_ratio'),
                  f"{cell['width']}x{cell['height']}", cell['fit'], obs.get('verdict', 'unobserved'),
                  None if layout is None else layout['ok'], None if click is None else click['ok'],
                  obs.get('build'), cell['build_comparison']]
        rows.append('<tr>' + ''.join(f'<td>{esc(v)}</td>' for v in values) + '</tr>')
    return ('<h2>Capture evidence (independent of pixel difference)</h2><p>' + esc(GUIDANCE) + '</p>'
            '<p><a href="../index.html">Platform facts and layout/click details</a> · '
            '<a href="report.json">JSON report</a> · <a href="sheet.png">PNG sheet</a></p>'
            '<p>Coverage: ' + esc(report['coverage']) + '</p><table><tr>'
            + ''.join(f'<th>{h}</th>' for h in ['Page', 'Environment', 'DPR', 'Raster size', 'Tile crop/pad',
                                             'Observation', 'Shot layout OK', 'Shot click OK', 'Build', 'Reference build'])
            + '</tr>' + ''.join(rows) + '</table>\n')


def generate(root, out, *, ironmate, runners, adapter_dir, shot='width-384', pages=None, environments=('*',)):
    # Explicit checkout is trusted executable code, like the workflow's pinned tools.
    sys.path.insert(0, str(Path(ironmate).resolve()))
    matrix = importlib.import_module('browser_matrix')
    pages, envs = select_targets(adapter_dir, runners, pages=pages, environments=environments)
    records = select_records(probe.load_records([str(root)]), pages, envs)
    shots = matrix.find_shots(root, shot, pages=pages, environments=envs)
    report, sheet, tiles = matrix.build(shots, expected_pages=pages, expected_envs=envs)
    annotate(report, records, shot)
    matrix.write(out, report, sheet, tiles)  # validates output before creating anything
    index = Path(out) / 'index.html'
    with index.open('a', encoding='utf-8') as stream:
        stream.write(evidence_html(report))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('root', type=Path)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--ironmate', type=Path)
    parser.add_argument('--runners-json', required=True, help='the plan job OS JSON, including failed lanes')
    parser.add_argument('--adapters', type=Path, default=probe.ROOT / 'adapters')
    parser.add_argument('--shot', default='width-384')
    selection_arguments(parser)
    args = parser.parse_args(argv)
    try:
        if selected_plan(args, parser):
            return 0
        report = generate(args.root, args.out, ironmate=args.ironmate, runners=json.loads(args.runners_json),
                          adapter_dir=args.adapters, shot=args.shot, pages=args.page, environments=args.env or ('*',))
    except (OSError, ValueError, ImportError, TypeError, KeyError) as error:
        print(f'error: {error}', file=sys.stderr)
        return 2
    print(json.dumps(report['coverage']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
