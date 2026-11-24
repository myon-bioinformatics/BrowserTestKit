"""Repository contracts: workflows, vendor CI, btk fetch/plan and python -m entry points (lock bytes, action and parent pins: test_fleet.py)."""

import base64
import hashlib
import importlib.util
import io
import json
import shlex
import subprocess
import sys
import tarfile
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import btk
import cli_args
import fleet_check


def test_python_compatibility_lanes_keep_their_executable_support_checks():
    jobs = yaml.safe_load((ROOT / '.github/workflows/runtime-compatibility.yml').read_text())['jobs']
    assert jobs['stdlib']['strategy']['matrix']['python'] == ['3.11']
    commands = '\n'.join(step.get('run', '') for step in jobs['stdlib']['steps'])
    assert 'python -S -m unittest discover -s tests/stdlib' in commands
    assert 'uv pip install --no-deps .' in commands
    assert 'python -S tools/image_diff.py --help' in commands
    floor = jobs['development-floor']['steps']
    assert next(step for step in floor if step.get('uses', '').startswith('astral-sh/setup-uv@'))['with']['python-version'] == '3.11'
    assert any(step.get('run') == 'uv sync --locked --all-groups' for step in floor)
    assert any(step.get('run') == 'python -S tools/image_diff.py --help' for step in floor)


# ═══ · browser pages dependencies ════════════════════════════════════════════════════════════════════════
# Keep render dependencies in the locked, no-dev browser lanes.

def _assert_browser_groups(workflow):
    for name in ("python-browser", "python-browser-optional"):
        job = workflow["jobs"][name]
        runs = [s["run"] for s in job["steps"] if "run" in s]
        commands = [line.strip() for run in runs for line in run.splitlines()
                    if "uv run " in line]
        assert commands, name
        for command in commands:
            assert "--locked --no-dev --group browser-test --group pages python" in command
        tests = [s for s in job["steps"] if "python -m pytest tests/python" in s.get("run", "")]
        assert len(tests) == 1
        step = tests[0]
        assert not job.get("continue-on-error") and not step.get("continue-on-error")
        assert not step.get("if")
        suffix = ('-m browser --junitxml=test-results/junit/python-browser-3.15.xml'
                  if name == "python-browser" else
                  '-m browser -k "firefox or webkit" --junitxml=test-results/junit/python-browser-optional-3.15.xml')
        assert shlex.split(step["run"]) == shlex.split(
            "uv run --locked --no-dev --group browser-test --group pages python -m pytest tests/python -q " + suffix)


def test_browser_lanes_include_pinned_page_renderer():
    _assert_browser_groups(yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text()))


def test_browser_contract_rejects_missing_pages_or_masked_tests():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    _assert_browser_groups(workflow)
    for name in ("python-browser", "python-browser-optional"):
        for mutation in ("missing-pages", "masked-failure", "skip"):
            broken = deepcopy(workflow)
            step = next(s for s in broken["jobs"][name]["steps"]
                        if "python -m pytest tests/python" in s.get("run", ""))
            if mutation == "missing-pages":
                step["run"] = step["run"].replace(" --group pages", "")
            elif mutation == "masked-failure":
                step["run"] += " || true"
            else:
                step["if"] = "false"
            with pytest.raises(AssertionError):
                _assert_browser_groups(broken)


# ═══ · browser test kit junit workflow contract ══════════════════════════════════════════════════════════
# Guard the real workflow's failure/evidence contract, using test-only PyYAML.

WORKFLOW = ROOT / ".github/workflows/ci.yml"
COLLECTOR = (
    "myon-bioinformatics/myon-bioinformatics/.github/workflows/"
    "reusable-junit-identity.yml@1368174821fb03be258ecf3312ce0ea809f6ea22"
)
COMMAND = (
    'uv run --locked python -m pytest tests/python -q -m "not browser" '
    "--junitxml=test-results/junit/python-3.15.xml"
)


