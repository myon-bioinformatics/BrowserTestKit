# Discover files and production entry points by convention

## Built-in Git-aware path selection

```sh
python -S tools/repo_overview.py . --paths-only --match 'tools/*probe*' --match '*.toml' --json
python -S tools/repo_overview.py . --paths-only --match '*.py' --exclude 'tests/*'
```

`repo_overview.select_files(root, patterns=('*.py',), exclude=('tests/*',))`
filters the existing combined tracked/untracked Git inventory. Git ignore rules
and the established dependency/cache/result exclusions remain in force; a plain
directory retains the existing walk fallback. Repeated matches are ORed once,
then exclusions are applied. Rules match relative paths or basenames using
case-sensitive fnmatch semantics (`*` spans `/`).

`--paths-only` prints paths without rendering about/key-file contents. `--json`
returns `files`, `total` and `truncated`; text mode keeps one line per path,
escaping control characters. `--max-files` applies after selection (default
400). Exact-limit/empty output returns 0; truncated output, invalid roots or
inventory errors return 2. In paths-only mode, a Git failure inside a worktree
(including ancestor and linked-worktree `.git` markers) never falls back to a
walk that could expose ignored files. Plain-directory walk/stat errors also
return 2; plain directories still work when Git is unavailable. The inventory's own shared limit is preserved, and
paths-only cannot be mixed with content/stats/churn options. This uses the
repository's installed Git inspector and needs no sibling xprobe checkout.

The first command was run on this checkout: it selected 5 paths (pyproject.toml
and four probe scripts), with no truncation.

Validation: **45 repo_overview pytest tests** passed, including tracked/untracked
Git inventory and ignore rules, default overview behavior, OR/exclusion rules,
exact limits, shared-inventory failures, mixed-mode errors and direct
`python -S` path extraction. Regression checks include a real corrupt Git
index, ancestor/linked-worktree markers, missing Git on a plain directory,
unstaged deletions/dangling links, stat permission failures and injected walk errors. Existing overview fallback behavior
is preserved.

## Optional source-symbol inspection

Find probing, capture and validation entry points across browser engineering scripts.

Use a sibling xprobe checkout with the convention-discovery API from
[xprobe PR #28](https://github.com/myon-bioinformatics/xprobe/pull/28).
For a fresh checkout, select this tested revision:

```sh
git clone https://github.com/myon-bioinformatics/xprobe.git ../xprobe
git -C ../xprobe checkout --detach 045b54dd35baf43758a484e458712ed5ad47ba24
```

The commands below run from this repository root. If xprobe is elsewhere,
replace `../xprobe/xprobe.py` with that checkout's path. They use Python's
standard library and work with `python -S`.

```sh
python -S ../xprobe/xprobe.py --symbols . --include 'tools/*.py' --symbol-pattern '*probe*' --symbol-pattern '*capture*' --symbol-pattern '*validate*' --brief
python -S ../xprobe/xprobe.py --discover --root . --include '*.yml' --include '*.toml' --include-hidden --brief
```

Repeat `--symbol-pattern` or `--include` to inject different naming conventions;
alternatives are ORed. `--symbol-kind class` (also `function`, `async_function`)
filters the selected definitions. For example, replace the name rules with
`--symbol-pattern '*Adapter' --symbol-pattern '*Reader' --symbol-kind class`
to look for class families. There is no required `tests/` path or `test_*` prefix.

Filename patterns match basenames or relative paths; symbol patterns match
short or lexical dotted names. Both are case-sensitive. Wildcard `*` spans
slashes/dots, so a parent-name match also selects nested definitions. Repeated
source definitions remain separate. This is a source inventory, not a count of
callable runtime members or an import/plugin registration mechanism.

JSON is the default for further processing; omit `--brief`. Exit status 0 means
complete matches, 1 means none, and 2 means errors, skipped inputs or truncated
output. Default bounds are 5,000 matched files, 1 MB per source and 1,000 symbols;
set `--max-files`, `--max-bytes`, `--max-matches` when needed. Hidden paths are
opt-in; dependency/build directories are excluded by default. Targets are parsed
without importing them. No `.gitignore` interpretation is implied.

## Observed trial

On 2026-10-21 at `75d736d26f6752ff7bc1436ae414f6578e321f43`, the first command parsed
31 files and found 7 definitions with no errors, skips or
truncation. Examples:

- `tools/btk_events.py`: `validate`
- `tools/gh_identity.py`: `validate_repository_metadata_v1`
- `tools/gh_ops.py`: `_validate_branch_name`
- `tools/gh_ops.py`: `_validate_branch_segment`

The second command selected 7 YAML/TOML files at the same revision.
These are observations for that snapshot, not future expected counts. Full
query options, source SHA-256 values and results are recorded in xprobe's
[`docs/evidence/convention-discovery.json`](https://github.com/myon-bioinformatics/xprobe/blob/045b54dd35baf43758a484e458712ed5ad47ba24/docs/evidence/convention-discovery.json).

This is an explicit developer checkout, separate from existing vendored copies.
Existing vendor locks and CI/deployment configuration are unchanged; promoting
these APIs to an installed consumer requires its normal source-pin review.
