"""Evidence: btk events and evidence commands, the board, GIFs, JSONL digests, the pytest plugin and image diffs."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import signal
import struct
import subprocess
import sys
import textwrap
import time
import xml.etree.ElementTree as ET
import zipfile
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


# ═══ · btk events ════════════════════════════════════════════════════════════════════════════════════════

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


# ═══ · btk evidence ══════════════════════════════════════════════════════════════════════════════════════
# 📸 btk.py's evidence commands, which were separate tools: check-png (check_png.py), check-evidence (check_evidence.py),
# check-capture-evidence / write-capture-evidence (check_capture_evidence.py, write_capture_evidence.py) and
# probe-capture-rejections (probe_capture_rejections.py). The CLIs run under ``python -S``.

BTK = ROOT / "tools" / "btk.py"
spec = importlib.util.spec_from_file_location("btk", BTK)
btk = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(btk)


# ---- check-png ----------------------------------------------------------------------------------------------------

def test_png_header_dimensions() -> None:
    assert btk.png_dimensions(minimal_png()) == (390, 844)

def test_rejects_non_png() -> None:
    with pytest.raises(ValueError):
        btk.png_dimensions(b"not an image")

def test_rejects_wrong_ihdr_length() -> None:
    data = btk.PNG_SIGNATURE + struct.pack(">I", 12) + b"IHDR" + b"x" * 32 + b"IEND" + b"xxxx"
    with pytest.raises(ValueError, match="IHDR"):
        btk.png_dimensions(data)

def test_rejects_missing_iend() -> None:
    with pytest.raises(ValueError, match="IEND"):
        btk.png_dimensions(minimal_png()[:-8] + b"NOPE1234")


def test_check_png_cli_prints_dimensions(tmp_path):
    (tmp_path / "a.png").write_bytes(minimal_png())
    done = subprocess.run([sys.executable, "-S", str(BTK), "check-png", str(tmp_path / "a.png")], capture_output=True, text=True)
    assert done.returncode == 0 and done.stdout.strip().endswith(": png 390x844 45 bytes")
    assert subprocess.run([sys.executable, "-S", str(BTK), "check-png"], capture_output=True).returncode == 2


# ---- check-evidence: one PNG per project ---------------------------------------------------------------------------

NODE_PROJECTS = ["chromium", "firefox", "webkit", "mobile-chromium", "mobile-webkit"]


def minimal_png(width: int = 390, height: int = 844) -> bytes:
    return (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height)
            + b"\x08\x06\x00\x00\x00" + b"xxxx" + struct.pack(">I", 0) + b"IEND" + b"xxxx")


def node_evidence(root: Path, project: str, *, attempt: str = "", png: bytes | None = None, **extra) -> Path:
    directory = root / f"smoke-shared-fixture-{project}{attempt}"
    directory.mkdir(parents=True)
    (directory / "evidence.png").write_bytes(minimal_png() if png is None else png)
    meta = {"runtime": "node", "browser": project.split("-")[-1], "project": project, "stage": "complete",
            "artifact": "evidence.png", **extra}
    (directory / "evidence.json").write_text(json.dumps(meta) + "\n", encoding="utf-8")
    return directory


def run(directory: Path, pattern: str, expected: list) -> tuple:
    return btk.check_evidence(directory, pattern, expected)


def test_every_expected_node_project_passes(tmp_path: Path) -> None:
    for project in NODE_PROJECTS:
        node_evidence(tmp_path, project)
    (tmp_path / ".last-run.json").write_text('{"status": "passed"}', encoding="utf-8")
    ok, notes, problems = run(tmp_path, "**/evidence.json", NODE_PROJECTS)
    assert problems == [] and notes == []
    assert len(ok) == 5 and all("png 390x844" in line for line in ok)


def test_missing_desktop_evidence_fails_even_when_mobile_evidence_exists(tmp_path: Path) -> None:
    node_evidence(tmp_path, "mobile-chromium")
    node_evidence(tmp_path, "mobile-webkit")
    _, _, problems = run(tmp_path, "**/evidence.json", ["chromium", "mobile-chromium", "webkit", "mobile-webkit"])
    assert problems == ["missing complete evidence for project(s): chromium, webkit"]


def test_every_retry_artifact_is_validated(tmp_path: Path) -> None:
    node_evidence(tmp_path, "chromium")
    node_evidence(tmp_path, "chromium", attempt="-retry1")
    ok, _, problems = run(tmp_path, "**/evidence.json", ["chromium"])
    assert problems == [] and len(ok) == 2
    node_evidence(tmp_path, "chromium", attempt="-retry2", png=b"not a png")
    _, _, problems = run(tmp_path, "**/evidence.json", ["chromium"])
    assert len(problems) == 1 and "retry2" in problems[0] and "not a valid PNG header" in problems[0]


def test_complete_record_without_its_png_fails(tmp_path: Path) -> None:
    directory = node_evidence(tmp_path, "firefox")
    (directory / "evidence.png").unlink()
    _, _, problems = run(tmp_path, "**/evidence.json", ["firefox"])
    assert "evidence.png for firefox" in problems[0]
    assert problems[1] == "missing complete evidence for project(s): firefox"


def test_python_layout_failure_records_do_not_count(tmp_path: Path) -> None:
    (tmp_path / "webkit-desktop.png").write_bytes(minimal_png(1280, 720))
    (tmp_path / "webkit-desktop.json").write_text(json.dumps({
        "runtime": "python", "project": "webkit-desktop", "stage": "complete", "artifact": "webkit-desktop.png",
        "width": 1280, "height": 720}), encoding="utf-8")
    (tmp_path / "firefox-mobile.json").write_text(json.dumps({
        "runtime": "python", "project": "firefox-mobile", "stage": "navigate", "error": "TimeoutError"}),
        encoding="utf-8")
    (tmp_path / "firefox-mobile-failure.png").write_bytes(minimal_png())
    ok, notes, problems = run(tmp_path, "*.json", ["webkit-desktop", "firefox-mobile"])
    assert ok == [f"ok webkit-desktop: {tmp_path / 'webkit-desktop.png'} png 1280x720"]
    assert notes == [f"{tmp_path / 'firefox-mobile.json'}: firefox-mobile attempt ended at stage 'navigate'; "
                     "not counted"]
    assert problems == ["missing complete evidence for project(s): firefox-mobile"]


def test_recorded_dimensions_must_match_the_png(tmp_path: Path) -> None:
    node_evidence(tmp_path, "webkit", width=100, height=200)
    _, _, problems = run(tmp_path, "**/evidence.json", ["webkit"])
    assert "metadata says 100x200, PNG header says 390x844" in problems[0]


def test_metadata_without_project_or_unreadable_fails(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "evidence.json").write_text('{"stage": "complete"}', encoding="utf-8")
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "evidence.json").write_text("{not json", encoding="utf-8")
    _, _, problems = run(tmp_path, "**/evidence.json", ["chromium"])
    assert 'metadata has no "project"' in problems[0]
    assert "unreadable metadata" in problems[1]


def test_project_names_match_exactly_and_extras_are_noted(tmp_path: Path) -> None:
    node_evidence(tmp_path, "mobile-chromium")
    _, notes, problems = run(tmp_path, "**/evidence.json", ["chromium"])
    assert problems == ["missing complete evidence for project(s): chromium"]
    assert notes == ["evidence for project(s) not in --expect: mobile-chromium"]


def test_missing_directory_reports_every_expected_project(tmp_path: Path) -> None:
    _, _, problems = run(tmp_path / "absent", "*.json", ["chromium-desktop", "webkit-mobile"])
    assert problems == [f"{tmp_path / 'absent'}: no such directory",
                        "missing complete evidence for project(s): chromium-desktop, webkit-mobile"]


def test_cli_exit_codes_without_site_packages(tmp_path: Path) -> None:
    node_evidence(tmp_path, "chromium")
    cli = [sys.executable, "-S", str(BTK), "check-evidence", str(tmp_path), "--expect"]
    passed = subprocess.run([*cli, "chromium"], capture_output=True, text=True)
    assert passed.returncode == 0 and passed.stdout.startswith("ok chromium: ")
    failed = subprocess.run([*cli, "chromium,firefox"], capture_output=True, text=True)
    assert failed.returncode == 1
    assert "error: missing complete evidence for project(s): firefox" in failed.stderr
    usage = subprocess.run([*cli, " , "], capture_output=True, text=True)
    assert usage.returncode == 2


# ---- check-capture-evidence: multi-capture receipts ----------------------------------------------------------------

SHA = "a" * 40
CANONICAL = {"head": {"sha": SHA, "timestamp": "2026-09-30T01:00:00Z"},
             "generated_at": "2026-09-30T02:00:00Z"}
FILES = ["home.png", "build-diagnostics.png"]


def png(width=1, height=1):
    def chunk(name, data):
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">II5B", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\0\xff\0\0\xff")) + chunk(b"IEND", b""))


def receipt(root, project="chromium", attempt="", **changes):
    directory = root / (project + attempt)
    directory.mkdir(parents=True)
    records = []
    for filename in FILES:
        data = png()
        (directory / filename).write_bytes(data)
        records.append({"file": filename, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    value = {"project": project, "sha": SHA, "committed_at": CANONICAL["head"]["timestamp"],
             "generated_at": CANONICAL["generated_at"], "captures": records, **changes}
    save(directory, value)
    return directory, value


def save(directory, value):
    (directory / "evidence.json").write_text(json.dumps(value), encoding="utf-8")


def check(root, **kwargs):
    return btk.check_capture_evidence(root, "**/evidence.json", ["chromium", "webkit"], FILES, SHA,
                           canonical=CANONICAL, **kwargs)


def test_matrix_retries_and_flutter_legacy_shape(tmp_path):
    for project in ["chromium", "webkit"]:
        receipt(tmp_path, project)
        receipt(tmp_path, project, "-retry1")
    ok, notes, problems = check(tmp_path)
    assert ok and not problems
    assert "run identity" in notes[0]


@pytest.mark.parametrize("field,value", [("sha", "b" * 40), ("committed_at", "stale"),
                                        ("generated_at", "stale"), ("captures", None),
                                        ("project", None)])
def test_bad_receipt_is_not_covered(tmp_path, field, value):
    receipt(tmp_path, **{field: value}) if field != "project" else receipt(tmp_path, project="chromium")
    if field == "project":
        directory = tmp_path / "chromium"
        data = json.loads((directory / "evidence.json").read_text())
        data["project"] = value
        save(directory, data)
    assert not check(tmp_path)[0]


@pytest.mark.parametrize("change", ["missing", "duplicate", "hash", "size", "bool-size", "width", "corrupt", "zero", "escape", "absolute", "symlink"])
def test_invalid_capture_cannot_hide_behind_valid_retry(tmp_path, change):
    for project in ["chromium", "webkit"]:
        receipt(tmp_path, project)
    directory, value = receipt(tmp_path, "chromium", "-retry1")
    capture = value["captures"][0]
    if change == "missing":
        value["captures"].pop()
    elif change == "duplicate":
        value["captures"].append(dict(capture))
    elif change == "hash":
        capture["sha256"] = "0" * 64
    elif change == "size":
        capture["bytes"] += 1
    elif change == "bool-size":
        capture["bytes"] = True
    elif change == "width":
        capture["width"] = 2
    elif change in {"corrupt", "zero"}:
        data = b"broken" if change == "corrupt" else png(width=0)
        (directory / FILES[0]).write_bytes(data)
        capture.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    else:
        outside = tmp_path / "outside.png"
        outside.write_bytes(png())
        if change == "escape":
            capture["file"] = "../outside.png"
        elif change == "absolute":
            capture["file"] = str(outside)
        else:
            (directory / FILES[0]).unlink()
            (directory / FILES[0]).symlink_to(outside)
    save(directory, value)
    ok, _, problems = check(tmp_path)
    assert not ok and any("retry1" in problem for problem in problems)


def test_partial_receipts_do_not_combine_into_complete_scenario_set(tmp_path):
    for attempt, index in [("", 0), ("-retry", 1)]:
        directory, value = receipt(tmp_path, attempt=attempt)
        value["captures"] = [value["captures"][index]]
        save(directory, value)
    assert not check(tmp_path)[0]


def test_failure_receipt_ignored_and_missing_project_fails(tmp_path):
    receipt(tmp_path)
    receipt(tmp_path, "webkit", stage="failed")
    ok, notes, problems = check(tmp_path)
    assert not ok and any("incomplete" in note for note in notes)
    assert any("webkit" in problem for problem in problems)


def test_run_identity(tmp_path):
    for project in ["chromium", "webkit"]:
        receipt(tmp_path, project, run_id="123", run_attempt="2")
    assert check(tmp_path, run_id="123", run_attempt="2")[0]
    assert not check(tmp_path, run_id="123", run_attempt="1")[0]
    assert not check(tmp_path, run_id="124", run_attempt="2")[0]


@pytest.mark.parametrize("canonical", [None, {}, [], {**CANONICAL, "head": {"sha": "b" * 40}}])
def test_invalid_canonical_shape(tmp_path, canonical):
    receipt(tmp_path)
    # None deliberately disables canonical comparison.
    assert btk.check_capture_evidence(tmp_path, "**/evidence.json", ["chromium"], FILES, SHA, canonical)[0] is (canonical is None)


def test_cli_without_site_packages_and_exit_codes(tmp_path):
    for project in ["chromium", "webkit"]:
        receipt(tmp_path, project)
    args = [sys.executable, "-S", str(BTK), "check-capture-evidence", str(tmp_path), "--expect", "chromium,webkit",
            "--captures", ",".join(FILES), "--sha", SHA]
    result = subprocess.run(args, capture_output=True, text=True)
    assert result.returncode == 0 and "PASS" in result.stdout
    (tmp_path / "webkit" / FILES[0]).unlink()
    assert subprocess.run(args, capture_output=True).returncode == 1
    assert subprocess.run(args[:-1] + ["short"], capture_output=True).returncode == 2
    canonical = tmp_path / "canonical.json"
    canonical.write_text("null")
    result = subprocess.run(args + ["--canonical", str(canonical)], capture_output=True, text=True)
    assert result.returncode == 1 and "must be an object" in result.stderr


@pytest.mark.parametrize("value", [[], "text", {"captures": []}])
def test_malformed_receipt(tmp_path, value):
    tmp_path.joinpath("evidence.json").write_text(json.dumps(value))
    assert not check(tmp_path)[0]


# ---- write-capture-evidence and probe-capture-rejections against each consumer's bundle shape -------------------------

@pytest.mark.parametrize("names", [
    ["original.png", "roundtrip.png"],
    [f"ironmate-{i}.png" for i in range(6)],
    [f"web-ui-{i}.png" for i in range(6)],
    ["pixiv-pages-desktop.png", "repository-diagnostics-desktop.png", "pixiv-pages-mobile.png"],
    ["pixiv-live-desktop.png", "pixiv-live-mobile.png"],
])
def test_actual_bundle_can_be_sealed_and_all_four_corruptions_rejected(tmp_path, names):
    for name in names:
        (tmp_path / name).write_bytes(png())
    identity = ["--sha", SHA, "--run-id", "123", "--run-attempt", "2", "--captures", ",".join(names)]
    writer = subprocess.run([sys.executable, "-S", str(BTK), "write-capture-evidence",
        str(tmp_path), "--project", "bundle", "--stage", "complete", *identity], capture_output=True, text=True)
    assert writer.returncode == 0, writer.stderr
    probe = subprocess.run([sys.executable, "-S", str(BTK), "probe-capture-rejections",
        str(tmp_path), "--expect", "bundle", *identity], capture_output=True, text=True)
    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.count("correctly rejected:") == 4
    # Negative checks do not alter the source receipts/images.
    assert json.loads((tmp_path / "evidence.json").read_text())["stage"] == "complete"
    assert all((tmp_path / name).is_file() for name in names)


@pytest.mark.parametrize("stage", ["complete", "failed", "skipped"])
def test_absent_images_never_cover_required_success_and_old_receipt_is_replaced(tmp_path, stage):
    (tmp_path / "evidence.json").write_text('{"stage":"complete"}')
    writer = subprocess.run([sys.executable, "-S", str(BTK), "write-capture-evidence", str(tmp_path),
        "--project", "bundle", "--captures", "required.png", "--sha", SHA,
        "--run-id", "123", "--run-attempt", "2", "--stage", stage], capture_output=True, text=True)
    assert (writer.returncode == 0) == (stage != "complete")
    assert json.loads((tmp_path / "evidence.json").read_text())["stage"] != "complete"
    checker = subprocess.run([sys.executable, "-S", str(BTK), "check-capture-evidence", str(tmp_path),
        "--expect", "bundle", "--captures", "required.png", "--sha", SHA,
        "--run-id", "123", "--run-attempt", "2"], capture_output=True, text=True)
    assert checker.returncode == 1


# ═══ · evidence board ════════════════════════════════════════════════════════════════════════════════════

SCRIPTS = ROOT / "tools"

_be_spec = importlib.util.spec_from_file_location("btk_events", SCRIPTS / "btk_events.py")
btk_events_evidence_board = importlib.util.module_from_spec(_be_spec)
assert _be_spec.loader is not None
_be_spec.loader.exec_module(btk_events_evidence_board)

sys.modules.setdefault("btk_events", btk_events_evidence_board)  # so `import btk_events` inside evidence_board.py resolves
_spec_evidence_board = importlib.util.spec_from_file_location("evidence_board", SCRIPTS / "evidence_board.py")
evidence_board = importlib.util.module_from_spec(_spec_evidence_board)
assert _spec_evidence_board.loader is not None
_spec_evidence_board.loader.exec_module(evidence_board)


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


def test_help_runs_under_dash_s_evidence_board():
    result = subprocess.run(
        [sys.executable, "-S", "-m", "evidence_board", "--help"],
        cwd=str(SCRIPTS),
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0
    assert "evidence" in result.stdout.lower()
    assert "--allow" in result.stdout


# ═══ · evidence gif ══════════════════════════════════════════════════════════════════════════════════════
# evidence_gif: shots are checked before any browser starts; a recording in each engine is read back by a stdlib parser, the engine itself and Pillow.

spec_evidence_gif = importlib.util.spec_from_file_location("evidence_gif", ROOT / "tools" / "evidence_gif.py")
eg = importlib.util.module_from_spec(spec_evidence_gif)
assert spec_evidence_gif.loader
spec_evidence_gif.loader.exec_module(eg)


def test_shot_and_plan_checks():
    assert eg.parse_shot("🏠 Top|/index.html|1280x800") == {"label": "🏠 Top", "url": "/index.html", "viewport": "1280x800"}
    plan = eg.check_plan([{"url": "/a.html", "viewport": "392x848", "steps": [{"click": "#go"}, {"wait": 100}], "allow": ["sri"]}])
    assert plan[0]["viewport"] == (392, 848) and plan[0]["label"] == "/a.html" and plan[0]["hold"] == 1200
    assert plan[0]["allow"] == ["sri"]
    for bad in (["no-bars"], ["a|/x|12by8"]):
        with pytest.raises(ValueError):
            eg.check_plan([eg.parse_shot(s) for s in bad])
    with pytest.raises(ValueError):
        eg.check_plan([{"url": "/a.html", "steps": [{"hover": "#x"}]}])
    with pytest.raises(ValueError):
        eg.check_plan([])
    with pytest.raises(ValueError):
        eg.resolve("/a.html", None)
    with pytest.raises(ValueError):
        eg.parse_map("cdn.example.test=/")
    assert eg.resolve("https://example.test/", None) == "https://example.test/"


def test_cli_usage_errors_exit_2(capsys):
    with pytest.raises(SystemExit) as exit_info:
        eg.main(["out.gif"])
    assert exit_info.value.code == 2 and "no shots" in capsys.readouterr().err


def gif_blocks(data: bytes) -> dict:
    """The GIF's structure, read here with the standard library (independent of the encoder): screen, loop, comment, frames."""
    assert data[:6] == b"GIF89a" and data[-1:] == b";"
    width, height, packed = int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little"), data[10]
    pos, frames, gce, loop, comment = 13 + (3 * (2 << (packed & 7)) if packed & 0x80 else 0), [], None, None, b""
    while data[pos] != 0x3B:
        kind = data[pos]
        if kind == 0x21:
            label, pos, blocks = data[pos + 1], pos + 2, []
            while data[pos]:
                blocks.append(data[pos + 1:pos + 1 + data[pos]])
                pos += 1 + data[pos]
            pos += 1
            if label == 0xF9:
                gce = {"delay": int.from_bytes(blocks[0][1:3], "little"), "transparent": bool(blocks[0][0] & 1), "disposal": (blocks[0][0] >> 2) & 7}
            elif label == 0xFF and blocks[0] == b"NETSCAPE2.0":
                loop = int.from_bytes(blocks[1][1:3], "little")
            elif label == 0xFE:
                comment = b"".join(blocks)
            continue
        assert kind == 0x2C, hex(kind)
        x, y, w, h = (int.from_bytes(data[pos + 1 + 2 * i:pos + 3 + 2 * i], "little") for i in range(4))
        assert x + w <= width and y + h <= height and data[pos + 9] & 0xC0 == 0
        pos += 11
        while data[pos]:
            pos += 1 + data[pos]
        pos += 1
        frames.append({"rect": (x, y, w, h), **(gce or {})})
        gce = None
    return {"size": (width, height), "loop": loop, "comment": comment.decode("latin1"), "frames": frames}