def _step(job, name):
    matches = [step for step in job["steps"] if step.get("name") == name]
    assert len(matches) == 1, name
    return matches[0]


def _upload(job, name):
    matches = [step for step in job["steps"]
               if step.get("uses", "").startswith("actions/upload-artifact@")
               and step.get("with", {}).get("name") == name]
    assert len(matches) == 1, name
    return matches[0]


def _assert_contract(workflow):
    producer = workflow["jobs"]["python"]
    run = _step(producer, "Run Python tests with JUnit")
    assert producer.get("continue-on-error", False) is False
    assert run.get("continue-on-error", False) is False
    assert run.get("if") is None
    # The exact normalized command rejects || true, set +e and trailing exit 0.
    assert " ".join(run["run"].split()) == COMMAND
    assert run["env"]["FAILURE_EVIDENCE_DIR"] == "test-results/controlled-failure"
    for name, path in (
        ("junit-python-3.15", "test-results/junit/python-3.15.xml"),
        ("junit-controlled-python-3.15", "test-results/controlled-failure/"),
    ):
        upload = _upload(producer, name)
        assert producer["steps"].index(run) < producer["steps"].index(upload)
        assert upload.get("if") == "always()"
        assert upload.get("continue-on-error", False) is False
        assert upload["with"]["path"].strip() == path
        assert upload["with"]["if-no-files-found"] == "error"
        assert upload["with"]["retention-days"] == 14
    collector = workflow["jobs"]["failure-identity"]
    assert collector["needs"] == ["changes", "python", "node-browserless", "python-browser", "python-browser-optional"]
    assert collector["if"] == (
        "always() && needs.changes.outputs.product_changed == 'true'"
    )
    assert collector.get("continue-on-error", False) is False
    assert collector["uses"] == COLLECTOR
    reports = json.loads(collector["with"]["expected-reports"])
    assert isinstance(reports, list) and len(reports) == 5
    assert set(reports) == {
        "junit-python-3.15/python-3.15.xml",
        "junit-controlled-python-3.15/junit.xml",
        "junit-node-browserless/browserless-node.xml",
        "junit-python-browser-3.15/python-browser-3.15.xml",
        "junit-python-browser-optional-3.15/python-browser-optional-3.15.xml",
    }
    browser = _upload(workflow["jobs"]["python-browser"], "python-browser-evidence")
    assert browser["with"]["path"].splitlines() == [
        "test-results", "!test-results/junit/**",
        "!test-results/controlled-failure/**",
    ]


