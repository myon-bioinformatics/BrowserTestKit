# Formal checks for evidence reduction

[`formal/EvidenceReduction.lean`](../formal/EvidenceReduction.lean) models the
status reduction in [`tools/evidence_board.py`](../tools/evidence_board.py).
It checks how one execution's pytest phases contribute to the board's status
counts. It does not prove that a browser ran correctly, that screenshot pixels
match expectations, or that the producer's observations are true.

## Model and implementation boundary

The model receives a snapshot with at most one setup, call and teardown report
for one test execution. The fallback order is setup before teardown. The
production pytest plugin emits only setup skip/error, call passed/failed/skipped,
and teardown error; the bridge also tests the full accepted status vocabulary.

| Python behavior | Lean representation |
| --- | --- |
| The seven statuses in `btk_events.STATUSES` | `BrowserTestKit.Status` |
| Missing setup/teardown status | `Phases.setup`/`teardown = none` |
| No call event | `Phases.call = none` |
| Call event present without a status | `Phases.call = some none` |
| Call failure wins over setup/teardown error | First branch of `reducePhases` |
| Otherwise setup/teardown error wins | Second branch of `reducePhases` |
| Otherwise call status, then first other observed status | Remaining branches of `reducePhases` |
| One grouped test contributes at most one status | `Evidence.test` and its optional `contribution` |
| Event without a grouped test identity contributes directly | `Evidence.direct` |
| Session rollups are excluded from per-test status counts | `Evidence.summary` contributes no status |

The model starts after events have been grouped. JSON parsing, provenance,
identity grouping across files/runs, message contents, and the overall exit-code
policy are outside the proof. Arbitrary producers may emit repeated call events
or other phase names; the single-execution snapshot does not claim to model
every such stream. In particular, a missing status is not a passing status, but
this alone does not establish a failing board exit code.

## Named invariants

| Theorem | Property |
| --- | --- |
| `BrowserTestKit.call_failure_priority` | A failed call remains failed even when cleanup also errors |
| `BrowserTestKit.phase_error_not_passed` | Setup or teardown error cannot reduce to passed |
| `BrowserTestKit.passed_requires_passed_phase` | A passed result requires an observed passed phase |
| `BrowserTestKit.missing_status_stays_missing` | Absent/empty phase reports do not create a status |
| `BrowserTestKit.session_summary_count_neutral` | A session summary inserted anywhere adds no per-test status count |
| `BrowserTestKit.failed_call_counted_once` | Failed call plus teardown error adds one failed test and no second error count |

The count theorem concerns counts only. A session's blocking status can still be
relevant to a CI gate without adding a second failing test to the count table.

## Connection tests

[`tests/stdlib/test_formal_evidence_reduction.py`](../tests/stdlib/test_formal_evidence_reduction.py)
calls the real `_reduce_test_result`, JSONL reader, `aggregate`, and CLI entry
function. It runs with the standard library and synthetic local data:

```sh
python -S -m unittest discover -s tests/stdlib -v
```

The phase test covers **576 combinations**: 8 setup states, 9 call states
(including both an absent report and a report without status), and 8 teardown
states. Each combination is reduced directly and read through an actual JSONL
file. An independent priority ranking supplies the expected result; each test
identity contributes zero or one status count.

Other cases check retention of the teardown error message, summary insertion at
every position, phase reordering for the pytest producer's profile, and allowed
blocking statuses retaining their original names. Invalid status strings are
counted as malformed by the real reader. Empty or wholly invalid input returns
CLI code 2. These are executable regression checks, not additional Lean proofs.

## Run Lean

[`formal/lean-toolchain`](../formal/lean-toolchain) fixes Lean 4.36.0. The proof
uses Lean's standard library, without Mathlib. Normal Python execution does not
require Lean. From the repository root, use the parent's shared
[`lean_verify.py`](https://github.com/myon-bioinformatics/myon-bioinformatics/blob/27c46411c0a07d9b4d0f1ad5751ed1bb9e869b5c/lean_verify.py):

```sh
python -S /path/to/myon-bioinformatics/lean_verify.py \
  --cwd formal --required \
  --theorem BrowserTestKit.call_failure_priority \
  --theorem BrowserTestKit.phase_error_not_passed \
  --theorem BrowserTestKit.passed_requires_passed_phase \
  --theorem BrowserTestKit.missing_status_stays_missing \
  --theorem BrowserTestKit.session_summary_count_neutral \
  --theorem BrowserTestKit.failed_call_counted_once \
  --json-output build/btk-lean-verification.json \
  EvidenceReduction.lean
```

Passing all theorem names enables the shared axiom audit. The dedicated CI lane
uses `--required`, so a missing toolchain, failed proof or failed audit fails the
lane. Optional local execution without this flag still reports a missing Lean
toolchain as `unverified`, distinct from a successful proof.

## Execution identity and gate integration

The runtime groups test phases by `(source, project, run_id, test_id)`. Missing
optional project/run fields remain `None`, preserving legacy grouping within
one producer. Separate executions need distinct identities, and every phase of
one execution must use consistent identity fields. A filename is not an
identity: one execution may span several input files. Repeated attempts that
reuse all four identity fields cannot be distinguished by the current schema.

Session summaries remain absent from per-test status counts, while their
blocking statuses participate in the gate together with the test statuses.
`--allow` applies to both. The producer supplies the validated `status`; the
board retains `returncode` as metadata without independently reclassifying it.

[`tests/stdlib/test_evidence_execution_identity.py`](../tests/stdlib/test_evidence_execution_identity.py)
checks separation of source/project/run identities, legacy records, split files,
failure detail retention, and summary gating without double counting.
[`tests/python/test_evidence_board_sessions.py`](../tests/python/test_evidence_board_sessions.py)
runs a real empty pytest session: pytest exits 5, the plugin emits a failed
summary, and the real board CLI exits 1 while its per-test counts stay empty.
These integration regressions do not extend the scope of the Lean proof.
