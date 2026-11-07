#!/usr/bin/env python3
"""Opt-in pytest plugin: write btk-event/1 JSONL per test result.

This does **nothing** unless explicitly loaded and given ``--btk-events``:

    pytest -p pytest_btk_events --btk-events test-results/btk-events.jsonl

There is deliberately no ``conftest.py`` anywhere in this kit that would load
this plugin implicitly -- browser-test-kit is used across projects, and a
pytest plugin that starts writing files just because it is importable would
surprise every one of them. Loading it with ``-p`` and PYTHONPATH pointing at
``scripts/`` (or installing it) is the only way it activates; without
``--btk-events`` it registers no behavior at all.

Per test, this writes one ``event: "test_result"`` btk-event/1 record (see
``docs/evidence-events.md`` for the contract and status vocabulary):

- the ``call`` phase outcome maps directly to ``passed``/``failed``/``skipped``
  (a runtime ``pytest.skip()`` inside the test body is reported at ``call``);
- a failure during the ``setup`` or ``teardown`` phase -- the test itself
  never got to run, or cleanup broke -- maps to ``error``, not ``failed``;
- a skip evaluated during ``setup`` (a ``@pytest.mark.skip``/``skipif``
  marker, or a fixture that calls ``pytest.skip()``) maps to ``skipped`` with
  ``phase: "setup"``; the ``call``/``teardown`` phases never happen after
  that, so no duplicate event follows.

Each ``test_result`` event records pytest's own report phase (``setup``,
``call``, or ``teardown``) in the optional ``phase`` field, not ``stage``:
``stage`` is reserved for the ``FAILURE_LAYER_FLATTENING`` pipeline layers in
``btk_events.STAGES`` (``install``, ``launch``, ...), which pytest's
setup/call/teardown phases are not part of.

A closing ``event: "session_summary"`` record is always written, with
``status: "interrupted"`` if a ``KeyboardInterrupt`` reached pytest during the
run, else ``"passed"``/``"failed"`` from the session exit status. When pytest
collects no tests at all (exit code 5), the session still finishes normally
(no ``KeyboardInterrupt``) with a non-zero exit status, so
``session_summary`` reports ``status: "failed"``. An ``xfail`` test (not
``xfail(strict=True)``) is reported by pytest itself with ``outcome ==
"skipped"`` at the ``call`` phase, so it maps to ``status: "skipped"`` here
like any other skip.

``--btk-events PATH`` truncates PATH at session start by default, so each run's
file holds only that run's events; pass ``--btk-events-append`` to append to
an existing file instead.

Stdlib only; runs under ``python -S``. This module never imports ``pytest``
itself -- pytest discovers plugins by the hook names below, so the plugin
works whether or not ``pytest`` happens to be importable in the interpreter
that merely inspects this file (for example ``python -S -m pytest_btk_events
--help``).
"""
from __future__ import annotations

import argparse, os
from pathlib import Path; from typing import Any, Union

import btk_events

SOURCE = "pytest-btk-events"

# Report phases that can end a test without a "call" phase ever running.
_TERMINAL_NONCALL_OUTCOMES = {"failed": "error", "skipped": "skipped"}


def pytest_addoption(parser: Any) -> None:
    group = parser.getgroup("btk-events", "btk-event/1 JSONL recording (opt-in)")
    group.addoption(
        "--btk-events",
        dest="btk_events_path",
        default=None,
        metavar="PATH",
        help=(
            "Write one btk-event/1 JSONL record per test result, plus a session "
            "summary, to PATH. Opt-in: nothing is written unless this is given, "
            "and this plugin only runs at all when loaded explicitly with "
            "'-p pytest_btk_events'."
        ),
    )
    group.addoption(
        "--btk-events-project",
        dest="btk_events_project",
        default=None,
        metavar="NAME",
        help="optional project name recorded on every btk-event this run writes",
    )
    group.addoption(
        "--btk-events-run-id",
        dest="btk_events_run_id",
        default=None,
        metavar="ID",
        help=(
            "optional run id recorded on every btk-event this run writes "
            "(default: $GITHUB_RUN_ID if set, else omitted)"
        ),
    )
    group.addoption(
        "--btk-events-append",
        dest="btk_events_append",
        action="store_true",
        default=False,
        help=(
            "append to an existing --btk-events PATH instead of truncating it "
            "at session start (default: truncate, so each run's file holds "
            "only that run's events)"
        ),
    )