def test_failure_evidence_workflow_contract():
    _assert_contract(yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")))


def test_failure_evidence_contract_rejects_unsafe_mutations():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    _assert_contract(workflow)  # A broken baseline must not make all mutants pass.
    producer = workflow["jobs"]["python"]
    run_index = producer["steps"].index(_step(producer, "Run Python tests with JUnit"))
    job_path = ("jobs", "python")
    run_path = job_path + ("steps", run_index)
    collector_path = ("jobs", "failure-identity")
    mutations = [
        ("job masks failure", job_path + ("continue-on-error",), True),
        ("step masks failure", run_path + ("continue-on-error",), True),
        ("shell masks failure", run_path + ("run",), COMMAND + " || true"),
        ("shell disables fail-fast", run_path + ("run",), "set +e\n" + COMMAND),
        ("collector skips failed producer", collector_path + ("if",), "success()"),
        ("collector loses dependency", collector_path + ("needs",), []),
        ("collector missing report", collector_path + ("with", "expected-reports"), "[]"),
        ("collector unexpected report", collector_path + ("with", "expected-reports"),
         json.dumps(["junit-python-3.15/python-3.15.xml",
                     "junit-controlled-python-3.15/junit.xml", "unexpected/report.xml"])),
    ]
    for name in ("junit-python-3.15", "junit-controlled-python-3.15"):
        index = producer["steps"].index(_upload(producer, name))
        path = job_path + ("steps", index)
        mutations.extend([
            (name + " loses always", path + ("if",), None),
            (name + " ignores missing XML", path + ("with", "if-no-files-found"), "ignore"),
            (name + " retains raw for 90 days", path + ("with", "retention-days"), 90),
        ])
    browser_job = workflow["jobs"]["python-browser"]
    browser_index = browser_job["steps"].index(_upload(browser_job, "python-browser-evidence"))
    mutations.extend([
        ("raw JUnit duplicated", ("jobs", "python-browser", "steps", browser_index, "with", "path"),
         "test-results\n!test-results/controlled-failure/**\n"),
        ("controlled evidence duplicated", ("jobs", "python-browser", "steps", browser_index, "with", "path"),
         "test-results\n!test-results/junit/**\n"),
    ])
    for label, path, value in mutations:
        mutated = deepcopy(workflow)
        target = mutated
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        try:
            _assert_contract(mutated)
        except AssertionError:
            continue
        pytest.fail(f"unsafe workflow mutation escaped: {label}")


# ═══ · browser test kit vendor automation ════════════════════════════════════════════════════════════════
# CI contract for vendor.lock.json verification (no promote/update sidecars).

BROWSER_TEST_KIT_VENDOR_AUTOMATION_WORKFLOW = '.github/workflows/ci.yml'
TEST_JOB = 'python'
LOCK = json.loads((ROOT / 'vendor.lock.json').read_text())
SNAPSHOT = ['vendor.lock.json'] + [e['destination'] for e in LOCK['files']]
PIN = '1368174821fb03be258ecf3312ce0ea809f6ea22'


def _workflow():
    import yaml
    return yaml.load((ROOT / BROWSER_TEST_KIT_VENDOR_AUTOMATION_WORKFLOW).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_vendor_lock_lists_all_checked_in_sources():
    expected = {(e['repository'], e['source'], e['destination']) for e in LOCK['files']}
    assert len(expected) == len(LOCK['files'])


def test_public_vendor_ci_verifies_lock_without_repository_writes():
    ci = _workflow()
    assert {"push", "pull_request"} <= set(ci["on"])
    assert "schedule" not in ci["on"]
    assert "resolve-vendor" not in ci["jobs"]
    jobs = ci["jobs"]
    for job in (jobs[TEST_JOB], jobs["vendor-locked"]):
        assert job.get("permissions") == {"contents": "read"}
        assert "continue-on-error" not in job
    text = (ROOT / BROWSER_TEST_KIT_VENDOR_AUTOMATION_WORKFLOW).read_text(encoding="utf-8")
    assert "vendor_sync.py promote" not in text
    assert "vendor_sync.py update" not in text
    assert "sync_vendor_provenance" not in text
    assert "provenance.json" not in text
    assert not any(x in text for x in ("secrets.", "VENDOR_UPDATE_TOKEN", "VENDOR_UPDATES_ENABLED", "GH_TOKEN"))
    for name in (TEST_JOB, "vendor-locked"):
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
    locked = jobs["vendor-locked"]["steps"]
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
    job = ci['jobs']['vendor-locked']
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


# ═══ · node26 profile workflow ═══════════════════════════════════════════════════════════════════════════
# The live Node browser lane deliberately selects the exact Node 26 profile.

def test_live_browser_workflow_keeps_pins_and_exact_node26_guards():
    workflow = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())
    step = next(s for s in workflow['jobs']['node']['steps']
                if s.get('name') == 'Prepare and install the fixed 30-package Node browser environment')
    assert step['env'] == {
        'NANO_PARENT_REV': '8ce289a78754cf8ca0ae2ca3889b52281929fdc4',
        'NANO_HELPER_SHA256': '349921219cfdcd8a5054df962c7132324ac7134784f73147d1e8867ab6aa01b0',
    }
    script = step['run']
    for suffix in ('FEATURE', 'SOURCE_BLOBS', 'SELECTED', 'EXPECTED_PATHS'):
        assert 'nano.BTK_NODE26_PARSERS_' + suffix in script
        assert 'nano.BTK_' + suffix not in script
    assert 'hashlib.sha256(data).hexdigest() != digest' in script
    assert 'dependencies_only=True' in script
    assert "receipt['candidate_locked_package_records'] != 30" in script
    assert 'installed != expected or len(installed) != 30' in script
    assert 'nano.validate_projection(' in script
    assert 'nano.read_snapshot(checkout' in script
    assert "'live_checkout_tests': True" in script
    assert '|| true' not in script


# ═══ · btk fetch ═════════════════════════════════════════════════════════════════════════════════════════
# btk fetch: integrity first, then only the named members; no traversal; fixed host list.

def make_archive(path: Path, members: dict) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, (body, mode) in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            info.mode = mode
            archive.addfile(info, io.BytesIO(body))
    data = buffer.getvalue()
    path.write_bytes(data)
    return data


def sri(data: bytes) -> str:
    return "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()


def test_extracts_only_the_named_member_and_keeps_it_executable(tmp_path):
    archive = tmp_path / "pkg.tgz"
    data = make_archive(archive, {
        "package/bin/node": (b"#!/bin/sh\necho v24\n", 0o755),
        "package/README": (b"not needed", 0o644),
    })
    result = btk.fetch_members(str(archive), sri(data), ["package/bin/node"], str(tmp_path / "out"))
    assert result["ok"] is True
    extracted = tmp_path / "out" / "package" / "bin" / "node"
    assert extracted.read_bytes() == b"#!/bin/sh\necho v24\n"
    assert extracted.stat().st_mode & 0o111
    assert not (tmp_path / "out" / "package" / "README").exists()


def test_integrity_mismatch_extracts_nothing(tmp_path):
    archive = tmp_path / "pkg.tgz"
    make_archive(archive, {"package/bin/node": (b"x", 0o755)})
    wrong = "sha256-" + base64.b64encode(b"0" * 32).decode()
    with pytest.raises(btk.FetchError, match="integrity"):
        btk.fetch_members(str(archive), wrong, ["package/bin/node"], str(tmp_path / "out"))
    assert not (tmp_path / "out").exists()


def test_missing_member_and_traversal_are_refused(tmp_path):
    archive = tmp_path / "pkg.tgz"
    data = make_archive(archive, {"package/bin/node": (b"x", 0o755)})
    with pytest.raises(btk.FetchError, match="not found"):
        btk.fetch_members(str(archive), sri(data), ["package/bin/npm"], str(tmp_path / "out"))
    with pytest.raises(btk.FetchError, match="plain relative"):
        btk.fetch_members(str(archive), sri(data), ["../escape"], str(tmp_path / "out"))
    with pytest.raises(btk.FetchError, match="plain relative"):
        btk.fetch_members(str(archive), sri(data), ["/etc/passwd"], str(tmp_path / "out"))


def test_hosts_outside_the_allowlist_are_refused():
    with pytest.raises(btk.FetchError, match="not allowed"):
        btk.read_archive("https://example.com/pkg.tgz")
    with pytest.raises(btk.FetchError):
        btk.read_archive("http://registry.npmjs.org/pkg.tgz")


def test_integrity_formats_are_parsed_or_refused():
    assert btk.parse_integrity("sha256-" + base64.b64encode(b"a" * 32).decode())[0] == "sha256"
    assert btk.parse_integrity("ab" * 32)[0] == "sha256"
    with pytest.raises(btk.FetchError):
        btk.parse_integrity("md5-abc")


def test_a_named_part_from_the_lock_file_resolves_source_integrity_and_member(tmp_path):
    archive = tmp_path / "node.tgz"
    data = make_archive(archive, {"package/bin/node": (b"#!/bin/sh\necho ok\n", 0o755)})
    lock = tmp_path / "parts.lock.json"
    lock.write_text(__import__("json").dumps({"schema": "btk-parts-lock/1", "parts": {
        "node": {"source": str(archive), "integrity": sri(data), "member": "package/bin/node"},
        "chromium": {"source": "environment", "integrity": None, "member": None},
    }}), encoding="utf-8")
    args = btk.fetch_parser().parse_args([
        "--lock", str(lock), "--part", "node", "--dest", str(tmp_path / "out")])
    source, integrity, members = btk.resolve(args)
    result = btk.fetch_members(source, integrity, members, str(tmp_path / "out"))
    assert result["ok"] and (tmp_path / "out" / "package" / "bin" / "node").exists()
    with pytest.raises(btk.FetchError, match="cannot be fetched"):
        btk.load_part(str(lock), "chromium")
    with pytest.raises(btk.FetchError, match="no part named"):
        btk.load_part(str(lock), "firefox")


# ═══ · btk plan ══════════════════════════════════════════════════════════════════════════════════════════
# btk plan: per-job install recommendation from configs and later commands.

spec = importlib.util.spec_from_file_location("btk", ROOT / "tools" / "btk.py")
pip = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pip)

