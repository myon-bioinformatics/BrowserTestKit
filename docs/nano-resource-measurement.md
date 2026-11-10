# Resource measurements for the local Bun adapter

PR #159 consolidates #152, #156 and #163. The two dispatch-only workflows,
fanout/experiment/Docker tools and their obsolete guides are removed.
The retained path is `tools/nano_bun.py`: Bun installs the pinned snapshot,
then real Node 26+ executes the unchanged tests. Python 3.11+ is required.
No dependency or new workflow is added. CI cache saves remain main-only.

## Run

Prefix the existing adapter invocation with the isolated standard-library meter:

```sh
python -S tools/nano_resources.py /absolute/path/resources.json -- \
  python -S tools/nano_bun.py \
  --prepared /absolute/prepared --work-root /absolute/jobs \
  --cache-root /absolute/cache --node /absolute/node \
  --bun /absolute/bun --python /absolute/python
```

The adapter stays offline unless its existing `--allow-network` is explicitly
supplied. The meter does not prepare snapshots or install runtimes.
Use a new report path outside inputs. For cgroup v2 observations add
`--cgroup /sys/fs/cgroup/your-scope` **before** the report path.
Only supply trusted commands and scopes. The meter does not enroll processes or
apply limits; a supplied scope may include unrelated jobs.

| Retained quantity | Meaning |
| --- | --- |
| Wall seconds | Entire adapter invocation, including staging, installation and validation |
| CPU seconds | User + system CPU of waited descendants, isolated in the meter interpreter |
| Peak process RSS | Largest waited process high-water mark; Linux KiB converted to bytes, macOS bytes; never simultaneous tree memory |
| cgroup CPU/throttling | Read-only before/after deltas; unavailable or decreased counters are unmeasured |
| Sampled memory peak | memory.current every 50 ms; lower bound including every scope member |
| Limits / OOM counters | Raw cpu.max, memory.max, memory.events before/after |
| Historical memory peak | Raw memory.peak; may predate this command |

Unsupported/missing/malformed/permission-denied readings have null values and
an explicit unmeasured reason. No cgroup is guessed when the option is absent.
Physical host peak and per-worker throttling remain unmeasured.
Budget verification is always false. Higher reset counters cannot be detected;
sampling can miss short peaks and adds uncalibrated overhead.

The command exit code is propagated (signals use 128 + signal). Normal command
failure still writes a report. Launch errors and hard termination can leave no
report; missing reports are never zero usage. The adapter retains its own
per-stage timeout, process-group cleanup and cache quarantine semantics.

## Deliberately retired

Parallel 1/2/4 fanout, validated batch throughput, copy-inclusive batch timing,
automatic seed preparation and Docker comparison are removed together with the
unmaintained experiments. Their aggregation code and tests are not retained:
there is no maintained multi-worker receipt stream to validate. A single-command
meter cannot establish throughput or constrained-scheduling improvements.
Historical benchmark artifacts are unchanged. No new performance claim is made.

The reusable meter retains CPU/RSS/cgroup instrumentation from #152 while
avoiding revival of any deleted experiment. Offline tests cover real command
success/failure, adapter help, malformed/absent cgroups, counter reset,
historical versus sampled peaks and unsupported RSS/resource platforms.
