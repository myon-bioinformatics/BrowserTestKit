"""Keep runtime, distribution, and development on the same Python floor."""
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
    requirements = (ROOT / 'requirements-test-core.txt').read_text()
    assert 'pillow' not in requirements
    assert 'pip>=26.2' in requirements
    assert 'pytest>=9.0.3' in requirements


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
            license_path = backend.rsplit('/', 1)[0] + '/Ironmate-LICENSE'
            assert archive.read(backend) == (ROOT / 'vendor/pillow_min/pillow_min.py').read_bytes()
            assert archive.read(license_path) == (ROOT / 'vendor/pillow_min/Ironmate-LICENSE').read_bytes()
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