BTK_PLAN_WORKFLOW = """name: x
jobs:
  tests:
    steps:
      - run: npx playwright install --with-deps chromium firefox
      - run: npx playwright test
  shots:
    steps:
      - run: npx playwright install --with-deps --only-shell chromium
      - run: npx playwright screenshot --browser=chromium http://x a.png
  py:
    steps:
      - run: python -m playwright install --with-deps chromium
      - run: python -m pytest
"""


def repo(tmp_path, config):
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text(BTK_PLAN_WORKFLOW)
    (tmp_path / "playwright.config.js").write_text(config)
    return tmp_path


def test_channel_chromium_needs_full_build_only_where_the_config_is_used(tmp_path):
    rows = pip.plan(str(repo(tmp_path, "use: { channel: 'chromium' }, projects: [{ use: devices['Desktop Chrome'] }]")))
    by_line = {where: (state, text) for state, where, text in rows if state != "warn"}
    assert by_line[".github/workflows/ci.yml:5"] == ("suggest", "add --no-shell (playwright.config.js uses channel 'chromium')")
    assert by_line[".github/workflows/ci.yml:9"][0] == "ok"
    assert by_line[".github/workflows/ci.yml:13"] == ("suggest", "add --only-shell (job runs headless only)")
    assert ("warn", ".github/workflows/ci.yml:5", "installs firefox but no config project uses it") in rows


