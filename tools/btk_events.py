#!/usr/bin/env python3
"""Read and write btk-event/1 JSONL: one shared event contract for this kit.

See ``docs/evidence-events.md`` for the full contract. In short, one JSON
object per line with required fields ``schema`` (``"btk-event/1"``),
``source``, ``time`` (UTC ISO 8601), and ``event``; an optional ``status``
restricted to a fixed vocabulary (``passed``/``failed``/``error``/
``interrupted``/``blocked``/``unavailable``/``skipped``); an optional
``stage`` restricted to :data:`STAGES` (the ``FAILURE_LAYER_FLATTENING``
pipeline layers); an optional, unconstrained ``phase`` (a producer-defined
sub-step, e.g. pytest's ``setup``/``call``/``teardown``); and several other
optional fields consumers must tolerate even when unrecognized.

This module is a library, not a CLI: it is imported by
``tools/terminal_browser.py``, ``tools/pytest_btk_events.py``, and
``tools/evidence_board.py``. Nothing here is loaded implicitly -- there is
no ``conftest.py`` and no import-time side effect beyond defining names.

Stdlib only; runs under ``python -S``.
"""
from __future__ import annotations

import argparse, json
from datetime import datetime, timezone; from pathlib import Path; from typing import Any, Iterable, NamedTuple, TextIO, Union

SCHEMA = "btk-event/1"

REQUIRED_FIELDS = ("schema", "source", "time", "event")

STAGES = frozenset(
    {"install", "launch", "navigate", "interact", "assert", "screenshot", "artifact", "cleanup"}
)

# Statuses that must never be reported as "passed" by an aggregator, even
# though the run did produce evidence: see docs/evidence-events.md.
NEVER_PASSING_STATUSES = frozenset({"failed", "error", "interrupted", "blocked", "unavailable"})

STATUSES = frozenset(NEVER_PASSING_STATUSES | {"passed", "skipped"})


class EventError(ValueError):
    """A btk-event/1 payload is missing a required field or has a bad status."""


def now_iso() -> str:
    """The current UTC time as an ISO 8601 string, matching every producer here."""
    return datetime.now(timezone.utc).isoformat()


def validate(fields: dict) -> None:
    """Raise :class:`EventError` unless ``fields`` is a well-formed btk-event/1 payload."""
    missing = [name for name in REQUIRED_FIELDS if not fields.get(name)]
    if missing:
        raise EventError(f"btk-event/1: missing required field(s): {', '.join(missing)}")
    status = fields.get("status")
    if status is not None and status not in STATUSES:
        raise EventError(f"btk-event/1: unknown status {status!r}; expected one of {sorted(STATUSES)}")
    stage = fields.get("stage")
    if stage is not None and stage not in STAGES:
        raise EventError(f"btk-event/1: unknown stage {stage!r}; expected one of {sorted(STAGES)}")


def emit(stream: TextIO, **fields: Any) -> dict:
    """Write one validated btk-event/1 JSON line to ``stream`` and return the payload.

    ``schema`` defaults to :data:`SCHEMA` and ``time`` defaults to
    :func:`now_iso` when not given. Every other required/optional field is
    whatever the caller passes as a keyword argument, for example::

        btk_events.emit(sys.stderr, source="terminal-browser", event="terminal_browser_exit",
                         returncode=0, status="passed")

    Raises :class:`EventError` if a required field is missing or ``status``
    is not part of the vocabulary; nothing is written in that case.
    """
    payload = dict(fields)
    payload.setdefault("schema", SCHEMA)
    payload.setdefault("time", now_iso())
    validate(payload)
    print(json.dumps(payload, ensure_ascii=False), file=stream, flush=True)
    return payload


class ReadResult(NamedTuple):
    """The result of :func:`read`: parsed events plus how many lines were skipped."""

    events: list
    malformed: int
    total_lines: int


def _iter_lines(source: Union[str, Path, Iterable[str], TextIO]) -> Iterable[str]:
    if isinstance(source, (str, Path)):
        with Path(source).open("r", encoding="utf-8") as handle:
            yield from handle
        return
    yield from source


def read(path_or_lines: Union[str, Path, Iterable[str], TextIO]) -> ReadResult:
    """Read a btk-event/1 JSONL stream, skipping and counting malformed lines.

    ``path_or_lines`` is a file path (``str``/``Path``), an open text file, or
    any iterable of lines (for example a list already split from a string).
    A line is malformed -- counted in ``malformed`` and left out of
    ``events`` -- if it is not valid JSON, is not a JSON object, or is
    missing a required btk-event/1 field. Blank lines are skipped silently
    and are not counted at all.
    """
    events: list = []
    malformed = 0
    total = 0
    for raw_line in _iter_lines(path_or_lines):
        line = raw_line.strip()
        if not line:
            continue
        total += 1
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if not isinstance(record, dict):
            malformed += 1
            continue
        try:
            validate(record)
        except EventError:
            malformed += 1
            continue
        events.append(record)
    return ReadResult(events=events, malformed=malformed, total_lines=total)


def classify_terminal_browser(event: dict) -> Union[str, None]:
    """Map a ``tools/terminal_browser.py`` event to a btk-event/1 ``status``.

    See "Mapping tools/terminal_browser.py exits to status" in
    ``docs/evidence-events.md``. Returns ``None`` for terminal-browser events
    that carry no status of their own (``terminal_browser_found``,
    ``terminal_browser_start``).
    """
    name = event.get("event")
    if name == "TERMINAL_BROWSER_UNAVAILABLE":
        return "unavailable"
    if name != "terminal_browser_exit":
        return None
    if event.get("interrupted"):
        return "interrupted"
    if event.get("signal") is not None:
        return "error"
    if event.get("returncode") == 0:
        return "passed"
    return "failed"


def main(argv: Union[list, None] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="btk_events",
        description=(
            "btk-event/1 library: emit(), read(), classify_terminal_browser(). "
            "This module has no standalone behavior; import it from a producer "
            "or consumer script, such as tools/evidence_board.py."
        ),
    )
    parser.parse_args(argv)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
