#!/usr/bin/env python3
"""Aggregate one or more btk-event/1 JSONL streams into an evidence board.

Reads every event from the given files (see ``docs/evidence-events.md`` for
the contract) and reports:

- counts per ``status``, one per **test** rather than one per raw event (see
  "Aggregating pytest phases per test_id" below) -- so a test whose ``call``
  and ``teardown`` phases each report their own status is not double
  counted;
- ``session_summary`` events reported separately, never folded into the
  per-status counts (a session's own pass/fail rollup would otherwise double
  count every test already reflected in its own ``test_result`` events);
- counts per ``source``/``project`` (grouped as ``source`` alone, or
  ``source/project`` when the event carries a ``project``);
- the first failure recorded for each ``stage``
  (``FAILURE_LAYER_FLATTENING`` in ``docs/antipatterns.md``);
- ``interrupted``/``unavailable`` counts, called out separately -- neither is
  ever folded into "passed".

## Aggregating pytest phases per test_id

A pytest test can emit more than one ``test_result`` event for the same
``test_id`` -- one per report phase (``phase``: ``setup``/``call``/
``teardown``; see ``tools/pytest_btk_events.py``). Counting every event
would double count a test whose ``call`` phase reports a status and whose
``teardown`` phase *also* reports one (a teardown that raises after the test
body already ran). Events that carry a ``test_id`` are therefore buffered
and reduced to exactly one status per ``test_id``, in this order:

1. A ``call``-phase event with ``status: "failed"`` makes the whole test
   **failed**, even if ``teardown`` also reports ``"error"`` afterward --
   that teardown problem is noted (message recorded), not counted as a
   second failing test.
2. Otherwise, any ``setup``/``teardown`` event with ``status: "error"`` makes
   the test **error** (the test itself never got to run, or cleanup broke).
3. Otherwise, the ``call``-phase status (``passed``/``skipped``) is used.
4. Otherwise (no ``call`` phase ran at all, e.g. a ``setup`` skip), whatever
   single status is present is used.

Events without a ``test_id`` (for example ``terminal_browser_exit``) are
counted directly, one per event, as before.

Output is a Markdown table by default, or ``--json``. ``--step-summary``
also appends the Markdown table to ``$GITHUB_STEP_SUMMARY`` when that
environment variable is set (as in a GitHub Actions step), regardless of
``--json``.

Exit codes: 0 = nothing failed/errored/interrupted/blocked/unavailable (or
every such status is covered by ``--allow``), at least one valid event was
read, and no malformed line was skipped (or ``--allow-malformed`` was
given); 1 = a blocking status was not allowed, or a malformed line was
skipped without ``--allow-malformed``; 2 = bad input (missing file,
unreadable file, unknown ``--allow`` status, or zero valid btk-event/1
events across all input files).

Stdlib only; runs under ``python -S``.
"""
from __future__ import annotations

import argparse, json, os, sys
from pathlib import Path; from typing import Union

import btk_events

# Statuses that block a green board unless explicitly --allow-ed.
BLOCKING_STATUSES = btk_events.NEVER_PASSING_STATUSES
# Of those, the two the review specifically asked never be silently counted
# as passed, and that get their own callout line/fields.
CALLOUT_STATUSES = ("interrupted", "unavailable")
# Statuses that pin down which stage a failure happened at.
FAILING_STATUSES = frozenset({"failed", "error"})


def _reduce_test_result(events: list) -> tuple:
    """Reduce one ``test_id``'s buffered ``test_result`` events to one status.

    ``events`` is every ``test_result`` record seen for a single ``test_id``,
    in the order read. See "Aggregating pytest phases per test_id" above for
    the rule. Returns ``(status, info)`` where ``info`` carries ``message``
    and, when a call failure was followed by a teardown error, a
    ``teardown_message`` noting it. Returns ``(None, {})`` if no event in the
    group carries a status at all.
    """
    call_event = None
    error_events = []
    other_event = None
    for event in events:
        status = event.get("status")
        if event.get("phase") == "call":
            call_event = event
        elif status == "error":
            error_events.append(event)
        elif other_event is None and status is not None:
            other_event = event

    if call_event is not None and call_event.get("status") == "failed":
        teardown_errors = [event for event in error_events if event.get("phase") == "teardown"]
        info = {"message": call_event.get("message")}
        if teardown_errors:
            info["teardown_message"] = teardown_errors[0].get("message")
        return "failed", info
    if error_events:
        return "error", {"message": error_events[0].get("message")}
    if call_event is not None:
        return call_event.get("status"), {"message": call_event.get("message")}
    if other_event is not None:
        return other_event.get("status"), {"message": other_event.get("message")}
    return None, {}