MIXED = """name: x
jobs:
  collect:
    steps:
      - run: python -m playwright install --with-deps chromium firefox webkit
      - run: |
          for browser in chromium firefox webkit; do
            collect --browser "$browser" --profile desktop
          done
          xvfb-run -a python tools/platform_probe.py collect --browser "$browser" --headed
  headed_only:
    steps:
      - run: python -m playwright install --with-deps chromium firefox webkit
      - run: xvfb-run -a python tools/platform_probe.py collect --browser chromium --headed
  branded:
    steps:
      - run: python -m playwright install --with-deps --no-shell chromium
      - run: python tools/platform_probe.py collect --browser chromium --channel chrome --headed
"""


def test_headed_and_headless_chromium_keep_both_builds(tmp_path):
    root = repo(tmp_path, "projects: [{ name: 'chromium' }, { name: 'firefox' }, { name: 'webkit' }]")
    (root / ".github" / "workflows" / "ci.yml").write_text(MIXED)
    by_line = {where: (state, text) for state, where, text in pip.plan(str(root)) if state != "warn"}
    assert by_line[".github/workflows/ci.yml:5"] == ("ok", "full + shell (job uses both the full build and the shell)")
    assert by_line[".github/workflows/ci.yml:13"] == ("suggest", "add --no-shell (job runs --headed)")
    assert by_line[".github/workflows/ci.yml:17"] == ("ok", "--no-shell (job runs --headed)")


