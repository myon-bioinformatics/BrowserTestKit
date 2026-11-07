# browser-test-kit

<img src="docs/assets/brand/validation-buddy.png" alt="Validation Buddy: browser, desktop, and mobile inputs leading to screenshot validation" width="191" height="212">

Cross-browser testing reference kit for Playwright Node/Python, screenshots, mobile web, and GitHub Actions. Stagehand and Replay lanes are planned but not yet implemented.

[Artwork and icon usage](docs/assets/brand/README.md) describes the supplied visual marks and their separate roles.

## Scope

The kit's center is browser testing: Playwright lanes, evidence acquisition and validation, execution strategy, and reproducible diagnostics. It also hosts stdlib-only engineering utilities that the same agents and CI jobs use around that work, such as repository orientation, GitHub/CI operations, log digests, Python-version matrices, HTML fixture building, and explicit-file commits. Stdlib utilities must run under `python -S`, stay bounded in their output, and have unit tests. A utility that grows its own dependencies or domain belongs in its own repository.

## Workflow concept

<a href="docs/assets/repository-concept.png"><img src="docs/assets/repository-concept.png" alt="Browser Test Kit concept: local fixtures and a platform matrix feed Node/Python Playwright checks, evidence events, and CI/debugging lanes." width="498"></a>

This supplied panel is an orientation aid, not a complete execution diagram.
[`evidence_board.py`](tools/evidence_board.py) summarizes event logs rather than validating screenshot
pixels. Screenshot validation is a separate check, and Stagehand/Replay remain
optional lanes. [Crop provenance](docs/assets/repository-concept.json).

### Challenges, roles, and outcomes

<a href="docs/assets/repository-concept-v2.png"><img src="docs/assets/repository-concept-v2.png" alt="Browser Test Kit challenges, roles, and outcomes: compare repeated browser checks, divide test execution, parity checking, and evidence collection, and reuse CI templates and test examples." width="507"></a>

This additional supplied panel complements the workflow view above; it does not
replace it. The outcomes describe the kit's intended uses, not a guarantee
that every browser or environment has been tested.
[Additional panel provenance](docs/assets/repository-concept-v2.json).

## Python support

- Stdlib-only modules and the base package: **Python 3.9+**, with no runtime dependencies.
  Run a source script with `python -S tools/check_png.py --help`, or install the
  base package with `uv venv && uv pip install .` (an active virtual environment is required) and use `python -m btk --help`. Tools under `tools/` are
  repository tooling; original `scripts/` entries and installed modules remain supported.
- Image diff (`scripts/image_diff.py` / `python -m image_diff`): **Python 3.11+**, using the vendored stdlib-only `pillow_min.py` backend. Python 3.9/3.10 exit 2 before decoding images. See [image comparison](docs/image-diff.md).
- Development, tests, browser integrations, and this repository's **uv project
  environment/lockfile: Python 3.11+**. Use
  `uv run --locked --python 3.11 pytest tests/python -q -m "not browser"`. Add `--group browser` for browser
  integration dependencies. The requirements-test files have the same floor.
  Python 3.9/3.10 consumers should install the base package with `uv pip` (or pip), not `uv sync`.

The Python support CI checks actual 3.9/3.10 stdlib execution and base wheel
installation without test dependencies, and the complete browserless test suite at the 3.11 floor. Existing browser CI
continues on 3.13. `py_matrix.py` can still probe older interpreters for independent
stdlib consumers; that does not extend this repository's development support.

## Fast Init

Implementation is staged by evidence layer. Deterministic Node/Python Playwright lanes are already implemented; optional agent/exploratory lanes remain separate and must not be described as proven until their own contracts are measured.

Initial implementation target (implemented status is listed under Sequence status below):
- Playwright Node/TypeScript and Playwright Python API/CLI as equal reference lanes.
- Chromium, Firefox, and WebKit, with explicit desktop/mobile profiles.
- Screenshot evidence validated by command result, file existence/size, PNG signature, and human-inspectable CI artifacts.
- Stagehand v4 Python as an optional agent-oriented lane, not a replacement for deterministic Playwright.
- terminal-browser as an optional real-Chromium CLI/TUI exploratory lane for fast local/agent feedback before the deterministic matrix.
- Replay as an optional recorded-execution/debug lane.
- Core CI uses a local deterministic fixture; external network failures remain a separate failure layer.

### Anti-patterns to preserve

The implementation must guard against lessons already observed in sibling repositories:

