"""app_digest: one small page per run; thumbnails shrink to a quarter until it fits."""
import importlib.util, json, sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
spec = importlib.util.spec_from_file_location('app_digest', ROOT / 'tools/app_digest.py')
digest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(digest)

FAKE_IRONMATE = '''
from pathlib import Path
def find_shots(root, shot, *, pages=('*',), environments=('*',)):
    return {(p.parent.parent.name, p.parent.name): p for p in Path(root).rglob(shot + '.png')}
def build(shots, *, tile_w, tile_h, expected_pages=(), expected_envs=()):
    return None, None, {key: (tile_w, tile_h, b'') for key in shots}
def encode_png(w, h, rgba):
    return bytes(w * h * 4)  # size grows with pixels, so halving the tile shrinks the page
'''


def record(adapter, os_name, verdict='pass', failed=()):
    return {'schema': 'btk-platform-facts/1', 'verdict': verdict,
            'env': {'id': f'{os_name}-chromium-desktop-headless', 'os': os_name, 'browser': 'chromium',
                    'profile': 'desktop'},
            'target': {'adapter': adapter, 'key': f'{adapter}-{os_name}', 'provenance': {'value': 'c' * 40}},
            'layout': {'ok': not failed, 'failed': list(failed)}, 'click': {'ok': True}, 'samples': []}


@pytest.fixture
def run(tmp_path):
    ironmate = tmp_path / 'ironmate'
    ironmate.mkdir()
    (ironmate / 'browser_matrix.py').write_text(FAKE_IRONMATE, encoding='utf-8')
    sys.modules.pop('browser_matrix', None)
    adapters = tmp_path / 'adapters'
    adapters.mkdir()
    for name in ('flutter-home', 'flutter-hub'):
        (adapters / f'{name}.json').write_text((ROOT / 'adapters' / f'{name}.json').read_text(encoding='utf-8'),
                                              encoding='utf-8')
    results = tmp_path / 'app-results'
    for n, (adapter, verdict, failed) in enumerate([('flutter-home', 'pass', ()), ('flutter-hub', 'fail', ('overlap:#a:#b',))]):
        (results / 'x').mkdir(parents=True, exist_ok=True)
        (results / 'x' / f'{n}.json').write_text(json.dumps(record(adapter, 'Linux', verdict, failed)), encoding='utf-8')
        shot = results / 'x' / adapter / 'Linux-chromium-desktop' / 'width-384.png'
        shot.parent.mkdir(parents=True)
        shot.write_bytes(b'png')
    yield results, ironmate, adapters
    sys.modules.pop('browser_matrix', None)
    sys.path[:] = [p for p in sys.path if p != str(ironmate.resolve())]


def test_digest_fits_one_page_and_quarters_thumbnails_until_it_fits(run, tmp_path):
    results, ironmate, adapters = run
    big = digest.generate(results, tmp_path / 'big', ironmate=ironmate, runners=['ubuntu-24.04'], adapter_dir=adapters,
                          max_bytes=1_000_000, run_url='https://example.test/run')
    assert big['tile'] == [64, 128] and big['fits'] and big['pages'] == 2 and big['envs'] == 5 and big['passed'] == 1
    small = digest.generate(results, tmp_path / 'small', ironmate=ironmate, runners=['ubuntu-24.04'],
                            adapter_dir=adapters, max_bytes=50_000)
    assert small['tile'] == [32, 64] and small['fits'] and small['bytes'] <= 50_000  # a quarter of the pixels
    page = (tmp_path / 'big' / 'digest.html').read_text(encoding='utf-8')
    assert page.count('data:image/png;base64,') == 2 and 'overlap:#a:#b' in page and 'not run' in page
    assert 'builds cccccccccccc' in page and 'href="https://example.test/run"' in page
    md = (tmp_path / 'big' / 'summary.md').read_text(encoding='utf-8')
    assert md.startswith('<!-- platform-apps-digest -->') and '1/10 page x environment checks passed' in md
    assert '| flutter-hub | 0/5 | Linux-chromium-desktop (overlap:#a:#b);' in md
    with pytest.raises(ValueError, match='new or empty'):
        digest.generate(results, tmp_path / 'big', ironmate=ironmate, runners=['ubuntu-24.04'], adapter_dir=adapters)
    with pytest.raises(ValueError, match='max_bytes'):
        digest.generate(results, tmp_path / 'z', ironmate=ironmate, runners=['ubuntu-24.04'], adapter_dir=adapters,
                        max_bytes=10)


def test_digest_counts_only_planned_cells_and_reports_each_build(run, tmp_path):
    results, ironmate, adapters = run
    (results / 'x' / 'outside.json').write_text(json.dumps(record('unplanned-page', 'OtherOS')), encoding='utf-8')
    info = digest.generate(results, tmp_path / 'out', ironmate=ironmate, runners=['ubuntu-24.04'], adapter_dir=adapters)
    assert info['passed'] == 1
    page = (tmp_path / 'out' / 'digest.html').read_text(encoding='utf-8')
    assert 'build cccccccccccc' in page
    assert '1/10 page x environment checks passed' in (tmp_path / 'out' / 'summary.md').read_text()


def test_digest_rejects_ambiguous_records():
    with pytest.raises(ValueError, match='ambiguous'):
        digest._cells([record('flutter-home', 'Linux', 'fail'), record('flutter-home', 'Linux', 'pass')])
