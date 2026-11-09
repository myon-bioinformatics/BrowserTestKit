"""Selected plans stay consistent across comparison and digest entry points."""
import json, subprocess, sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import app_comparison as comparison
import app_digest as digest


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
