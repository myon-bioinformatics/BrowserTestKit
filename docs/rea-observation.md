# REA observation for browser-test-kit

Studied from the public docs of `morluto/rea` (MIT): `docs/browser-observation.md` at commit `b6a0f32b14ff9c6b651a7897aaf9f546bfdda24b` and `docs/browser-scenario-contract.md` at commit `809f3b7b5b6a8cff3a3170542b4af4d67c88b02a`. This note maps those mechanisms onto browser-test-kit. It does not vendor REA, and it does not run REA's server or any reverse-engineering runtime. The page under test is a browser document, not a program database.

Issue 116 is the spec for this probe. Issue 117 still tracks the phases that are not in this increment.

## Mechanisms that transfer

| REA mechanism | What it does there | browser-test-kit use |
| --- | --- | --- |
| Separate passive observation from actions | `inspect_web_page` does not drive the page. `capture_browser_scenario` is a declared action list with its own authority. | `observe`, `locate`, and `act` are different commands. Locate is offline. Act does not reuse an observe ordinal. |
| Do not call the page during observation | Passive capture uses CDP. It does not send a page call, navigate as a generic remote control, or invoke registered tools. | Chromium `observe` sends DOM, Accessibility, Page, and DOMStorage commands. Network and console come from Playwright events (`response`, `requestfailed`, `console`) and record the console argument count only, not its values. It never sends a page call. Firefox and WebKit have no CDP session in Playwright, so observe returns `unavailable` coverage and an empty control list. |
| Evidence names its source and its limit | Every section says where it came from and whether it is complete, filtered, attach-limited, truncated, or not requested. | Each section has `source` and `coverage`. A digest is stored only for `complete` and `complete_within_window`. |
| Missing is not the same | Comparison refuses equality when a section is missing, truncated, or not requested. | `evidence_relation` returns `unknown` unless both sides are complete and both have digests. Two `not_requested` sections are unknown, not same. Attach-limited network and console stay unknown even when the arrays match, because events before attach were not seen. |
| Storage is keys and fingerprints | Values stay redacted. Fingerprints are SHA-256, version 2, with a distinct encoding for unpaired UTF-16 surrogates. | `--include-storage-keys` lists names. `--include-storage-fingerprints` adds the digest and still omits the value. Omitting the flags leaves storage `not_requested`. |
| Loopback endpoints only | HTTP discovery accepts `127.0.0.1` or `[::1]` with an explicit port. Hostnames, credentials, and implicit ports are rejected. | `observe` and `act` accept only those URLs. The route handler aborts any other request and any redirect. |
| Fixed action vocabulary | A scenario names click, wait, and similar steps. Unsupported tags fail before launch. | `observe --step` and `act` share one vocabulary (keyboard, mouse, form, page, and touch). A step Playwright does not implement is `unsupported` on Chromium, Firefox, and WebKit. `h:N` is rejected before the browser starts. |
| Headless by default | Launch mode defaults to headless and owns a temporary profile. | Both commands default to headless so CI can install the shell build only. `--headed` is explicit. |
| One result, two hosts | CLI and the other surface return the same evidence shape. | Python implements observe and act. Node runs that CLI through vendored `vendor/py_launch.cjs --env-var BTK_PYTHON --site` (Node 26+, one synchronous spawn, no resident channel). Score, loopback policy, storage fingerprints, and evidence relation have a single Python implementation. |

Failure stages remain `input`, `launch`, `navigate`, `inspect`, and `cleanup`. A navigation failure is not reported as a bad flag. A cleanup failure does not replace an earlier error.

This repository does not vendor the `cli_args` toolkit. The Python CLI uses argparse with no abbreviations, no shell, exit `0` / `1` / `2`, and `--json`. The Node side is CommonJS. The bridge is the existing `py_launch.cjs` from `myon-bioinformatics/myon-bioinformatics`, copied into `vendor/` and recorded in `vendor.lock.json`. A second bridge was not added. Python does not call back into Node during that spawn.

