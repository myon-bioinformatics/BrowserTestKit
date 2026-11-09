"""Offline replay preflight must reject bad evidence before importing any min."""
import hashlib, importlib.util, json, subprocess, sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'examples/replay_dom_with_blueprobe_min.py'
spec = importlib.util.spec_from_file_location('btk_aoi_min_replay_test', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


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
