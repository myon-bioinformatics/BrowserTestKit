"""Keep failures scoped to an execution and session gates separate from counts."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import evidence_board


def event(**fields):
    return {"schema": "btk-event/1", "source": "synthetic",
            "time": "2026-01-01T00:00:00Z", "event": "test_result", **fields}


class EvidenceExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def write(self, name, records):
        path = self.directory / name
        path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
        return path

    def test_each_identity_dimension_separates_a_failed_and_a_passed_execution(self):
        for dimension in ("source", "project", "run_id"):
            with self.subTest(dimension=dimension):
                identity = {"source": "producer", "project": "profile", "run_id": "first"}
                first = event(**identity, test_id="same-case", phase="call", status="failed")
                second = event(**{**identity, dimension: "different"},
                               test_id="same-case", phase="call", status="passed")
                for order in ((first, second), (second, first)):
                    path = self.write("identity.jsonl", order)
                    stats = evidence_board.aggregate([path], allow=set())
                    self.assertEqual(stats["counts_by_status"], {"failed": 1, "passed": 1})
                    self.assertEqual(stats["blocking_statuses"], ["failed"])
                    self.assertEqual(stats["exit_code"], 1)
                    failure, = stats["test_failures"].values()
                    self.assertEqual({key: failure[key] for key in identity}, identity)
                    self.assertEqual(failure["test_id"], "same-case")

    def test_one_execution_split_across_files_still_counts_once(self):
        identity = {"project": "profile", "run_id": "one", "test_id": "case"}
        call = self.write("call.jsonl", [event(**identity, phase="call", status="failed", message="body")])
        cleanup = self.write("cleanup.jsonl", [event(**identity, phase="teardown", status="error", message="cleanup")])
        for paths in ([call, cleanup], [cleanup, call]):
            stats = evidence_board.aggregate(paths, allow=set())
            self.assertEqual(stats["counts_by_status"], {"failed": 1})
            self.assertEqual(stats["test_failures"]["case"]["message"], "body")
            self.assertEqual(stats["test_failures"]["case"]["teardown_message"], "cleanup")

    def test_missing_legacy_project_and_run_fields_keep_existing_keys(self):
        path = self.write("legacy.jsonl", [
            event(test_id="case", phase="call", status="failed"),
            event(test_id="case", phase="teardown", status="error"),
        ])
        stats = evidence_board.aggregate([path], allow=set())
        self.assertEqual(stats["counts_by_status"], {"failed": 1})
        self.assertEqual(list(stats["test_failures"]), ["case"])
        self.assertIsNone(stats["test_failures"]["case"]["project"])
        self.assertIsNone(stats["test_failures"]["case"]["run_id"])

    def test_non_string_identity_fields_are_malformed_instead_of_unhashable(self):
        good = event(test_id="valid-case", run_id="1", phase="call", status="passed")
        for field in ("source", "project", "run_id", "test_id"):
            for value in (["run"], {"run": "one"}, 1, 1.5, True):
                with self.subTest(field=field, value=value):
                    bad = {**event(test_id="invalid-case", phase="call", status="failed"), field: value}
                    path = self.write("invalid-identity.jsonl", [bad, good])
                    read = evidence_board.btk_events.read(path)
                    self.assertEqual(read.events, [good])
                    self.assertEqual(read.events[0]["run_id"], "1")
                    self.assertEqual(read.malformed, 1)
                    stats = evidence_board.aggregate([path], allow=set())
                    self.assertEqual(stats["counts_by_status"], {"passed": 1})
                    self.assertEqual(stats["malformed_lines"], 1)
                    self.assertEqual(stats["exit_code"], 1)
                    allowed = evidence_board.aggregate([path], allow=set(), allow_malformed=True)
                    self.assertEqual(allowed["malformed_lines"], 1)
                    self.assertEqual(allowed["exit_code"], 0)

    def test_emitter_rejects_invalid_identity_before_writing(self):
        output = io.StringIO()
        with self.assertRaisesRegex(evidence_board.btk_events.EventError, "run_id must be a string"):
            evidence_board.btk_events.emit(output, source="synthetic", event="probe", run_id=["one"])
        self.assertEqual(output.getvalue(), "")

    def test_optional_null_and_omission_remain_distinct_from_literal_strings(self):
        for field in ("project", "run_id"):
            with self.subTest(field=field):
                records = [
                    event(test_id="case", phase="call", status="failed"),
                    event(test_id="case", phase="teardown", status="error", **{field: None}),
                    event(test_id="case", phase="call", status="passed", **{field: "missing"}),
                    event(test_id="case", phase="call", status="passed", **{field: "None"}),
                ]
                path = self.write("optional-identity.jsonl", records)
                stats = evidence_board.aggregate([path], allow=set())
                self.assertEqual(stats["counts_by_status"], {"failed": 1, "passed": 2})
                self.assertEqual(stats["malformed_lines"], 0)
                failure, = stats["test_failures"].values()
                self.assertIsNone(failure[field])

    def test_multiple_failures_keep_each_execution_and_teardown_message(self):
        records = []
        for run_id in ("first", "second"):
            records.extend([
                event(run_id=run_id, test_id="case", phase="call", status="failed", message=f"body-{run_id}"),
                event(run_id=run_id, test_id="case", phase="teardown", status="error", message=f"cleanup-{run_id}"),
            ])
        path = self.write("failures.jsonl", records)
        stats = evidence_board.aggregate([path], allow=set())
        self.assertEqual(stats["counts_by_status"], {"failed": 2})
        failures = {failure["run_id"]: failure for failure in stats["test_failures"].values()}
        self.assertEqual(set(failures), {"first", "second"})
        for run_id, failure in failures.items():
            self.assertEqual(failure["message"], f"body-{run_id}")
            self.assertEqual(failure["teardown_message"], f"cleanup-{run_id}")
        markdown = evidence_board.render_markdown(stats, [path])
        self.assertIn("case (synthetic/first)", markdown)
        self.assertIn("case (synthetic/second)", markdown)

    def test_a_literal_test_id_cannot_collide_with_a_qualified_failure_key(self):
        lookalike = json.dumps(("synthetic", None, "first", "case"))
        records = [
            event(run_id="first", test_id="case", phase="call", status="failed"),
            event(run_id="second", test_id="case", phase="call", status="failed"),
            event(test_id=lookalike, phase="call", status="error"),
        ]
        path = self.write("keys.jsonl", records)
        stats = evidence_board.aggregate([path], allow=set())
        self.assertEqual(stats["counts_by_status"], {"failed": 2, "error": 1})
        self.assertEqual(len(stats["test_failures"]), 3)
        self.assertEqual({(value["run_id"], value["test_id"]) for value in stats["test_failures"].values()},
                         {("first", "case"), ("second", "case"), (None, lookalike)})

    def test_blocking_summary_fails_the_gate_without_increasing_test_counts(self):
        for status in ("failed", "error", "interrupted", "blocked", "unavailable"):
            for allowed in (set(), {status}):
                with self.subTest(status=status, allowed=allowed):
                    path = self.write("summary.jsonl", [
                        event(test_id="case", phase="call", status="passed"),
                        event(event="session_summary", status=status),
                    ])
                    stats = evidence_board.aggregate([path], allow=allowed)
                    self.assertEqual(stats["counts_by_status"], {"passed": 1})
                    self.assertEqual(stats["blocking_statuses"], [] if allowed else [status])
                    self.assertEqual(stats["exit_code"], 0 if allowed else 1)
                    self.assertEqual(stats["session_summaries"][0]["status"], status)

    def test_failed_summary_alone_is_a_failed_cli_gate(self):
        path = self.write("no-tests.jsonl", [event(event="session_summary", status="failed", returncode=5)])
        output = io.StringIO()
        with redirect_stdout(output):
            code = evidence_board.main([str(path), "--json"])
        self.assertEqual(code, 1)
        stats = json.loads(output.getvalue())
        self.assertEqual(stats["counts_by_status"], {})
        self.assertEqual(stats["blocking_statuses"], ["failed"])
        self.assertEqual(stats["session_summaries"][0]["returncode"], 5)

    def test_test_and_session_blockers_are_unioned_without_count_duplication(self):
        path = self.write("mixed.jsonl", [
            event(test_id="case", phase="setup", status="error"),
            event(event="session_summary", status="failed", returncode=1),
        ])
        stats = evidence_board.aggregate([path], allow=set())
        self.assertEqual(stats["counts_by_status"], {"error": 1})
        self.assertEqual(stats["blocking_statuses"], ["error", "failed"])
        self.assertEqual(stats["exit_code"], 1)

    def test_passed_summary_alone_retains_the_valid_event_contract(self):
        path = self.write("passed-summary.jsonl", [event(event="session_summary", status="passed", returncode=0)])
        stats = evidence_board.aggregate([path], allow=set())
        self.assertEqual(stats["counts_by_status"], {})
        self.assertEqual(stats["exit_code"], 0)


if __name__ == "__main__":
    unittest.main()