def aggregate(paths: list, allow: set, allow_malformed: bool = False) -> dict:
    counts_by_status: dict = {}
    counts_by_group: dict = {}
    first_failure_by_stage: dict = {}
    session_summaries: list = []
    tests_by_id: dict = {}
    malformed_lines = 0
    total_events = 0

    for path in paths:
        result = btk_events.read(path)
        malformed_lines += result.malformed
        for record in result.events:
            total_events += 1
            source = record.get("source") or "(unknown source)"
            project = record.get("project")
            group = f"{source}/{project}" if project else source
            counts_by_group[group] = counts_by_group.get(group, 0) + 1

            status = record.get("status")
            stage = record.get("stage")
            if stage and status in FAILING_STATUSES and stage not in first_failure_by_stage:
                first_failure_by_stage[stage] = {
                    "status": status,
                    "source": source,
                    "project": project,
                    "test_id": record.get("test_id"),
                    "time": record.get("time"),
                    "message": record.get("message"),
                }

            if record.get("event") == "session_summary":
                # Reported separately below -- a session's own rollup status
                # would otherwise double count every test it already covers.
                session_summaries.append(
                    {
                        "source": source,
                        "project": project,
                        "run_id": record.get("run_id"),
                        "status": status,
                        "returncode": record.get("returncode"),
                        "time": record.get("time"),
                    }
                )
                continue

            test_id = record.get("test_id")
            if record.get("event") == "test_result" and test_id:
                tests_by_id.setdefault(test_id, []).append(record)
                continue

            if status is None:
                continue
            counts_by_status[status] = counts_by_status.get(status, 0) + 1

    test_failures: dict = {}
    for test_id, events in tests_by_id.items():
        status, info = _reduce_test_result(events)
        if status is None:
            continue
        counts_by_status[status] = counts_by_status.get(status, 0) + 1
        if status in FAILING_STATUSES:
            test_failures[test_id] = {"status": status, **info}

    present_blocking = {status for status in BLOCKING_STATUSES if counts_by_status.get(status, 0) > 0}
    blocking = sorted(present_blocking - allow)
    malformed_blocking = bool(malformed_lines) and not allow_malformed

    return {
        "counts_by_status": counts_by_status,
        "counts_by_group": counts_by_group,
        "first_failure_by_stage": first_failure_by_stage,
        "session_summaries": session_summaries,
        "test_failures": test_failures,
        "interrupted": counts_by_status.get("interrupted", 0),
        "unavailable": counts_by_status.get("unavailable", 0),
        "malformed_lines": malformed_lines,
        "malformed_blocking": malformed_blocking,
        "total_events": total_events,
        "allowed": sorted(allow),
        "blocking_statuses": blocking,
        "exit_code": 1 if (blocking or malformed_blocking) else 0,
    }