@pytest.mark.browser
@pytest.mark.parametrize("browser_name", ["chromium", "firefox", "webkit"])
def test_records_frames_and_receipt(tmp_path, browser_name):
    pytest.importorskip("pysaiyanweboo")
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<meta name=generator content='web_ui " + "a" * 40 + "'><h1>ok</h1><button id=b onclick=\"this.textContent='done'\">go</button><select id=s><option>a<option>b</select><img src='https://example.invalid/x.png'><link rel=stylesheet href='https://cdn.example.test/pin/site.css'>"
                                     "<div id=bar style='width:10px;height:16px;background:#c62828'></div><script>let w=10;setInterval(()=>{w=(w+23)%300;bar.style.width=w+'px'},60)</script>", encoding="utf-8")
    (site / "broken.html").write_text("<script>throw new Error('boom')</script>", encoding="utf-8")
    cdn = tmp_path / "cdn"
    cdn.mkdir()
    (cdn / "site.css").write_text("h1{color:rgb(1,2,3)}", encoding="utf-8")
    out = tmp_path / "evidence" / "browser.gif"
    code = eg.main([str(out), "--browser", browser_name, "--serve", str(site), "--sha", "0" * 40, "--map", f"https://cdn.example.test/pin={cdn}",
                    "--shot", "🏠 Top|/index.html|392x848",
                    "--plan", str(_plan(tmp_path, [{"label": "🖱️ click", "url": "/index.html", "steps": [
                        {"click": "#b"}, {"select": ["#s", "b"]}, {"record": {"every": 100, "for": 600, "clip": "#bar"}},
                        {"zoom": {"selector": "#b", "steps": 4, "ms": 400, "hold": 600}}]},
                        {"label": "💥", "url": "/broken.html", "steps": [{"wait": 100}]}, {"label": "🚫", "url": "/missing.html"}]))])
    receipt = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert code == 1 and receipt["summary"] == {"pass": 2, "fail": 2} and receipt["schema"] == "btk-evidence-gif/2"
    assert [s["status"] for s in receipt["shots"]] == ["pass", "pass", "fail", "fail"] and receipt["browser"].startswith(browser_name)
    assert any("boom" in e for e in receipt["shots"][2]["errors"]) and "http 404" in receipt["shots"][3]["errors"]
    steps = receipt["shots"][1]["steps"]
    assert steps[:2] == ["click #b", "select #s → b"] and steps[2].startswith("⏺ #bar 100 ms ×") and steps[3] == "🔍 #b"
    assert receipt["shots"][0]["external"] == ["https://example.invalid/x.png"]       # refused, listed, not a failure
    assert receipt["shots"][0]["generator"] == "web_ui " + "a" * 40 and receipt["shots"][2]["generator"] is None
    assert receipt["mapped"] == ["https://cdn.example.test/pin/"] and receipt["commit"] == "0" * 40
    x, e = receipt["transfer"], receipt["encoder"]
    assert x["patches"] >= 3 and x["identical"] >= 1                                  # the clip and the quiet wait cost no full screenshot
    assert x["gif_bytes"] == receipt["bytes"] == out.stat().st_size and x["calls"] >= 4
    assert x["sent_bytes"] < x["png_bytes"] * 4 / 3 + 4 * x["calls"]                  # each screenshot went over at most once
    assert e["synthesized"] == 3 and e["colors"] <= 255 and not e["overBudget"]
    gif = gif_blocks(out.read_bytes())                                                 # the GIF, read without the encoder
    assert gif["size"] == (720, 512) and gif["loop"] == 0 and gif["comment"] == "BrowserTestKit evidence_gif " + "0" * 40
    assert len(gif["frames"]) == receipt["frames"] and gif["frames"][0]["rect"] == (0, 0, 720, 512)
    assert all(f["delay"] >= 2 and f["disposal"] == 1 for f in gif["frames"]) and any(f["transparent"] for f in gif["frames"])
    assert gif["frames"][-1]["delay"] >= 200                                           # the last frame holds before the loop
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:                                                      # the same engine opens it
        browser = getattr(pw, browser_name).launch()
        page = browser.new_page()
        size = page.evaluate("async s => { const i = new Image(); i.src = s; await i.decode(); return [i.naturalWidth, i.naturalHeight]; }",
                             "data:image/gif;base64," + __import__("base64").b64encode(out.read_bytes()).decode())
        browser.close()
    assert size == [720, 512]
    try:
        from PIL import Image                                                          # and Pillow, where it is installed
    except ImportError:
        pass
    else:
        assert Image.open(out).n_frames == receipt["frames"]
    assert receipt["plan"][0]["url"] == "/index.html" and len(receipt["plan"]) == 4
    small = tmp_path / "small.gif"                       # the receipt is the recipe; a budget trades colours, then ⏺ frames
    assert eg.main([str(small), "--browser", browser_name, "--serve", str(site), "--plan", str(out.with_suffix(".json")), "--max-bytes", "20K"]) == 1
    tight = json.loads(small.with_suffix(".json").read_text(encoding="utf-8"))
    assert tight["summary"] == receipt["summary"] and tight["limits"]["max_bytes"] == 20480
    assert tight["encoder"]["colors"] < e["colors"] and (tight["bytes"] <= 20480 or tight["encoder"]["overBudget"])