def test_platform_matrix_keeps_the_unflagged_install(capsys):
    workflow = (ROOT / ".github" / "workflows" / "platform-matrix.yml").read_text()
    install = "python -m playwright install chromium firefox webkit"
    assert install in workflow
    assert "--with-deps" not in workflow
    assert workflow.index("playwright install-deps chromium firefox webkit") < workflow.index(install)
    assert "--no-shell" not in workflow
    probe = (ROOT / "tools" / "platform_probe.py").read_text()
    assert "headed: bool = False" in probe
    assert "headless=not headed" in probe
    assert 'for browser in chromium firefox webkit; do' in workflow
    assert 'collect --browser "$browser" --profile "$profile"' in workflow
    assert pip.plan_cli([str(ROOT)]) == 0
    out = capsys.readouterr().out
    assert "platform-matrix.yml:50 full + shell (job uses both the full build and the shell)" in out
    assert "suggest" not in out


def test_headless_config_suggests_only_shell_and_cli(tmp_path, capsys):
    root = repo(tmp_path, "projects: [{ name: 'chromium' }, { name: 'firefox' }]")
    rows = pip.plan(str(root))
    assert rows[0] == ("suggest", ".github/workflows/ci.yml:5", "add --only-shell (job runs headless only)")
    assert pip.plan_cli([str(root)]) == 1
    assert pip.plan_cli([str(tmp_path / "nope")]) == 2
    empty = tmp_path / "empty"
    empty.mkdir()
    assert pip.plan_cli([str(empty)]) == 0 and "no playwright install lines" in capsys.readouterr().out


def test_install_deps_is_not_a_browser_install():
    """`playwright install-deps` installs system libraries; it must not be planned as a browser install."""
    assert pip.INSTALL.search("npx playwright install-deps --dry-run chromium firefox webkit") is None
    assert pip.INSTALL.search("npx playwright install-deps chromium firefox webkit") is None
    match = pip.INSTALL.search("npx playwright install --only-shell chromium")
    assert match is not None and match.group(1).split() == ["--only-shell", "chromium"]


def _pw_test_repository(tmp_path, exit_code=0):
    """A repository whose "Playwright CLI" is a Python script, so the run needs no Node: it prints its arguments and a result."""
    root = tmp_path / "repo"
    cli = root / "node_modules/@playwright/test/cli.js"
    cli.parent.mkdir(parents=True)
    (root / "playwright.config.js").write_text("module.exports = {};\n", encoding="utf-8")
    cli.write_text("import sys\nprint('ARGS', *sys.argv[1:])\nprint('noise')\nprint('  3 passed (1.0s)')\n"
                   f"print('  1 failed', file=sys.stderr)\nraise SystemExit({exit_code})\n", encoding="utf-8")
    browser = tmp_path / "chrome"
    browser.write_text("", encoding="utf-8")
    return root, browser


