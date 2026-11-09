"""Reinspect a saved BTK DOM capture with an explicitly supplied BlueProbe min.

Offline only. The min file is caller-selected trusted Python code, not a sandbox.
No bundle is downloaded, vendored or imported until the snapshot is verified.
"""
import argparse, hashlib, importlib.util, json, re, sys
from pathlib import Path; from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from page_text import find_elements


def read_snapshot(path):
    raw = Path(path).read_bytes()
    if len(raw) > 2_000_000:
        raise ValueError('snapshot exceeds two million bytes')
    record = json.loads(raw)
    if (not isinstance(record, dict) or record.get('schema') != 'btk-browser-dom/1' or record.get('source_kind') != 'dom'
            or not isinstance(record.get('html'), str) or not isinstance(record.get('url'), str)
            or not record['url'] or hashlib.sha256(record['html'].encode()).hexdigest() != record.get('html_sha256')):
        raise ValueError('expected a hash-verified btk-browser-dom/1 capture')
    return record, hashlib.sha256(raw).hexdigest()


def load_min(path):
    path = Path(path).resolve()
    data = path.read_bytes()
    text = data.decode('utf-8')
    marker = re.search(r'(?m)^# content-sha256: ([0-9a-f]{64})$', text)
    if not marker:
        raise ValueError('missing minpy content digest')
    blanked = text[:marker.start(1)] + '0' * 64 + text[marker.end(1):]
    if hashlib.sha256(blanked.encode()).hexdigest() != marker[1]:
        raise ValueError('minpy content digest mismatch')
    digest = hashlib.sha256(data).hexdigest()
    name = '_btk_blueprobe_min_' + digest
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(name, None)
            raise
    module = sys.modules[name]
    if not all(callable(getattr(module, key, None)) for key in ('new_report', 'inspect')):
        raise ValueError('the supplied min file lacks the BlueProbe report/inspect API')
    return module, digest


def replay(snapshot, min_path, query):
    record, snapshot_sha = read_snapshot(snapshot)
    blueprobe, bundle_sha = load_min(min_path)
    unknown = []
    def parse(html, url):
        report = blueprobe.new_report()
        matches = find_elements(html, query, url=url, limit=1000)
        for index, match in enumerate(matches):
            report['records'].append({'key': str(index), 'raw': match})
        if not matches:
            item = {'raw': query, 'reason': 'no_static_match; live presence is unknown'}
            report['unknown'].append(item)
            unknown.append(item)
        if len(matches) == 1000:
            item = {'raw': query, 'reason': 'static hit limit reached; remaining coverage unknown'}
            report['unknown'].append(item)
            unknown.append(item)
        return report
    hits = []
    rows = blueprobe.inspect(SimpleNamespace(parse=parse),
        [(snapshot_sha, record['url'], 'saved-dom', record['html'])], records_out=hits)
    return {'schema': 'btk-min-dom-replay/1', 'scope': 'historical-static-dom',
            'snapshot_sha256': snapshot_sha, 'blueprobe_min_sha256': bundle_sha,
            'html_sha256': record['html_sha256'], 'url': record['url'],
            'captured_at': record.get('captured_at'), 'query': query,
            'live_page_executed': False, 'operability_verified': False,
            'groups': rows, 'hits': hits, 'unknown': unknown}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('--blueprobe-min', type=Path, required=True)
    parser.add_argument('--find', default='')
    args = parser.parse_args(argv)
    try:
        result = replay(args.snapshot, args.blueprobe_min, args.find)
    except (OSError, ValueError, ImportError) as error:
        parser.exit(2, str(error) + '\n')
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
