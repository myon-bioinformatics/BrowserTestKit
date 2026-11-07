# Public vendor placement in CI

Push and PR CI verify `vendor.lock.json` with the pinned shared `vendor_sync.py`
tool. The primary Python job runs against the checked-in vendored bytes after
`check`. A separate `vendor-locked` job removes the allowlisted files, materializes
their exact upstream commits, verifies again, and runs the same Python suite on
that recreated snapshot. Existing documentation-only change detection is retained.
No dedicated token, scheduled update PR, or main writeback is required. Source
acquisition uses anonymous HTTP/public Git in the pinned shared stdlib tool.
Checkout credentials are not persisted.

`vendor.lock.json` is the only provenance record for vendored files (schema
`vendor-lock/1`). Each entry records repository, full 40-hex `ref` and matching
`commit`, source path, destination, Git blob SHA-1, and SHA-256 of the checked-in
bytes. Tests assert those identities directly; there are no per-file
`*.provenance.json` sidecars or local projection scripts.

ALM agents can use the same mechanism in a disposable checkout:

```bash
git clone https://github.com/myon-bioinformatics/myon-bioinformatics.git .vendor-sync-tools
git -C .vendor-sync-tools checkout --detach 1eda992aa6fa31a902e2c06428e53ab17e8fe3c1
python -S .vendor-sync-tools/vendor_sync.py check --manifest vendor.lock.json
python -S .vendor-sync-tools/vendor_sync.py materialize --manifest vendor.lock.json
python -S .vendor-sync-tools/vendor_sync.py check --manifest vendor.lock.json
```

Run the existing Python suite with its test-only dependencies after verification.
The shared tool rejects edited baseline copies before contacting upstream.
Cross-repository rollout: myon-bioinformatics/myon-bioinformatics#35. Existing
JUnit work remains tracked separately in myon-bioinformatics/myon-bioinformatics#22.

## What a green run covers

The primary Python job tests the checked-in lock. `vendor-locked` exercises the
materialize path on one representative Python version on every selected push/PR
run. Locked evidence uses a `locked-` artifact prefix and is retained on failure.

Pages/Docker continue shipping checked-in bytes; no source is written back to
main. Green `vendor-locked` covers the materialize baseline on its one Python
version, not the whole primary matrix.

The pinned shared tool uses anonymous public Git fallback on HTTP 403/429 for
locked `materialize`. Locked placement preserves each entry's exact commit and
verifies Git blob/SHA-256 before writing; other errors remain nonzero.

## Canonical GitHub operations and evidence staging

The parent owns `gh_ops.py` and its generic regression tests. `vendor.lock.json`
records its exact source commit and verified bytes at `scripts/gh_ops.py`;
GHI remains adjacent for installed-module compatibility. Consumer tests cover
that wiring, while PR #48/#49 parity cases are maintained by the parent suite.

Vendor artifact uploads use the pinned parent `vendor_stage.py` with `--kind
locked`. Source members come from the lock only. The staged `vendor-evidence.json`
records member digests.