- **EXIT_ZERO_ONLY / NONEMPTY_IMAGE_ONLY:** exit 0 or `test -s` alone does not prove a valid screenshot; validate PNG magic bytes and useful metadata.
- **BROWSER_MATRIX_DRIFT:** keep configured Chromium/Firefox/WebKit projects in parity with CI/Docker browser installation.
- **ONE_ENGINE_ASSUMPTION:** Chromium green is not cross-browser green.
- **SAFARI_EQUALS_WEBKIT:** call Playwright's engine WebKit, not Safari.
- **MOBILE_VIEWPORT_ONLY:** distinguish a narrow viewport from a named device profile.
- **STALE_ELEMENT_HANDLE_WAIT:** re-query locators when frameworks replace nodes.
- **SILENT_CLEANUP_EXCEPTION:** do not discard the diagnostics needed to understand the original failure.
- **CLI_ARG_GREEDINESS:** unit-test exact wrapper argv, especially Playwright `--project=...` handling.
- **BROWSER_BINARY_MISMATCH:** keep package and installed browser revisions aligned.
- **EXTERNAL_SITE_AS_CORE_FIXTURE:** deterministic core tests must not depend on third-party availability/403/429 behavior.
- **FAILURE_LAYER_FLATTENING:** report whether failure occurred at install, launch, navigate, interact, assert, screenshot, artifact, or cleanup.
- **VISUAL_DIFF_TOO_EARLY:** validate deterministic screenshot candidates before making pixel baselines blocking.
- **NODE_OR_PYTHON_MONOCULTURE:** show equivalent Node and Python patterns rather than selecting one as canonical for every repository.
- **AGENT_REPLACES_DETERMINISTIC_TEST:** Stagehand remains an additional lane.
- **TERMINAL_BROWSER_AS_MATRIX:** terminal-browser is a fast Chromium exploratory lane, not evidence of Firefox/WebKit parity.
- **MISSING_FAILURE_ARTIFACTS:** retain screenshot/trace/video/logs on failure where useful.

### GitHub operations without `gh` (`scripts/gh_ops.py`)

Stdlib-only (`python -S`) REST helpers for PR/CI work where the `gh` CLI is unavailable. Read-only by default; writes need `--write` and re-check their preconditions first. Token: `GITHUB_TOKEN` (fallback `GH_TOKEN`), never printed. Exit codes: 0 OK, 1 condition not met, 2 input/communication error.

```sh
python -S scripts/gh_ops.py issue-comments OWNER/REPO 24 --save comments.json   # one line per comment
python -S scripts/gh_ops.py issue-comments OWNER/REPO 24 --show 6,18            # full text of selected comments
python -S scripts/gh_ops.py comments-file saved-tool-result.txt --last 5        # same digest from a saved JSON dump, offline
python -S scripts/gh_ops.py --json pr-observe OWNER/REPO 24 > before.json     # PR/head/check/review snapshot
python -S scripts/gh_ops.py pr-diff before.json after.json                         # offline meaningful-change events
python -S scripts/gh_ops.py issue-comment OWNER/REPO 24 --file note.md --write     # verified top-level PR comment
python -S scripts/gh_ops.py pr-for-branch OWNER/REPO my-branch                    # existing PR for a branch? merged? (none -> exit 1)
python -S scripts/gh_ops.py open-prs OWNER [--org] [--repos a,b]               # open PRs across repositories, newest first
python -S scripts/gh_ops.py open-issues OWNER [--org] [--repos a,b]             # all open Issues, excluding PRs
python -S scripts/gh_ops.py repo-counts OWNER [--org] [--repos a,b]              # repo / open issues / open PRs / commits
python -S scripts/gh_ops.py --json repo-counts OWNER --repos a,b                # structured rows and totals
python -S scripts/gh_ops.py checks-wait OWNER/REPO <sha> --min 5
python -S scripts/gh_ops.py pr-merge OWNER/REPO 11 --sha ecfd0ba --min-checks 5 --method squash --write
python -S scripts/gh_ops.py pr-body-set OWNER/REPO 11 --file body.md --write     # replace the whole PR body
python -S scripts/gh_ops.py pr-edit OWNER/REPO 11 --title "New title" --write    # PATCH title/base/state (draft/ready needs GraphQL)
python -S scripts/gh_ops.py file-put OWNER/REPO path/to/file --from local.txt --branch my-branch --message "msg" --write
python -S scripts/gh_ops.py url pr OWNER/REPO 11 --tab checks                    # build a github.com/api URL; no network
```

Also: `pr-status`, `runs`, `workflow-state`, `workflow-dispatch`, `pr-body-replace`, `sync-main`. Every subcommand is a function (`from gh_ops import pr_merge`) returning a dict; the CLI is a thin adapter.

`pr-observe` binds the check summary to the PR's current head SHA and records small digests for conversation comments, reviews, and inline review comments. Zero checks are `pending`, never green. `pr-diff` is offline and emits events such as `head_changed`, `ci_became_green`, `ci_failed`, `draft_changed`, `merged`, and review/comment activity changes; it deliberately does not infer semantic labels such as Blocking/Should from prose. `issue-comment` is dry-run by default, first verifies that the number is a PR, and after POST re-reads the created comment. If verification is uncertain it reports that the comment may already exist and tells the caller to inspect before retrying, rather than encouraging duplicate posts.

