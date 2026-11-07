import importlib.util, io, json, subprocess, sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "tools" / "btk_events.py"

_spec = importlib.util.spec_from_file_location("btk_events", MODULE_PATH)
btk_events = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(btk_events)


def test_emit_writes_one_json_line_with_defaults(capsys):
    stream = io.StringIO()
    written = btk_events.emit(stream, source="unit-test", event="probe")
    line = stream.getvalue().strip()
    assert line.count("\n") == 0
    record = json.loads(line)
    assert record == written
    assert record["schema"] == "btk-event/1"
    assert record["source"] == "unit-test"
    assert record["event"] == "probe"
    assert "time" in record and "T" in record["time"]


def test_emit_rejects_missing_required_field():
    stream = io.StringIO()
    with pytest.raises(btk_events.EventError, match="source"):
        btk_events.emit(stream, event="probe")
    assert stream.getvalue() == ""  # nothing written on a rejected event


def test_emit_rejects_unknown_status():
    stream = io.StringIO()
    with pytest.raises(btk_events.EventError, match="status"):
        btk_events.emit(stream, source="unit-test", event="probe", status="ok")
    assert stream.getvalue() == ""


@pytest.mark.parametrize("status", sorted(btk_events.STATUSES))
def test_emit_accepts_every_vocabulary_status(status):
    stream = io.StringIO()
    btk_events.emit(stream, source="unit-test", event="probe", status=status)
    assert json.loads(stream.getvalue())["status"] == status


def test_emit_rejects_unknown_stage():
    stream = io.StringIO()
    with pytest.raises(btk_events.EventError, match="stage"):
        btk_events.emit(stream, source="unit-test", event="probe", stage="call")
    assert stream.getvalue() == ""


@pytest.mark.parametrize("stage", sorted(btk_events.STAGES))
def test_emit_accepts_every_vocabulary_stage(stage):
    stream = io.StringIO()
    btk_events.emit(stream, source="unit-test", event="probe", stage=stage)
    assert json.loads(stream.getvalue())["stage"] == stage


def test_emit_accepts_unconstrained_phase():
    # Unlike stage, phase carries no fixed vocabulary (pytest's own
    # setup/call/teardown report phases, or anything else a producer wants).
    stream = io.StringIO()
    btk_events.emit(stream, source="unit-test", event="probe", phase="call")
    assert json.loads(stream.getvalue())["phase"] == "call"


def test_read_from_path_skips_and_counts_malformed_lines(tmp_path):
    good = {"schema": "btk-event/1", "source": "unit-test", "time": "2026-01-01T00:00:00+00:00", "event": "probe"}
    path = tmp_path / "events.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(good),
                "not json at all",
                json.dumps({"source": "unit-test", "time": "x", "event": "missing-schema"}),
                "",  # blank lines are skipped, not counted
                json.dumps(good),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    result = btk_events.read(path)
    assert len(result.events) == 2
    assert result.malformed == 2
    assert result.total_lines == 4


def test_read_accepts_an_iterable_of_lines_and_a_string_path():
    good_line = json.dumps(
        {"schema": "btk-event/1", "source": "unit-test", "time": "2026-01-01T00:00:00+00:00", "event": "probe"}
    )
    result = btk_events.read([good_line, "garbage"])
    assert len(result.events) == 1
    assert result.malformed == 1


def test_classify_terminal_browser_mapping():
    classify = btk_events.classify_terminal_browser
    assert classify({"event": "TERMINAL_BROWSER_UNAVAILABLE"}) == "unavailable"
    assert classify({"event": "terminal_browser_exit", "returncode": 0}) == "passed"
    assert classify({"event": "terminal_browser_exit", "returncode": 1}) == "failed"
    assert classify({"event": "terminal_browser_exit", "returncode": 143, "signal": 15}) == "error"
    assert (
        classify({"event": "terminal_browser_exit", "returncode": 130, "signal": 2, "interrupted": True})
        == "interrupted"
    )
    assert classify({"event": "terminal_browser_found", "executable": "/x"}) is None


def test_help_runs_under_dash_s():
    result = subprocess.run(
        [sys.executable, "-S", "-m", "btk_events", "--help"],
        cwd=str(ROOT / "tools"),
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0
    assert "btk-event/1" in result.stdout
