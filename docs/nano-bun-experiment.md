# Manual Bun cache comparison

The independent `Nano Bun cache experiment` workflow is dispatch-only. It does
not change normal CI or test/runtime selection. It depends on PR #145's
`tools/nano_bun.py`; merge #145 first, then retarget this PR to main.

Run from Actions → Nano Bun cache experiment → Run workflow, on main after merge.
The artifact is named with run ID and attempt. `summary.json.status` must be
`passed`; a green upload step alone is not a successful experiment.

Every dispatch starts with an absent private Bun cache. It measures one online
cold install, then an offline warm install into a fresh job, then an npm baseline
with an empty private npm cache. There is no cross-run Actions cache or restore
fallback. The cache is sealed with path/content SHA-256 after cold execution and
checked immediately before warm execution. Adapter locking/quarantine remains
active; changed locks, corrupt cache content, timeouts, failed commands, retries,
skips, missing outputs and unequal output hashes cannot produce a passing result.
The cache is trusted same-job storage, not an adversarial storage sandbox.

Node is fixed to 26.0.0 for all three conditions,
npm to explicitly installed 11.9.0, Bun to 1.4.4 plus the adapter's executable SHA-256.
Node is used explicitly for npm and all JavaScript tests. This experimental pin
also follows the Node 26 CI policy. Parent preparation is fixed to
3128b4019b9949a6ccc80c686cd84c93f04af119 and verified before execution; all child
sources and projected inputs retain #145's hashes.

Bun download/extraction/hash/version-check time and archive/executable bytes are
separate from snapshot preparation, lock migration, install and test seconds.
Per-condition cache and node_modules capacity means summed logical file bytes,
not allocated disk blocks, peak disk use or RSS; hardlinks may share blocks.
Cold means package-manager cache empty, not OS page cache or network cold.
Node setup and baseline npm preparation precede the experiment and are excluded
from install comparisons; Bun preparation is recorded explicitly. Single sequential samples are reproducibility evidence, not a speed claim.

The original two browserless cases must each pass once without skips/retries.
Four relative output paths and exact SHA-256 hashes must agree across all three
conditions. Playwright report timing/path metadata is retained but is not itself
byte-identical. Inputs, runtime identity, commands, return codes, timings, package
inventories, output hashes, logs, reports and failure diagnostics are uploaded
with `always()`. Cancellation/runner loss may prevent complete artifact upload;
a missing summary or artifact is never evidence of success. Packages and Bun
binaries are omitted from the artifact; source pins and hashes enable retrieval.

Offline guards are covered by `test_nano_bun.py` and
`test_nano_bun_experiment.py`; normal PR CI runs these without package downloads
from this experiment. The real dispatch remains an explicit separate operation.