def test_step_and_budget_checks():
    assert eg.check_step({"record": {"every": 50}}) == {"record": {"every": 50, "for": 2000, "clip": None}}
    assert eg.check_step({"zoom": "#x"})["zoom"] == {"selector": "#x", "ms": 900, "steps": 6, "hold": 1200, "pad": 16, "scale": 2.0}
    for bad in ({"record": {"every": 10}}, {"record": {"for": 0}}, {"record": {"fps": 3}}, {"zoom": {"ms": 1}}, {"zoom": {"selector": "#x", "steps": 99}}):
        with pytest.raises(ValueError):
            eg.check_step(bad)
    assert [eg.parse_bytes(v) for v in ("800K", "2M", "1000", None, "0")] == [819200, 2097152, 1000, None, None]
    with pytest.raises(ValueError):
        eg.parse_bytes("lots")
    shots = eg.check_plan([{"url": "/a", "steps": [{"click": "#a"}, {"zoom": "#a"}, {"record": {}}]}], hold=900)
    assert shots[0]["hold"] == 900 and eg.static_frames(shots) == 1 + 1 + 6 + 1


def test_frame_step_checks_marks():
    mark = {"selector": "#x", "shape": "rect", "color": "#e53935", "width": 4, "pad": 8, "label": None, "number": None, "spotlight": False}
    assert eg.check_step({"frame": "#x"})["frame"] == {"marks": [mark], "keep": False}
    two = eg.check_step({"frame": ["#a", {"selector": "#b", "label": "ここ", "spotlight": True, "keep": True, "number": 7}]})["frame"]
    assert two["keep"] and [m["number"] for m in two["marks"]] == [1, 7] and two["marks"][1]["label"] == "ここ"
    for bad in ({"frame": {"shape": "rect"}}, {"frame": {"selector": "#x", "shape": "star"}}, {"frame": {"selector": "#x", "color": "red"}},
                {"frame": {"selector": "#x", "color": "#12345g"}}, {"frame": {"selector": "#x", "width": 0}}, {"frame": {"selector": "#x", "keep": "yes"}},
                {"frame": {"selector": "#x", "fill": "#fff"}}, {"frame": {"selector": "#x", "label": "x" * 81}}, {"frame": []}, {"frame": ["#a"] * 9},
                {"frame": {"selector": "#x", "spotlight": 1}}, {"frame": {"selector": "#x", "number": True}}):
        with pytest.raises(ValueError):
            eg.check_step(bad)
    assert eg.overlay.mark_svg is eg._sibling("element_overlay").mark_svg               # one drawer
    assert eg.static_frames(eg.check_plan([{"url": "/a", "steps": [{"frame": "#a"}, {"click": "#a"}]}])) == 3