`repo_counts(owner, org=False, repos=(), client=None)` and `open_issues(...)`
use only REST GETs with the same injectable HTTP transport. Without `--repos`,
they enumerate the user's repositories, or all organization repositories with
`--org`; explicit comma-separated **short repository names** bypass enumeration.
Archived and zero-count repositories are included, duplicate names are counted
once, and rows are sorted by repository name. Every Link `next` page is read,
without the search API's result cap or a silent page limit. The `/issues` response
contains both Issues and PRs: presence of `pull_request` identifies a PR;
`open_issues_count` is never used as an Issue count.

`repo-counts --json` returns `repositories` rows with `repo`, `open_issues`,
`open_prs`, `commits`, and `default_branch`, plus `repository_count`, `totals`,
`owner`, `org`, `ok`, and `commit_scope: "default_branch"`. Commits means all
commits reachable from the default branch, including merge commits, rather than
all branches or just commits in open PRs. Counting reads the complete commit
history, so large repositories may require many requests. An empty Git repository
counts as zero commits; other HTTP errors fail with exit 2 rather than silently
reporting zero or partial counts. Successful empty results exit 0. Reads are
sequential, so activity during collection can change the counts; this is not an
atomic snapshot. `open-issues --json` returns `issues` rows and `total`.

### Screenshot knowledge across repositories

See [docs/screenshot-evidence.md](docs/screenshot-evidence.md) for the shared
capture/validation/publishing procedure, existing validator limits and consumer
adoption map. Application-specific incidents retain source head/run and measured
repair status; the first record covers flutter_navigation_basic PR #128 (merged).

### Provenance

The initial rules were distilled from working patterns and incidents in `mcp-toolcall-lab`, `flutter_navigation_basic`, `web-ui`, `markdown`, and `Ironmate`.

Notable portable lessons include WebKit producing a PNG before a non-zero shutdown, browser-config/install parity tests, exact Playwright CLI argv regression tests, staged visual-regression rollout, `python -m playwright screenshot` for simple Python CLI capture, and keeping Stagehand separate from deterministic Playwright.

### Incident record format

When a new repository teaches a reusable lesson, record: repository/PR, runtime (Node/Python/Stagehand/Replay), browser, device/profile, failure stage, observed symptom, root cause, fix, regression guard, and portable lesson.

### Page text without a browser (`tools/page_text.py`)

Stdlib-only static extraction (runs under `python -S`) in the shape of a browser `get_page_text`: `Title:`, `URL:` (after redirects), `Source element:`, then the text.

```bash
python tools/page_text.py https://example.com/ --max-chars 20000
python tools/page_text.py fixtures/index.html --json
curl -sL https://example.com/ | python tools/page_text.py - --url https://example.com/
python tools/page_text.py <URL> --find "status page"
python tools/page_text.py <URL> --compare-rendered rendered.txt
python tools/page_text.py <URL> --compare-rendered page.json   # capture_browser_dom.py output
```

- The text comes from the first `<main>`, else the first `<article>`, else `<body>`; `script`, `style`, `noscript`, `template`, `svg`, and `textarea` are dropped with their contents, as is the never-rendered fallback content of `iframe`, `noembed`, and `noframes`.
- Line breaks follow `innerText` for each element's default display: a blank line around `<p>`, a new line for other block elements and `<br>`, and a tab between table cells. `<pre>` keeps its indentation, whitespace runs collapse to one space, and blank-line runs collapse to one.
- `--find` lists links, buttons, headings, and `[role=link|button]` elements whose text, `aria-label`, or `title` contains the query (case-insensitive), with hrefs resolved to absolute URLs. No match exits 1.
- `--json` adds `truncated`, `total_chars`, `bytes`, `byte_limit_hit`, `status`, `content_type`, and `fetched_at`. `--find ""` lists every candidate. `--max-chars`, `--max-bytes`, and `--timeout` bound the work.
- Exit codes: 0 OK (or EQUAL comparison); 1 no `--find` match, non-EQUAL comparison, or an HTTP error status (the page is still printed; a 404 adds a `LOGIN_WALL_AS_404` hint); 2 usage, input, or network error.
- JavaScript is not run and CSS is not evaluated, so an empty result on a single-page app is `PAGE_TEXT_AS_RENDERED_TEXT`, not an empty page. Core tests use only local fixtures and a local HTTP server.
- `--compare-rendered FILE` compares static extracted text against Playwright's `inner_text()` (or terminal-browser's rendered text) from a file or stdin, classifying differences into `EQUAL`, `JS_RENDERED_ADDED`, `CSS_HIDDEN_REMOVED`, or `MUTATED_TEXT`. A `capture_browser_dom.py` JSON file contributes its `visible` (`innerText`); `textContent` and `html` are never used as rendered text. A truncated static text (`--max-chars`) is refused with exit 2.