The launcher is vendored from `myon-bioinformatics/myon-bioinformatics` at first-parent merge commit `384f5dd532076062f7833038764e9a62322c387c` (`py_launch.cjs`), with matching blob and SHA-256 provenance. Its CLI form `--env-var BTK_PYTHON --site` replaces the former `btk.cjs` wrapper: `--site` drops `-S` so site-packages (Playwright) is importable. Its asynchronous `main` forwards repeated signals while the child is alive. Node remains 26 or later with no upper bound.

## Where Playwright still runs code

Navigation loads the document, so the page's own scripts run. That is ordinary browsing, recorded as `navigation_runs_page_scripts`. Observe does not add a call of its own.

Playwright installs its driver in an isolated world whenever it owns the browser. This tool does not call that driver during observe. The CDP method list on the result is the list this command sent.

`act` and `observe --step` ask Playwright to resolve one locator per action. That selector engine runs in the page. The result sets `page_js_called` when a locator method ran. `wait:timeout:N` and page navigation do not resolve a locator, so `page_js_called` stays false. Caller-supplied script text is not accepted as an action. Typed values are redacted; `env:NAME` and `file:PATH` keep a secret out of the command string that the result echoes. `Primary` is sent to Playwright as `ControlOrMeta`.

Service workers are blocked and redirects are aborted. Those are bounds on the session, not a claim that the page was unable to run its own scripts after a successful navigation.

## Mechanisms that do not transfer

- REA's server, its tool catalog, generic CDP passthrough, and tool invocation.
- Ghidra, Hopper, IDA, and every other reverse-engineering runtime. Issue 116 keeps those out.
- Electron `file://` observation, Node Inspector, and .NET targets.
- Holding a DOM object across calls. `h:N` is an ordinal in one snapshot file. Act queries `id:`, `testid:`, `role:`, or `css:` again.
- Screenshot pixel comparison and its 256 MiB working-memory budget. Not this increment.
- Full scenario comparison (step alignment, history, event families). `evidence_relation` is only the missing-is-not-same rule.
- Treating WebKit as Safari, a headless phone profile as a device, or one engine as the whole matrix.

## Locator ranking that stays

`locate` still ranks controls by the longest common substring divided by the shorter UTF-16 length, after simple Unicode `lower`. The default threshold is `1` (containment). `--unique` fails when the top score is shared. That ranking is local arithmetic on the snapshot. It does not call the page. Addresses prefer `id:`, then `testid:`, then `role:`, then `css:`.

## Snapshot compare

`observe --step` repeats that snapshot once before the steps and once after each step, in the same browser, and writes one `btk-page-observe-run/1` file. Each observation is still `btk-page-observe/1`. Each pair is `btk-page-compare/1` from the same `compare` function. `--after-action` (and `--after-click`, an alias for `click:`) is one step from that same performer, written to two snapshot files. `scroll:DX,DY` is one `mouse.wheel` of that delta. `limits.scroll_y` is the CSS visual viewport `pageY` from `Page.getLayoutMetrics` after the wheel settles, sent on the observe CDP session. `scroll:LOCATOR` and `scroll-into:LOCATOR` are `scroll_into_view_if_needed`.

The snapshot after a step has `step` (index, action, target, URL before and after). A click sets `limits.click_runs_page_scripts`. Hover sets `hover_may_trigger_css`. Scroll sets `scroll_may_run_page_scripts`. Network and console on a later snapshot are the events since the previous snapshot. This is not passive observation: a locator action runs page scripts.

JavaScript dialogs are recorded and dismissed unless `--dialog-policy accept` is set. New pages opened by the step are recorded and not entered. iPhone and Pixel profiles are labeled Playwright device emulation (`real_device: false`). `--viewport` is a size, not a device. `--touch` sets `hasTouch` without naming a phone.

`compare` reads two `btk-page-observe/1` files and runs no browser. It reuses `evidence_relation`, so a section is `same` only when both sides are complete and their digests match. Shadow-root counts and child-frame and element totals are reported as left, right, and delta. A count is `null` when that side's section was not complete. Environment differences (OS, browser, profile, headless) are reported but do not change the section relations.

## Touch gestures

`tap` is Playwright `locator.tap`. It is supported on Chromium, Firefox, and WebKit only when the context `hasTouch` is true. A mouse click is not substituted.

