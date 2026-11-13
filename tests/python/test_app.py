"""App comparison, digest and selection, and the offline Aoi replay preflight."""

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))

import app_comparison as comparison
import app_digest as digest


# ═══ · app comparison ════════════════════════════════════════════════════════════════════════════════════
# Offline coverage and evidence interpretation; real codecs tested via explicit checkout.

spec = importlib.util.spec_from_file_location('app_comparison', ROOT / 'tools/app_comparison.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


def record(env='Linux', build='a', verdict='pass'):
    return {'env': {'os': env, 'browser': 'chromium', 'profile': 'desktop', 'browser_version': '1'},
            'target': {'adapter': 'p', 'url': 'https://example.test/', 'provenance': {'value': build}},
            'facts': {'viewport': {'device_pixel_ratio': 3 if env == 'macOS' else 1}},
            'layout': {'ok': True}, 'click': {'ok': True}, 'verdict': verdict,
            'samples': [{'width': 384, 'layout': {'ok': verdict == 'pass'}, 'click': {'ok': True}}]}


def report():
    return {'pages': ['p'], 'envs': ['Linux-chromium-desktop', 'macOS-chromium-desktop'],
            'reference': 'Linux-chromium-desktop', 'missing': [],
            'cells': [{'page': 'p', 'env': e, 'width': 384, 'height': 800, 'fit': {},
                       'difference': {'rgb_mae': 0}} for e in ['Linux-chromium-desktop', 'macOS-chromium-desktop']]}


def test_os_plan_keeps_missing_lanes_instead_of_inventing_observations():
    assert len(app.planned_envs(list(app.OS_NAMES))) == 15
    with pytest.raises(ValueError):
        app.planned_envs(['unknown'])
    r = app.annotate(report(), [record()], 'width-384')
    assert r['cells'][1]['observation'] is None
    assert r['cells'][1]['build_comparison'] == 'unknown'


def test_zero_pixel_difference_does_not_override_failed_click_or_layout_observation():
    r = app.annotate(report(), [record(), record('macOS', 'b', 'fail')], 'width-384')
    assert r['cells'][1]['difference']['rgb_mae'] == 0
    assert r['cells'][1]['observation']['verdict'] == 'fail'
    assert r['cells'][1]['observation']['shot_layout']['ok'] is False
    assert r['cells'][1]['build_comparison'] == 'different'
    assert r['observed_builds'] == ['a', 'b']
    assert 'not UI defects' in app.evidence_html(r)


def test_unknown_build_unmeasured_shot_and_duplicate_records_are_not_assumed_successful():
    r = app.annotate(report(), [record(build=None)], 'width-320')
    assert r['cells'][0]['build_comparison'] == 'unknown'
    assert r['cells'][0]['observation']['shot_click'] is None
    with pytest.raises(ValueError, match='ambiguous'):
        app.annotate(report(), [record(), record()], 'width-384')


def test_evidence_html_escapes_observed_values():
    r = app.annotate(report(), [record(build='<script>alert(1)</script>')], 'initial')
    rendered = app.evidence_html(r)
    assert '<script>' not in rendered and '&lt;script&gt;' in rendered


# ═══ · app digest ════════════════════════════════════════════════════════════════════════════════════════
# app_digest: one small page per run; thumbnails shrink to a quarter until it fits.

spec_app_digest = importlib.util.spec_from_file_location('app_digest', ROOT / 'tools/app_digest.py')
digest_app_digest = importlib.util.module_from_spec(spec_app_digest)
spec_app_digest.loader.exec_module(digest_app_digest)

FAKE_IRONMATE = '''
from pathlib import Path
def find_shots(root, shot, *, pages=('*',), environments=('*',)):
    return {(p.parent.parent.name, p.parent.name): p for p in Path(root).rglob(shot + '.png')}
def build(shots, *, tile_w, tile_h, expected_pages=(), expected_envs=()):
    return None, None, {key: (tile_w, tile_h, b'') for key in shots}
def encode_png(w, h, rgba):
    return bytes(w * h * 4)  # size grows with pixels, so halving the tile shrinks the page
'''


def record_app_digest(adapter, os_name, verdict='pass', failed=()):
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
        (results / 'x' / f'{n}.json').write_text(json.dumps(record_app_digest(adapter, 'Linux', verdict, failed)), encoding='utf-8')
        shot = results / 'x' / adapter / 'Linux-chromium-desktop' / 'width-384.png'
        shot.parent.mkdir(parents=True)
        shot.write_bytes(b'png')
    yield results, ironmate, adapters
    sys.modules.pop('browser_matrix', None)
    sys.path[:] = [p for p in sys.path if p != str(ironmate.resolve())]


def test_digest_fits_one_page_and_quarters_thumbnails_until_it_fits(run, tmp_path):
    results, ironmate, adapters = run
    big = digest_app_digest.generate(results, tmp_path / 'big', ironmate=ironmate, runners=['ubuntu-24.04'], adapter_dir=adapters,
                          max_bytes=1_000_000, run_url='https://example.test/run')
    assert big['tile'] == [64, 128] and big['fits'] and big['pages'] == 2 and big['envs'] == 5 and big['passed'] == 1
    small = digest_app_digest.generate(results, tmp_path / 'small', ironmate=ironmate, runners=['ubuntu-24.04'],
                            adapter_dir=adapters, max_bytes=50_000)
    assert small['tile'] == [32, 64] and small['fits'] and small['bytes'] <= 50_000  # a quarter of the pixels
    page = (tmp_path / 'big' / 'digest.html').read_text(encoding='utf-8')
    assert page.count('data:image/png;base64,') == 2 and 'overlap:#a:#b' in page and 'not run' in page
    assert 'builds cccccccccccc' in page and 'href="https://example.test/run"' in page
    md = (tmp_path / 'big' / 'summary.md').read_text(encoding='utf-8')
    assert md.startswith('<!-- platform-apps-digest -->') and '1/10 page x environment checks passed' in md
    assert '| flutter-hub | 0/5 | Linux-chromium-desktop (overlap:#a:#b);' in md
    with pytest.raises(ValueError, match='new or empty'):
        digest_app_digest.generate(results, tmp_path / 'big', ironmate=ironmate, runners=['ubuntu-24.04'], adapter_dir=adapters)
    with pytest.raises(ValueError, match='max_bytes'):
        digest_app_digest.generate(results, tmp_path / 'z', ironmate=ironmate, runners=['ubuntu-24.04'], adapter_dir=adapters,
                        max_bytes=10)


def test_digest_counts_only_planned_cells_and_reports_each_build(run, tmp_path):
    results, ironmate, adapters = run
    (results / 'x' / 'outside.json').write_text(json.dumps(record_app_digest('unplanned-page', 'OtherOS')), encoding='utf-8')
    info = digest_app_digest.generate(results, tmp_path / 'out', ironmate=ironmate, runners=['ubuntu-24.04'], adapter_dir=adapters)
    assert info['passed'] == 1
    page = (tmp_path / 'out' / 'digest.html').read_text(encoding='utf-8')
    assert 'build cccccccccccc' in page
    assert '1/10 page x environment checks passed' in (tmp_path / 'out' / 'summary.md').read_text()


def test_digest_rejects_ambiguous_records():
    with pytest.raises(ValueError, match='ambiguous'):
        digest_app_digest._cells([record_app_digest('flutter-home', 'Linux', 'fail'), record_app_digest('flutter-home', 'Linux', 'pass')])


# ═══ · app selection ═════════════════════════════════════════════════════════════════════════════════════
# Selected plans stay consistent across comparison and digest entry points.

def adapters(root):
    root.mkdir()
    original = json.loads((ROOT / 'adapters/flutter-home.json').read_text(encoding='utf-8'))
    for name in ('flutter-home', 'portal-home'):
        (root / (name + '.json')).write_text(json.dumps(dict(original, id=name)), encoding='utf-8')
    return root


def test_selected_plan_ors_deduplicates_and_keeps_default_flutter_scope(tmp_path):
    directory = adapters(tmp_path / 'adapters')
    runners = ['ubuntu-24.04', 'ubuntu-24.04', 'windows-latest']
    pages, envs = comparison.select_targets(directory, runners)
    assert pages == ['flutter-home'] and len(envs) == 10
    pages, envs = comparison.select_targets(directory, runners, pages=('portal-*', '*-home'),
                                            environments=('Linux-chromium-*', '*-firefox-*'))
    assert pages == ['flutter-home', 'portal-home']
    assert envs == ['Linux-chromium-desktop', 'Linux-chromium-pixel', 'Linux-firefox-desktop', 'Windows-firefox-desktop']
    with pytest.raises(ValueError, match='no selected'):
        comparison.select_targets(directory, runners, pages=('PORTAL-*',))


def test_excluded_adapter_is_not_loaded_and_selected_duplicate_id_is_rejected(tmp_path):
    directory = adapters(tmp_path / 'adapters')
    (directory / 'broken.json').write_text('invalid json')
    assert comparison.select_targets(directory, ['ubuntu-24.04'])[0] == ['flutter-home']
    (directory / 'flutter-duplicate.json').write_bytes((directory / 'flutter-home.json').read_bytes())
    with pytest.raises(ValueError, match='duplicate selected'):
        comparison.select_targets(directory, ['ubuntu-24.04'])


def test_records_are_selected_before_duplicate_detection():
    inside = {'target': {'adapter': 'flutter-home'}, 'env': {'os': 'Linux', 'browser': 'chromium', 'profile': 'desktop'}}
    outside = dict(inside, target={'adapter': 'other'})
    empty = dict(inside, target=None)
    assert comparison.select_records([outside, empty, inside, outside], ['flutter-home'], ['Linux-chromium-desktop']) == [inside]


@pytest.mark.parametrize('module', [comparison, digest])
def test_list_needs_no_ironmate_records_or_images(tmp_path, monkeypatch, capsys, module):
    directory = adapters(tmp_path / 'adapters')
    (tmp_path / 'invalid.json').write_text('not records')
    def forbidden(*args, **kwargs):
        raise AssertionError('list must not generate anything')
    monkeypatch.setattr(module, 'generate', forbidden)
    args = [str(tmp_path), '--adapters', str(directory), '--runners-json', '["ubuntu-24.04"]',
            '--page', 'portal-*', '--env', '*-firefox-*', '--list']
    assert module.main(args) == 0
    assert json.loads(capsys.readouterr().out) == {'pages': ['portal-home'], 'envs': ['Linux-firefox-desktop']}
    with pytest.raises(SystemExit) as error:
        module.main(args + ['--out', str(tmp_path / 'out')])
    assert error.value.code == 2


@pytest.mark.parametrize('script', ['app_comparison.py', 'app_digest.py'])
def test_list_runs_without_site_packages(tmp_path, script):
    directory = adapters(tmp_path / 'adapters')
    result = subprocess.run([sys.executable, '-S', str(ROOT / 'tools' / script), str(tmp_path),
                             '--adapters', str(directory), '--runners-json', '["ubuntu-24.04"]', '--list'],
                            capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['pages'] == ['flutter-home']


@pytest.mark.parametrize('options', [{'pages': ('',)}, {'environments': ()}, {'pages': ()}])
def test_bad_or_empty_plans_are_errors(tmp_path, options):
    directory = adapters(tmp_path / 'adapters')
    with pytest.raises(ValueError):
        comparison.select_targets(directory, ['ubuntu-24.04'], **options)


# ═══ · aoi min replay ════════════════════════════════════════════════════════════════════════════════════
# Offline replay preflight must reject bad evidence before importing any min.

SCRIPT = ROOT / 'examples/replay_dom_with_blueprobe_min.py'
spec_aoi_min_replay = importlib.util.spec_from_file_location('btk_aoi_min_replay_test', SCRIPT)
module = importlib.util.module_from_spec(spec_aoi_min_replay)
spec_aoi_min_replay.loader.exec_module(module)


def test_rejects_changed_dom_before_min_import(tmp_path):
    path = tmp_path / 'dom.json'
    path.write_text(json.dumps({'schema': 'btk-browser-dom/1', 'source_kind': 'dom',
        'html': '<button>Add</button>', 'url': 'https://example.test/', 'html_sha256': '0' * 64}))
    result = subprocess.run([sys.executable, '-S', str(SCRIPT), str(path),
        '--blueprobe-min', str(tmp_path / 'must-not-be-opened.py')], capture_output=True, text=True, timeout=5)
    assert result.returncode == 2
    assert 'hash-verified' in result.stderr
    assert 'No such file' not in result.stderr


def test_verified_snapshot_retains_original_bytes_identity(tmp_path):
    path = tmp_path / 'dom.json'
    html = '<button>日本語</button>'
    record = {'schema': 'btk-browser-dom/1', 'source_kind': 'dom', 'html': html,
              'url': 'https://example.test/', 'html_sha256': hashlib.sha256(html.encode()).hexdigest()}
    path.write_text(json.dumps(record))
    actual, digest = module.read_snapshot(path)
    assert actual == record
    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize('value', [None, [], {'schema': 'different'}])
def test_wrong_snapshot_shape_is_not_accepted(tmp_path, value):
    path = tmp_path / 'dom.json'
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='hash-verified'):
        module.read_snapshot(path)


def test_changed_min_bundle_is_rejected_before_execution(tmp_path):
    path = tmp_path / 'bundle.py'
    path.write_text('# content-sha256: ' + '0' * 64 + '\nraise RuntimeError("must not execute")\n')
    with pytest.raises(ValueError, match='digest mismatch'):
        module.load_min(path)
