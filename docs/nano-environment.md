# Opt-in Markdown/HTML document-mirror environment

Keep the actual independent **markdown-it and marked** readers, linkedom's HTML
reader, the stdlib comparator and the existing Playwright **test runner**, while
omitting unrelated ExcelJS/XLSX dependencies. The selected tests do not request a
browser fixture or need a browser engine, Docker, model or external API. This is
not the whole browser-test-kit suite. Existing sources, dependencies, tests and
workflow definitions remain unchanged.

The common helper is pinned to the [merged parent source](https://github.com/myon-bioinformatics/myon-bioinformatics/blob/3128b4019b9949a6ccc80c686cd84c93f04af119/tools/nano_node.py).
The exact child snapshot is
[`111f6d78befae28acfb198a45b72e5aa0e59dcde`](https://github.com/myon-bioinformatics/browser-test-kit/tree/111f6d78befae28acfb198a45b72e5aa0e59dcde).
Its 221 non-root lock records project to **30**, including the existing test
runner; this is not the smaller 27-record reader-only analysis. All 30 complete
records and the uuid/minimatch overrides remain unchanged. Only linkedom's
reviewed optional canvas peer declaration is allowed; canvas is not installed.
Changed snapshots fail closed rather than silently resolving new versions.

## 1. Verify and prepare outside the checkout (no installation)

Use an existing Python 3.12+, Node 26+ and npm 11.9.0. Nothing here installs or
upgrades those tools. Run the shell blocks in order in the same POSIX shell.
Choose a TMPDIR outside the checkout. Source download needs GitHub access; lock-only preparation is offline. The
helper is downloaded at the merged parent commit, SHA-256 checked **before
execution**, and then all 9 required child files are downloaded at their immutable
commit and checked against its Git-blob allowlist. No clone, vendor enrollment,
branch switch or write inside this checkout is required.

```sh
set -eu
WORK=$(mktemp -d "${TMPDIR:-/tmp}/nano-browser-test-kit-XXXXXXXX")
WORK=$(python3 -I -B -S -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve())' "$WORK")
python3 -I -B -S - "$WORK" <<'PY'
import hashlib, importlib.util, sys, urllib.request
from pathlib import Path
work = Path(sys.argv[1])
parent = '3128b4019b9949a6ccc80c686cd84c93f04af119'
child = '111f6d78befae28acfb198a45b72e5aa0e59dcde'
def fetch(url):
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024:
        raise ValueError('download exceeds the bounded input size')
    return data
data = fetch(f'https://raw.githubusercontent.com/myon-bioinformatics/myon-bioinformatics/{parent}/tools/nano_node.py')
if hashlib.sha256(data).hexdigest() != '9e042eb70a80c75f99aafb1956386b3f5e27ee795ec501d3bcece3a369d09777':
    raise ValueError('parent helper SHA-256 mismatch')
tool = work / 'nano_node.py'
tool.write_bytes(data)
spec = importlib.util.spec_from_file_location('nano_node', tool)
nano = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nano)
source = work / 'source'
for name, expected in nano.BTK_SOURCE_BLOBS.items():
    data = fetch(f'https://raw.githubusercontent.com/myon-bioinformatics/browser-test-kit/{child}/{name}')
    if nano.git_blob(data) != expected:
        raise ValueError(f'child Git-blob mismatch: {name}')
    path = source / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
nano.read_snapshot(source, blobs=nano.BTK_SOURCE_BLOBS)
PY
python3 -I -B -S "$WORK/nano_node.py" --feature browser-test-kit-document-mirrors \
  --source "$WORK/source" --output-dir "$WORK/prepared" > "$WORK/prepared-path.txt"
PREPARED=$(cat "$WORK/prepared-path.txt")
PROJECT="$PREPARED/browser-test-kit"
```

Every required source, fixture and license file is staged; the helper records
source identities and projected manifest/lock hashes in `$PREPARED/receipt.json`.
Its source check concerns exact file bytes, not checkout HEAD or ancestry.
Official npm uses `--package-lock-only --offline --ignore-scripts` with empty
configuration and a private cache. Unknown graphs or changed retained records
fail closed. If download or preparation fails, stop and inspect the error;
do not bypass a hash check, weaken the guard, or use an unpinned fallback.

## 2. Optional explicit installation

This separate step downloads the locked packages from the official npm registry.
It is not run by preparation or by importing a module. Review it before running.
Fresh HOME/config/tmp/cache and disabled lifecycle scripts avoid using a broad
developer environment; downloads still require network access. First recheck the
helper and prepared manifest/lock digests to catch accidental intervening edits.

```sh
python3 -I -B -S - "$WORK" "$PREPARED" <<'PY'
import hashlib, importlib.util, json, sys
from pathlib import Path
work, prepared = map(Path, sys.argv[1:])
if hashlib.sha256((work / 'nano_node.py').read_bytes()).hexdigest() != '9e042eb70a80c75f99aafb1956386b3f5e27ee795ec501d3bcece3a369d09777':
    raise ValueError('helper changed after preparation')
spec = importlib.util.spec_from_file_location('nano_node', work / 'nano_node.py')
nano = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nano)
snapshot = nano.read_snapshot(work / 'source', blobs=nano.BTK_SOURCE_BLOBS)
original = json.loads(snapshot['package-lock.json'])
manifest = json.loads(snapshot['package.json'])
nano.validate_original(manifest, original, feature='browser-test-kit-document-mirrors')
project = prepared / 'browser-test-kit'
nano.validate_projection(original, json.loads((project / 'package-lock.json').read_bytes()),
                         feature='browser-test-kit-document-mirrors')
manifest['devDependencies'] = nano.BTK_SELECTED
if json.loads((project / 'package.json').read_bytes()) != manifest:
    raise ValueError('projected manifest differs from the fixed contract')
for name, data in snapshot.items():
    if name not in ('package.json', 'package-lock.json') and (project / name).read_bytes() != data:
        raise ValueError(f'prepared source changed: {name}')
receipt = json.loads((prepared / 'receipt.json').read_bytes())
if receipt['feature'] != 'browser-test-kit-document-mirrors' or receipt['source_commit'] != '111f6d78befae28acfb198a45b72e5aa0e59dcde':
    raise ValueError('wrong feature receipt')
for name, field in [('package.json', 'projected_manifest_sha256'),
                    ('package-lock.json', 'projected_lock_sha256')]:
    if hashlib.sha256((prepared / 'browser-test-kit' / name).read_bytes()).hexdigest() != receipt[field]:
        raise ValueError(f'prepared input changed: {name}')
PY
mkdir -p "$WORK/install-home" "$WORK/install-config" "$WORK/install-tmp" "$WORK/install-cache"
: > "$WORK/user.npmrc"
: > "$WORK/global.npmrc"
(cd "$PROJECT" && env -i PATH="$PATH" HOME="$WORK/install-home" \
  XDG_CONFIG_HOME="$WORK/install-config" TMPDIR="$WORK/install-tmp" \
  PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
  npm ci --ignore-scripts --no-audit --no-fund --no-update-notifier \
  --registry https://registry.npmjs.org/ --fetch-retries=0 --fetch-timeout=30000 \
  --userconfig "$WORK/user.npmrc" --globalconfig "$WORK/global.npmrc" \
  --cache "$WORK/install-cache")
```

Stop on installation failure; keep the partial work directory for diagnosis.
This does not install Docker, browsers, Office, Python test tools or model data.
Do not run `npm install` or add packages to this projected environment to make
unrelated tests pass; use the unchanged full environment for those tests.

## 3. Run both readers and the two original browserless cases

The unchanged test file invokes `python` for its stdlib comparator, so an existing
Python 3.9+ must also be available under that command name. `python3` above was
used for the parent helper. Do not let either command resolve to an unrelated
environment. No pytest package is needed for these two Node-runner cases.

```sh
JOB=$(mktemp -d "$WORK/job-XXXXXXXX")
mkdir -p "$JOB/home" "$JOB/tmp" "$JOB/cache" "$JOB/browsers"
(cd "$PROJECT" && env -i PATH="$PATH" HOME="$JOB/home" TMPDIR="$JOB/tmp" \
  XDG_CACHE_HOME="$JOB/cache" PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
  PLAYWRIGHT_BROWSERS_PATH="$JOB/browsers" \
  node "$PROJECT/node_modules/@playwright/test/cli.js" test \
  --config "$PROJECT/playwright.browserless.config.ts" \
  tests/browserless/mirror.spec.ts \
  --grep '(markdown-it and marked agree on the CommonMark fixture|HTML reader emits complete facts accepted by compare)$' \
  --workers=1 --reporter=line --output "$JOB/test-results" \
  > "$JOB/test.log" 2>&1)
cat "$JOB/test.log"
```

Expected scope: **two unique original tests**, both passing with zero skips.
The first checks both Markdown parsers and full fixture expectations; the second
checks HTML facts against explicit expected values. Review the log; a smaller
count or skip is not success. The installed absolute CLI avoids `npx` auto-fetch.
The line reporter deliberately replaces the shared default JUnit destination;
each invocation has its own log, outputs, HOME/cache/temp and empty browser path.
Use a fresh `JOB` per parallel invocation. Share dependencies read-only and do
not run npm while workers are active. The unchanged XLSX case is intentionally
not selected and remains in the full environment, not removed or called passing.

## Evidence, limits and normal CI

### Optional cached Bun installation, still executing with Node

`tools/nano_bun.py` is a local, opt-in adapter for **this exact prepared snapshot**.
It does not replace the parent preparation helper, the full environment, or any
CI job. Bun materializes `node_modules` from its package cache; Node does **not**
read Bun's cache directly. The JavaScript algorithms, readers and two test cases
still execute under the explicit Node binary. Installer improvements therefore
do not imply faster algorithm execution under Node or a Bun runtime migration.

The reviewed adapter currently supports Linux x86_64 and the official
`bun-linux-x64` **1.4.4** executable (its bytes are verified), existing Node 24+
and a Python 3.9+ comparator. Node 24.22.1 was exercised locally; other Node
versions are separated by cache key, not claimed to have been benchmarked.
The adapter downloads or upgrades **no runtime**. Use the preparation from
section 1 above and already available, explicitly selected executables:

```sh
# BUN, NODE and PYTHON are absolute paths to existing executables.
# PREPARED is the verified parent-helper output from section 1.
python3 -I -B -S tools/nano_bun.py \
  --prepared "$PREPARED" --work-root "$WORK/bun-jobs" \
  --cache-root "$HOME/.cache/btk-nano-bun" \
  --bun "$BUN" --node "$NODE" --python "$PYTHON"
```

The default is **offline**, with no fallback. For an explicitly authorized first
cache population, add `--allow-network`; only the fixed package installation is
enabled, not browser downloads or new package versions. Both modes use the
frozen reviewed Bun lock and disable all lifecycle scripts. Existing CA-file
settings are retained, but npm/Node options and arbitrary configuration are not
inherited. TLS and package-integrity verification are never disabled.

The cache key includes OS/architecture, Bun version and executable hash, both
lock identities, Node version, executable hash and module ABI. A new key cannot
silently consume another runtime's cache. The migration has **32 lock records
and up to 32 installed copies for the same 30 unique package identities**: Bun
duplicates the already locked `domelementtype@3.0.0` at two additional paths.
The exact migrated lock hash, package identities and dependency-resolution edges
are checked; no generic dependency deduplication or version adjustment is made.

Each invocation creates a mode-0700 job outside the checkout/prepared inputs,
with private `node_modules`, HOME/config/temp, browser path, logs and test outputs.
Only the package cache is shared. It must be mode 0700, owned by the same trusted
user, and must not be a symlink. Do not share it with untrusted users or jobs,
edit package files through hardlinks, or mutate/clean it with other tools.
This is a trusted-user cache, **not** a sandbox that authenticates arbitrary
tampered cache contents. New downloads retain Bun's integrity checks.

An exclusive nonblocking lock is held through **installation and tests**, since
Bun can hardlink cache files into a job. Concurrent users of the same key get a
clear `cache busy` error; use separate cache roots for parallel jobs. Timeouts,
SIGINT and SIGTERM stop/reap the child process group. Any failed/interrupted job
leaves an `active.json` quarantine marker; subsequent reuse fails closed. After
SIGKILL or a machine failure, inspect surviving processes and the cache before
any manual recovery, or use a fresh cache root. No automatic marker removal,
cache deletion, npm fallback or unrestricted re-resolution is attempted.

The printed job directory retains command timings, raw logs, runtime/key details,
four document-output hashes, and a receipt requiring **two passes, zero skips**.
A cache miss, changed source/lock, wrong runtime, missing dependency or runner
error is failure, not a partial success. The XLSX case and all browser/platform
tests remain unchanged in the full environment; this command does not select them.

Offline helper regression tests require only the Python standard library:

```sh
python3 -B -m unittest discover -s tests/python -p test_nano_bun.py -v
```

The [bounded results](https://github.com/myon-bioinformatics/myon-bioinformatics/blob/3128b4019b9949a6ccc80c686cd84c93f04af119/docs/nano-client-mirror-environments.md) retain
all six installation samples, 12 two-job batches and complete fact checks,
including Unicode and intentional dialect disagreement. Two unique original
tests repeated 24 times were 48 executions, not 48 unique tests. A separate
54-test Python comparator check used an existing test-tool environment outside
both Node byte/time figures. The [historical reproducer](https://github.com/myon-bioinformatics/myon-bioinformatics/blob/3128b4019b9949a6ccc80c686cd84c93f04af119/docs/evidence/nano-features/2026-11-01-browser-test-kit/REPRODUCE.md)
has its own exact helper/test hash manifest; use that matching historical helper
when reproducing its measurements, not a later file with the same name.

These observations do not prove general installation/runtime speedups, optimal
worker counts, Docker-image savings or build-time reduction. Browser, headed,
platform and XLSX suites remain unchanged in their full environments. This
documentation adds no CI job, vendor registration or automatic benchmark.
Existing PR/main CI still runs Python-support jobs and change detection for
docs-only changes. Browser/product lanes normally skip on a reliable docs-only
diff, but the existing detector fails open on an unreliable diff; Draft status
does not suppress Actions. No workflow, permission or Pages setting is changed.