`long-press`, `swipe`, and `touch-scroll` also need `hasTouch`. Chromium performs them. Firefox and WebKit stay `unsupported`: Playwright's Firefox protocol only has `Page.dispatchTapEvent`, and WebKit only has `Input.dispatchTapEvent`. Both are a point tap, with no hold, no move, and no touch scroll, and neither engine opens a CDP Input session. A mouse event is not substituted. A Chromium step that does run still carries `unsupported_reason` for those two engines, and the `support` map names each engine.

Deterministic logic borrowed from other code, with no model call:

| Source | License | What was used |
| --- | --- | --- |
| Stagehand understudy (`browserbase/stagehand`) | MIT | Do not dispatch when the element has no positive box after scrolling it into view. Interpolate a drag as `t = i / steps`, about one point every 20 CSS pixels, at least 2 points and at most 30. |
| Chromium DevTools `Input` | BSD-3-Clause, Chromium | `Input.synthesizeTapGesture` with `gestureSourceType: "touch"` and a duration for long-press. `Input.dispatchTouchEvent` (`touchStart`, `touchMove`, `touchEnd`) for swipe and touch-scroll. Playwright's own Chromium tap is that same touch pair with no gap; the hold and the move are the part it does not expose. A short dwell before `touchEnd` keeps the browser from flinging past the finger travel. |
| Playwright | Apache-2.0 | `locator.tap`, `mouse.wheel`, `scroll_into_view_if_needed`, and `bounding_box` wherever those already do the job. |

Not ported: Stagehand's model tools, its `Runtime.callFunctionOn` scroll, and its mouse-wheel scroll. `scroll:DX,DY` stays one Playwright `mouse.wheel`. `Input.synthesizeScrollGesture` with `gestureSourceType: "touch"` was measured on headless Chromium and did not change `cssVisualViewport.pageY`. The same call without that source scrolled as a mouse wheel, so it is not used as touch-scroll.

`long-press:LOCATOR` holds for 700 milliseconds. `long-press:LOCATOR=MS` is from 500 to 3000. `swipe:LOCATOR` moves the finger `160,0`. `swipe:LOCATOR=DX,DY` is that finger movement. `touch-scroll:LOCATOR` scrolls the content by `0,400`, the same sign as `mouse.wheel`; the finger moves the opposite way. A stroke that does not fit in the viewport is repeated from the element. Later strokes do not scroll that element back into view, so a document scroll is not undone. `limits.scroll_y` after `touch-scroll` is the same `Page.getLayoutMetrics` value as after the wheel.

## Child frames, step files, and settle

`frames.items` lists child frames in document order. Each row is `name`, `url`, and `origin` from `Page.getFrameTree`. Frame ids are omitted because they change per load. `contents` stays `not_entered`: the child document is not read. Nested frames are listed after their parent, still without entering them. The same list was added on `feat/page-inspect-shadow-roots` and copied here.

`observe --step --out` remains one `btk-page-observe-run/1` document: `observations` are `btk-page-observe/1`, `comparisons` are `btk-page-compare/1`, and `steps` are the action records. `--out-dir` also writes those observation objects as `00-initial.json` and `NN-action.json`. The files are slices of `observations`, not a second schema. `btk-page-observe-steps/1` is not used: that envelope dropped the comparisons and the step records that this run already keeps. `compare` can read any two slice files because each one is a normal observe snapshot.

`settle` is Stagehand's quiet wait (`packages/integrations/core/src/facade/runtime.ts`, MIT, no model call): readyState past `loading`, then 400 ms with no DOM mutation, checked about every 100 ms. The timeout ends the wait with `quiet: false` and status `done`. Chromium watches `Page.lifecycleEvent` plus `DOM.attributeModified`, `DOM.characterDataModified`, `DOM.childNodeInserted`, `DOM.childNodeRemoved`, and `DOM.documentUpdated` on the CDP session. Playwright's `wait_for_load_state("domcontentloaded")` supplies the load state it already tracked. No `page.evaluate` and no `Runtime.evaluate`. Firefox and WebKit are `unsupported` because they have no CDP DOM mutation stream; a page script is not substituted, and the no-evaluate guard on `tools/btk.py` stays intact. A Chromium step still carries the per-engine `support` map.