### Hit maps with few probes (`scripts/hit_probe.py` / `scripts/hit_probe.mjs`, `littlebit-approx`)

Stdlib-only (`python -S`) search for where a target really receives clicks, Minesweeper-style: probe a coarse lattice, infer blocks whose corners agree, and split and probe again only where they disagree or score near the threshold. Scores are 0..1 (in the browser test, the fraction of a cell's 2x2 sub-points that `elementFromPoint` lands on the target), so a near-threshold cell is treated like a low-confidence Jev decision. The map is then described LittleBit-style as a few binary row-mask x column-mask rectangles with a scale (mean probed score); a plain box is rank 1 with no residual, while rounded corners, rotation, or an overlay leave a residual or need more rank.

```bash
python -S scripts/hit_probe.py factorize test-results/hit-probe/map.json --rank 3
node scripts/hit_probe.mjs factorize test-results/hit-probe/map.json --rank 3
node --test tests/node-unit/hit_probe.test.mjs
```

- The Node ES module uses only Node standard modules (Node 26+); it exports `refine`, `exhaustive`, `factorize`, `boundary_pairs`, `ranges`, `render`, `rows_from_map`, and `to_json`. `await refine(width, height, probe, {stride: 8, margin: 0.25})` accepts a synchronous or asynchronous `probe(points)` returning one numeric score per integer `[x, y]` cell. `exhaustive` is also awaited. There is no Playwright runtime dependency in the module.
- Python integer bitsets become Node `BigInt` rows/factor masks, including columns beyond 32/53 bits. Node scores are a `Map` keyed by `"x,y"`. Use `to_json(result, {rank: 3})` for JSON-safe `btk-hit-probe/1` evidence (same map strings, factor ranges, scale rounding, sorted/truncated uncertain cells and metadata as Python); raw BigInt results cannot be JSON-stringified. Node options use the Python names, including `max_probes`. The CLI accepts the same saved map and emits the same summary/ranges with exit 0 or 2.
- Node validates dimensions/stride/budgets as safe integers, thresholds/margins and callback scores as finite numeric 0..1 values, and masks as non-negative BigInt values. It rejects coercible strings/booleans and malformed callback lengths; Python's existing coercion behavior is unchanged. Compatibility covers valid numeric inputs, not Python-specific permissive coercions.
- Scale accumulation follows Python 3.12+ compensated `sum` (the reference CI uses Python 3.15), then Python's six-decimal rounding. Python 3.9–3.11 uses an older summation method, so arbitrary non-dyadic scores can differ at a six-decimal rounding boundary; quarter-step browser scores have identical results on both. A regression covers the modern summation boundary and importing from stdin/eval without running the CLI.
- `tests/node-unit/hit_probe.test.mjs` compares Python/Node evidence, full uncertainty lists, raw masks, factors, boundary pairs and ordered probe batches on 84 deterministic cases: tiny/wide grids, thresholds, ties, low confidence, rotated boxes, overlays and seeded score matrices. It also checks repeated-run determinism, exhaustive maps, malformed inputs, budgets and CLI parity. The existing browserless Playwright CI runs this suite through `tests/browserless/hit-probe.spec.ts`; Python is used only as a test oracle.
- `tests/node/hit-probe.spec.ts` observes the same four fixture targets using integer origins/sub-points, compares live Node adaptive maps with exhaustive observations and Python results from those same observations, and verifies real boundary clicks. Existing Node browser projects execute it, including their existing emulated profiles; this adds no device configuration or Flutter integration. It records fractional-edge behavior without assuming equality across engines. Evidence is saved as `hit-probe.json` / `fractional-edge.json` under each test output directory.
- `tests/python/test_hit_probe_browser.py` maps four targets in `fixtures/hit_probe.html` (plain, rounded, covered by a blocker plus a `pointer-events:none` ghost, rotated) on Chromium, Firefox, and WebKit. It asserts that the map equals an exhaustive probe, uses under half the probes, and that real clicks on cells either side of the boundary match the map. Evidence goes to `test-results/hit-probe/<browser>.json`.
- Assumption: no feature thinner than the starting stride; the exhaustive comparison is what guards it. Only the computation is borrowed; there is no model or special runtime.
- Lessons from the first CI runs (PR #57), kept as regression tests:
  - `CORNER_POKES_BETWEEN_CORNERS`: a corner of a rotated target can poke into a block between its four agreeing corners (Firefox showed `exact=False`). Blocks of 4+ cells next to a differently labelled probe are therefore split, like opening the cells next to a Minesweeper number. On 300 random rotated boxes this went from 62 misses to none, at about 21% of cells probed; `test_random_rotated_boxes_match_exhaustive` keeps 60 of them.
  - `FRACTIONAL_HIT_COORDINATES`: engines round fractional coordinates differently, both in `elementFromPoint` and in `mouse.click` (WebKit clicked where the prediction said miss). Sub-points and the grid origin are integer pixels.
  - Each run prints one `::notice` per browser (probes, exact match, clicks, factors, residual), so the numbers are readable from the check annotations without downloading artifacts.
- Exit codes: 0 OK; 2 input error.

### Emoji page trails to URLs (`scripts/route_trail.py`)

Stdlib-only (`python -S`). Pages are named by their emoji identity, and a trail is walked in order: `A → B` goes to B, `A ↔ B` goes to B and back (ASCII `->` / `<->` also work). The registry is a JSON object or any source file with `'/route': '🏠'` pairs, such as flutter_navigation_basic's generated `page_identities.g.dart` (`--map pageEmojis`; values that are routes, i.e. alias maps, are skipped).

The `page_identities.g.dart` example needs a flutter_navigation_basic checkout; `fixtures/route_trail_registry.json` works without one.

```bash
python -S scripts/route_trail.py '🏠 → 🗺 → 🌍 ↔ 🕶' --registry lib/shared/navigation/page_identities.g.dart \
  --map pageEmojis --base https://myon-bioinformatics.github.io/flutter_navigation_basic/
python -S scripts/route_trail.py '🏠 ↔ 🗺' --registry fixtures/route_trail_registry.json --json   # emoji, route, url
```

- Emoji are compared without variation selectors (`EMOJI_VARIATION_SELECTOR`); an emoji naming two routes after that is a registry error.
- Hash routing (`BASE#/route`, as Flutter web) by default; `--path-routing` for `BASE/route`.
- `tests/python/test_route_trail_browser.py` walks a trail on Chromium, Firefox, and WebKit against a hash-router fixture and checks each step shows its page.
- Exit codes: 0 OK; 2 unknown emoji, malformed trail, or bad registry.

Execution APIs and CLI (`--run`) are documented in [route trail execution](docs/route-trail-execution.md).

Measured mouse/touch `press` for canvas-painted UIs (Flutter Web) and a `watch` that records requests, dialogs, errors and storage while untrusted input is handled: [interaction](docs/interaction.md) (`scripts/interaction.mjs` / `scripts/interaction.py`).

Browserless mirror tests — several independent implementations (markdown-it / marked, ExcelJS / SheetJS / a stdlib OOXML reader, your own code) must agree on one artifact: [mirror tests](docs/mirror-tests.md) (`tools/mirror_facts.mjs` / `tools/mirror_check.py`).

Run these existing comparisons without downloading browsers: `npm ci --ignore-scripts && npm run test:browserless`. Python rules use `uv run --locked pytest -m "not browser"` (3.11+ uv environment; see **Python support** above); real browser tests use `uv run --locked --group browser pytest -m browser`. See [separate execution](docs/mirror-tests.md#separate-browserless-execution).
The same JSON catalog supports stable `@id` names and readiness markers; Python
sync/async and dependency-light Node execution reuse one Page for the whole trail.

### OS × browser × device comparison (`tools/platform_probe.py`, `platform-matrix`)

Opens `fixtures/platform_probe.html` in one environment and records `btk-platform-facts/1`: screen and available area, window outer/inner size (the difference is the window frame, real only with `--headed`), scrollbar width (page and an always-scrolling probe, so overlay scrollbars show as 0), `devicePixelRatio`, `visualViewport`, safe-area insets, pointer/hover/touch, measured font widths, platform and browser version. An environment **passes** when the layout holds (no horizontal overflow, no overlap, nothing clipped) and a real click — a tap on touch profiles — at the integer centre of the button registers. `min_ok_width` is the narrowest viewport width where the layout still holds, found by binary search (≤ 13 probes).

```bash
python tools/platform_probe.py collect --browser webkit --profile iphone --out platform-results
xvfb-run -a python tools/platform_probe.py collect --browser chromium --headed --out platform-results
python -S tools/platform_probe.py summary platform-results            # one line each; exit 1 if any fail
python -S tools/platform_probe.py render platform-results --out matrix.html
```

- `matrix.html` is self-contained: tab rows for OS, browser (engine or channel), device and mode. Switching a tab highlights every value that changed from the previous selection; `*` marks values that differ anywhere in the dataset; a scaled diagram shows screen, available area, window, viewport and scrollbar.
- `.github/workflows/platform-matrix.yml` runs every engine × profile × mode on `ubuntu-24.04`, `windows-latest` and `macos-latest` (plus branded `chrome`/`msedge` where installed), prints one `::notice` per environment, and uploads the merged `platform-matrix` artifact (`dataset.json`, `index.html`).
- Profiles are emulations (`MOBILE_VIEWPORT_ONLY`), and Playwright WebKit is not Safari (`SAFARI_EQUALS_WEBKIT`). Engines are compared by pass/fail and properties, not equal numbers (`CROSS_ENGINE_PIXEL_EQUALITY`).
- Adopted third-party JS (`js-adopt`, see the parent's `js_adopt.py`) is checked here too: the fixture loads every file in `js.lock.json` with its SRI, and an environment passes only if DOMPurify sanitizes, seedrandom returns the same first value as every other engine, and a tag with a deliberately wrong `integrity` is refused. `collect` serves the fixture over a local HTTP server because SRI with `crossorigin` does not apply to `file://` pages.
- Only `collect` needs Playwright. Exit codes: 0 OK; 1 an environment failed; 2 input error.

### Element annotation overlay, DOM highlight CSS, and TAP assertions (`scripts/element_overlay.py` / `scripts/element_overlay.mjs`)

Target coordinate calculation, SVG annotation overlays, DOM element highlight CSS, and Poku-style TAP element assertions: [element overlay](docs/element-overlay.md) (`scripts/element_overlay.py` / `scripts/element_overlay.mjs`). Absorbs patterns from SeleniumBase, Callstack agent-device, Midscene, Poku, and PuppetSharp.

```bash
python -S scripts/element_overlay.py --file elements.json --viewport 1280x720
python -S scripts/element_overlay.py --file elements.json --format svg --out overlay.svg
python -S scripts/element_overlay.py --file elements.json --assert-role button --format tap
python -m element_overlay --file elements.json --format css
```

### Page observe, locate, and act (`scripts/btk.py`)

Separate commands for a loopback page: passive observe, offline locate, and one declared action. The design follows REA's browser observation notes (morluto/rea, MIT), not that project's server. See [REA observation](docs/rea-observation.md). Later phases stay on [issue 117](https://github.com/myon-bioinformatics/browser-test-kit/issues/117).

```bash
python scripts/btk.py observe http://127.0.0.1:8765/page --json --out observe.json
python -S scripts/btk.py locate observe.json --query "Save draft" --unique --json
python scripts/btk.py act http://127.0.0.1:8765/page --action click --locator id:save-draft --json
node vendor/py_launch.cjs --env-var BTK_PYTHON --site scripts/btk.py locate observe.json --query "Save draft" --unique --json
python -S scripts/btk.py compare before.json after.json --json
python scripts/btk.py observe http://127.0.0.1:8765/page --viewport 390x844 \
  --step focus:id:search --step type:id:search=env:SEARCH --step press:Enter \
  --step wait:id:results --step click:id:result-1 --out obs.json
python scripts/btk.py observe http://127.0.0.1:8765/page --out before.json --after-action click:id:next --after-out after.json
```

- `observe --step` records a `btk-page-observe/1` snapshot before the steps and after each step, then compares each pair with the existing `compare` function. The file schema is `btk-page-observe-run/1`. `--out-dir DIR` also writes each of those observation objects as `00-initial.json` and `NN-action.json` in an existing directory. Those files are slices of the run, not a second schema. `--after-action` (`click:`, `hover:`, `scroll:DX,DY`, `scroll-into:`) and `--after-click` are one step from that same performer, written to two snapshot files.
- Steps cover keyboard (`type`, `fill`, `clear`, `press`, `shortcut`, `focus`, `blur`), mouse (`click`, `dblclick`, `rightclick`, `hover`, `drag`, `scroll`, `scroll-into-view`), form (`check`, `uncheck`, `select`, `set-files`, `submit`), page (`goto`, `back`, `forward`, `reload`, `wait`, `resize`, `settle`), and touch (`tap` on every engine when `hasTouch` is set; `long-press`, `swipe`, and `touch-scroll` on Chromium via CDP Input, and `unsupported` on Firefox and WebKit because those protocols only have a point tap). `settle` is Chromium-only: readyState past loading and no DOM mutation for 400 ms, from CDP lifecycle and DOM events, with `quiet: false` on timeout. Firefox and WebKit report `settle` unsupported because they have no CDP DOM mutation stream. `Primary` is Playwright `ControlOrMeta`. Values from `env:NAME` and `file:PATH` are redacted in full. Exception text and JSON output also apply Stagehand's key redaction (query keys, `sk-` keys, Browserbase keys, `AIza` keys, and bearer tokens).
- `compare` reads two observe files offline. Each section is `same`, `different`, or `unknown` (a missing or truncated section is never `same`), with open, closed, and total shadow-root counts and their deltas. It reports when the browser, profile, touch, emulation, or viewport differ.
- Targets are `http://127.0.0.1:PORT/...` or `http://[::1]:PORT/...` only. Hostnames, file URLs, and missing ports are rejected.
- `observe` on Chromium reads CDP and does not call the page. It counts author open and closed shadow roots in a pierced DOM (`shadow_roots` section) without entering them; Chromium user-agent shadow roots are reported separately as `user_agent`. Child frames are listed by name, URL, and origin in document order (`frames.items`) and their documents are not entered. Firefox and WebKit report CDP unavailable instead of inventing a DOM. Storage is keys and SHA-256 fingerprints, never values. A missing section is not a claim that two captures match. `python -S scripts/fetch_member.py --lock parts.lock.json --part node --dest DIR` checks one archive's integrity and extracts the named member. Playwright there is `>=1.64.1`, a lower bound with no upper bound. The Node package `@playwright/test` uses the same lower bound.
- iPhone and Pixel profiles are Playwright device emulation (`real_device: false`). `--viewport` is only a size. `--touch` sets `hasTouch` without naming a phone.
- `act` runs one action from the same vocabulary. It re-queries the locator and rejects `h:N`. Dialogs are recorded and dismissed unless `--dialog-policy accept`.
- From Node, run `btk.py` through the vendored `vendor/py_launch.cjs` CLI (Node 26 or later, no upper bound): `node vendor/py_launch.cjs --env-var BTK_PYTHON --site scripts/btk.py ...`. `--site` keeps site-packages so Playwright is importable. `BTK_PYTHON` picks the interpreter; when it is set there is no fallback, and an interpreter that cannot run fails the launch with exit 2. Without it the launcher tries `PYTHON`, `python3`, then `python`. `scripts/page_inspect.cjs` is a thin compatibility entry that does the same. It is CLI-only now: the former `require()` API (`module.exports`, including JS helpers such as `similarity`) is gone. The former `scripts/btk.cjs` and the `twin_parity.py` helper-parity design were removed; Python is the single implementation of score, loopback, fingerprint, and evidence comparison.
- Whitespace steps (`--step "fill id:q TEXT"`) and `--trail` retain the previous per-file envelope; colon steps use `btk-page-observe-run/1`. Emoji routes from `pages --routes-out` work with `observe --routes --trail` and `act --action goto --routes --text`. Both syntaxes use the same action performer and URL evidence privacy rules.
- Exit codes: `0` success; `1` no match or a shared top score; `2` usage, a bad snapshot, or launch/navigate/inspect/cleanup failure. Headless is the default.

### Platform and fault scenarios (PR #123)

The **active** `scenario` subcommand verifies OS-dependent shortcuts, display settings,
browser-context offline/HTTP errors/latency, permissions and bounded file interactions.
It uses the same `btk-page-observe/1` envelope and Node `vendor/py_launch.cjs --env-var BTK_PYTHON --site`
launch without replacing `observe --step`, `act`, or `compare`.

```bash
python scripts/btk.py scenario http://127.0.0.1:8765/scenarios --case display --viewport 390x844 --color-scheme dark --reduced-motion reduce --locale ja-JP --timezone Asia/Tokyo --json
python scripts/btk.py scenario http://127.0.0.1:8765/scenarios --case offline --json
python scripts/btk.py scenario http://127.0.0.1:8765/scenarios --case permission --permission geolocation --permission-state grant --json
node vendor/py_launch.cjs --env-var BTK_PYTHON --site scripts/btk.py scenario http://127.0.0.1:8765/scenarios --case shortcut --json
```

The local test fixture is `fixtures/page_inspect_scenarios.html`, served with the
`/api/probe` and `/scenario.txt` fixture routes. `scenario` can also test
`http-error`, `latency`, `upload`, and `download`; see
[platform scenario contract](docs/page-inspect-platform-scenarios.md).
`.nvmrc` selects **Node 26**. Emulated phone profiles are not real devices.
Only a complete section with an eligible digest can compare as `same`.

### Sequence status

Implemented:

1. Documentation and evidence contract (`docs/`, `docs/evidence-events.md`).
2. Shared local deterministic fixtures and artifact validators (`fixtures/`, `tools/evidence_board.py`).
3. Playwright Node matrix (`.github/workflows/ci.yml`, `platform-matrix.yml`).
4. Playwright Python API and CLI matrix (`tools/platform_probe.py`).
5. Browser parity guards (Node `tests/node-launch` runs observe, act, and scenario through `vendor/py_launch.cjs` in Chromium).
7. terminal-browser quick CLI/TUI lane (`tools/terminal_browser.py`).
10. Passive page observe, locate, and a fixed act vocabulary (`scripts/btk.py`). Stagehand is still a separate, unimplemented lane.

Not yet implemented in this repository:

6. Stagehand v4 Python optional lane.
8. Replay optional lane.
9. Reusable workflow and template examples.

### terminal-browser quick lane

See [`docs/terminal-browser.md`](docs/terminal-browser.md). The dependency-free wrapper keeps missing-tool failures explicit and passes upstream CLI arguments through unchanged:

```sh
python tools/terminal_browser.py --check        # verified: discovery + JSONL events
python tools/terminal_browser.py -- <args...>   # passes args through unchanged
```

Upstream [zenbu-labs/terminal-browser](https://github.com/zenbu-labs/terminal-browser) documents `open <url>`, `action`, `ls`, and `upgrade`, installed via its own install script or Homebrew -- not the npm `terminal-browser@1.0.2` package, which is an unrelated same-name project. This repo's CI does not exercise the real upstream tool, so the wrappers here remain unverified against it. Arguments are forwarded as-is; see "Non-interactive subcommand wrappers" in docs/terminal-browser.md.

### Evidence events (btk-event/1)

See [`docs/evidence-events.md`](docs/evidence-events.md) for the shared JSONL
event contract (schema `btk-event/1`) that `tools/terminal_browser.py`
emits, that the opt-in `tools/pytest_btk_events.py` pytest plugin writes
per test, and that `tools/evidence_board.py` aggregates into a Markdown
table, `--json`, or a GitHub Step Summary. Nothing here loads implicitly:
there is no `conftest.py`, and the plugin only runs when a project opts in
with `pytest -p pytest_btk_events --btk-events PATH`.

### Shared Git inventory and churn

`repo_overview.list_files()` uses `git_ls_files()` from the stdlib-only
`scripts/gh_identity.py` vendored from `myon-bioinformatics/gh_identity`
(recorded in `vendor.lock.json`).

The combined inventory includes tracked and untracked-but-not-ignored files.
The consumer deduplicates, filters dependency/cache/result directories and
missing/non-file entries, and sorts the final result independently of Git's
order. Missing Git or a failed Git observation retains the directory-walk
fallback (which does not interpret `.gitignore`). A truncated successful
observation raises an explicit error rather than publishing a partial list or
walking ignored files: the shared bounds are 10,000 paths and 1,000,000 bytes.
Direct invocation (`python -S tools/repo_overview.py`) still works with `python -S`.

`repo_overview.churn()` delegates Git execution and numstat parsing to
`git_log_numstat()`. Commit scanned counts include empty/merge/binary-only commits;
binary file counts remain excluded from ranking. Ranking is still commit count
descending, then raw path ascending, with committer dates and `--since` retained.

Intentional display improvements (covered by before/after fixtures): renames
are attributed to the destination path rather than Git's compact `old => new`
label, without folding earlier old-path history into the destination. Unicode
paths appear as Unicode rather than Git's quoted octal spelling. Control
characters (including tab/newline) use JSON string escaping so each path stays
on one row. Ordinary path output is unchanged. History now has explicit bounds
of 10,000 commits / 1,000,000 bytes: only complete commits count as scanned and
an additional truncation note makes partial-history rankings explicit. The top-N
file omission note remains independent. Missing/failed Git retains the existing
`not a git work tree; skipping --churn` message.

`scripts/gh_ops.py` is a lock-managed copy of the parent canonical `gh_ops.py`.
Generic behavior and its regression suite are maintained in the parent repository;
this repository tests imports, CLI compatibility and vendor wiring.

Public source placement and automatic Python CI updates: [vendor automation](docs/vendor-automation.md).

### Shared bounded browser exploration

`scripts/browser_execution.mjs` owns resource-aware `workerBudget`, stateless
seeded `choice`, and bounded `explore` on an existing Playwright-compatible Page.
Applications supply readiness, allowed button names and input values; the helper
returns `browser-exploration/1` receipts and preserves partial receipts on errors.
See [browser execution](docs/browser-execution.md). Generic checks run once in
this repository's browserless Node lane; consumers keep their application
contracts and immutable source/hash checks, rather than copying the test suite.
Emoji identities and ordered page movement continue to use `route_trail.py`.

Application adapters for the platform matrix (including published Flutter Home and Counter):
[configuration, scope and independent network lane](docs/platform-adapters.md).


## Discover production entry points

```sh
python -S tools/repo_overview.py . --paths-only --match 'scripts/*probe*' --match '*.toml' --json
```

Select paths from the existing Git-aware inventory without rendering file contents. [Discovery usage and optional source inspection](docs/convention-discovery.md).

Comparison and digest selection now accept `--page`, `--env`, and a read-only
`--list` plan. See [selected comparison plans](docs/platform-adapters.md#select-a-comparison-or-digest-plan).


Page-inspect network boundary (issue 116): `observe` and `act` accept only
`http://127.0.0.1:PORT/...` or `http://[::1]:PORT/...` (HTTPS the same way).
The port must be written in the URL. Hostnames (including `localhost`),
credentials, file URLs, and other addresses are rejected before browser launch.
The same request guard applies to page resources; HTTP redirects are refused
without following them. Service workers are blocked. This is a browser request
boundary, not an OS-level sandbox. Storage records key names and SHA-256
fingerprints only. See [REA observation](docs/rea-observation.md).

## Optional nano environment

[Opt-in Markdown/HTML nano environment](docs/nano-environment.md): both parsers and the existing browserless runner, with isolated outputs and no browser download; full XLSX/browser lanes stay unchanged.

## Focused test discovery

See [standard-library discovery commands](docs/unittest-discovery.md) for
validated `python -S -m unittest discover` recipes, filename/name filtering,
and the boundary with the full pytest suite.
