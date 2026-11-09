import importlib.util, json, os, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "tools"

_be_spec = importlib.util.spec_from_file_location("btk_events", SCRIPTS / "btk_events.py")
btk_events = importlib.util.module_from_spec(_be_spec)
assert _be_spec.loader is not None
_be_spec.loader.exec_module(btk_events)

sys.modules.setdefault("btk_events", btk_events)  # so `import btk_events` inside evidence_board.py resolves
_spec = importlib.util.spec_from_file_location("evidence_board", SCRIPTS / "evidence_board.py")
evidence_board = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(evidence_board)


def _write_jsonl(path: Path, records):
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def _event(**fields):
    base = {"schema": "btk-event/1", "source": "unit-test", "time": "2026-01-01T00:00:00+00:00", "event": "x"}
    base.update(fields)
    return base


def test_aggregate_counts_status_and_group_and_first_failure_per_stage(tmp_path):
    path = tmp_path / "events.jsonl"
    _write_jsonl(
        path,
        [
            _event(event="test_result", status="passed", stage="assert", source="pytest-btk-events", project="p1"),
            _event(event="test_result", status="failed", stage="assert", source="pytest-btk-events", project="p1",
                   test_id="t1", message="boom"),
            _event(event="test_result", status="failed", stage="assert", source="pytest-btk-events", project="p1",
                   test_id="t2"),  # same stage: must not replace the first failure
            _event(event="terminal_browser_exit", status="unavailable", stage="launch", source="terminal-browser"),
            _event(event="lifecycle", source="terminal-browser"),  # no status: counted in group only
        ],
    )
    stats = evidence_board.aggregate([path], allow=set())
    assert stats["counts_by_status"] == {"passed": 1, "failed": 2, "unavailable": 1}
    assert stats["counts_by_group"] == {"pytest-btk-events/p1": 3, "terminal-browser": 2}
    assert set(stats["first_failure_by_stage"]) == {"assert"}
    assert stats["first_failure_by_stage"]["assert"]["test_id"] == "t1"
    assert stats["first_failure_by_stage"]["assert"]["message"] == "boom"
    assert stats["interrupted"] == 0
    assert stats["unavailable"] == 1
    assert stats["exit_code"] == 1  # failed + unavailable both block


def test_interrupted_and_unavailable_are_never_counted_as_passed(tmp_path):
    path = tmp_path / "events.jsonl"
    _write_jsonl(
        path,
        [
            _event(event="terminal_browser_exit", status="interrupted"),
            _event(event="terminal_browser_exit", status="unavailable"),
        ],
    )
    stats = evidence_board.aggregate([path], allow=set())
    assert stats["counts_by_status"].get("passed", 0) == 0
    assert stats["interrupted"] == 1
    assert stats["unavailable"] == 1
    assert stats["exit_code"] == 1


def test_allow_unavailable_makes_the_board_pass(tmp_path):
    path = tmp_path / "events.jsonl"
    _write_jsonl(path, [_event(event="terminal_browser_exit", status="unavailable")])
    stats = evidence_board.aggregate([path], allow={"unavailable"})
    assert stats["exit_code"] == 0
    assert stats["blocking_statuses"] == []


def test_malformed_lines_are_counted_not_crashing(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text("not json\n" + json.dumps(_event(status="passed")) + "\n", encoding="utf-8")
    stats = evidence_board.aggregate([path], allow=set())
    assert stats["malformed_lines"] == 1
    assert stats["counts_by_status"] == {"passed": 1}


def test_render_markdown_is_a_table_and_notes_callouts(tmp_path):
    path = tmp_path / "events.jsonl"
    _write_jsonl(path, [_event(event="terminal_browser_exit", status="unavailable")])
    stats = evidence_board.aggregate([path], allow=set())
    markdown = evidence_board.render_markdown(stats, [path])
    assert "| Status | Count |" in markdown
    assert "| unavailable | 1 |" in markdown
    assert "never counted as passed" in markdown
    assert "**Result: FAIL**" in markdown


def _run_cli(args, env=None):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "evidence_board.py"), *args],
        text=True,
        capture_output=True,
        env=env,
    )


def test_cli_exit_codes(tmp_path):
    passing = tmp_path / "pass.jsonl"
    _write_jsonl(passing, [_event(status="passed")])
    failing = tmp_path / "fail.jsonl"
    _write_jsonl(failing, [_event(status="failed")])

    ok = _run_cli([str(passing)])
    assert ok.returncode == 0
    assert "PASS" in ok.stdout

    bad = _run_cli([str(failing)])
    assert bad.returncode == 1
    assert "FAIL" in bad.stdout

    missing = _run_cli([str(tmp_path / "does-not-exist.jsonl")])
    assert missing.returncode == 2

    bad_allow = _run_cli([str(passing), "--allow", "not-a-status"])
    assert bad_allow.returncode == 2