def render_markdown(stats: dict, paths: list) -> str:
    lines = ["# Evidence board", ""]
    lines.append("Files: " + ", ".join(f"`{path}`" for path in paths))
    lines.append("")

    lines.append("| Status | Count |")
    lines.append("| --- | --- |")
    for status in sorted(btk_events.STATUSES):
        count = stats["counts_by_status"].get(status, 0)
        if count:
            lines.append(f"| {status} | {count} |")
    if not stats["counts_by_status"]:
        lines.append("| _(none)_ | 0 |")
    lines.append("")

    lines.append("| Source/project | Events |")
    lines.append("| --- | --- |")
    for group, count in sorted(stats["counts_by_group"].items()):
        lines.append(f"| {group} | {count} |")
    if not stats["counts_by_group"]:
        lines.append("| _(none)_ | 0 |")
    lines.append("")

    if stats["first_failure_by_stage"]:
        lines.append("| Stage | First failure |")
        lines.append("| --- | --- |")
        for stage, info in sorted(stats["first_failure_by_stage"].items()):
            label = info.get("test_id") or info.get("source")
            message = f" -- {info['message']}" if info.get("message") else ""
            lines.append(f"| {stage} | `{label}` ({info['status']}){message} |")
        lines.append("")

    if stats["session_summaries"]:
        lines.append("| Session summary | Status |")
        lines.append("| --- | --- |")
        for summary in stats["session_summaries"]:
            label = f"{summary['source']}/{summary['project']}" if summary.get("project") else summary["source"]
            if summary.get("run_id"):
                label = f"{label} ({summary['run_id']})"
            lines.append(f"| {label} | {summary.get('status')} |")
        lines.append("")

    notes = []
    for status in CALLOUT_STATUSES:
        count = stats[status]
        if count:
            notes.append(f"{count} `{status}` event(s) (never counted as passed)")
    for test_id, info in sorted(stats["test_failures"].items()):
        if info.get("teardown_message"):
            notes.append(f"`{test_id}` failed in call, then also errored in teardown: {info['teardown_message']}")
    if stats["session_summaries"]:
        notes.append(
            f"{len(stats['session_summaries'])} session_summary event(s) reported above, not counted in Status"
        )
    if stats["malformed_lines"]:
        allowed_note = "" if stats["malformed_blocking"] else " (allowed via --allow-malformed)"
        notes.append(f"{stats['malformed_lines']} malformed JSONL line(s) skipped{allowed_note}")
    if stats["allowed"]:
        notes.append(f"allowed (excluded from failing the board): {', '.join(stats['allowed'])}")
    if notes:
        lines.append("Notes: " + "; ".join(notes))
        lines.append("")

    lines.append(f"**Result: {'FAIL' if stats['exit_code'] else 'PASS'}**")
    if stats["blocking_statuses"]:
        lines.append(f"Blocking status(es): {', '.join(stats['blocking_statuses'])}")
    if stats["malformed_blocking"]:
        lines.append("Blocking: malformed JSONL line(s) present without --allow-malformed")
    return "\n".join(lines)


def main(argv: Union[list, None] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="evidence_board",
        description="Aggregate one or more btk-event/1 JSONL files into a Markdown or JSON evidence board.",
    )
    parser.add_argument("paths", nargs="+", type=Path, help="btk-event/1 JSONL file(s) to aggregate")
    parser.add_argument("--json", action="store_true", help="print the aggregate as JSON instead of a Markdown table")
    parser.add_argument(
        "--step-summary",
        action="store_true",
        help="also append the Markdown table to $GITHUB_STEP_SUMMARY, when that variable is set",
    )
    parser.add_argument(
        "--allow",
        default="",
        metavar="STATUS[,STATUS...]",
        help=(
            "comma-separated statuses that must not fail the board even though they are "
            "not 'passed' (for example: unavailable, when terminal-browser is missing on CI)"
        ),
    )
    parser.add_argument(
        "--allow-malformed",
        action="store_true",
        help=(
            "do not fail the board when a malformed JSONL line was skipped "
            "(default: any malformed line fails the board)"
        ),
    )
    args = parser.parse_args(argv)

    allow = {name.strip() for name in args.allow.split(",") if name.strip()}
    unknown = allow - btk_events.STATUSES
    if unknown:
        parser.error(f"--allow: unknown status(es): {', '.join(sorted(unknown))}")

    for path in args.paths:
        if not path.is_file():
            print(f"error: {path}: no such file", file=sys.stderr)
            return 2

    try:
        stats = aggregate(args.paths, allow, allow_malformed=args.allow_malformed)
    except (OSError, UnicodeDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if stats["total_events"] == 0:
        files = ", ".join(str(path) for path in args.paths)
        print(f"error: no valid btk-event/1 events found in: {files}", file=sys.stderr)
        return 2

    markdown = render_markdown(stats, args.paths)
    if args.json:
        print(json.dumps(stats, indent=2, sort_keys=True))
    else:
        print(markdown)

    if args.step_summary:
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with open(summary_path, "a", encoding="utf-8") as handle:
                handle.write(markdown + "\n")

    return stats["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