def test_output_names_follow_pysaiyanweboo_rules(tmp_path, monkeypatch):
    assert eg.still_stem(1, "🏠 Top", (1280, 800), 0, "page") == "01-top-1280x800-00-page"
    assert eg.still_stem(12, "📱 トップ/画面", (392, 848), 3, "💾 保存ボタン #save") == "12-トップ-画面-392x848-03-保存ボタン-save"
    assert eg.slug("🔲🔲") == "x" and len(eg.still_stem(1, "a" * 80, (8192, 8192), 99, "b" * 80)) <= 64
    shots = eg.check_plan([{"label": "🏠 Top", "url": "/a", "viewport": "392x320", "steps": [{"frame": {"selector": "#s", "label": "保存"}}, {"click": "#s"}]}])
    gif, stills = eg.output_names(tmp_path / "browser.gif", shots, stills=tmp_path / "s", stills_prefix="p-", stills_suffix="-dark")
    assert gif == tmp_path / "browser.gif" and [p.name for p in stills.values()] == ["p-01-top-392x320-00-page-dark.png", "p-01-top-392x320-01-保存-dark.png"]
    from datetime import datetime, timezone
    run = datetime(2026, 11, 13, 2, 15, tzinfo=timezone.utc)
    gif, stills = eg.output_names(tmp_path / "browser.gif", shots, stills=tmp_path / "s", run_details=("timestamp",), now=run)
    assert gif.name == "browser-20261113T021500Z.gif" and all(p.name.endswith("-20261113T021500Z.png") for p in stills.values())
    (tmp_path / "browser.gif").write_bytes(b"x")                                      # a rerun without --timestamp is refused …
    with pytest.raises(FileExistsError, match="--force"):
        eg.output_names(tmp_path / "browser.gif", shots)
    assert eg.output_names(tmp_path / "browser.gif", shots, force=True)[0] == tmp_path / "browser.gif"   # … unless --force
    assert eg.output_names(tmp_path / "browser.gif", shots, run_details=("timestamp",), now=run)[0] == gif   # --timestamp: its own file
    with pytest.raises(Exception, match="letters, digits"):                            # PySaiyanWeBoo's name-part check
        eg.output_names(tmp_path / "b.gif", shots, stills=tmp_path / "s", stills_prefix="a b", force=True)
    monkeypatch.setitem(sys.modules, "pysaiyanweboo", None)                            # 🪶 without PySaiyanWeBoo: plain names, no run details
    assert [p.name for p in eg.output_names(tmp_path / "b.gif", shots, stills=tmp_path / "n")[1].values()] == ["01-top-392x320-00-page.png", "01-top-392x320-01-保存.png"]
    with pytest.raises(ValueError, match="pysaiyanweboo"):
        eg.output_names(tmp_path / "b.gif", shots, stills=tmp_path / "n", run_details=("git-commit",))


