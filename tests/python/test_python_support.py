"""Keep runtime, distribution, and development on the same Python floor, and installed modules working for consumers."""
import importlib.util, json, os, subprocess, sys, zipfile
from pathlib import Path
import tomllib

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_metadata_has_one_runtime_floor():
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text())
    assert project['project']['requires-python'] == '>=3.11'
    assert project['project'].get('dependencies', []) == []
    assert project['project'].get('optional-dependencies', {}) == {}
    assert "environments" not in project["tool"]["uv"]
    for name in ('dev', 'browser', 'browser-test'):
        assert project['tool']['uv']['dependency-groups'][name]['requires-python'] == '>=3.11'
    assert 'pillow' not in str(project['dependency-groups']['dev'])
    assert 'pip>=26.2' in project['dependency-groups']['dev']
    assert 'pytest>=9.0.3' in project['dependency-groups']['dev']
    # pyproject.toml's groups (resolved in uv.lock) are the only dependency list; no requirements-*.txt copies.
    assert not list(ROOT.glob('requirements*.txt'))


def test_lock_has_no_legacy_pillow_or_pip():
    lock = tomllib.loads((ROOT / 'uv.lock').read_text())
    assert lock["requires-python"] == ">=3.11"
    assert lock.get("supported-markers", []) == []
    names = {package['name'] for package in lock['package']}
    assert 'pillow' not in names
    for package in lock['package']:
        version = tuple(map(int, package['version'].split('.')))
        if package['name'] == 'pip':
            assert version >= (26, 2)
        if package['name'] == 'pytest':
            assert version >= (9, 0, 3)


def test_built_wheel_metadata_when_present():
    from email.parser import Parser
    import zipfile

    wheels = list((ROOT / 'dist').glob('*.whl'))
    if not wheels:
        import pytest
        pytest.skip('Run uv build first to inspect the distributable metadata')
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as archive:
            path = next(p for p in archive.namelist() if p.endswith('.dist-info/METADATA'))
            metadata = Parser().parsestr(archive.read(path).decode())
            backend = next(p for p in archive.namelist()
                           if p.endswith('/share/browser-test-kit/pillow_min/pillow_min.py'))
            license_path = backend.rsplit('/', 1)[0] + '/myon-bioinformatics-LICENSE'
            assert archive.read(backend) == (ROOT / 'vendor/pillow_min/pillow_min.py').read_bytes()
            assert archive.read(license_path) == (ROOT / 'vendor/pillow_min/myon-bioinformatics-LICENSE').read_bytes()
        assert metadata['Requires-Python'] == '>=3.11'
        assert metadata.get_all('Requires-Dist') is None


@pytest.mark.parametrize('layout', ['target', 'prefix'])
def test_built_wheel_image_diff_outside_checkout(tmp_path, layout):
    """Install the actual wheel and exercise PNGs, not just --help, without PIL."""
    import os
    import subprocess
    import sys
    import sysconfig

    wheels = sorted((ROOT / 'dist').glob('*.whl'))
    if not wheels:
        pytest.skip('Run uv build first to exercise the installed CLI')
    for index, wheel in enumerate(wheels):
        target = tmp_path / f'installed-{index}'
        subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-deps', '--no-index',
                        '--ignore-installed', '--' + layout, str(target), str(wheel)],
                       check=True, capture_output=True, timeout=60)
        module_path = target if layout == 'target' else sysconfig.get_path(
            'purelib', vars={'base': str(target), 'platbase': str(target)})
        # -S bypasses site packages; only the distribution just installed is visible.
        env = {**os.environ, 'PYTHONPATH': str(module_path)}
        fixtures = ROOT / 'tests/fixtures/image_diff'
        import json
        golden = json.loads((fixtures / 'golden.json').read_text())
        for name, left, right in [('identical', 'identical_a.png', 'identical_b.png'),
                                  ('red_rect', 'red_rect_a.png', 'red_rect_b.png')]:
            result = subprocess.run(
                [sys.executable, '-S', '-m', 'image_diff', str(fixtures / left), str(fixtures / right)],
                cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30,
            )
            assert result.returncode == golden[name]['returncode'], result.stderr
            assert result.stdout.strip() == golden[name]['stdout']
            assert result.stderr == ''


def test_removed_shared_tools_are_not_reintroduced_by_layout_migration():
    """Consolidating scripts into tools must preserve the #153 deletions."""
    import json

    removed = {
        'app_digest_comment.py', 'fetch_member.py', 'gh_identity.py',
        'gh_ops.py', 'git_commit.py', 'publish_check.py', 'py_matrix.py',
        'pytest_brief.py', 'recreate_locked_vendor.py', 'repo_overview.py',
    }
    for directory in ('scripts', 'tools'):
        assert not {p.name for p in (ROOT / directory).glob('*.py')} & removed
    lock = json.loads((ROOT / 'vendor.lock.json').read_text())
    assert not {Path(entry['destination']).name for entry in lock['files']} & removed


# Pre-consolidation consumers keep working, including installed modules (formerly test_legacy_entrypoints.py).
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