`btk fetch` (`python -S tools/btk.py fetch`) and `parts.lock.json` (`btk-parts-lock/1`) are the integrity-checked single-member extract from `feat/page-inspect-shadow-roots`. Playwright in that lock is the lower bound `>=1.64.1`, matching `pyproject.toml`, with no upper bound. `@playwright/test` in `package.json` uses the same lower bound. The Node part stays an integrity-checked tarball with minimum 24.0.0 and no maximum.

## Secret redaction

Typed `env:NAME` and `file:PATH` values are still removed in full, including percent-encoded forms, before anything else. Exception text and JSON output then pass through Stagehand's `sanitizeErrorMessage` (`packages/integrations/core/src/harness/redact.ts`, MIT, Copyright 2024 Browserbase Inc.): query parameters named `signingKey`, `apiKey`, `api_key`, `token`, or `key`; OpenAI-style `sk-` keys; Browserbase `bb_live_` / `bb_test_` keys; Google `AIza` keys; and `Bearer` tokens. The patterns keep the short prefix Stagehand keeps and replace the rest with `[redacted]`.

## Deferred on issue 117

Child frames and shadow roots as their own address spaces. Observe counts child frames and author open plus closed shadow roots (pierced DOM, `shadow_roots` section, `contents: not_entered`, `user_agent` for Chromium built-in roots); it does not enter either and does not read shadow attributes. macOS, Windows, Linux, and Android Chrome or WebView as a recorded matrix. Branded channels as a matrix. A Stagehand caller. A headed lane in CI. An agent transport after these envelopes stay stable. Screenshot comparison and full scenario diffs.

Stages 3 and 4 of human input stay on issue 117: an OS shortcut behavior matrix beyond `ControlOrMeta`, display settings (`color-scheme`, `reduced-motion`, locale, timezone), and offline, throttling, and permissions.

### URL evidence privacy

Navigation uses the original URL, including query and fragment. Saved URL evidence
keeps only scheme, host, port and path: userinfo, query and fragment are omitted
from initial observations, frames, scripts, resources, network events, step results,
act results and compare URLs. The Python and Node entry points share this policy.
Navigation error call logs also omit URL values. Equal evidence URLs do not prove
that no navigation occurred: query-only or fragment-only changes are intentionally
indistinguishable in the URL evidence.

New section digests are computed from sanitized URLs. Compare sanitizes legacy
top-level URLs before emitting them, but preserves existing section digests rather
than rewriting historical evidence; recapture both sides to compare under the new
policy. Old snapshot files are not modified. This URL policy does not redact values
that a page itself displays as control text or its title.
## Platform and fault scenarios (PR #123)

The `btk.py scenario` command is a bounded, **active** Playwright fixture
experiment that exercises OS/browser display options, platform shortcuts, offline →
recovery, HTTP error/latency → recovery, permission grant/clear and file upload/download
cleanup. It uses `assemble_observe` and `_section` from the same CLI and emits
`btk-page-observe/1`; DOM, AX, and storage not measured here remain `not_requested`.
The existing `compare` reads two scenario snapshots and applies `evidence_relation` to
`scenario`, `environment_settings`, and `network` sections. A missing or truncated
section is `unknown`, not `same`. See [detailed contract](page-inspect-platform-scenarios.md)
for loopback/origin controls, event caps, short-circuit behaviors and privacy limits.
Node 26 is selected by `.nvmrc`, but existing CI workflows are not modified.

### Page discovery limits

`btk.py pages` walks same-origin routes in breadth-first DOM link order and
assigns emoji in visit order. It reads at most 256 href attributes per page and
2,048 across the entire exploration, using indexed locators rather than
materializing every anchor. The queue and seen-route set contain at most
`--max-pages` routes (including already visited routes). External links and
repeated routes consume the inspection budget too. Queries, fragments and link
text are not recorded.

The `btk-page-routes/1` result reports `links_inspected`, `link_limits` and
`queue_peak` (total retained queue entries, including visited entries).
`truncated` is true when a link budget omits links, a new route cannot be queued,
or queued routes remain unvisited. Counts and routes describe only the inspected
prefix when truncated; they do not claim a complete site inventory.