@pytest.mark.browser
@pytest.mark.parametrize("browser_name", ["chromium", "firefox", "webkit"])
def test_frame_marks_one_frame_and_takes_no_clicks(tmp_path, browser_name):
    pytest.importorskip("pysaiyanweboo")
    (tmp_path / "page.js").write_text("b.onclick = () => { b.textContent = document.querySelectorAll('[data-btk-frame]').length; };\n"
                                      "addEventListener('click', e => { document.title = e.target.id; });", encoding="utf-8")
    (tmp_path / "index.html").write_text("<meta http-equiv=Content-Security-Policy content=\"default-src 'self'; style-src 'self'\">"   # no inline style
                                         "<button id=b>go</button><p id=n></p><script src=page.js></script>", encoding="utf-8")
    marks = "n.textContent += document.querySelectorAll('[data-btk-frame]').length"
    out = tmp_path / "frame.gif"
    code = eg.main([str(out), "--browser", browser_name, "--serve", str(tmp_path), "--plan", str(_plan(tmp_path, [{"url": "/index.html",
        "allow": ["Refused to apply a stylesheet"],     # WebKit: the caret-hiding <style> Playwright adds for a screenshot, refused by this CSP
        "steps": [
        {"frame": {"selector": "#b", "shape": "ellipse", "color": "#1565c0"}}, {"click": "#b"},     # the mark is gone when the click lands
        {"frame": {"selector": "#b", "keep": True}}, {"click": "#b"},                              # a kept mark covers the button and lets the click through
        {"eval": "const m = document.querySelector('[data-btk-frame]'); if (document.title !== 'b' || getComputedStyle(m).position !== 'fixed') throw new Error('mark')"},
        {"frame": {"selector": "#b", "keep": True}}, {"eval": marks}, {"frame": {"selector": "#n", "shape": "arrow"}}, {"eval": marks},
        {"eval": "if (b.textContent !== '1' || n.textContent !== '10') throw new Error(b.textContent + '/' + n.textContent)"},
        {"frame": "#missing"}]}]))])
    shot = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))["shots"][0]
    assert code == 1 and shot["steps"][:3] == ["🔲 #b", "click #b", "🔲 #b"] and len(shot["steps"]) == 10      # only the missing selector failed
    assert len(shot["errors"]) == 1 and "TimeoutError" in shot["errors"][0]
    assert len(gif_blocks(out.read_bytes())["frames"]) >= 4                             # the marked frames differ from their neighbours


@pytest.mark.browser
def test_stills_are_written_and_a_nano_run_needs_no_pysaiyanweboo(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<h1 id=t>ok</h1><button id=b>go</button>", encoding="utf-8")
    plan = _plan(tmp_path, [{"label": "🏠 Top", "url": "/index.html", "viewport": "392x320", "steps": [
        {"frame": [{"selector": "#t", "label": "見出し", "spotlight": True}, {"selector": "#b", "shape": "arrow"}]}, {"click": "#b"}]}])
    out = tmp_path / "frame.gif"
    monkeypatch.setitem(sys.modules, "pysaiyanweboo", None)                                  # 🪶 PySaiyanWeBoo is not installed
    with pytest.raises(SystemExit, match="2"):                                         # a GIF needs it …
        eg.main([str(out), "--serve", str(tmp_path), "--plan", str(plan)])
    stills = tmp_path / "stills"
    assert eg.main([str(out), "--serve", str(tmp_path), "--plan", str(plan), "--stills", str(stills), "--stills-prefix", "p-"]) == 0
    receipt = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))         # … the stills do not
    assert not out.exists() and receipt["gif"] is None and "stills only" in receipt["encoder"]["skipped"]
    files = [f["file"] for f in receipt["stills"]["files"]]
    assert files == ["p-01-top-392x320-00-page.png", "p-01-top-392x320-01-見出し-b.png"]
    assert receipt["executable"]["source"] in ("playwright", "revision", "system")
    for name in files:
        data = (stills / name).read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n" and data[16:24] == (392).to_bytes(4, "big") + (320).to_bytes(4, "big")
    assert (stills / files[0]).read_bytes() != (stills / files[1]).read_bytes()          # the marks are in the still
    bundle = json.loads((stills / "evidence-chromium.json").read_text(encoding="utf-8"))
    assert bundle["stage"] == "failed" and "--sha" in bundle["error"] and receipt["stills"]["stage"] == "failed"   # no --sha: not checkable
    sha = "c" * 40
    with pytest.raises(SystemExit, match="2"):                                         # the stills exist now: refused …
        eg.main([str(out), "--serve", str(tmp_path), "--plan", str(plan), "--stills", str(stills), "--stills-prefix", "p-", "--sha", sha])
    assert eg.main([str(out), "--serve", str(tmp_path), "--plan", str(plan), "--stills", str(stills), "--stills-prefix", "p-", "--sha", sha, "--force"]) == 0
    bundle = json.loads((stills / "evidence-chromium.json").read_text(encoding="utf-8"))
    assert bundle["stage"] == "complete" and bundle["project"] == "chromium" and [c["file"] for c in bundle["captures"]] == files
    assert bundle["captures"][0]["width"] == 392 and bundle["captures"][0]["height"] == 320
    check = ["check-capture-evidence", str(stills), "--pattern", "evidence-*.json", "--expect", "chromium", "--captures", ",".join(files), "--sha", sha]
    assert eg._sibling("btk").main(check) == 0                                         # the kit's own validator accepts the stills
    (stills / files[1]).write_bytes((stills / files[0]).read_bytes())                   # a swapped picture is caught
    assert eg._sibling("btk").main(check) == 1


def _plan(tmp_path, shots):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(shots), encoding="utf-8")
    return path


# ═══ · jsonl digest ══════════════════════════════════════════════════════════════════════════════════════

SCRIPT = ROOT / "tools" / "jsonl_digest.py"
spec_jsonl_digest = importlib.util.spec_from_file_location("jsonl_digest", SCRIPT)
jsonl_digest = importlib.util.module_from_spec(spec_jsonl_digest)
assert spec_jsonl_digest.loader
spec_jsonl_digest.loader.exec_module(jsonl_digest)

EVENTS = [
    {"source": "terminal-browser", "time": "2026-09-26T01:00:00Z", "event": "terminal_browser_found"},
    {"source": "terminal-browser", "time": "2026-09-26T01:00:01Z", "event": "terminal_browser_start", "argv": ["x"]},
    {"time": "2026-09-26T01:00:05Z", "event": "tool_call", "status": "failed", "detail": "y" * 300},
    {"time": "2026-09-26T01:00:09Z", "event": "terminal_browser_exit", "returncode": 130, "interrupted": True},
]

# A mixed MCP/JSON-RPC log: a request/response pair nested under "message" (inspector/proxy style,
# id 1), two direct top-level pairs (an MCP tool error via result.isError, id 2; a JSON-RPC error
# object, id 3), an unanswered request (id 4), and two notification methods.
RPC_EVENTS = [
    {"time": "2026-09-26T02:00:00Z", "direction": "send",
     "message": {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                 "params": {"name": "search_web", "arguments": {"q": "llm"}}}},
    {"time": "2026-09-26T02:00:01Z", "direction": "recv",
     "message": {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "ok"}]}}},
    {"time": "2026-09-26T02:00:02Z", "jsonrpc": "2.0", "id": 2, "method": "tools/call",
     "params": {"name": "broken_tool", "arguments": {}}},
    {"time": "2026-09-26T02:00:02Z", "jsonrpc": "2.0", "id": 2,
     "result": {"isError": True, "content": [{"type": "text", "text": "boom"}]}},
    {"time": "2026-09-26T02:00:03Z", "jsonrpc": "2.0", "id": 3, "method": "tools/call",
     "params": {"name": "erroring_tool", "arguments": {}}},
    {"time": "2026-09-26T02:00:03Z", "jsonrpc": "2.0", "id": 3, "error": {"code": -32000, "message": "boom"}},
    {"time": "2026-09-26T02:00:04Z", "jsonrpc": "2.0", "id": 4, "method": "tools/call",
     "params": {"name": "slow_tool", "arguments": {}}},
    {"jsonrpc": "2.0", "method": "initialized", "params": {}},
    {"jsonrpc": "2.0", "method": "progress", "params": {"progress": 1}},
    {"jsonrpc": "2.0", "method": "progress", "params": {"progress": 2}},
]

# mcp-toolcall-lab MCP_TOOLCALL_LOG-shaped rows: "jev" judgment-model events plus real JSON-RPC
# traffic in the same file, "at" timestamps with a "+00:00" offset, a dotted "answer.*" path, one
# row missing "backend"/"kind", and one boolean "noul" that --num must ignore.
TRACE_EVENTS = [
    {"at": "2026-09-26T01:00:00.123456+00:00", "event": "jev/backend_answer", "backend": "fixture",
     "prob_source": "scenario", "name": "check_a", "kind": "noul", "answer": {"type": "noul", "noul": 0.92},
     "duration_ms": 12.5, "chat_id": "c1"},
    {"at": "2026-09-26T01:00:00.500000+00:00", "event": "jev/backend_answer", "backend": "fixture",
     "prob_source": "scenario", "name": "check_b", "kind": "choice", "answer": {"type": "choice", "choice": "yes"},
     "duration_ms": 8, "chat_id": "c1"},
    {"at": "2026-09-26T01:00:01.000000+00:00", "event": "jev/route_decision", "backend": "live",
     "prob_source": "model", "name": "check_c", "kind": "score", "answer": {"type": "score", "score": 0.5},
     "duration_ms": 20.25, "chat_id": "c1"},
    {"at": "2026-09-26T01:00:02.000000+00:00", "event": "jev/backend_answer", "backend": "fixture",
     "prob_source": "scenario", "name": "check_d", "kind": "noul", "answer": {"type": "noul", "noul": True},
     "duration_ms": 5, "chat_id": "c1"},
    {"at": "2026-09-26T01:00:03.000000+00:00", "event": "jev/backend_answer",
     "prob_source": "scenario", "name": "check_e", "answer": {"type": "noul"}, "duration_ms": 9.5, "chat_id": "c1"},
]


