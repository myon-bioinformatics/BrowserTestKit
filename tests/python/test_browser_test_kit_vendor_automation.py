"""CI contract for vendor.lock.json verification (no promote/update sidecars)."""
import hashlib, json, os, subprocess, sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = '.github/workflows/playwright.yml'
TEST_JOB = 'python'
LOCK = json.loads((ROOT / 'vendor.lock.json').read_text())
SNAPSHOT = ['vendor.lock.json'] + [e['destination'] for e in LOCK['files']]
PIN = '9e3da7ae1846e9604ebc24bb5178f7de094a4e77'


def _workflow():
    import yaml
    return yaml.load((ROOT / WORKFLOW).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_vendor_lock_lists_all_checked_in_sources():
    expected = {(e['repository'], e['source'], e['destination']) for e in LOCK['files']}
    assert len(expected) == len(LOCK['files'])


def test_public_vendor_ci_verifies_lock_without_repository_writes():
    ci = _workflow()
    assert {"push", "pull_request"} <= set(ci["on"])
    assert "schedule" not in ci["on"]
    assert "resolve-vendor" not in ci["jobs"]
    jobs = ci["jobs"]
    for job in (jobs[TEST_JOB], jobs["test-locked"]):
        assert job.get("permissions") == {"contents": "read"}
        assert "continue-on-error" not in job
    text = (ROOT / WORKFLOW).read_text(encoding="utf-8")
    assert "vendor_sync.py promote" not in text
    assert "vendor_sync.py update" not in text
    assert "sync_vendor_provenance" not in text
    assert "provenance.json" not in text
    assert not any(x in text for x in ("secrets.", "VENDOR_UPDATE_TOKEN", "VENDOR_UPDATES_ENABLED", "GH_TOKEN"))
    for name in (TEST_JOB, "test-locked"):
        steps = jobs[name]["steps"]
        check = next(s for s in steps if s.get("name") == "Verify checked-in vendor copies")
        assert check["run"] == "python -S .vendor-sync-tools/vendor_sync.py check --manifest vendor.lock.json"
        tool = next(s for s in steps if s.get("name") == "Fetch pinned shared vendor tool")
        assert tool["with"]["ref"] == PIN
        assert tool["with"]["repository"] == "myon-bioinformatics/myon-bioinformatics"
        for step in steps:
            if step.get("uses", "").startswith("actions/checkout@"):
                assert step["with"]["persist-credentials"] == "false"
            assert not any(x in step.get("run", "") for x in ("|| true", "|| :", "set +e", "git push", "git commit", "gh pr"))
            assert "continue-on-error" not in step
    locked = jobs["test-locked"]["steps"]
    recreate = next(s for s in locked if s.get("name") == "Recreate locked vendor files from GitHub")
    assert recreate["shell"] == "bash"
    run = recreate["run"]
    assert "tools/recreate_locked_vendor.py" not in run
    assert ".vendor-sync-tools/vendor_sync.py" in run
    assert "materialize" in run
    assert "TemporaryDirectory" in run or "tempfile" in run
    assert "unlink" not in run
    assert not any(s.get("uses", "").startswith("actions/download-artifact@") for s in locked)
    stage = next(s for s in locked if s.get("name") == "Stage canonical vendor evidence")
    assert "--kind locked" in stage["run"]
    assert "--legacy-evidence" not in stage["run"]
    assert "--promotion-receipt" not in stage["run"]


def test_locked_baseline_runs_automatically_without_candidate_snapshot():
    ci = _workflow()
    job = ci['jobs']['test-locked']
    assert job.get('needs') == ci['jobs'][TEST_JOB].get('needs')
    assert job.get('if') == ci['jobs'][TEST_JOB].get('if')
    matrix = job.get('strategy', {}).get('matrix', {})
    assert all(len(values) == 1 for values in matrix.values())
    steps = job['steps']
    verify = next(i for i, s in enumerate(steps) if s.get('name') == 'Verify checked-in vendor copies')
    recreate = next(i for i, s in enumerate(steps) if s.get('name') == 'Recreate locked vendor files from GitHub')
    tests = [i for i, s in enumerate(steps) if 'pytest ' in s.get('run', '')]
    assert tests and verify < recreate < min(tests)
    for step in steps:
        if step.get('uses', '').startswith('actions/upload-artifact@'):
            assert step['with']['name'].startswith('locked-')
            if step['with']['name'].startswith(('locked-vendor-', 'locked-junit-', 'locked-controlled-')):
                assert step['if'] == 'always()'
            assert step['with']['if-no-files-found'] == 'error'
    lock = next(s for s in steps if s.get('name') == 'Preserve vendor lock used by this run')
    assert lock['with']['path'] == 'build/vendor-evidence-locked'


def test_real_canonical_staging_preserves_locked_bytes(tmp_path):
    import shutil
    tool_root = Path(os.environ.get('BTK_CANONICAL_TOOLS', str(ROOT / '.vendor-sync-tools')))
    if not (tool_root / 'vendor_stage.py').is_file() and not os.environ.get('CI'):
        pytest.skip('BTK_CANONICAL_TOOLS / .vendor-sync-tools not available locally (CI still requires it)')
    for name in SNAPSHOT:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, path)
    stage_argv = [sys.executable, '-S', str(tool_root / 'vendor_stage.py'),
                  '--root', str(tmp_path), '--kind', 'locked', '--output', 'build/staged']
    result = subprocess.run(stage_argv, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    output = tmp_path / 'build/staged'
    meta = json.loads((output / 'vendor-evidence.json').read_text())
    locked = {'vendor.lock.json'} | {e['destination'] for e in LOCK['files']}
    assert set(meta['locked']) == locked
    assert meta['legacy'] == []
    assert meta['runtime'] == []
    expected = locked
    assert set(meta['sha256']) == expected
    assert {p.relative_to(output).as_posix() for p in output.rglob('*') if p.is_file()} == expected | {'vendor-evidence.json'}
    for name in expected:
        data = (output / name).read_bytes()
        assert data == (tmp_path / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == meta['sha256'][name]


def test_launcher_pin_uses_canonical_first_parent_source():
    entry = next(e for e in LOCK["files"] if e["destination"] == "vendor/py_launch.cjs")
    assert entry["repository"] == "myon-bioinformatics/myon-bioinformatics"
    assert entry["source"] == "py_launch.cjs"
    assert entry["commit"] == entry["ref"] == "384f5dd532076062f7833038764e9a62322c387c"
