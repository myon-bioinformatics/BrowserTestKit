"""Connect Lean phase/count invariants to the actual stdlib evidence board."""

from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
import io
import itertools
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import evidence_board


STATUSES = ("passed", "failed", "error", "interrupted", "blocked", "unavailable", "skipped")
ABSENT = object()


def event(**fields):
    return {"schema": "btk-event/1", "source": "synthetic-phase-check",
            "project": "synthetic", "run_id": "single-execution",
            "time": "2026-01-01T00:00:00Z", "event": "test_result", **fields}


def phases(setup, call, teardown):
    records = []
    for phase, status in (("setup", setup), ("call", call), ("teardown", teardown)):
        if status is ABSENT or (phase != "call" and status is None):
            continue
        fields = {} if status is None else {"status": status}
        records.append(event(test_id="case", phase=phase, **fields))
    return records


def expected_status(records):
    # Independent priority oracle: failed call > non-call error > call >
    # first other observed status. A call report with no status is still a call.
    candidates = []
    for index, record in enumerate(records):
        phase, status = record["phase"], record.get("status")
        priority = (3 if (phase, status) == ("call", "failed") else
                    2 if phase != "call" and status == "error" else
                    1 if phase == "call" else 0)
        if phase == "call" or status is not None:
            candidates.append((priority, -index, status))
    return max(candidates)[2] if candidates else None


class EvidenceReductionBridge(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "synthetic.jsonl"

    def write(self, records):
        self.path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")

    def test_every_phase_status_combination_reduces_to_at_most_one_count(self):
        # 8 setup states × 9 call states × 8 teardown states = 576 cases.
        # Missing call report and call report lacking status remain distinct.
        for setup, call, teardown in itertools.product((None, *STATUSES),
                                                       (ABSENT, None, *STATUSES),
                                                       (None, *STATUSES)):
            with self.subTest(setup=setup, call="absent" if call is ABSENT else call,
                              teardown=teardown):
                records = phases(setup, call, teardown)
                expected = expected_status(records)
                actual, _ = evidence_board._reduce_test_result(records)
                self.assertEqual(actual, expected)
                if actual == "passed":
                    self.assertIn("passed", [record.get("status") for record in records])
                self.write(records)
                stats = evidence_board.aggregate([self.path], allow=set())
                self.assertEqual(stats["counts_by_status"], {} if expected is None else {expected: 1})
                self.assertLessEqual(sum(stats["counts_by_status"].values()), 1)

    def test_call_failure_keeps_teardown_detail_without_counting_a_second_failure(self):
        records = phases(None, "failed", "error")
        records[0]["message"] = "synthetic call failure"
        records[1]["message"] = "synthetic cleanup failure"
        status, info = evidence_board._reduce_test_result(records)
        self.assertEqual(status, "failed")
        self.assertEqual(info, {"message": "synthetic call failure",
                                "teardown_message": "synthetic cleanup failure"})
        self.write(records)
        stats = evidence_board.aggregate([self.path], allow=set())
        self.assertEqual(stats["counts_by_status"], {"failed": 1})

    def test_session_summaries_do_not_change_per_test_counts(self):
        base = [event(test_id=f"case-{index}", phase="call", status=status)
                for index, status in enumerate(("passed", "failed", "skipped"))]
        for status in (None, *STATUSES):
            for position in range(len(base) + 1):
                with self.subTest(status=status, position=position):
                    summary = event(event="session_summary", **({} if status is None else {"status": status}))
                    self.write(base[:position] + [summary] + base[position:])
                    stats = evidence_board.aggregate([self.path], allow=set())
                    self.assertEqual(stats["counts_by_status"], dict(Counter(("passed", "failed", "skipped"))))
                    self.assertEqual(len(stats["session_summaries"]), 1)
                    self.assertEqual(stats["session_summaries"][0]["status"], status)
                    self.assertEqual(stats["total_events"], len(base) + 1)

    def test_blocking_direct_statuses_are_not_renamed_passed_when_allowed(self):
        for status in ("failed", "error", "interrupted", "blocked", "unavailable"):
            for allowed in (set(), {status}):
                with self.subTest(status=status, allowed=allowed):
                    self.write([event(event="probe_result", status=status)])
                    stats = evidence_board.aggregate([self.path], allow=allowed)
                    self.assertEqual(stats["counts_by_status"], {status: 1})
                    self.assertNotIn("passed", stats["counts_by_status"])
                    self.assertEqual(stats["exit_code"], 0 if allowed else 1)

    def test_unknown_status_and_empty_input_are_explicit_at_the_cli_boundary(self):
        self.write([event(status="passed"), event(status="unknown")])
        stats = evidence_board.aggregate([self.path], allow=set())
        self.assertEqual(stats["counts_by_status"], {"passed": 1})
        self.assertEqual(stats["malformed_lines"], 1)
        self.assertEqual(stats["exit_code"], 1)
        for records in ([], [event(status="unknown")]):
            with self.subTest(records=records):
                self.write(records)
                output, errors = io.StringIO(), io.StringIO()
                with redirect_stdout(output), redirect_stderr(errors):
                    code = evidence_board.main([str(self.path), "--json"])
                self.assertEqual(code, 2)
                self.assertEqual(output.getvalue(), "")
                self.assertIn("no valid", errors.getvalue())

    def test_valid_pytest_profiles_keep_their_status_when_phase_events_are_reordered(self):
        # The plugin emits setup skip/error, call passed/failed/skipped, and
        # teardown error. These profiles have at most one call per execution.
        for setup, call, teardown in itertools.product((None, "skipped", "error"),
                                                       (ABSENT, "passed", "failed", "skipped"),
                                                       (None, "error")):
            records = phases(setup, call, teardown)
            expected = expected_status(records)
            for reordered in itertools.permutations(records):
                with self.subTest(setup=setup, call="absent" if call is ABSENT else call,
                                  teardown=teardown, order=[r["phase"] for r in reordered]):
                    self.assertEqual(evidence_board._reduce_test_result(reordered)[0], expected)


if __name__ == "__main__":
    unittest.main()