def lines(records, extra=()):
    return [json.dumps(record) for record in records] + list(extra)


def test_digest_counts_time_range_errors_and_last_lines() -> None:
    report, code = jsonl_digest.digest(lines(EVENTS, ["not json", ""]), source="run.jsonl", last=2, width=40)
    assert code == 0
    out = report.splitlines()
    assert out[0] == "run.jsonl: 4 record(s) (1 unparsable line(s))"
    assert out[1] == "time: 2026-09-26T01:00:00Z .. 2026-09-26T01:00:09Z"
    assert out[2].startswith("by kind: terminal_browser_found 1, terminal_browser_start 1, tool_call 1")
    assert out[3] == "errors: 1"
    assert out[4].startswith('  [2] 2026-09-26T01:00:05Z tool_call: {"status":"failed","detail":"yyy')
    assert len(out[4]) <= 2 + len("[2] 2026-09-26T01:00:05Z tool_call: ") + 40
    assert out[5] == "last 2:"
    assert out[7] == '  [3] 2026-09-26T01:00:09Z terminal_browser_exit: {"returncode":130,"interrupted":true}'


def test_key_option_show_and_errors_by_level() -> None:
    records = [{"type": "user", "level": "info"}, {"type": "assistant", "level": "error", "message": "boom"}]
    report, code = jsonl_digest.digest(lines(records), source="s.jsonl", key="type", last=0, show=(1,))
    assert code == 0
    assert "by type: user 1, assistant 1" in report
    assert "errors: 1\n  [1] assistant: " in report
    assert report.endswith('--- [1] ---\n{\n  "type": "assistant",\n  "level": "error",\n  "message": "boom"\n}')


def test_no_records_is_exit_1_and_bad_show_is_exit_2() -> None:
    assert jsonl_digest.digest(["", "nope"], source="x")[1] == 1
    assert jsonl_digest.digest(lines(EVENTS), source="x", show=(9,))[1] == 2


