"""Verify a real pytest no-tests session remains a failing evidence-board gate."""

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def test_no_tests_collected_summary_fails_the_real_board_cli(tmp_path):
    empty = tmp_path / "test_empty.py"
    empty.write_text("# Synthetic empty test module.\n", encoding="utf-8")
    config = tmp_path / "pytest.ini"
    config.write_text("[pytest]\n", encoding="utf-8")
    events = tmp_path / "events.jsonl"
    env = {**os.environ, "PYTHONPATH": str(ROOT / "tools"), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    env.pop("PYTEST_ADDOPTS", None)
    child = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-c", str(config), "-p", "pytest_btk_events",
         "--btk-events", str(events), "--btk-events-run-id", "synthetic-empty", str(empty)],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30,
    )
    assert child.returncode == 5, child.stdout + child.stderr
    records = [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines()]
    assert not [record for record in records if record["event"] == "test_result"]
    summary, = [record for record in records if record["event"] == "session_summary"]
    assert summary["status"] == "failed" and summary["returncode"] == 5
    board = subprocess.run(
        [sys.executable, "-S", str(ROOT / "tools" / "evidence_board.py"), str(events), "--json"],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
    )
    assert board.returncode == 1, board.stdout + board.stderr
    stats = json.loads(board.stdout)
    assert stats["counts_by_status"] == {}
    assert stats["blocking_statuses"] == ["failed"]
    assert stats["session_summaries"][0]["returncode"] == 5