def test_pw_test_runs_the_repository_config_through_a_bridge_and_keeps_a_short_receipt(tmp_path, capsys):
    root, browser = _pw_test_repository(tmp_path, exit_code=1)
    base = ["--root", str(root), "--node", sys.executable, "--browser", str(browser)]
    assert btk.main(["pw-test", *base, "--dry-run", "tests/a.spec.js"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["schema"] == "btk-pw-test/1" and plan["browser"] == {"source": "explicit", "path": str(browser.resolve())}
    assert plan["command"][1:3] == [str(root / "node_modules/@playwright/test/cli.js"), "test"]
    assert plan["command"][-2:] == ["tests/a.spec.js", "--reporter=line"] and plan["cwd"] == str(root)
    assert btk.main(["pw-test", *base, "tests/a.spec.js"]) == 1                                   # the run's own exit code
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["exit_code"] == 1 and receipt["timed_out"] is False
    assert receipt["lines"] == ["  3 passed (1.0s)", "  1 failed"]                                # the result lines only
    assert btk.main(["pw-test", *base, "--limit", "1"]) == 1
    assert json.loads(capsys.readouterr().out)["lines"] == ["  3 passed (1.0s)"]
    bridge = btk.PW_TEST_BRIDGE % json.dumps({"config": str(root / "playwright.config.js"), "browser": str(browser)})
    assert "next.launchOptions.executablePath = o.browser" in bridge and "delete next.channel" in bridge and str(browser) in bridge


def test_pw_test_reports_timeout_missing_node_and_usage_errors(tmp_path, capsys):
    root, browser = _pw_test_repository(tmp_path)
    (root / "node_modules/@playwright/test/cli.js").write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    base = ["--root", str(root), "--browser", str(browser)]
    assert btk.main(["pw-test", *base, "--node", sys.executable, "--timeout", "0.5"]) == 124
    assert json.loads(capsys.readouterr().out)["timed_out"] is True
    assert btk.main(["pw-test", *base, "--node", str(tmp_path / "no-such-node")]) == 127
    assert "error" in json.loads(capsys.readouterr().out)
    for bad in (["--root", str(tmp_path)], [*base, "--timeout", "0"], ["--root", str(root), "--browser", str(tmp_path / "missing")]):
        with pytest.raises(SystemExit) as stop:
            btk.main(["pw-test", *bad])
        assert stop.value.code == 2
    assert "pw-test" in btk.COMMANDS


def test_find_browser_uses_a_build_already_on_disk(tmp_path):
    def build(*parts):
        path = tmp_path.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        return str(path)
    expected = str(tmp_path / "chromium-1248" / "chrome-linux64" / "chrome")
    assert pip.find_browser("chromium", expected, roots=[], system=())["source"] == "missing"
    older = build("chromium-1200", "chrome-linux64", "chrome")
    assert pip.find_browser("chromium", expected, roots=[], system=()) == {"source": "revision", "path": older, "revision": 1200}
    shell = build("chromium_headless_shell-1243", "chrome-headless-shell-linux64", "chrome-headless-shell")
    assert pip.find_browser("chromium", expected, roots=[], system=())["path"] == shell                # newest; headless runs the shell
    assert pip.find_browser("chromium", expected, headless=False, roots=[], system=())["path"] == older
    build("chromium_headless_shell-1248", "chrome-headless-shell-linux64", "chrome-headless-shell")
    assert pip.find_browser("chromium", expected, roots=[], system=())["source"] == "playwright"       # --only-shell installs count
    assert pip.find_browser("chromium", older, roots=[], system=())["source"] == "playwright"
    elsewhere = tmp_path / "cache"
    other = build("cache", "firefox-1500", "firefox", "firefox")
    assert pip.find_browser("firefox", str(tmp_path / "firefox-1510" / "firefox" / "firefox"), roots=[elsewhere], system=())["path"] == other
    assert pip.find_browser("webkit", None, roots=[], system=(older,))["source"] == "missing"          # no system WebKit
    assert pip.find_browser("chromium", None, roots=[], system=(older,)) == {"source": "system", "path": older}


def test_find_browser_same_revision_in_other_cache_needs_explicit_path(tmp_path):
    for engine, folder, executable in (
        ("firefox", "firefox-1500", "firefox/firefox"),
        ("chromium", "chromium_headless_shell-1500", "chrome-headless-shell-linux64/chrome-headless-shell"),
    ):
        expected = tmp_path / "configured" / f"{engine}-1500" / ("firefox/firefox" if engine == "firefox" else "chrome-linux64/chrome")
        other = tmp_path / "other" / folder / executable
        other.parent.mkdir(parents=True, exist_ok=True)
        other.touch()
        assert pip.find_browser(engine, str(expected), roots=[tmp_path / "other"], system=()) == {
            "source": "revision", "path": str(other), "revision": 1500}


# ═══ · python m ══════════════════════════════════════════════════════════════════════════════════════════

SCRIPTS = Path(__file__).resolve().parents[2] / "tools"


@pytest.mark.parametrize("module", sorted(path.stem for path in SCRIPTS.glob("*.py")))
def test_every_script_runs_as_a_module(module: str, tmp_path: Path) -> None:
    """pyproject.toml installs each tools/*.py as a top-level module; each must support `python -m NAME --help`."""
    env = cli_args.build_env({"PYTHONPATH": str(SCRIPTS), "PYTHONDONTWRITEBYTECODE": "1"})
    done = cli_args.run_cli(module, "--help", python_options=("-S", "-m"), cwd=tmp_path, env=env, timeout=60)
    assert done.returncode == 0, done.stderr
    assert "usage" in done.stdout.lower()


def test_pw_test_bridge_preserves_engines_and_project_relative_paths(tmp_path):
    node = fleet_check.node(minimum=0)  # any Node version runs the CommonJS bridge
    if not node:
        pytest.skip("Node is required to execute the Playwright configuration bridge")
    root = tmp_path / "repo"
    root.mkdir()
    config = root / "playwright.config.cjs"
    original = {
        "testDir": "tests", "outputDir": "results", "snapshotDir": "snapshots",
        "use": {"viewport": {"width": 800, "height": 600}, "launchOptions": {"timeout": 1234}},
        "projects": [
            {"name": "chromium", "testDir": "tests/chrome", "outputDir": "results/chrome",
             "snapshotDir": "shots/chrome", "use": {"channel": "chrome"}},
            {"name": "firefox", "use": {"browserName": "firefox", "launchOptions": {"executablePath": "/own/firefox"}}},
            {"name": "webkit", "use": {"browserName": "webkit"}},
        ],
    }
    config.write_text("module.exports = " + json.dumps(original), encoding="utf-8")
    bridge = tmp_path / "bridge.cjs"  # deliberately not beside the original config
    bridge.write_text(btk.PW_TEST_BRIDGE % json.dumps({"config": str(config), "browser": "/fallback/chromium"}), encoding="utf-8")
    output = subprocess.run([node, "-e", "console.log(JSON.stringify(require(process.argv[1])))", str(bridge)],
                            check=True, capture_output=True, text=True)
    actual = json.loads(output.stdout)
    chrome, firefox, webkit = actual["projects"]
    assert chrome["use"]["launchOptions"] == {"timeout": 1234, "executablePath": "/fallback/chromium"}
    assert "channel" not in chrome["use"]
    assert firefox["use"]["launchOptions"] == {"timeout": 1234, "executablePath": "/own/firefox"}
    assert webkit["use"]["launchOptions"] == {"timeout": 1234}
    assert "executablePath" not in actual["use"]["launchOptions"]
    assert chrome["use"]["viewport"] == original["use"]["viewport"]
    for key, value in (("testDir", "tests/chrome"), ("outputDir", "results/chrome"), ("snapshotDir", "shots/chrome")):
        assert chrome[key] == str(root / value)
    assert actual["testDir"] == str(root / "tests")
    assert actual["snapshotDir"] == str(root / "snapshots")

    # Without projects Playwright's implicit project must stay implicit.
    del original["projects"]
    original["use"]["browserName"] = "firefox"
    config.write_text("module.exports = " + json.dumps(original), encoding="utf-8")
    output = subprocess.run([node, "-e", "console.log(JSON.stringify(require(process.argv[1])))", str(bridge)],
                            check=True, capture_output=True, text=True)
    actual = json.loads(output.stdout)
    assert "projects" not in actual
    assert "executablePath" not in actual["use"]["launchOptions"]
