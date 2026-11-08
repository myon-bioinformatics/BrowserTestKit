"""Consumer wiring only; generic gh_ops regressions live in the parent repository."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def test_canonical_module_imports_with_adjacent_identity():
    spec = importlib.util.spec_from_file_location('consumer_gh_ops', ROOT / 'tools/gh_ops.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    assert Path(module.gh_identity.__file__).resolve() == ROOT / 'tools/gh_identity.py'
    assert callable(module.pr_status)
    assert callable(module.checks_status)


def test_canonical_cli_help_works_without_site_packages_or_credentials(tmp_path):
    result = subprocess.run([sys.executable, '-S', str(ROOT / 'tools/gh_ops.py'), '--help'],
                            cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0
    assert 'usage:' in result.stdout.lower()
    assert not list(tmp_path.iterdir())


def test_gh_ops_is_a_locked_parent_dependency():
    lock = json.loads((ROOT / 'vendor.lock.json').read_text())
    entries = [e for e in lock['files'] if e['destination'] == 'tools/gh_ops.py']
    assert len(entries) == 1
    assert entries[0]['repository'] == 'myon-bioinformatics/myon-bioinformatics'
    assert entries[0]['source'] == 'gh_ops.py'
    assert entries[0]['ref'] == entries[0]['commit']
    assert len(entries[0]['ref']) == 40


def test_git_push_dry_run_uses_actual_adjacent_identity_without_network(tmp_path):
    # A deliberately unusable remote and guarded real Git runner keep this offline.
    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, capture_output=True,
                              text=True, check=True, timeout=15)
    git("init", "--initial-branch=refresh")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
        "commit", "--allow-empty", "-m", "fixture")
    git("remote", "add", "origin", "https://invalid.invalid/never/pushed.git")
    head = git("rev-parse", "HEAD").stdout.strip()
    code = """
import json, pathlib, sys
sys.path.insert(0, sys.argv[1])
import gh_ops
assert pathlib.Path(gh_ops.gh_identity.__file__).resolve() == pathlib.Path(sys.argv[1], 'gh_identity.py').resolve()
actual_run = gh_ops.gh_identity._git_run
calls = []
def local_only(cwd, args, **kwargs):
    assert args[0] in {'branch', 'check-ref-format', 'rev-parse', 'remote'}
    assert kwargs.get('inherit_auth') is False
    calls.append(args[0])
    return actual_run(cwd, args, **kwargs)
gh_ops.gh_identity._git_run = local_only
receipt = gh_ops.git_push(cwd=sys.argv[2], write=False, timeout=10)
assert calls == ['branch', 'check-ref-format', 'rev-parse', 'remote']
print(json.dumps(receipt))
"""
    result = subprocess.run([sys.executable, "-S", "-c", code, str(ROOT / 'tools'), str(tmp_path)],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["ok"] and receipt["dry_run"]
    assert receipt["pushed"] is False
    assert receipt["head_sha"] == head
    assert receipt["branch"] == "refresh"
    assert receipt["mutation_status"] == receipt["verification_status"] == "not_attempted"
    assert "invalid.invalid" not in result.stdout
    assert not git("for-each-ref", "refs/remotes/").stdout
