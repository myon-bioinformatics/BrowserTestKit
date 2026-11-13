"""Keep pre-consolidation consumers working, including installed modules."""
import importlib.util, json, os, subprocess, sys, zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
# check_png, check_evidence, check_capture_evidence, write_capture_evidence, probe_capture_rejections, page_inspect
# and pw_install_plan are btk.py now (check-png, ..., observe, plan): their files were removed on purpose.
LEGACY = ('app_comparison app_digest btk_events evidence_board html_builder image_diff jsonl_digest '
          'mirror_check page_text platform_adapter platform_probe pytest_btk_events terminal_browser').split()


def test_legacy_modules_remain_distributed():
    for name in LEGACY:
        assert (ROOT / 'tools' / (name + '.py')).is_file(), name
    for wheel in (ROOT / 'dist').glob('*.whl'):
        with zipfile.ZipFile(wheel) as archive:
            assert set(name + '.py' for name in LEGACY) <= set(archive.namelist())


@pytest.mark.parametrize('runtime', ['python', 'node'])
def test_inspection_runs_current_privacy_and_error_contract(tmp_path, runtime):
    # Node runs btk.py through vendor/py_launch.cjs (page_inspect.py/.cjs and btk.cjs are gone).
    def command():
        if runtime == 'python':
            return [sys.executable, str(ROOT / 'tools' / 'btk.py')]
        return ['node', str(ROOT / 'vendor' / 'py_launch.cjs'), '--env-var', 'BTK_PYTHON', '--site',
                str(ROOT / 'tools' / 'btk.py')]
    def run(args):
        return subprocess.run([*command(), *args],
                              env={**os.environ, 'PYTHON': sys.executable, 'BTK_PYTHON': sys.executable},
                              capture_output=True, text=True, timeout=60)
    snapshot = {'schema': 'btk-page-observe/1', 'elements': [],
                'url': 'http://127.0.0.1:8765/a?token=private-value#private-fragment'}
    path = tmp_path / 'old.json'
    path.write_text(json.dumps(snapshot))
    args = ['compare', str(path), str(path), '--json']
    done = run(args)
    assert done.returncode == 0 and json.loads(done.stdout)['left_url'] == 'http://127.0.0.1:8765/a'
    assert 'private-value' not in done.stdout + done.stderr
    assert 'private-fragment' not in done.stdout + done.stderr
    assert run(['unknown', '--json']).returncode == 2


def test_python_api_including_private_helpers():
    spec = importlib.util.spec_from_file_location('btk_inspect', ROOT / 'tools/btk.py')
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
    done = subprocess.run([sys.executable, '-S', '-m', 'btk', 'compare',
                           str(snapshot), str(snapshot), '--json'], cwd=tmp_path,
                          env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)['left_url'] == 'http://127.0.0.1:8765/'
    assert 'private-value' not in done.stdout
