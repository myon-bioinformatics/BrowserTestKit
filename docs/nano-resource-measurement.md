# Parallel nano resource telemetry (parent #133)

## Scope and reuse

This is an offline instrumentation step, not a constrained-throughput result.
The existing fanout queue, ten identical jobs, rotated concurrency 1/2/4 across
three trials, receipts, isolated logs and timeout cleanup remain in use.
Docker comparison's `copy_private_cache` is reused (including seal validation and
cache-local link relocation). Each worker's relocated seal is retained. The
prepared helper's single nested snapshot is selected and validated before use,
as in the Docker comparison. No Docker run, workflow or runtime dependency is added.

An existing validated seed can be used without download/preparation:

```sh
python -S tools/nano_bun_fanout.py --output /new/experiment \
  --seed /existing/seed --measure-resources --cgroup /sys/fs/cgroup/experiment
```

Without `--seed`, the original network-using seed preparation still runs.
Without `--measure-resources`, the original command path is retained. A cgroup
path is explicit: automatic namespace/mount mapping is deliberately not guessed.
Only trusted prepared snapshots, commands and cgroup paths should be supplied.

## Measurement contract

| Quantity | Method and scope |
| --- | --- |
| Worker wall time | Separate `python -S` meter around the adapter; includes adapter preparation/test/validation, excludes private-cache copy and meter startup |
| Worker CPU seconds | Isolated meter's `RUSAGE_CHILDREN` user + system CPU for waited command descendants; avoids process-wide counter races between orchestration threads |
| Worker maximum process RSS | `ru_maxrss`: Linux KiB converted to bytes, macOS bytes. Largest individual waited process high-water mark, **not simultaneous worker-tree RSS**; detached/unwaited children are excluded |
| Worker throttling | Explicitly unmeasured: workers do not have dedicated enrolled cgroups |
| Aggregate CPU and throttling | Before/after deltas from supplied cgroup's `cpu.stat`, including every member/descendant; decreases are unmeasured, never negative deltas |
| Aggregate memory | `memory.current` sampled every 50 ms from before copying until batch completion; sampled peak is a lower bound, not sum of per-worker RSS |
| Kernel memory peak | Raw `memory.peak` before/after retained; lifetime high-water mark may predate this batch, so never relabelled as batch peak |
| Physical host peak memory | Explicitly unmeasured; cgroup memory includes charges such as cache and is not whole physical-host RSS |
| Limits / OOM evidence | Raw `cpu.max`, `memory.max`, `memory.events` before/after; no writes, resets or cap enforcement |
| Throughput | Validated successful workers/tests per execution second and workers per total batch second (copy included); same repeated corpus for each concurrency |

Unreadable, missing, malformed and unsupported values have `status: unmeasured`,
`value: null` and a reason. Zero is a measured value. A successful sample or
finite visible cap does not establish isolation, membership or an enforced shared
experimental budget. `budget_verified` and `comparison_eligible` remain false.
Unrelated members can contaminate aggregate readings. A reset/replacement that
raises counters cannot be detected by simple deltas. The sampler can miss short
peaks. Meter/sampler overhead is included in batch wall and is not calibrated.

A failed/timeout batch retains its existing failure report and aggregate scope
telemetry but emits no successful throughput. Hard termination can prevent
worker telemetry files from being written; absent files are not zero usage.
The pre-existing timeout mechanism remains responsible for adapter cleanup.

## Validation and decision (2026-11-05)

This development environment exposes cgroup v2 but its mount is read-only.
Visible `cpu.max` was `800000 100000` and `memory.max` was `8589934592`.
These are infrastructure settings, not experiment-applied worker or aggregate
budgets. No Bun/Docker constrained benchmark was run and no performance figures
are claimed. No seed or dependency downloads are needed for the regression tests.

`python -S -m unittest discover -s tests/python -p 'test_nano*.py' -q`:
74 tests passed, including unavailable/malformed/permission-denied cgroups,
unlimited limits, counter reset, sampled versus historical peak, real success
and failure child commands, unmeasured RSS on unknown platforms, timeout/log
retention, failed-batch persistence, and mocked fanout aggregation at 1/2/4.
The 1/2/4 integration fixtures are mocked workers, not throughput measurements.
The broader browserless suite finished with 1151 passed, 2 failed, 5 skipped,
109 deselected and 41 subtests passed. Both failures are existing Node entrypoint
tests rejected by their Node 26+ guard because this environment has Node 24.22.1;
the changed files do not alter those entrypoints. The stdlib boundary suite ran
5 tests successfully with 1 skip. Existing CI uses Node 26.

The small read-only meter is worth reusing experimentally across repositories,
but shared-budget scheduling is **not ready for rollout**. The next reference
runner needs delegated, dedicated aggregate and worker cgroups, verified process
membership, fixed identical corpus, immutable source IDs, repeated baseline and
constrained runs, OOM/timeout evidence, and budget accounting for the orchestrator,
cache and descendants. Only those results can determine useful worker counts
and stable caps. Affinity alone is not a CPU quota. No workflow is registered or
changed by this work; the existing CI discovers the new offline test module.