def test_cli_reads_stdin_without_site_packages() -> None:
    done = subprocess.run([sys.executable, "-S", str(SCRIPT), "-", "--last", "1"], input="\n".join(lines(EVENTS)),
                          capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    assert done.stdout.startswith("-: 4 record(s)\n")
    assert done.stdout.rstrip().endswith("terminal_browser_exit: {\"returncode\":130,\"interrupted\":true}")




def test_btk_never_passing_statuses_are_errors() -> None:
    records = [
        {"schema": "btk-event/1", "source": "x", "time": "2026-09-26T00:00:00Z",
         "event": "run_end", "status": status}
        for status in ("failed", "error", "interrupted", "blocked", "unavailable")
    ]
    report, code = jsonl_digest.digest(lines(records), source="btk.jsonl", last=0)
    assert code == 0
    assert "errors: 5" in report

# -- JSON-RPC / MCP awareness (default digest) -------------------------------------------------


def test_method_is_a_kind_key_in_the_default_digest() -> None:
    records = [{"time": "2026-09-26T02:00:00Z", "method": "tools/list", "id": 1, "params": {}}]
    report, code = jsonl_digest.digest(lines(records), source="m.jsonl", last=0)
    assert code == 0
    assert "by kind: tools/list 1" in report


def test_is_error_covers_rpc_error_object_and_mcp_tool_error() -> None:
    assert jsonl_digest.is_error({"error": {"code": -32000, "message": "boom"}}) is True
    assert jsonl_digest.is_error({"result": {"isError": True, "content": []}}) is True
    assert jsonl_digest.is_error({"result": {"isError": False, "content": []}}) is False
    assert jsonl_digest.is_error({"result": {"ok": True}}) is False


def test_default_digest_counts_mcp_tool_error_without_pairs() -> None:
    records = [
        {"time": "2026-09-26T02:00:00Z", "id": 2, "method": "tools/call", "params": {"name": "x"}},
        {"time": "2026-09-26T02:00:01Z", "id": 2, "result": {"isError": True, "content": []}},
    ]
    report, code = jsonl_digest.digest(lines(records), source="e.jsonl", last=0)
    assert code == 0
    assert "errors: 1" in report


# -- --pairs -------------------------------------------------------------------------------------


def test_pairs_matches_direct_and_message_nested_requests_to_responses() -> None:
    report, code = jsonl_digest.digest(lines(RPC_EVENTS), source="mcp.jsonl", pairs=True, last=0)
    assert code == 0
    out = report.splitlines()
    # id 1: request/response nested under "message" (inspector/proxy style).
    assert '  [0->1] tools/call name=search_web args={"q":"llm"} -> ok {"content":[{"type":"text","text":"ok"}]} (1s)' in out
    # id 2: direct top-level request/response.
    assert any(line.startswith("  [2->3] tools/call name=broken_tool") for line in out)


def test_pairs_reports_mcp_tool_error_and_json_rpc_error_object() -> None:
    report, code = jsonl_digest.digest(lines(RPC_EVENTS), source="mcp.jsonl", pairs=True, last=0)
    assert code == 0
    assert '[2->3] tools/call name=broken_tool args={} -> ERROR {"isError":true,"content":[{"type":"text","text":"boom"}]}' in report
    assert '[4->5] tools/call name=erroring_tool args={} -> ERROR {"code":-32000,"message":"boom"}' in report


def test_pairs_lists_unanswered_requests_and_counts_notifications() -> None:
    report, code = jsonl_digest.digest(lines(RPC_EVENTS), source="mcp.jsonl", pairs=True, last=0)
    assert code == 0
    assert "[6] tools/call name=slow_tool args={} -> no response" in report
    assert "calls: 4 (2 errors, 1 unanswered), notifications: initialized 1, progress 2" in report




def test_pairs_reused_id_matches_fifo_without_cross_pairing() -> None:
    records = [
        {"time": "2026-09-26T04:00:00Z", "id": 1, "method": "tools/call",
         "params": {"name": "first", "arguments": {}}},
        {"time": "2026-09-26T04:00:01Z", "id": 1, "result": {"value": "first-result"}},
        {"time": "2026-09-26T04:00:02Z", "id": 1, "method": "tools/call",
         "params": {"name": "second", "arguments": {}}},
        {"time": "2026-09-26T04:00:03Z", "id": 1,
         "result": {"isError": True, "content": [{"type": "text", "text": "boom"}]}},
    ]
    report, code = jsonl_digest.digest(lines(records), source="reuse.jsonl", pairs=True, last=0)
    assert code == 0
    assert '[0->1] tools/call name=first args={} -> ok {"value":"first-result"}' in report
    assert '[2->3] tools/call name=second args={} -> ERROR {"isError":true' in report
    assert "calls: 2 (1 errors, 0 unanswered)" in report


def test_pairs_response_before_request_is_not_reused() -> None:
    records = [
        {"time": "2026-09-26T04:10:00Z", "id": 7, "result": {"value": "orphan"}},
        {"time": "2026-09-26T04:10:01Z", "id": 7, "method": "ping", "params": {}},
    ]
    report, code = jsonl_digest.digest(lines(records), source="orphan.jsonl", pairs=True, last=0)
    assert code == 0
    assert "[1] ping -> no response" in report
    assert "calls: 1 (0 errors, 1 unanswered)" in report

def test_pairs_elapsed_time_needs_zone_aware_iso_timestamps_on_both_ends() -> None:
    with_zone = [
        {"time": "2026-09-26T00:00:00.250000+00:00", "method": "ping", "id": 5, "params": {}},
        {"time": "2026-09-26T00:00:00.750000+00:00", "id": 5, "result": {}},
    ]
    report, _ = jsonl_digest.digest(lines(with_zone), source="e.jsonl", pairs=True, last=0)
    assert "[0->1] ping -> ok {} (0.5s)" in report

    naive = [
        {"time": "2026-09-26 00:00:00", "method": "ping", "id": 6, "params": {}},
        {"time": "2026-09-26 00:00:01", "id": 6, "result": {}},
    ]
    report, _ = jsonl_digest.digest(lines(naive), source="n.jsonl", pairs=True, last=0)
    assert report.splitlines()[-1] == "  [0->1] ping -> ok {}"


def test_pairs_respects_width_for_previews() -> None:
    records = [
        {"time": "2026-09-26T03:00:00Z", "method": "tools/call", "id": 9,
         "params": {"name": "wide_tool", "arguments": {"payload": "x" * 100}}},
        {"time": "2026-09-26T03:00:00Z", "id": 9, "result": {"content": "y" * 100}},
    ]
    report, code = jsonl_digest.digest(lines(records), source="w.jsonl", pairs=True, width=20, last=0)
    assert code == 0
    assert "x" * 100 not in report and "y" * 100 not in report
    assert "…" in report
    call_line = next(line for line in report.splitlines() if line.startswith("  [0->1]"))
    assert len(call_line) < 120


def test_cli_pairs_on_stdin_without_site_packages() -> None:
    done = subprocess.run([sys.executable, "-S", str(SCRIPT), "-", "--pairs", "--last", "0"],
                          input="\n".join(lines(RPC_EVENTS)), capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    assert "calls: 4 (2 errors, 1 unanswered), notifications: initialized 1, progress 2" in done.stdout
    assert "[0->1] tools/call name=search_web" in done.stdout


# -- mcp-toolcall-lab trace format: "at", --where, --count, --num --------------------------------


def test_at_is_a_time_key() -> None:
    report, code = jsonl_digest.digest(lines(TRACE_EVENTS), source="t.jsonl", last=0)
    assert code == 0
    assert "time: 2026-09-26T01:00:00.123456+00:00 .. 2026-09-26T01:00:03.000000+00:00" in report


def test_where_filters_records_before_digesting_including_dotted_path() -> None:
    report, code = jsonl_digest.digest(lines(TRACE_EVENTS), source="t.jsonl", last=0,
                                       where=(("event", "jev/backend_answer"),))
    assert code == 0
    assert "t.jsonl: 4 record(s)" in report
    assert "by kind: jev/backend_answer 4" in report  # jev/route_decision (check_c) is filtered out

    report, code = jsonl_digest.digest(lines(TRACE_EVENTS), source="t.jsonl", last=0,
                                       where=(("answer.type", "noul"),))
    assert code == 0
    assert "t.jsonl: 3 record(s)" in report  # check_a, check_d, check_e


def test_where_with_no_matches_is_exit_1() -> None:
    report, code = jsonl_digest.digest(lines(TRACE_EVENTS), source="t.jsonl", where=(("event", "nope"),))
    assert code == 1
    assert "0 record(s) match --where" in report


def test_count_reports_top_values_with_missing_bucket() -> None:
    report, code = jsonl_digest.digest(lines(TRACE_EVENTS), source="t.jsonl", last=0, count_fields=("backend",))
    assert code == 0
    assert "count backend: fixture 3, live 1, (missing) 1" in report


def test_num_ignores_bool_and_handles_dotted_path_and_missing_values() -> None:
    # answer.noul: check_a=0.92 (float), check_d=True (bool, ignored), check_b/c/e have no "noul" key.
    report, code = jsonl_digest.digest(lines(TRACE_EVENTS), source="t.jsonl", last=0, num_fields=("answer.noul",))
    assert code == 0
    assert "num answer.noul: n=1 min=0.92 median=0.92 mean=0.92 max=0.92" in report


def test_num_computes_min_median_mean_max_over_a_field() -> None:
    report, code = jsonl_digest.digest(lines(TRACE_EVENTS), source="t.jsonl", last=0, num_fields=("duration_ms",))
    assert code == 0
    # sorted durations: 5, 8, 9.5, 12.5, 20.25 -> median is the middle value (20.25 excluded by no --where
    # here, so all 5 rows count).
    assert "num duration_ms: n=5 min=5 median=9.5 mean=11.05 max=20.25" in report




def test_num_ignores_non_finite_values() -> None:
    records = [{"x": 1.5}, {"x": float("nan")}, {"x": float("inf")}, {"x": float("-inf")}]
    report, code = jsonl_digest.digest(lines(records), source="n.jsonl", last=0, num_fields=("x",))
    assert code == 0
    assert "num x: n=1 min=1.5 median=1.5 mean=1.5 max=1.5" in report

def test_num_with_no_numeric_values() -> None:
    report, code = jsonl_digest.digest(lines(TRACE_EVENTS), source="t.jsonl", last=0, num_fields=("chat_id",))
    assert code == 0
    assert "num chat_id: (no numeric values)" in report


def test_cli_where_bad_syntax_is_exit_2() -> None:
    done = subprocess.run([sys.executable, "-S", str(SCRIPT), "-", "--where", "no-equals-sign"],
                          input="\n".join(lines(TRACE_EVENTS)), capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 2
    assert "error:" in done.stderr


# ═══ · pytest btk events ═════════════════════════════════════════════════════════════════════════════════
# Exercise tools/pytest_btk_events.py the way a project actually loads it:
# as a real, separate `pytest -p pytest_btk_events` subprocess against a tiny
# temp test file. No pytester plugin is used or required.

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


# ═══ · browser test kit image diff ═══════════════════════════════════════════════════════════════════════

FIXTURES = ROOT / "tests" / "fixtures" / "image_diff"
GOLDEN = json.loads((FIXTURES / "golden.json").read_text(encoding="utf-8"))
BROWSER_TEST_KIT_IMAGE_DIFF_SCRIPT = ROOT / "tools" / "image_diff.py"
PILLOW_MIN = ROOT / "vendor" / "pillow_min" / "pillow_min.py"
spec_browser_test_kit_image_diff = importlib.util.spec_from_file_location("image_diff", BROWSER_TEST_KIT_IMAGE_DIFF_SCRIPT)
image_diff = importlib.util.module_from_spec(spec_browser_test_kit_image_diff)
assert spec_browser_test_kit_image_diff.loader
spec_browser_test_kit_image_diff.loader.exec_module(image_diff)

pm_spec = importlib.util.spec_from_file_location("pillow_min", PILLOW_MIN)
pillow_min = importlib.util.module_from_spec(pm_spec)
assert pm_spec.loader
pm_spec.loader.exec_module(pillow_min)


def run_image_diff(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(BROWSER_TEST_KIT_IMAGE_DIFF_SCRIPT), *argv],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def test_identical_images_exit_zero() -> None:
    left, right = FIXTURES / "identical_a.png", FIXTURES / "identical_b.png"
    proc = run_image_diff(str(left), str(right))
    expected = GOLDEN["identical"]
    assert proc.returncode == expected["returncode"]
    assert proc.stdout.strip() == expected["stdout"]
    assert "changed=0" in proc.stdout
    assert "differs=False" in proc.stdout


def test_red_rect_diff_metrics() -> None:
    left, right = FIXTURES / "red_rect_a.png", FIXTURES / "red_rect_b.png"
    proc = run_image_diff(str(left), str(right))
    expected = GOLDEN["red_rect"]
    assert proc.returncode == expected["returncode"]
    assert proc.stdout.strip() == expected["stdout"]
    assert "changed=50" in proc.stdout
    assert "bbox=(20, 10, 30, 15)" in proc.stdout


def test_clip_excluding_rect_exit_zero() -> None:
    left, right = FIXTURES / "red_rect_a.png", FIXTURES / "red_rect_b.png"
    proc = run_image_diff(str(left), str(right), "--clip", "0,0,20,50")
    expected = GOLDEN["clip_exclude"]
    assert proc.returncode == expected["returncode"]
    assert proc.stdout.strip() == expected["stdout"]
    assert "changed=0" in proc.stdout


def test_viewport_width_scales_clip() -> None:
    left, right = FIXTURES / "viewport_a.png", FIXTURES / "viewport_b.png"
    proc = run_image_diff(
        str(left),
        str(right),
        "--clip",
        "0,0,390,60",
        "--viewport-width",
        "390",
    )
    expected = GOLDEN["viewport"]
    assert proc.returncode == expected["returncode"]
    assert proc.stdout.strip() == expected["stdout"]
    assert "changed=0" in proc.stdout


def test_tolerance_boundary() -> None:
    left, right = FIXTURES / "tolerance_a.png", FIXTURES / "tolerance_b.png"
    assert run_image_diff(str(left), str(right), "--tolerance", "5").returncode == GOLDEN["tol5"]["returncode"]
    assert run_image_diff(str(left), str(right), "--tolerance", "4").returncode == GOLDEN["tol4"]["returncode"]


def test_different_sizes_exit_one(tmp_path: Path) -> None:
    left, right = FIXTURES / "small_a.png", FIXTURES / "small_b.png"
    proc = run_image_diff(str(left), str(right))
    expected = GOLDEN["diff_size"]
    assert proc.returncode == expected["returncode"]
    assert proc.stdout.strip() == expected["stdout"]
    assert "same_size=False" in proc.stdout
    assert "size_a=(10, 10)" in proc.stdout
    assert "size_b=(11, 10)" in proc.stdout


def test_zip_member_success_and_missing(tmp_path: Path) -> None:
    archive = tmp_path / "evidence.zip"
    header_bytes = (FIXTURES / "zip_header.png").read_bytes()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("t/header.png", header_bytes)
    proc = run_image_diff(f"{archive}::t/header.png", f"{archive}::t/header.png")
    assert proc.returncode == 0
    missing = run_image_diff(f"{archive}::t/nope.png", f"{archive}::t/header.png")
    assert missing.returncode == 2
    assert "image_diff:" in missing.stderr


def test_nonexistent_file_exit_two(tmp_path: Path) -> None:
    proc = run_image_diff(str(tmp_path / "missing.png"), str(tmp_path / "also-missing.png"))
    assert proc.returncode == 2


def test_bad_zip_exit_two(tmp_path: Path) -> None:
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    proc = run_image_diff(f"{bad}::member.png", f"{bad}::member.png")
    assert proc.returncode == 2


@pytest.mark.parametrize(
    "clip",
    [
        "1,2,3",
        "a,b,c,d",
        "0,0,0,10",
        "0,0,10,-1",
    ],
)
def test_invalid_clip_exit_two(clip: str) -> None:
    left = FIXTURES / "square_20.png"
    proc = run_image_diff(str(left), str(left), "--clip", clip)
    assert proc.returncode == 2


def test_clip_out_of_bounds_exit_two() -> None:
    left = FIXTURES / "square_10.png"
    proc = run_image_diff(str(left), str(left), "--clip", "0,0,20,20")
    assert proc.returncode == 2
    assert "outside image bounds" in proc.stderr


def test_viewport_width_non_positive_exit_two() -> None:
    left = FIXTURES / "square_10.png"
    proc = run_image_diff(str(left), str(left), "--viewport-width", "0")
    assert proc.returncode == 2


def test_tolerance_out_of_range_exit_two() -> None:
    left = FIXTURES / "square_4.png"
    assert run_image_diff(str(left), str(left), "--tolerance", "256").returncode == 2
    assert run_image_diff(str(left), str(left), "--tolerance", "-1").returncode == 2


def test_missing_pillow_min_exit_two(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    left = FIXTURES / "square_4.png"
    missing = tmp_path / "missing_pillow_min.py"
    monkeypatch.setattr(image_diff, "_PILLOW_MIN", missing)
    monkeypatch.setattr(image_diff, "_IMAGE_BACKEND", None)
    with pytest.raises(SystemExit) as error:
        image_diff.load(str(left))
    assert error.value.code == 2


def test_missing_pillow_min_cli_exit_two() -> None:
    left = FIXTURES / "square_4.png"
    code = textwrap.dedent(
        f"""
        import importlib.util
        import sys
        from pathlib import Path
        spec = importlib.util.spec_from_file_location("image_diff", {repr(str(BROWSER_TEST_KIT_IMAGE_DIFF_SCRIPT))})
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader
        spec.loader.exec_module(mod)
        mod._PILLOW_MIN = Path({repr(str(ROOT / "missing-pillow-min.py"))})
        mod._IMAGE_BACKEND = None
        raise SystemExit(mod.main([{repr(str(left))}, {repr(str(left))}]))
        """
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT)
    assert proc.returncode == 2
    assert "pillow_min" in proc.stderr


@pytest.mark.parametrize("flag,value", [
    ("--viewport-width", "nan"), ("--viewport-width", "inf"),
    ("--viewport-width", "-inf"), ("--clip", "nan,0,1,1"),
    ("--clip", "0,inf,1,1"), ("--clip", "0,0,inf,1"),
    ("--clip", "0,0,1,nan"), ("--clip", "1e308,0,1e308,1"),
])
def test_nonfinite_and_overflow_input_exit_two(flag, value):
    path = FIXTURES / "square_10.png"
    proc = run_image_diff(str(path), str(path), flag, value)
    assert proc.returncode == 2
    assert "Traceback" not in proc.stderr


def test_regular_file_over_limit_exit_two(monkeypatch):
    path = FIXTURES / "square_10.png"
    monkeypatch.setattr(image_diff, "MAX_INPUT_BYTES", 16)
    with pytest.raises(ValueError, match="size limit"):
        image_diff.load(str(path))


def test_compressed_zip_member_over_limit_exit_two(tmp_path, monkeypatch):
    archive = tmp_path / "compressed.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("huge.png", b"0" * 8192)
    monkeypatch.setattr(image_diff, "MAX_INPUT_BYTES", 1024)
    with pytest.raises(ValueError, match="size limit"):
        image_diff.load(f"{archive}::huge.png")


def test_normal_zip_and_file_within_limit(tmp_path):
    path = FIXTURES / "tiny.png"
    archive = tmp_path / "ok.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(path, "ok.png")
    assert run_image_diff(str(path), f"{archive}::ok.png").returncode == 0


def test_negative_clip_coordinate_exit_two():
    path = FIXTURES / "square_10.png"
    proc = run_image_diff(str(path), str(path), "--clip", "-1,0,5,5")
    assert proc.returncode == 2
    assert "Traceback" not in proc.stderr


@pytest.mark.parametrize("channel", range(4), ids=["red", "green", "blue", "alpha"])
@pytest.mark.parametrize("tolerance,changed", [(4, 1), (5, 0), (6, 0)])
def test_each_rgba_channel_tolerance_metrics(channel, tolerance, changed):
    base = pillow_min.Image.new("RGBA", (2, 1), (100, 100, 100, 100))
    altered = base.copy()
    pixel = [100, 100, 100, 100]
    pixel[channel] += 5
    altered.putpixel((1, 0), tuple(pixel))
    result = image_diff.compare(base, altered, tolerance=tolerance)
    assert result["changed"] == changed
    assert result["ratio"] == changed / 2
    assert result["bbox"] == ((1, 0, 2, 1) if changed else None)
    assert result["max_delta"] == 5
    assert result["differs"] is bool(changed)


@pytest.mark.parametrize("in_zip", [False, True], ids=["file", "zip"])
def test_invalid_image_bytes_exit_two(tmp_path, in_zip):
    path = FIXTURES / "invalid.png"
    source = str(path)
    if in_zip:
        archive = tmp_path / "invalid.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(path, "invalid.png")
        source = f"{archive}::invalid.png"
    proc = run_image_diff(source, source)
    assert proc.returncode == 2
    assert "image_diff:" in proc.stderr
    assert "Traceback" not in proc.stderr


@pytest.mark.parametrize("version", [(3, 9), (3, 10)])
def test_unsupported_python_fails_before_backend(monkeypatch, capsys, version):
    monkeypatch.setattr(image_diff.sys, "version_info", version)
    with pytest.raises(SystemExit) as error:
        image_diff.load("does-not-exist.png")
    assert error.value.code == 2
    assert "Python >=3.11 is required" in capsys.readouterr().err


def test_vendored_pillow_min_matches_lock() -> None:
    lock = json.loads((ROOT / "vendor.lock.json").read_text(encoding="utf-8"))
    entry = next(
        e for e in lock["files"]
        if e["destination"] == "vendor/pillow_min/pillow_min.py"
    )
    data = PILLOW_MIN.read_bytes()
    import hashlib
    assert entry["sha256"] == hashlib.sha256(data).hexdigest()
    assert entry["commit"] == "b45ccd3e529aefe6519aa14e14bb63fa5873639d"
