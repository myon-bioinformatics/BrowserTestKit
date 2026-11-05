# Docker / Bun + Node comparison record

## Status and scope

The comparison completed successfully in
[run 41955055837](https://github.com/myon-bioinformatics/browser-test-kit/actions/runs/41955055837)
on 2026-11-05, implementation `5c2bcca6cefecede8eade54f76a223c7d51d8190`.
The dedicated `nano-docker-compare.yml` manual workflow is now removed at the
user's request. No replacement automatic or scheduled comparison is added, and
no further manual dispatch is requested. Other workflows are unchanged. The
comparison scripts, Dockerfile, offline tests and this evidence record remain.

All six paired samples passed two unique tests with zero skips/retries, and the
four actual output SHA-256 hashes matched across all samples and the seed.
The median test time was 1.769 s (nano) / 1.771 s (Docker), execution-path wall
3.132 s / 2.398 s, and evidence-inclusive total wall 3.292 s / 2.865 s.
Seed preparation took 12.047 s; Docker base pull, runtime build and dependency
build took 4.478 s, 11.296 s and 2.675 s respectively, outside sample timings.
The test time was effectively similar in this run. Different preparation paths
and only three sequential pairs do not establish a general speedup or the pure
cost of containerization; the scope and measurement boundaries below still apply.

The retained artifact is
[nano-docker-41955055837-1](https://github.com/myon-bioinformatics/browser-test-kit/actions/runs/41955055837/artifacts/13382737926),
SHA-256 `826db6642ad31c593c4e6acce15a3e6fd34c7af9b8ca21778cca6120a6c9e319`,
with the original 14-day retention (expires 2026-11-19). Removing the workflow
file does not request deletion of historical runs or artifacts.

Rechecked 2026-11-03: main is #147's merge `cc7322bc2e0a7c8c28f43380f6417c9e00a819f0`.
#146's cache experiment succeeded in runs
[41438138214](https://github.com/myon-bioinformatics/browser-test-kit/actions/runs/41438138214),
[41438154206](https://github.com/myon-bioinformatics/browser-test-kit/actions/runs/41438154206), and
[41438159817](https://github.com/myon-bioinformatics/browser-test-kit/actions/runs/41438159817).
These are cache-experiment runs, not fanout or Docker results. The repository's
workflow-dispatch history (7 runs at inspection) contained no fanout execution.
The latest cache run has retained artifact `nano-bun-41438159817-1` (479,978 ZIP
bytes); its upload size is not dependency capacity. No previous timing values
are imported into this comparison.

Run [41951180221](https://github.com/myon-bioinformatics/browser-test-kit/actions/runs/41951180221)
(2026-11-05, implementation `b96be4f`) passed seed preparation and runtime-image
construction, then failed dependency-image construction because the preparation
helper nests its snapshot under a generated job directory. No paired samples
ran, so this run supplies no Docker parity or performance result. The comparison
now selects exactly one snapshot, validates its pinned receipt/source/lock,
copies its contents directly to the Docker prepared root, and passes that same
snapshot to the nano adapter. Missing, duplicate, malformed and symlinked inputs
remain fail-closed. Offline regression tests and replay of the retained input
artifact verify this layout fix; a new Docker experiment still requires approval.

Run [41953483576](https://github.com/myon-bioinformatics/browser-test-kit/actions/runs/41953483576)
(2026-11-05, implementation `d58caf3`) built the dependency image and verified
matching host/container Node binaries, but stopped before the first sample when
ordinary cache copying dereferenced Bun's 30 absolute cache-local symlinks.
The cache seal correctly rejected that changed layout. Private cache copying now
validates the source seal, rejects external/dangling/looping links, copies regular
files independently, and rewrites validated links to relative targets inside the
copy. It verifies the unchanged seed and the relocated copy seal, which is saved
per trial. Copy timing includes byte copying and link relocation; seal checks
remain in execution-path wall time. Neither failed run provides paired results.

This first independent experiment uses **three paired sequential trials on one
Linux x86_64 host**, in order nano/Docker, Docker/nano, nano/Docker. It does not
implement shared-budget fanout from parent issue #133. No experimental CPU or
memory quotas are requested on either path. Host metadata, visible host cgroup
limits, Docker info/version, image ID and each container's inspect are saved.
Infrastructure-imposed limits may exist. This is not evidence of equal effective
cgroups or unconstrained hardware; constrained throughput requires another
explicit experiment with verified aggregate and worker budgets.

## Fixed contract

- Source is the existing adapter's `111f6d78befae28acfb198a45b72e5aa0e59dcde` snapshot,
  including projected package.json/package-lock.json and their fixed hashes.
  The parent helper and source blobs retain #146's hash checks.
- Real Node **26.0.0**, npm **11.9.0**, Bun **1.4.4** plus its executable SHA-256.
  Docker Node must have the exact same executable SHA-256 as host Node or fail.
- Only the original markdown and HTML tests, `--workers=1`, JSON reporter,
  unchanged assertions. The original validator requires two unique passes,
  zero skips/retries and no runner errors. Four relative JSON output paths and
  SHA-256 hashes must match the seed's npm baseline for **every** sample.
- The seed is #146's existing cold/warm/npm experiment, run once as preparation.
  It must pass before building Docker. A missing receipt or partial plan cannot
  emit medians. Six samples must all pass before `docker-summary.json` is passed.

Docker uses a reviewed digest-pinned `node:26.0.0-bookworm-slim` image, Python3
from its Debian apt repository, and pinned npm. It runs `npm ci --ignore-scripts`
into the same projected snapshot, checks the package identity/dependency graph,
records installed apt package versions, and removes the download cache within
the dependency layer. No browser engine, application server or Python package is
installed. Python and OS libraries are intentionally from the respective host /
image; their versions are recorded, not asserted byte-identical. This compares
two deployment paths, not the isolated causal cost of containerization.

Each Docker sample creates a fresh writable container from that same image,
with `--network=none`, starts its idle process, then execs the test worker.
`start_api_seconds` measures the start API call, not readiness or test latency;
`exec_wall_seconds` includes exec/IPC and worker overhead. This split is explicit:
it is not a single `docker run` timing. Each nano sample makes a real independent
cache copy (including relocated cache-local links) and invokes the existing offline adapter with its usual validation,
lock and quarantine. Bun offline mode is a package-manager policy; it is not a
host network namespace restriction equivalent to Docker's network isolation.

## Measurements and capacity

| Stage / quantity | Saved evidence | Inclusion |
|---|---|---|
| Seed preparation | seed.json, seed/summary.json | Bun download, snapshot, cold/warm/npm install/test; once, outside samples |
| Input/context copy and seal | docker-summary.json | Separate preparation overhead |
| Base image pull | base-pull.json | Separate, local Docker storage may already contain layers |
| Python/npm runtime image build | runtime-build.json and build logs | No-cache runtime target, apt + npm preparation |
| Docker dependency build | dependency-build.json | Reuses just-built runtime layers; unique nonce forces fresh npm install |
| Actual npm ci | dependency-evidence/install.json | Install subprocess time within image build |
| Nano cache copy | sample cache_copy_seconds | Per sample, ordinary copies; no inter-worker hardlinks |
| Nano migration/install | job migration.json / install.json | Per sample, offline |
| Container create/start/exec | create.json / start.json / exec.json | Per Docker sample |
| Test subprocess | test.json | Same two tests; no copy/pull/build time |
| Execution path wall | execution_path_wall_seconds | Nano validation/copy through adapter return; Docker create through exec return |
| Sample total wall | total_wall_seconds | Also includes receipt validation, evidence collection, Docker removal / nano capacity scan |
| Whole experiment wall | experiment_wall_seconds | Seed, image preparation, samples, evidence checks and image cleanup |
| Logical file bytes | seed summary and per-sample capacities | Nano copied cache / node_modules; Docker node_modules and downloaded npm cache before its removal |
| Docker image Size / SizeRw | image-inspect.stdout / inspect.stdout | Uncompressed image size / writable layer size; Docker semantics, not registry transfer bytes |

All command records include argv, return status, wall seconds, stdout and stderr.
The summary retains raw samples and medians of test, execution-path and total
wall. They describe this paired experiment only, without a speed claim from
three trials. Do not add image preparation to one sample while ignoring nano
seed preparation. For a reuse scenario, report both preparation costs plus all
samples for each path, and state the reuse count. No synthetic amortized values
are emitted by this implementation.

Logical bytes, image Size, writable layer bytes, transfer bytes, allocated disk,
RSS and peak host memory are different quantities. Do not subtract unlike
capacity measures or claim memory reduction; RSS/aggregate peaks are unmeasured.
Hardlinked files can share blocks. Cold means private package-manager cache is
empty, not cold OS caches or network. Runtime layers were reused inside the recorded experiment. Do not compare these numbers directly with other hosts or earlier runs.

## Retained tooling and historical execution conditions

The scripts are retained for inspection and offline regression tests. They are
not invoked by a GitHub Actions comparison workflow. The ordinary test suite can
be run without Docker or downloads:

```sh
python3 -m unittest discover -s tests/python -p 'test_nano*.py' -q
```

The recorded experiment used one Linux x86_64 ubuntu-24.04 runner, four visible
CPUs, Node 26.0.0, npm 11.9.0 and the reviewed base image
`node:26.0.0-bookworm-slim@sha256:52d70d606181ef3b3c2137a190196a8c452e0358e6d0971573d0e59135ada9fc`.
It had no GPU/service/browser or fanout, six measured samples plus one three-
condition seed (18 test-case executions total). Network access was used during
source/helper, Bun, npm, base-image and apt preparation. apt package versions
were recorded rather than supplied by an immutable apt snapshot. BuildKit could
retain local build cache until the hosted runner was discarded; no broad Docker
prune was performed. These historical conditions are not authorization for a new
experiment.

## Results and failure recovery

The run/attempt-specific artifact `nano-docker-<run_id>-<attempt>` stores summary,
source/context seals, the small build context, raw logs, runtime/package identity,
install/test timings, inventories, inspect evidence, receipts and test outputs.
Installed node_modules, copied Bun caches and binaries are omitted. The seed's
timings/inventory live in seed/summary.json; its raw receipts/logs also remain in
the local output directory. The host output tree is new per run; refusing reuse
prevents stale artifacts from passing a failed experiment.

Any command, identity, test, output or plan failure leaves status failed and no
comparison medians. Failed Docker tests attempt inspect + output collection
before forced removal. Failed image builds retain build logs even when no
dependency receipt exists. Cancellation/runner loss may prevent final summary
or upload: missing evidence is not success. Containers/images created by this
invocation are removed; the nonce names in command logs enable manual cleanup
after hard termination. Nano's quarantine and failed working directories remain
as evidence. Never pass or report a benchmark using only an artifact-upload
success or a green workflow step without the passed complete summary.

Offline regression coverage exercises digest/version rejection, missing/changed
outputs, skips/missing tests/wrong source, incomplete plans, subprocess failure
and timeout logging, container cleanup even if collection fails, runtime mismatch
and failed-summary persistence. These tests perform no Docker or network work.
