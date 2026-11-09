"""Exercise tools/pytest_btk_events.py the way a project actually loads it:
as a real, separate `pytest -p pytest_btk_events` subprocess against a tiny
temp test file. No pytester plugin is used or required.
"""
import hashlib, importlib.util, json, os, signal, subprocess, sys, time, xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "tools"

FIXTURE_TESTS = '''
import pytest

def test_pass():
    assert True

def test_fail():
    assert False

def test_skip():
    pytest.skip("not relevant here")

@pytest.mark.skip(reason="marker skip, evaluated during setup")
def test_marker_skip():
    assert True

@pytest.fixture
def broken_fixture():
    raise RuntimeError("fixture setup boom")

def test_setup_error(broken_fixture):
    assert True
'''


def _env_with_scripts_on_path():
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(SCRIPTS) + (os.pathsep + existing if existing else "")
    return env


def _read_events(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _wait_for_session_start(path: Path, timeout: float) -> None:
    """Poll ``path`` until it contains a ``session_start`` line.

    Replaces a fixed ``time.sleep()`` before sending SIGINT: a flat sleep is
    flaky under load (the subprocess interpreter/pytest startup can easily
    take longer than the sleep on a busy CI runner), and sending SIGINT
    before ``pytest_sessionstart`` has even run makes the test race the
    plugin's own startup instead of testing its interrupt handling.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    if line.strip() and json.loads(line).get("event") == "session_start":
                        return
            except (OSError, json.JSONDecodeError):
                pass  # file may be mid-write; keep polling
        time.sleep(0.02)
    raise AssertionError(f"{path}: no session_start event within {timeout}s")


def test_opt_in_writes_nothing_without_the_flag(tmp_path):
    test_file = tmp_path / "test_fixture.py"
    test_file.write_text(FIXTURE_TESTS, encoding="utf-8")
    env = _env_with_scripts_on_path()

    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(test_file), "-p", "pytest_btk_events", "-q"],
        env=env,
        text=True,
        capture_output=True,
    )

    # The plugin loaded (no import error) and ran the tests normally: no
    # --btk-events path was given, so it must not write anywhere on its own.
    assert result.returncode == 1, result.stdout + result.stderr  # test_fail + test_setup_error
    assert "Error" not in result.stderr
    assert not any(tmp_path.glob("*.jsonl"))


def test_records_one_event_per_test_with_the_right_status_and_phase(tmp_path):
    test_file = tmp_path / "test_fixture.py"
    test_file.write_text(FIXTURE_TESTS, encoding="utf-8")
    events_path = tmp_path / "btk-events.jsonl"
    env = _env_with_scripts_on_path()

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(test_file),
            "-p",
            "pytest_btk_events",
            "--btk-events",
            str(events_path),
            "-q",
        ],
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1, result.stdout + result.stderr  # test_fail + test_setup_error
    assert events_path.is_file()
    events = _read_events(events_path)
    assert all(event["schema"] == "btk-event/1" for event in events)
    assert all(event["source"] == "pytest-btk-events" for event in events)

    by_test = {
        event["test_id"].rsplit("::", 1)[-1]: event
        for event in events
        if event["event"] == "test_result"
    }
    # test_result events carry the pytest report phase in "phase", not
    # "stage": "stage" is reserved for btk_events.STAGES.
    assert all("stage" not in event for event in events)
    assert by_test["test_pass"]["status"] == "passed"
    assert by_test["test_pass"]["phase"] == "call"
    assert by_test["test_fail"]["status"] == "failed"
    assert by_test["test_fail"]["phase"] == "call"
    # A runtime pytest.skip() inside the test body is reported at the "call" phase.
    assert by_test["test_skip"]["status"] == "skipped"
    assert by_test["test_skip"]["phase"] == "call"
    # A skip marker is evaluated before the test body ever runs: "setup" phase.
    assert by_test["test_marker_skip"]["status"] == "skipped"
    assert by_test["test_marker_skip"]["phase"] == "setup"
    assert by_test["test_setup_error"]["status"] == "error"
    assert by_test["test_setup_error"]["phase"] == "setup"
    assert "boom" in by_test["test_setup_error"]["message"]

    events_by_name = [event["event"] for event in events]
    assert events_by_name[0] == "session_start"
    summary = next(event for event in events if event["event"] == "session_summary")
    assert summary["status"] == "failed"
    assert summary["returncode"] == 1
    assert summary["count_passed"] == 1
    assert summary["count_failed"] == 1
    assert summary["count_skipped"] == 2
    assert summary["count_error"] == 1


def _run_pytest(test_file, events_path, env, extra_args=()):
    return subprocess.run(
        [
            sys.executable, "-m", "pytest", str(test_file),
            "-p", "pytest_btk_events", "--btk-events", str(events_path), "-q", *extra_args,
        ],
        env=env,
        text=True,
        capture_output=True,
    )


def test_same_child_junit_xprobe_and_btk_bridge(tmp_path):
    """JUnit compact identity complements native phase evidence; neither is input."""
    vendor = ROOT / "tests" / "vendor" / "xprobe"
    lock = json.loads((ROOT / "vendor.lock.json").read_text(encoding="utf-8"))
    entries = {entry["destination"]: entry for entry in lock["files"]}
    xprobe = entries["tests/vendor/xprobe/xprobe.py"]
    assert xprobe["repository"] == "myon-bioinformatics/xprobe"
    for name in ("xprobe.py", "LICENSE"):
        entry = entries["tests/vendor/xprobe/" + name]
        data = (vendor / name).read_bytes()
        assert hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest() == entry["blob_sha"]
        assert hashlib.sha256(data).hexdigest() == entry["sha256"]
    spec = importlib.util.spec_from_file_location("bridge_xprobe", vendor / "xprobe.py")
    xprobe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(xprobe)

    # Synthetic sentinels prove raw diagnostics/parameter labels stay local.
    fixture = FIXTURE_TESTS.replace(
        "def test_fail():\n    assert False",
        '@pytest.mark.parametrize("value", ["PARAMETER_SENTINEL"], ids=["PARAMETER_SENTINEL"])\n'
        'def test_fail(value):\n    print("STDOUT_SENTINEL")\n    assert False, "ASSERTION_SENTINEL"',
    ).replace("fixture setup boom", "SETUP_SENTINEL")
    test_file = tmp_path / "test_fixture.py"
    test_file.write_text(fixture, encoding="utf-8")
    evidence = Path(os.environ.get("FAILURE_EVIDENCE_DIR", tmp_path / "evidence")).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    events_path = evidence / "btk-events.jsonl"
    junit_path = evidence / "junit.xml"
    repository = "myon-bioinformatics/browser-test-kit"
    run_id = "controlled-bridge"
    env = _env_with_scripts_on_path()
    env.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTEST_ADDOPTS="")
    plain = subprocess.run(
        [sys.executable, "-m", "pytest", "test_fixture.py", "--rootdir", str(tmp_path),
         "-p", "pytest_btk_events", "--btk-events", str(tmp_path / "plain-events.jsonl"),
         "--btk-events-project", repository, "--btk-events-run-id", run_id, "-q"],
        cwd=tmp_path, env=env, text=True, capture_output=True, timeout=60,
    )
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "test_fixture.py", "--rootdir", str(tmp_path),
         "-p", "pytest_btk_events", "--btk-events", str(events_path),
         "--btk-events-project", repository, "--btk-events-run-id", run_id,
         "--junitxml", str(junit_path), "-o", "junit_logging=all", "-q"],
        cwd=tmp_path, env=env, text=True, capture_output=True, timeout=60,
    )
    (evidence / "exit.json").write_text(
        json.dumps({"without_junit": plain.returncode, "with_junit": result.returncode}),
        encoding="utf-8",
    )
    assert plain.returncode == result.returncode == 1, plain.stdout + plain.stderr + result.stdout + result.stderr
    events = _read_events(events_path)
    assert all(e["schema"] == "btk-event/1" and e["project"] == repository
               and e["run_id"] == run_id for e in events)
    summary = next(e for e in events if e["event"] == "session_summary")
    assert (summary["status"], summary["returncode"]) == ("failed", 1)
    results = {e["test_id"]: e for e in events if e["event"] == "test_result"}
    raw = junit_path.read_text(encoding="utf-8")
    imported = xprobe.cases_from_junit(raw, repository=repository, report_id=run_id)
    assert imported["truncated"] is False
    cases = imported["cases"]
    assert len(cases) == 2
    expected = {"test_fail": ("failure", "failed", "call"),
                "test_setup_error": ("error", "error", "setup")}
    # Fixture-local, exact raw identity mapping; not a generic nodeid parser.
    junit_tests = {tc.attrib["name"]: tc for tc in ET.fromstring(raw).iter("testcase")}
    for case in cases:
        value = case["value"]
        kind, status, phase = expected[value["test"]]
        raw_name = ("test_fail[PARAMETER_SENTINEL]" if value["test"] == "test_fail"
                    else "test_setup_error")
        tc = junit_tests[raw_name]
        assert tc.attrib["classname"] == value["class"] == "test_fixture"
        assert tc.find(kind) is not None
        assert value == {"test": value["test"], "class": "test_fixture", "kind": kind}
        event = results["test_fixture.py::" + raw_name]
        assert (event["status"], event["phase"]) == (status, phase)
        assert "stage" not in event  # pytest phase must not become a BTK stage
        assert case["context"] == {"repository": repository, "commit_sha": None,
                                   "report_id": run_id}
    assert {c["value"]["test"] for c in cases} == set(expected)
    assert sum(e["status"] == "skipped" for e in results.values()) == 2
    compact = xprobe.corpus_to_json(cases)
    (evidence / "compact.json").write_text(compact, encoding="utf-8")
    assert xprobe.corpus_from_json(compact) == cases
    for sentinel in ("PARAMETER_SENTINEL", "STDOUT_SENTINEL", "ASSERTION_SENTINEL", "SETUP_SENTINEL"):
        assert sentinel in raw
        assert sentinel not in compact
    assert "Traceback" not in compact

    board = subprocess.run(
        [sys.executable, "-S", str(SCRIPTS / "evidence_board.py"), str(events_path), "--json"],
        text=True, capture_output=True, timeout=60,
    )
    (evidence / "board.json").write_text(board.stdout, encoding="utf-8")
    assert board.returncode == 1, board.stdout + board.stderr
    stats = json.loads(board.stdout)
    assert stats["exit_code"] == 1
    assert stats["blocking_statuses"] == ["error", "failed"]
    assert stats["counts_by_status"] == {"passed": 1, "failed": 1, "error": 1, "skipped": 2}
    assert stats["test_failures"]["test_fixture.py::test_fail[PARAMETER_SENTINEL]"]["status"] == "failed"
    assert stats["test_failures"]["test_fixture.py::test_setup_error"]["status"] == "error"
    assert stats["session_summaries"][0]["returncode"] == 1
    assert stats["first_failure_by_stage"] == {}  # phases remain in the source stream
    assert stats["malformed_lines"] == 0


def test_btk_events_path_is_truncated_by_default(tmp_path):
    test_file = tmp_path / "test_fixture.py"
    test_file.write_text("def test_pass():\n    assert True\n", encoding="utf-8")
    events_path = tmp_path / "btk-events.jsonl"
    events_path.write_text("stale content from a previous run\n", encoding="utf-8")
    env = _env_with_scripts_on_path()

    result = _run_pytest(test_file, events_path, env)

    assert result.returncode == 0, result.stdout + result.stderr
    text = events_path.read_text(encoding="utf-8")
    assert "stale content from a previous run" not in text
    events = _read_events(events_path)
    assert events[0]["event"] == "session_start"


def test_btk_events_append_flag_keeps_appending(tmp_path):
    test_file = tmp_path / "test_fixture.py"
    test_file.write_text("def test_pass():\n    assert True\n", encoding="utf-8")
    events_path = tmp_path / "btk-events.jsonl"
    env = _env_with_scripts_on_path()

    first = _run_pytest(test_file, events_path, env, extra_args=["--btk-events-append"])
    assert first.returncode == 0, first.stdout + first.stderr
    second = _run_pytest(test_file, events_path, env, extra_args=["--btk-events-append"])
    assert second.returncode == 0, second.stdout + second.stderr

    events = _read_events(events_path)
    session_starts = [event for event in events if event["event"] == "session_start"]
    assert len(session_starts) == 2  # both runs' events are present, nothing truncated


def test_xfail_maps_to_skipped(tmp_path):
    test_file = tmp_path / "test_xfail.py"
    test_file.write_text(
        "import pytest\n\n@pytest.mark.xfail(reason='known issue')\ndef test_expected_to_fail():\n    assert False\n",
        encoding="utf-8",
    )
    events_path = tmp_path / "btk-events.jsonl"
    env = _env_with_scripts_on_path()

    result = _run_pytest(test_file, events_path, env)

    assert result.returncode == 0, result.stdout + result.stderr  # xfail does not fail the session
    events = _read_events(events_path)
    test_result = next(event for event in events if event["event"] == "test_result")
    assert test_result["status"] == "skipped"
    assert test_result["phase"] == "call"


def test_project_and_run_id_are_recorded_on_every_event(tmp_path):
    test_file = tmp_path / "test_fixture.py"
    test_file.write_text("def test_pass():\n    assert True\n", encoding="utf-8")
    events_path = tmp_path / "btk-events.jsonl"
    env = _env_with_scripts_on_path()

    subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(test_file),
            "-p",
            "pytest_btk_events",
            "--btk-events",
            str(events_path),
            "--btk-events-project",
            "demo-project",
            "--btk-events-run-id",
            "run-123",
            "-q",
        ],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    events = _read_events(events_path)
    assert all(event["project"] == "demo-project" for event in events)
    assert all(event["run_id"] == "run-123" for event in events)


def test_no_tests_collected_reports_session_summary_failed(tmp_path):
    test_file = tmp_path / "test_empty.py"
    test_file.write_text("# no tests here\n", encoding="utf-8")
    events_path = tmp_path / "btk-events.jsonl"
    env = _env_with_scripts_on_path()

    result = _run_pytest(test_file, events_path, env)

    assert result.returncode == 5, result.stdout + result.stderr  # pytest: no tests collected
    events = _read_events(events_path)
    summary = next(event for event in events if event["event"] == "session_summary")
    assert summary["status"] == "failed"
    assert summary["returncode"] == 5


def test_keyboard_interrupt_is_reported_as_interrupted_at_session_finish(tmp_path):
    test_file = tmp_path / "test_slow.py"
    test_file.write_text(
        "import time\n\ndef test_slow():\n    time.sleep(5)\n",
        encoding="utf-8",
    )
    events_path = tmp_path / "btk-events.jsonl"
    env = _env_with_scripts_on_path()

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "pytest",
            str(test_file),
            "-p",
            "pytest_btk_events",
            "--btk-events",
            str(events_path),
            "-q",
        ],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_for_session_start(events_path, timeout=10)
        os.kill(proc.pid, signal.SIGINT)
        proc.communicate(timeout=15)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    events = _read_events(events_path)
    summaries = [event for event in events if event["event"] == "session_summary"]
    assert len(summaries) == 1
    assert summaries[0]["status"] == "interrupted"
    assert summaries[0]["returncode"] == 2