def pytest_configure(config: Any) -> None:
    path = config.getoption("btk_events_path", default=None)
    if not path:
        return
    recorder = _Recorder(
        path=Path(path),
        project=config.getoption("btk_events_project", default=None),
        run_id=config.getoption("btk_events_run_id", default=None) or os.environ.get("GITHUB_RUN_ID"),
        append=config.getoption("btk_events_append", default=False),
    )
    config._btk_events_recorder = recorder  # keep it alive/reachable for pytest_unconfigure
    config.pluginmanager.register(recorder, "btk-events-recorder")


def pytest_unconfigure(config: Any) -> None:
    recorder = getattr(config, "_btk_events_recorder", None)
    if recorder is not None:
        recorder.close()


def _status_for_report(report: Any) -> Union[str, None]:
    """The btk-event/1 status for one pytest ``TestReport``, or ``None`` to skip it."""
    if report.when == "call":
        if report.outcome == "passed":
            return "passed"
        if report.outcome == "failed":
            return "failed"
        if report.outcome == "skipped":
            return "skipped"
        return None
    if report.when in ("setup", "teardown"):
        if report.outcome == "failed":
            return "error"
        if report.when == "setup" and report.outcome == "skipped":
            return "skipped"
        return None
    return None


def _short_message(report: Any) -> Union[str, None]:
    longrepr = getattr(report, "longrepr", None)
    if not longrepr:
        return None
    reprcrash = getattr(longrepr, "reprcrash", None)
    message = getattr(reprcrash, "message", None)
    if message:
        return str(message)[:500]
    text = str(longrepr).strip().splitlines()
    return text[-1][:500] if text else None


class _Recorder:
    """Registered as a pytest plugin object only when ``--btk-events`` is set."""

    def __init__(
        self,
        path: Path,
        project: Union[str, None],
        run_id: Union[str, None],
        append: bool = False,
    ) -> None:
        self.path = path
        self.project = project
        self.run_id = run_id
        self.append = append
        self._handle = None
        self._counts: dict = {}
        self._interrupted = False
        self._summary_written = False

    def _stream(self):
        if self._handle is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Truncate by default so each run's file holds only that run's
            # events; --btk-events-append keeps the historical append
            # behavior for a caller that wants to accumulate across runs.
            mode = "a" if self.append else "w"
            self._handle = self.path.open(mode, encoding="utf-8")
        return self._handle

    def _record(self, event: str, **fields: Any) -> None:
        payload = {key: value for key, value in fields.items() if value is not None}
        payload.setdefault("source", SOURCE)
        if self.run_id:
            payload.setdefault("run_id", self.run_id)
        if self.project:
            payload.setdefault("project", self.project)
        written = btk_events.emit(self._stream(), event=event, **payload)
        status = written.get("status")
        if isinstance(status, str):
            self._counts[status] = self._counts.get(status, 0) + 1

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    # -- pytest hooks -----------------------------------------------------

    def pytest_sessionstart(self, session: Any) -> None:
        self._record("session_start")

    def pytest_runtest_logreport(self, report: Any) -> None:
        status = _status_for_report(report)
        if status is None:
            return
        self._record(
            "test_result",
            test_id=report.nodeid,
            phase=report.when,
            status=status,
            message=_short_message(report) if status in ("failed", "error") else None,
        )

    def _record_summary(self, status: str, returncode: int) -> None:
        if self._summary_written:
            return
        counts = {f"count_{name}": count for name, count in sorted(self._counts.items())}
        self._record("session_summary", status=status, returncode=returncode, **counts)
        self._summary_written = True
        self.close()

    def pytest_keyboard_interrupt(self, excinfo: Any) -> None:
        self._interrupted = True
        # pytest may abort before pytest_sessionfinish when SIGINT lands just
        # after session_start. Persist the terminal evidence at the interrupt
        # hook itself; sessionfinish is idempotent through _summary_written.
        self._record_summary("interrupted", 2)

    def pytest_sessionfinish(self, session: Any, exitstatus: int) -> None:
        if self._interrupted:
            status = "interrupted"
        elif int(exitstatus) == 0:
            status = "passed"
        else:
            status = "failed"
        self._record_summary(status, int(exitstatus))


def main(argv: Union[list, None] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pytest_btk_events",
        description=(
            "Opt-in pytest plugin: writes btk-event/1 JSONL test results and a "
            "session summary. Load it explicitly -- it is never activated "
            "implicitly (no conftest.py) -- with: "
            "pytest -p pytest_btk_events --btk-events PATH"
        ),
    )
    parser.parse_args(argv)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
