"""Keep pre-consolidation consumers working, including installed modules."""
import importlib.util, json, os, subprocess, sys, zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LEGACY = ('app_comparison app_digest btk_events check_capture_evidence '
          'check_evidence check_png evidence_board html_builder image_diff jsonl_digest '
          'mirror_check page_inspect page_text platform_adapter platform_probe probe_capture_rejections '
          'pw_install_plan pytest_btk_events '
          'terminal_browser write_capture_evidence').split()


def test_legacy_modules_remain_distributed():
    for name in LEGACY:
        assert (ROOT / 'scripts' / (name + '.py')).is_file(), name
    for wheel in (ROOT / 'dist').glob('*.whl'):
        with zipfile.ZipFile(wheel) as archive:
            assert set(name + '.py' for name in LEGACY) <= set(archive.namelist())


@pytest.mark.parametrize('runtime', ['python', 'node'])
def test_legacy_inspection_runs_current_privacy_and_error_contract(tmp_path, runtime):
    # Node: page_inspect.cjs against btk.py through vendor/py_launch.cjs (btk.cjs is gone).
    def command(entry):
        if runtime == 'python':
            return [sys.executable, str(ROOT / 'scripts' / (entry + '.py'))]
        if entry == 'page_inspect':
            return ['node', str(ROOT / 'scripts' / 'page_inspect.cjs')]
        return ['node', str(ROOT / 'vendor' / 'py_launch.cjs'), '--env-var', 'BTK_PYTHON', '--site',
                str(ROOT / 'scripts' / 'btk.py')]
    def run(entry, args):
        return subprocess.run([*command(entry), *args],
                              env={**os.environ, 'PYTHON': sys.executable, 'BTK_PYTHON': sys.executable},
                              capture_output=True, text=True, timeout=60)
    snapshot = {'schema': 'btk-page-observe/1', 'elements': [],
                'url': 'http://127.0.0.1:8765/a?token=private-value#private-fragment'}
    path = tmp_path / 'old.json'
    path.write_text(json.dumps(snapshot))
    args = ['compare', str(path), str(path), '--json']
    old, new = run('page_inspect', args), run('btk', args)
    assert old.returncode == new.returncode == 0
    assert json.loads(old.stdout) == json.loads(new.stdout)
    assert 'private-value' not in old.stdout + old.stderr
    assert 'private-fragment' not in old.stdout + old.stderr
    old, new = run('page_inspect', ['unknown', '--json']), run('btk', ['unknown', '--json'])
    assert old.returncode == new.returncode == 2


def test_legacy_python_api_including_private_helpers():
    spec = importlib.util.spec_from_file_location('legacy_inspect', ROOT / 'scripts/page_inspect.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tree = {'shadowRoots': [{'shadowRootType': 'open'}, {'shadowRootType': 'closed'},
                            {'shadowRootType': 'user-agent'}]}
    assert module.count_shadow_roots(tree) == {
        'open': 1, 'closed': 1, 'user_agent': 1, 'count': 2, 'truncated': False}
    assert module._parse_step('goto - http://127.0.0.1:8765/a?q=secret', 1)['text'].endswith('?q=secret')


def test_installed_legacy_modules_outside_checkout(tmp_path):
    wheels = sorted((ROOT / 'dist').glob('*.whl'))
    if not wheels:
        pytest.skip('Build the wheel first to exercise installed compatibility')
    target = tmp_path / 'installed'
    subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-deps', '--no-index',
                    '--target', str(target), str(wheels[-1])], check=True,
                   capture_output=True, timeout=60)
    env = {**os.environ, 'PYTHONPATH': str(target)}
    for name in LEGACY:
        done = subprocess.run([sys.executable, '-S', '-m', name, '--help'], cwd=tmp_path,
                              env=env, capture_output=True, text=True, timeout=60)
        assert done.returncode == 0, (name, done.stderr)
    snapshot = tmp_path / 'snapshot.json'
    snapshot.write_text(json.dumps({'schema': 'btk-page-observe/1', 'elements': [],
                                   'url': 'http://127.0.0.1:8765/?q=private-value'}))
    done = subprocess.run([sys.executable, '-S', '-m', 'page_inspect', 'compare',
                           str(snapshot), str(snapshot), '--json'], cwd=tmp_path,
                          env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)['left_url'] == 'http://127.0.0.1:8765/'
    assert 'private-value' not in done.stdout