def test_cli_json_output(tmp_path):
    path = tmp_path / "events.jsonl"
    _write_jsonl(path, [_event(status="passed")])
    result = _run_cli([str(path), "--json"])
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["counts_by_status"] == {"passed": 1}


def test_cli_step_summary_appends_to_env_file(tmp_path):
    path = tmp_path / "events.jsonl"
    _write_jsonl(path, [_event(status="passed")])
    summary = tmp_path / "step-summary.md"
    summary.write_text("existing content\n", encoding="utf-8")
    env = {"GITHUB_STEP_SUMMARY": str(summary), "PATH": os.environ.get("PATH", "")}

    result = _run_cli([str(path), "--step-summary"], env=env)
    assert result.returncode == 0
    text = summary.read_text(encoding="utf-8")
    assert text.startswith("existing content\n")
    assert "# Evidence board" in text


def test_session_summary_is_reported_separately_not_double_counted(tmp_path):
    path = tmp_path / "events.jsonl"
    _write_jsonl(
        path,
        [
            _event(event="test_result", test_id="t1", phase="call", status="passed", source="pytest-btk-events"),
            _event(
                event="session_summary", status="passed", source="pytest-btk-events",
                returncode=0, run_id="run-1",
            ),
        ],
    )
    stats = evidence_board.aggregate([path], allow=set())
    # The session's own rollup status must not add a second "passed" on top
    # of the one already counted for t1's test_result.
    assert stats["counts_by_status"] == {"passed": 1}
    assert len(stats["session_summaries"]) == 1
    assert stats["session_summaries"][0]["status"] == "passed"
    assert stats["session_summaries"][0]["run_id"] == "run-1"


def test_call_failure_then_teardown_error_counts_as_one_failed_test(tmp_path):
    """3 passed + 1 failed (whose teardown also errors) + 1 xfail (skipped)
    must show failed 1 / passed 3 / skipped 1 -- not failed 1 + error 1."""
    path = tmp_path / "events.jsonl"
    events = [
        _event(event="test_result", test_id="t_pass_1", phase="call", status="passed"),
        _event(event="test_result", test_id="t_pass_2", phase="call", status="passed"),
        _event(event="test_result", test_id="t_pass_3", phase="call", status="passed"),
        _event(event="test_result", test_id="t_xfail", phase="call", status="skipped"),
        _event(event="test_result", test_id="t_broken", phase="call", status="failed", message="assert 1 == 2"),
        _event(
            event="test_result", test_id="t_broken", phase="teardown", status="error",
            message="fixture teardown boom",
        ),
        _event(event="session_summary", status="failed", returncode=1),
    ]
    _write_jsonl(path, events)

    stats = evidence_board.aggregate([path], allow=set())
    assert stats["counts_by_status"] == {"passed": 3, "failed": 1, "skipped": 1}
    assert "t_broken" in stats["test_failures"]
    assert stats["test_failures"]["t_broken"]["status"] == "failed"
    assert stats["test_failures"]["t_broken"]["teardown_message"] == "fixture teardown boom"
    assert stats["exit_code"] == 1  # failed blocks the board

    markdown = evidence_board.render_markdown(stats, [path])
    assert "| failed | 1 |" in markdown
    assert "| passed | 3 |" in markdown
    assert "| skipped | 1 |" in markdown
    assert "also errored in teardown" in markdown


def test_setup_error_with_no_call_is_one_error_not_double_counted(tmp_path):
    path = tmp_path / "events.jsonl"
    _write_jsonl(
        path,
        [_event(event="test_result", test_id="t_setup", phase="setup", status="error", message="fixture boom")],
    )
    stats = evidence_board.aggregate([path], allow=set())
    assert stats["counts_by_status"] == {"error": 1}


def test_empty_input_exits_2_not_a_pass(tmp_path):
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")
    result = _run_cli([str(path)])
    assert result.returncode == 2
    assert "no valid" in result.stderr.lower()


def test_all_malformed_lines_exits_2_not_a_pass(tmp_path):
    path = tmp_path / "junk.jsonl"
    path.write_text("not json\nalso not json\n", encoding="utf-8")
    result = _run_cli([str(path)])
    assert result.returncode == 2


def test_malformed_line_fails_the_board_unless_allowed(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text("not json\n" + json.dumps(_event(status="passed")) + "\n", encoding="utf-8")

    result = _run_cli([str(path)])
    assert result.returncode == 1
    assert "**Result: FAIL**" in result.stdout

    allowed = _run_cli([str(path), "--allow-malformed"])
    assert allowed.returncode == 0

    stats = evidence_board.aggregate([path], allow=set())
    assert stats["exit_code"] == 1
    stats_allowed = evidence_board.aggregate([path], allow=set(), allow_malformed=True)
    assert stats_allowed["exit_code"] == 0


def test_help_runs_under_dash_s():
    result = subprocess.run(
        [sys.executable, "-S", "-m", "evidence_board", "--help"],
        cwd=str(SCRIPTS),
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0
    assert "evidence" in result.stdout.lower()
    assert "--allow" in result.stdout
