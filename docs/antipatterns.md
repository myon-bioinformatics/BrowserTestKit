# Browser testing anti-patterns

This repository starts from observed browser-testing failures rather than from a preferred framework.

## Evidence rules

A browser command exiting successfully is not sufficient evidence by itself. Keep the command log and validate the artifact that matters.

For screenshots, the minimum useful contract is:

1. the browser command was attempted and its exit status is recorded;
2. the expected file exists and is non-empty;
3. the file signature matches the declared image format (PNG: `89 50 4e 47 0d 0a 1a 0a`);
4. dimensions/viewport/device/browser are recorded when they matter;
5. CI uploads the image and relevant logs for human inspection.

Screenshots are visual evidence, not a selector strategy. Prefer DOM/accessibility/locator state for interaction assertions, and screenshots for layout, rendering, canvas/chart content, and bug evidence.

## Stable anti-pattern catalogue

| ID | Anti-pattern | Failure mode | Preferred pattern |
| --- | --- | --- | --- |
| `EXIT_ZERO_ONLY` | Treating exit code 0 as proof of a valid screenshot | Empty/corrupt/wrong artifact can pass | Validate existence, size, signature, then retain artifact |
| `NONEMPTY_IMAGE_ONLY` | Checking only `test -s` | Non-image bytes can pass | Check magic bytes; optionally dimensions |
| `BROWSER_MATRIX_DRIFT` | Config declares Chromium/Firefox/WebKit while CI installs fewer browsers | Works locally/config looks complete, CI fails or silently skips coverage | Test config/install parity |
| `ONE_ENGINE_ASSUMPTION` | Chromium success is treated as cross-browser success | WebKit/Firefox-specific regressions escape | Keep explicit engine lanes |
| `SAFARI_EQUALS_WEBKIT` | Calling Playwright WebKit “Safari” | Overstates fidelity | Say WebKit; document when macOS/Safari fidelity matters |
| `MOBILE_VIEWPORT_ONLY` | A narrow viewport is treated as a real mobile profile | UA/touch/device scale/browser engine differences are missed | Use named device emulation where appropriate and record the profile |
| `DEVICE_DESCRIPTOR_CROSS_ENGINE` | Reusing one named device descriptor unchanged across every browser engine | Engine-specific context options can fail at launch/context creation (for example Firefox rejects Playwright `is_mobile`) | Treat device descriptors as engine-sensitive; adapt unsupported options explicitly and regression-test the adaptation |
| `DEAD_COMPATIBILITY_HELPER` | Adding a compatibility/helper function without routing the production/test call-site through it | Unit coverage for the helper can pass while the real browser path keeps using the old inline logic | Replace the old call-site explicitly and keep an integration/browser test that proves the helper is exercised end-to-end |
| `HELPER_NAME_SHADOWING` | Reusing a helper function name for a local variable at its call-site | The compatibility abstraction becomes unreachable or misleading | Give builders/helpers verb-based names and distinct local names; cover the real integration path |
| `SCREENSHOT_AS_SELECTOR` | Acting on pixels/screenshots when DOM/locator evidence exists | Brittle automation | Interact through locators/snapshots; screenshot for visual evidence |
| `STALE_ELEMENT_HANDLE_WAIT` | Polling a captured node while a framework replaces it | Timeout although visible DOM is correct | Re-query locator/selector during polling |
| `SILENT_CLEANUP_EXCEPTION` | Cleanup/diagnostic exceptions are swallowed | Original failure loses its evidence | Preserve logs and report cleanup failures without masking root cause |
| `CLI_ARG_GREEDINESS` | Wrapper constructs ambiguous Playwright/child CLI argv | Project/file/top-level args are consumed incorrectly | Unit-test exact argv construction; support an explicit `--` delimiter for child top-level flags |
| `BROWSER_BINARY_MISMATCH` | Python/Node package revision and installed browser binary drift | Launch failure in CI/sandbox | Install browsers from the same Playwright version; expose intentional executable override only when needed |
| `EXTERNAL_SITE_AS_CORE_FIXTURE` | CI depends on a third-party page | 403/429/network failures masquerade as product regressions | Core tests use local deterministic fixture; external evidence is a separate lane |
| `FAILURE_LAYER_FLATTENING` | Browser, network, app and artifact failures all become generic failure | Diagnosis becomes slow | Emit stage/browser/runtime/URL/artifact metadata |
| `VISUAL_DIFF_TOO_EARLY` | Pixel baseline is made blocking before deterministic capture is stable | Noisy CI | First validate candidate screenshots; promote stable baselines later |
| `NODE_OR_PYTHON_MONOCULTURE` | One Playwright binding is presented as the only reference | Other repos cannot reuse the pattern naturally | Maintain equivalent Node and Python examples with shared semantics |
| `AGENT_REPLACES_DETERMINISTIC_TEST` | Stagehand/agent lane replaces deterministic Playwright checks | Model/external variability weakens regression signal | Keep Stagehand optional and separate from deterministic Playwright |
| `TERMINAL_BROWSER_AS_MATRIX` | A terminal-browser Chromium exploratory pass is treated as cross-browser evidence | Firefox/WebKit/mobile-specific regressions escape while the exploratory lane looks green | Keep terminal-browser optional/non-blocking and retain the deterministic Playwright matrix as authoritative |
| `MISSING_FAILURE_ARTIFACTS` | Screenshot/trace/video exists only on success or is discarded on failure | Hardest failures are least observable | Retain-on-failure screenshot/trace/video where useful |
| `EVIDENCE_MASKS_ROOT_FAILURE` | Evidence capture throws while handling the original test failure | Screenshot/trace failure replaces the actual assertion/navigation error | Keep failure evidence best-effort and re-raise the original exception |
| `SUCCESS_COUNT_VS_RETRY_ARTIFACTS` | Global artifact count is assumed to equal project count | Retries create extra attempt artifacts and cause false failures | Validate exact project identity from metadata and validate every discovered artifact |
| `PAGE_TEXT_AS_RENDERED_TEXT` | Text extracted from static HTML is treated as the text a browser shows after rendering | An SPA shell yields an empty or partial body, and CSS-hidden text is counted as visible | Keep static extraction (`tools/page_text.py`) and the Playwright lane's `inner_text()` as separate evidence; compare them when rendering matters |
| `LOGIN_WALL_AS_404` | "Page not found" is treated as proof that a page does not exist | Sites such as GitHub answer 404 to clients without access, so an existing settings page looks missing to a signed-out browser or an unauthenticated fetch | Check the login/permission state first; report a 404 as "not found or not visible to this identity" |
| `FRACTIONAL_HIT_COORDINATES` | Hit-testing or clicking at fractional CSS pixels (a cell at `x + 0.5`, a grid origin taken from a rotated element's bounding box) | Engines round differently, in `elementFromPoint` and in `mouse.click`: Chromium turned a 0.5 offset into half hits on a plain box, and WebKit clicked where `elementFromPoint` predicted a miss (PR #57) | Probe and click at integer pixels; floor the grid origin; record fractional behaviour per engine as evidence, not as an assertion |
| `CORNER_POKES_BETWEEN_CORNERS` | Treating a sampled region as uniform because its four corners agree | A corner of a rotated or rounded target poking in between the corners is never probed; Firefox showed a map that differed from the exhaustive one (PR #57) | Also split blocks that touch a differently labelled probe (`scripts/hit_probe.py`: 4+ cells), and compare every sampled map with an exhaustive one in tests |
| `CROSS_ENGINE_PIXEL_EQUALITY` | Expecting the same hit map, residual, or probe count from every engine | Curved and rotated edges are rasterized slightly differently (rounded target residual: Chromium 28, WebKit 32, Firefox 40), so a cross-engine equality check fails on correct behaviour | Compare each engine with its own exhaustive reference and with real clicks; assert shape properties (rank-1 box, residual > 0), not equal numbers |
| `HEADLESS_AS_REAL_WINDOW` | Taking window frame, scrollbar or screen-position numbers from a headless run as what users see | Headless Chromium reports a 0×0 frame and 0 px classic scrollbars; the same machine headed (Xvfb) reports an 8×85 frame and 15 px scrollbars (platform-matrix, #89), so layouts that fit headless can overflow in a real window | Measure frame and scrollbar facts headed (`platform_probe.py collect --headed`, `xvfb-run` on Linux), record `headless` with every fact, and compare like with like |
| `CRLF_BREAKS_SRI` | Committing adopted JS without pinning its line endings | Windows runners check out with `core.autocrlf=true`, rewrite LF to CRLF, and every engine then refuses the file because its SRI hash no longer matches: all 14 Windows environments failed `purify_missing`/`seed_mismatch` while Linux and macOS passed (platform-matrix with js-adopt, #91) | Mark adopted files `-text` in `.gitattributes` (as vendor_sync already does per file); test the vendored bytes against `js.lock.json` on Windows too |
| `TWIN_PARSER_DRIFT` | Letting a Python/Node twin validate input with each language's own parser (`urllib.parse.urlsplit` vs WHATWG `URL`) | The parsers accept and repair different inputs: 27 of 250 URLs split the twins (spaces or `%` in hosts, `HTTP://`, `file:/x`, `file://`, `http:///..`) while both twins claimed "same rules" | One explicit rule with the same regex source in both files, a test that compares the two sources, and a parity run over generated inputs |
| `ROUNDING_TIE_DRIFT` | Rounding with Python `round()` on one side and JS `Math.round` on the other | `round()` ties to even, `Math.round` ties up: a 9/16 visible ratio became 0.562 in Python and 0.563 in Node | Write the same float operations on both sides (`floor(v * 1000 + 0.5) / 1000`) and keep a tie case in the parity test |
| `VIEWPORT_FLOOR_PER_PLATFORM` | Assuming any viewport size can be emulated on every engine and OS | WebKit on Windows (headed) rejects `set_viewport_size` below 250×240 (`Page.set_viewport_size: WebKit on Windows has a minimal viewport of 250x240`), so a width sweep from 240 crashed only there (platform-matrix, #89) | Start width sweeps at 250 or catch the rejection per environment; report a crashed measurement as an annotation naming the environment instead of a bare exit code |
| `EMOJI_VARIATION_SELECTOR` | Matching emoji identities byte for byte | A typed 🗺 (U+1F5FA) misses a registered 🗺️ (U+1F5FA U+FE0F); in flutter_navigation_basic 45 of 210 page emoji carry U+FE0F, and the hand-written trail one-liner exited `unknown page emoji: 🗺` | Compare emoji with U+FE0E/U+FE0F removed on both sides, and reject a registry whose emoji collide after that (`scripts/route_trail.py`) |
| `SYNTHETIC_DOM_CLICK` | Calling `el.click()` (or `dispatchEvent`) on a node that real input cannot reach | The test passes through a path no user can take; under Flutter Web the semantics node is not the hit target, and in `fixtures/interaction.html` the synthetic click succeeds while `locator.click()` is intercepted | Send measured mouse/touch input at the integer centre of the visible target (`press` in `scripts/interaction.mjs` / `.py`) and assert the application state |
| `OVERLAY_HIDES_SEMANTICS` | Reading page semantics (title, header) while a menu/dialog overlay is open | An open Flutter dropdown hides the underlying page's semantics, so the read times out or returns the overlay (flutter_navigation_basic PR #182) | Measure what you need before opening the overlay and record where the value came from (`header_source`) |
| `HANDROLLED_SPEC_CENSUS` | Checking "the spec still registers N tests" by transpiling it and running it against stubs | Extra compiler/vm code that diverges from the runner, and stubs hide real import errors | `npx playwright test --list --reporter=json` lists registered tests per project without launching browsers |
| `REPLACEMENT_STRING_SPECIALS` | Generating code with `String.prototype.replace(old, text)` where `text` contains `$` | `` $` ``, `$&`, `$'` are expanded: a replacement holding a template literal like `` `#/screen${id}$` `` duplicated the prefix (flutter_navigation_basic PR #182) | Pass a function, `s.replace(old, () => text)`, or use `split/join`; in Python `re.sub` has the same issue with `\1`/`\g<0>` |
| `MERGED_SEMANTICS_HIT_AREA` | Pressing the centre of a node that a framework merged around a smaller control | Flutter Web exposed a Door card as one button-role node (~122–130px tall) around a 40px dropdown; the centre press hit the card and the menu never opened in any of 5 projects (flutter_navigation_basic PR #182) | List boxes first (`inventory` in `scripts/interaction.mjs` / `.py`); give the control its own semantics boundary and identifier and press that, not a role-matched container |
| `INFRA_OUTAGE_AS_REGRESSION` | Reading "CI did not run / runners failed to start" as a test or implementation failure | A hosting-side outage (GitHub Actions' backing services) left runs unstarted while the same change passed a local build and browser run (flutter_navigation_basic PR #182) | Classify the failure layer first (`FAILURE_LAYER_FLATTENING`); confirm with a local build or a minimal launch probe with a hard deadline, and record which environment produced the evidence |

## GitHub / CI operation anti-patterns

These come from PR/CI operation rather than from browser runs, but they fail the same way: a green-looking signal that proves nothing. The parent repository owns the shared `gh_ops` helpers that encode several of these guards.

| ID | Anti-pattern | Failure mode | Preferred pattern |
| --- | --- | --- | --- |
| `ZERO_CHECKS_AS_GREEN` | Treating "no check runs" (or only skipped ones) as success | A PR merges before CI ever started | Require a minimum number of check runs, all completed; zero runs is "not finished", never green (`checks-wait --min`, `pr-merge --min-checks`) |
| `FORBIDDEN_AS_UNCONFIGURED` | Reading HTTP 403 as "that feature is not set up" | A permission gap is reported as a missing feature | Report 403 and 404 separately and point at token permissions |
| `MERGE_SHA_AS_HEAD` | Treating `GITHUB_SHA` of a `pull_request` run as the PR head | The tested merge commit is confused with the reviewed head | Show `github.event.pull_request.head.sha` and the tested commit side by side |
| `UNGUARDED_REMOTE_WRITE` | Merging or editing a PR body without re-checking preconditions | A moved head, a duplicated anchor, or a non-clean state gets written anyway | Verify head SHA, anchor uniqueness, and `mergeable_state` first; pin `sha` in the merge request; dry run unless `--write` |
| `UNBOUNDED_TOOL_OUTPUT` | Dumping a whole API response (e.g. every comment of a 43-comment issue, ~92k characters) into a log or an agent context | The tool result is truncated or rejected, and the part that mattered is lost | Print a digest first (index, time, author, size, preview), save the full JSON to a file, and show selected items on demand (`issue-comments --save/--show`) |
| `UNBOUNDED_NETWORK_SUBPROCESS` | Running a network command (`git fetch/push/ls-remote/clone`, `gh`, `urlopen`) from Python without a deadline or with the caller's interactive environment | Python's `subprocess` and `urlopen` wait forever by default; git without cached credentials asks for a username/password (or SSH passphrase) and blocks until the job limit (`fetch-guard`) | Pass `timeout=`; set `GIT_TERMINAL_PROMPT=0`, `GCM_INTERACTIVE=never`, SSH `BatchMode=yes` (keep a caller's `GIT_SSH_COMMAND`), `GH_PROMPT_DISABLED=1`, and `stdin=DEVNULL`; report a timeout as a failure of that step |
| `PARENT_ONLY_KILL` | On timeout, killing only the `git` process while threads drain its pipes | Transport helpers (`git-remote-https`, `ssh`, `upload-pack`) inherit and hold the pipes, so draining can still wait after Git exits; `subprocess.run(timeout=)` can also hit this | Use the canonical parent guard: process-group/tree termination, regular-file output capture, and a shared deadline for attempts and backoff |
| `ENV_MASKED_GUARD_TEST` | Testing a "no prompt" guard in an environment that already sets the guard variables | CI and agent sandboxes often export `GIT_TERMINAL_PROMPT=0`, so a test (and mutation testing) passes even when the code sets nothing (Aoi #67: 2 surviving mutants) | Clear the variables with `monkeypatch.delenv` before asserting the values the code sets |
| `EVIDENCE_ONLY_IN_ARTIFACTS` | Keeping the numbers that explain a failure only in job logs or uploaded artifacts | An agent session (or a reader after the retention period) gets 403/expired downloads and sees only "exit code 1" | Print a one-line `::notice` per lane with the key numbers, so they are readable from check-run annotations; copy decisive numbers into the PR |
| `KNOCK_ON_STEP_FAILURE` | Reporting every failed step of a job as its own problem | A later `if: always()` step fails only because an earlier step did not produce its input (PR #57: `btk-events.jsonl` missing after the pytest step failed) | Find the first failing step; treat missing inputs downstream as consequences until that step is green |

## Observed source patterns in this organization

These are intentionally generalized; copy the lesson, not necessarily the original implementation.

- `mcp-toolcall-lab`: WebKit screenshot commands can produce a PNG even when shutdown reports non-zero. The workflow therefore records the exit status and validates the PNG signature before deciding whether useful evidence exists.
- `browser-test-kit` PR #2: applying Playwright's `iPhone 13` descriptor unchanged to Firefox failed because Firefox does not support the `is_mobile` BrowserContext option. A first fix added an engine-aware compatibility helper and a unit regression test, but the real smoke-test call-site still used the old inline `dict(pw.devices[device_name])` path. CI therefore reproduced the same browser failure. The follow-up routed `browser.new_context()` through the helper. Portable lesson: testing a helper is insufficient if the integration path never calls it.
- `browser-test-kit` PR #4: terminal-browser's interactive lane must inherit terminal stdio. Capturing stdout by default made CI-friendly non-interactive commands look healthy while destroying the TUI proof layer. Structured wrapper events therefore use stderr, and capture is opt-in for non-interactive logging.
- `flutter_navigation_basic`: browser configuration and CI/Docker installation drift was prevented with a regression test that checks Chromium, Firefox, and WebKit parity. Its Playwright config also retains trace on first retry, screenshot only on failure, and video on failure.
- `flutter_navigation_basic`: the Python stdlib CLI wrapper has regression tests for the exact Node Playwright argv shape; this caught the CLI's project-argument parsing behavior.
- `web-ui`: visual regression was deliberately staged: first validate deterministic candidate screenshots, then require baselines after the capture lane is trustworthy.
- `web-ui`: Stagehand v4 is treated as an optional agent-oriented lane beside deterministic Playwright, not as its replacement.
- `markdown`: simple HTML rendering uses `python -m playwright screenshot` rather than writing a custom browser program when the CLI is sufficient. More interactive UI tests use the Python API.
- `markdown`: existing anti-pattern documentation records stale element handles and swallowed cleanup exceptions as browser-debugging hazards.
- `Ironmate`: a small screenshot lane shows the value of parameterizing Playwright version/browser rather than burying them in commands.
- `browser-test-kit` #8: while repository Code security settings were being checked (2026-09-27), a built-in browser that was not signed in to GitHub showed the settings page as "Page not found". A 404 from a permission-checked site is not evidence that the page is absent (`LOGIN_WALL_AS_404`).

- `browser-test-kit` PR #57 (`hit_probe`, `littlebit-approx`): the hit map of a rotated button was correct in Chromium but wrong in Firefox and WebKit. Firefox: the map differed from the exhaustive probe (`CORNER_POKES_BETWEEN_CORNERS`, an algorithm bug that only this engine's rasterization exposed). WebKit: real clicks disagreed with `elementFromPoint` at fractional pixels (`FRACTIONAL_HIT_COORDINATES`). The numbers were found from per-browser `::notice` lines because logs and artifacts could not be downloaded (`EVIDENCE_ONLY_IN_ARTIFACTS`). Portable lesson: a cross-browser hit test needs an exhaustive reference and a real click per engine, at integer coordinates.

## Failure record template

When a new incident teaches something reusable, append a short record:

```text
ID:
Repository / PR:
Runtime: node | python | stagehand | replay | terminal-browser
Browser: chromium | firefox | webkit | replay-chromium | other
Device/profile:
Stage: install | launch | navigate | interact | assert | screenshot | artifact | cleanup
Observed:
Root cause:
Fix:
Regression guard:
Portable lesson:
```

Do not store secrets, transient tokens, or large binary artifacts in this document.

### Recorded failures

```text
ID: CORNER_POKES_BETWEEN_CORNERS
Repository / PR: browser-test-kit #57
Runtime: python
Browser: firefox
Device/profile: desktop 480x320
Stage: assert
Observed: rotated target, refine() map != exhaustive map (exact=False); Chromium and WebKit happened to pass
Root cause: a block was settled from its four agreeing corners while a corner of the target poked in between them
Fix: blocks of 4+ cells next to a differently labelled probe are split (300 random rotated boxes: 62 misses -> 0, about 21% of cells probed)
Regression guard: tests/python/test_hit_probe.py::test_random_rotated_boxes_match_exhaustive, test_old_corner_rule_misses_a_poking_corner; the browser test compares with an exhaustive map on every engine
Portable lesson: sampling is only trustworthy next to a full reference; guard sampled maps with an exhaustive comparison

ID: FRACTIONAL_HIT_COORDINATES
Repository / PR: browser-test-kit #57
Runtime: python
Browser: webkit (also chromium for 0.5 sub-points)
Device/profile: desktop 480x320
Stage: interact
Observed: rotated target, real clicks 8/12 matched elementFromPoint; a 0.5 sub-point offset gave half hits on a plain box in Chromium
Root cause: the grid origin came from a fractional bounding box, and engines round fractional coordinates differently
Fix: integer sub-points and an integer grid origin
Regression guard: tests/python/test_hit_probe_browser.py::test_integer_edges_agree_and_fractional_edges_are_recorded (asserts integers, records fractional results per engine)
Portable lesson: never assert on fractional-pixel hit tests across engines
```

Measured at the plain box's left edge (x = 40) in CI on PR #58, as `elementFromPoint` hit / real click:

| x | Chromium | Firefox | WebKit |
| --- | --- | --- | --- |
| 39 | miss / no click | miss / no click | miss / no click |
| 39.25, 39.5, 39.75 | hit / click | miss / no click | **hit / no click** |
| 40 | hit / click | hit / click | hit / click |

Chromium rounds fractional points up and Firefox down, each consistently; WebKit's `elementFromPoint` and its click disagree with each other, which is why WebKit failed first.
