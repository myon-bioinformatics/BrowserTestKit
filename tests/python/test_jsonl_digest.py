from __future__ import annotations

import importlib.util, json, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "jsonl_digest.py"
spec = importlib.util.spec_from_file_location("jsonl_digest", SCRIPT)
jsonl_digest = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(jsonl_digest)

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
