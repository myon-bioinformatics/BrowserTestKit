# btk-event/1: the evidence event contract

This is the event contract a [PR #16](../../../pull/16) review asked for before adding more
terminal-browser features: one JSONL event stream that a pytest plugin, an
aggregate evidence board, the GitHub Step Summary, and incident reports can
all consume, instead of each terminal-browser feature growing its own ad hoc
log shape. It generalizes the JSONL events `tools/terminal_browser.py`
already emits on stderr (see `docs/terminal-browser.md`) into a shape any
producer in this kit -- or in a project that vendors this kit -- can reuse.

It exists to keep this repo's own anti-patterns
(`docs/antipatterns.md`) from recurring in the aggregation layer:
`FAILURE_LAYER_FLATTENING` (record which stage failed), `MISSING_FAILURE_ARTIFACTS`
and `EVIDENCE_MASKS_ROOT_FAILURE` (do not let evidence capture hide the real
failure), and `SUCCESS_COUNT_VS_RETRY_ARTIFACTS` (aggregate by identity, not
by raw count).

## Nothing here is implicit

This kit is used across projects. Nothing in this contract is loaded or
applied automatically:

- there is no `conftest.py`; a project that wants `tools/pytest_btk_events.py`
  opts in explicitly with `pytest -p pytest_btk_events --btk-events PATH`;
- the plugin does nothing at all unless `--btk-events PATH` is given;
- `tools/evidence_board.py` only reads the JSONL files you pass it.

## Shape

One JSON object per line (JSONL). A producer may write to its own file, or
to stderr the way `tools/terminal_browser.py` does; consumers read whatever
stream or file they are given.

### Required fields

Every event has all four:

| Field | Type | Meaning |
| --- | --- | --- |
| `schema` | string | Contract version, always the literal `"btk-event/1"` for this version. |
| `source` | string | Producer identity, e.g. `"terminal-browser"`, `"pytest-btk-events"`. |
| `time` | string | UTC ISO 8601 timestamp (`datetime.now(timezone.utc).isoformat()`). |
| `event` | string | Producer-defined event name, e.g. `"terminal_browser_exit"`, `"test_result"`, `"session_summary"`. |

### Optional fields

| Field | Type | Meaning |
| --- | --- | --- |
| `run_id` | string | Opaque id for one execution/run (e.g. a CI run id), to correlate events across files and sources. |
| `test_id` | string | Identifies one test/case, e.g. a pytest nodeid. |
| `project` | string | A sub-project/browser-profile/lane, e.g. a Playwright project name. |
| `stage` | string | One of `install`, `launch`, `navigate`, `interact`, `assert`, `screenshot`, `artifact`, `cleanup` -- the `FAILURE_LAYER_FLATTENING` layers. |
| `phase` | string | A producer-defined sub-step of one `test_id`, e.g. pytest's report phase (`setup`/`call`/`teardown`, as written by `tools/pytest_btk_events.py`). Unlike `stage`, `phase` is **not** restricted to a fixed vocabulary -- it exists because pytest's setup/call/teardown are not `FAILURE_LAYER_FLATTENING` layers and must not be written into `stage`. |
| `status` | string | One of the status vocabulary below. |
| `returncode` | int | Process exit code, normalized the way `terminal_browser.py` already normalizes signal deaths (`128 + signal`). |
| `signal` | int | Signal number, present only when the process died from a signal. |
| `interrupted` | bool | `true` when a person or CI asked the run to stop (Ctrl-C/SIGINT) and the producer is reporting that as a deliberate stop. |
| `artifact` | string | Path to a file the event refers to (screenshot, log, trace, ...). |
| `message` | string | Short human-readable detail, e.g. an exception summary. |

## Status vocabulary

`status` is optional (lifecycle events like `terminal_browser_found` carry no
status), but when present it must be one of exactly these seven values:

- **`passed`** -- the step/test ran to completion and its expectations held.
- **`failed`** -- the step/test ran to completion but did not meet its
  expectation (an assertion failure, or a plain non-zero exit that is not
  itself an infrastructure problem).
- **`error`** -- the step/test could not be evaluated at all because
  something in the harness/fixture/tooling broke: a setup/teardown
  exception, an unexpected signal (SIGSEGV, an externally sent SIGTERM,
  ...), or another wrapper-detected abnormal termination. The distinction
  from `failed` matters: `failed` means the code under test disagreed with
  the assertion; `error` means it never got far enough to disagree.
- **`interrupted`** -- a person or CI explicitly asked the run to stop
  (Ctrl-C/SIGINT) and the producer is honoring that as a deliberate stop
  rather than reporting a failure. Never counted as `passed`, and kept
  distinct from `failed` so a dashboard does not read "someone hit Ctrl-C"
  as "the code is broken".
- **`blocked`** -- the step/test could not proceed because a prerequisite it
  depends on did not hold (an unhealthy fixture/service dependency, a
  required earlier stage that did not reach the state this stage needs).
  Distinct from `unavailable`: `blocked` is an unmet precondition in this
  run; `unavailable` is a tool that is not installed at all.
- **`unavailable`** -- the tool/binary/service the step needed is not
  installed or reachable in this environment, so the step could not even
  attempt to run (for example: terminal-browser is not on `PATH`). It is a
  statement about the environment, not about the code, and is never counted
  as `passed`.
- **`skipped`** -- the step/test was deliberately not run (a skip marker, a
  platform guard). Nothing was attempted and nothing failed.

## Mapping `tools/terminal_browser.py` exits to `status`

`tools/btk_events.classify_terminal_browser(event)` implements this, in
this order:

1. `event == "TERMINAL_BROWSER_UNAVAILABLE"` -> **`unavailable`** (the
   wrapper's exit code is 127, but 127 alone is not sufficient attribution
   per `docs/terminal-browser.md`, since a discovered child could itself
   exit 127 -- use the event name).
2. `event == "terminal_browser_exit"` and `interrupted` is true ->
   **`interrupted`**, regardless of `returncode`/`signal`. An interrupted run
   can still exit via the SIGINT it received (`returncode: 130, signal: 2`);
   that stays `interrupted`, not `error`, because the wrapper handed off to
   the child deliberately (see the Ctrl-C handoff in `docs/terminal-browser.md`).
3. `event == "terminal_browser_exit"`, `interrupted` is not set, and `signal`
   is present -> **`error`** (the child died from a signal nobody asked for,
   e.g. an externally sent SIGTERM).
4. `event == "terminal_browser_exit"` and `returncode == 0` -> **`passed`**.
5. `event == "terminal_browser_exit"` and any other `returncode` ->
   **`failed`**.

## Compatibility

- Consumers must ignore fields they do not recognize.
- Producers must never rename or repurpose a field within schema version
  `"btk-event/1"`. A breaking change to a field's meaning ships as a new
  schema string (`"btk-event/2"`), not a silent redefinition.
- New optional fields and new `event` names can be added freely. Two fields
  are restricted, each when present: `status` to the seven-value vocabulary
  above, and `stage` to the `FAILURE_LAYER_FLATTENING` layers listed in the
  optional fields table (`tools/btk_events.validate()` rejects either one
  outside its vocabulary). `phase` is deliberately unconstrained.

## Using the board in CI

`evidence_board.py` exit codes:

- **0**: no event has a blocking status (failed, error, interrupted, blocked, unavailable), or every blocking status present is listed in `--allow`; at least one valid event was read; and no malformed line was skipped, or `--allow-malformed` was given.
- **1**: at least one blocking status is not allowed, or a malformed JSONL line was skipped without `--allow-malformed`.
- **2**: bad input -- a missing or unreadable file, an unknown status in `--allow`, or **zero valid btk-event/1 events** across every input file (an empty file, or a file that is nothing but malformed lines, must not report a passing board).

`skipped` and `passed` never fail the board. `--allow unavailable` is the usual choice for a CI job where an optional tool such as terminal-browser is not installed; the board still reports those events in its callout line. `--allow-malformed` is for a producer/consumer version mismatch a project has explicitly decided to tolerate; by default any malformed line fails the board, since a truncated or corrupted JSONL file should not be able to slip through as a pass.

`session_summary` events (written once per pytest session by `tools/pytest_btk_events.py`) are reported in their own table/notes, never folded into the Status counts: a session's own pass/fail rollup would otherwise double count every test already reflected in that session's `test_result` events. Similarly, a test whose `call` phase fails and whose `teardown` phase then also errors is counted once, as one `failed` test, with the teardown problem noted rather than counted as a second failure -- see "Aggregating pytest phases per test_id" in `tools/evidence_board.py`'s module docstring for the full per-`test_id` reduction rule.

Two pytest-specific outcomes worth knowing when reading the board:

- If pytest collects **no tests at all**, it exits with code 5. `tools/pytest_btk_events.py` still writes a normal `session_summary`, with `status: "failed"` (a non-zero, non-`KeyboardInterrupt` exit).
- `xfail` (an expected failure, without `strict=True`) is reported by pytest with `outcome == "skipped"` at the `call` phase, so it is written here with `status: "skipped"`, the same as any other skip.

Set `run_id` so JSONL files from several jobs or retries can be joined later, for example in GitHub Actions:

```sh
PYTHONPATH=scripts uv run pytest -p pytest_btk_events \
  --btk-events test-results/btk-events.jsonl \
  --btk-events-run-id "$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT"
python tools/evidence_board.py test-results/*.jsonl --allow unavailable --step-summary
```

`--btk-events PATH` truncates PATH at session start by default, so each run's file holds only that run's events; pass `--btk-events-append` to append to an existing file across several pytest invocations that should share one JSONL file instead.

## Reference implementation

### Same-run JUnit / xprobe bridge (test-only)

`tests/python/test_evidence.py::test_same_child_junit_xprobe_and_btk_bridge`
starts one controlled child pytest invocation that writes both `--junitxml`
and `--btk-events`. The child exits **1**, its session summary retains **1**,
and the evidence board exits **1** without allowing failed/error statuses.
The outer regression passes only when these expected failures and their
identities are preserved. This is not CI `continue-on-error`.

| Controlled child | Raw JUnit | xprobe compact identity | btk-event/1 |
| --- | --- | --- | --- |
| Assertion failure | `test_fixture.test_fail[PARAMETER_SENTINEL]`, `failure` | class `test_fixture`, test `test_fail`, kind `failure` | full nodeid, `failed`, phase `call` |
| Fixture exception | `test_fixture.test_setup_error`, `error` | class `test_fixture`, test `test_setup_error`, kind `error` | full nodeid, `error`, phase `setup` |
| Runtime/marker skip | `skipped` | omitted by JUnit importer | `skipped`, phase `call`/`setup` |

The checked-in baseline importer is byte-identical xprobe from merge SHA
`642999cea4185a68bffa7f7ccc46bd78dde03e5a`, vendored only under
`tests/vendor/xprobe` with MIT license and commit/blob/SHA-256 provenance
checked by the regression. No runtime dependency or parser is added.
Repository and report/run identity are explicitly supplied; measured
repository commit identity is unavailable in this fixture, so
`commit_sha=null` (the xprobe source pin is not the measured repository SHA).

Correlation uses exact raw JUnit names and full btk nodeids within this
single controlled fixture. The compact importer strips parameter labels;
its test/class pair is not a universally unique parameter-instance key.
Do not infer one-to-one parameter identity from compact cases alone or
generalize this fixture's mapping into a runner-neutral nodeid parser.

The board consumes the **same** btk stream and preserves failed/error test
counts and the failed session/returncode. Pytest phases remain in the source
stream; they are not reclassified as BTK stages, and the board does not
invent stage evidence. The regression asserts both source phase mapping
and board failure classification.

Raw JUnit, btk messages, board diagnostics and captured stdout are local
diagnostic evidence, not a share-safe learning payload. Synthetic sentinels
verify messages, parameter labels and stdout are absent from the serialized
xprobe corpus. JUnit identity is **not a reproducer**: original inputs and
source chat text must be attached explicitly with provenance, never inferred.
The two schemas stay separate.

This is the small follow-up to shared Issue
[myon-bioinformatics#22](https://github.com/myon-bioinformatics/myon-bioinformatics/issues/22)
and [PR #36's follow-up](https://github.com/myon-bioinformatics/browser-test-kit/pull/36#issuecomment-5947650447).
It provides a regression guard against failure-layer flattening and evidence
masking the original failure. A reusable learning/incident record still
requires explicit review of provenance, failure layer, fix and regression
guard; automatic chat ingestion, reproducer linkage, downstream native
rollout and cross-repository learning are not completed by this fixture.

Run it with `uv run pytest tests/python/test_evidence.py -q`.
The existing Python CI suite collects this regression directly.

- `tools/btk_events.py` -- `emit()`, `read()`, `classify_terminal_browser()`;
  stdlib-only, importable by any producer or consumer.
- `tools/pytest_btk_events.py` -- opt-in pytest plugin producer
  (`pytest -p pytest_btk_events --btk-events PATH`).
- `tools/evidence_board.py` -- aggregator/consumer: Markdown table, `--json`,
  `--step-summary`.

The test-only importer and its LICENSE are enrolled in `vendor.lock.json`.
Ordinary candidate CI can update them; the baseline job restores their exact
locked commits. The legacy grouped provenance is projected only after every
locked source/LICENSE hash verifies. Its top-level commit identifies xprobe.py;
the LICENSE commit is independently recorded in the lock. Both records and all
bytes are preserved in the candidate and locked evidence artifacts.
