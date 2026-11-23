# Platform and fault probe contract

This is the continuation of PR 123. General locator actions, multi-step parsing,
passive CDP collection and the compare command remain owned by PRs 121, 122,
124 and 125. No action parser or comparison implementation is copied here.
The existing `btk.py scenario` adapter still returns
`btk-page-observe/1` through `assemble_observe`, `_section` and
`evidence_relation`. Its local page is an active, versioned **test fixture**,
not an arbitrary application probe or a real device.

## Node 26

The workspace `.nvmrc` selects major 26. With nvm, run `nvm install` followed
by `nvm use`. The `vendor/py_launch.cjs --env-var BTK_PYTHON --site tools/btk.py` launch
requires Node 26 or newer. This change does not edit workflows, change their
existing newer Node pins, add a Node download during tests, or change permissions.
A local Node 26 runtime can execute the dependency-free fixture contract suite:

```sh
node --version
node --test tests/node-unit/page_inspect_scenario_fixture.test.mjs
```

The version reported by the executable is the runtime actually tested; a
configuration file does not establish that every CI job ran on that version.
The browserless Playwright lane also executes these tests with its configured
Node version. Real CLI browser checks run in the existing Python browser lane.

## Exact origin and reversible faults

Every HTTP request must match the initial fixture's **scheme, literal loopback
address and explicit port**. Another localhost service is not in scope, even
when it also uses 127.0.0.1. Redirects and WebSockets are blocked, and service
workers are disabled. This is a controlled browser test, not an OS-level
network sandbox; it makes no claim about WebRTC or other host processes.

For `offline`, `http-error` and `latency`, one browser session now measures:

```
baseline (online) -> fault (offline / HTTP 503 / delay) -> recovery (online)
```

A `recovered` boolean is backed by a new successful request, not merely by a
call to `set_offline(False)`. Each probe has an incrementing completion token,
so the previous phase's DOM attribute cannot be reused as fresh evidence.
`phases` and `verdict` distinguish a captured mismatch from a passing scenario.
The route guard rejects browser-offline traffic as well, since interception
and APIRequestContext fetches are not an OS network switch.

The `network` section contains only HTTP errors and failed requests. Each
entry has a fixed safe path category, status (`null` for transport failure)
and phase. Capture starts before navigation and ends after the last fixture
probe. It does not claim that no events could occur after this window. Exactly
30 events is complete; a 31st marks `truncated`, records the omitted count and
omits the digest. A frozen result cannot be mutated by later callbacks.
`evidence_relation` returns `unknown` for truncated or unavailable coverage.

The requested delay and a stable boolean are in the section. Actual elapsed
milliseconds live under top-level `measurements`, outside section digests;
clock jitter therefore does not manufacture a semantic difference.

## Permissions: baseline, requested, reset

Notification and geolocation permissions are queried **separately** with the
fixture's Permissions API. No permission prompt is requested and no position,
camera image or microphone data is acquired. The previous fixture's side
effect of requesting a geolocation result while checking notifications is gone.

The evidence records `states.baseline`, `states.configured`, `states.reset`,
`matches_requested` and `reset_matches_baseline`. `clear` restores browser
defaults; it is not synonymous with `deny`. A failed grant is a `mismatch`, not
a pass. Unsupported permission queries have unavailable coverage, null match
fields and no digest. Unexpected driver or fixture errors fail the command
rather than being relabeled as unsupported.

Geolocation grant and reset are asserted on real engines in the browser tests;
Chromium is required to provide the capability, not silently skipped. Other
engines must either pass or explicitly return unavailable evidence. This does
not replace Windows/macOS runs: all recorded OS values are actual host values,
not a label inferred from a device profile.

## Files, metadata and environment equality

Upload still uses a fixed 5-byte in-memory file; the test now removes the
selection and verifies `cleared`. Download checks completion and the fixed
fixture name, then calls `delete` on the browser-owned temporary artifact.
The browser may create temporary download bytes, but the tool does not read,
export or persist those bytes to a caller-selected host path.

The page must declare the expected fixture version. State attributes are
allowlisted; missing or arbitrary values fail without being echoed. Locale
and timezone readbacks report only the requested known value or a difference
marker. Raw exception messages, query strings, arbitrary file paths, form
values and secret attributes are not emitted.

`environment_settings` now includes actual host OS, browser version, channel,
headless mode and device profile in its digest, in addition to display settings.
`has_touch` and `viewport_source` are recorded in both environment outputs from
the context configuration and viewport selection used for the run. Desktop
headed mode without `--viewport` uses the native window (`headed_window`): no
scale override is sent, and `device_scale_factor` is null (not configured).
With `--viewport`, viewport emulation and the requested scale are enabled, and
the source is `flag`. Phone defaults use `device_descriptor` and retain touch
emulation even when the viewport is overridden. These are emulated profiles.
Thus section comparison cannot imply identical environments merely because
the viewport matches. The offline compare command also lists the individual environment settings that differ.

Exit zero and top-level `ok` mean evidence collection succeeded. They do not
mean every observed platform expectation matched; inspect `coverage`,
`verdict` and match fields. Passive `observe` remains separate and untouched.

## Closing record for issue #117 (2026-11-22)

Issue #117 is closed. This section records what was checked and what stays
undone in this repository, each with a short name saying what it asks.

Checked on main `f974ca0`:

| Question | Evidence |
| --- | --- |
| `url-value-leak`: do query and fragment values stay out of saved JSON after the first load and after `goto`? | 16 passed through both the Python and the Node entry point (Node 26.13.1, Chrome for Testing 153, Linux, headless): `pytest tests/python/test_page_inspect.py -k "evidence_url or legacy_urls or navigation_error or initial_goto or act_goto or colon_step or navigation_validation"` |
| `frame-shadow-listing`: are child frames and shadow roots counted and listed in document order? | 10 passed in the same environment: `pytest tests/python/test_page_inspect.py -k "frame or shadow"` |
| `frame-shadow-entry`: are same-process child frames and open shadow roots entered (element count and controls), and does an action inside a frame find a control again after a redraw? | Covered by the same 10 (2026-11-23), with the unit cases `frame_rows_keep_name_url_origin` and `counts_open_closed_and_nested_roots`. `frame:N>ADDRESS` reaches a control inside a frame; frames in another process and closed roots stay `not_entered` |
| `fault-permission-file`: HTTP error against connection failure, permission grant and reset, upload, download and cleanup | CI run [44999087770](https://github.com/myon-bioinformatics/BrowserTestKit/actions/runs/44999087770): the Chromium job and the Firefox and WebKit job both succeeded on Linux |

A local rerun of `tests/python/test_page_inspect_scenarios.py` finished 48 passed and
25 not started. `btk.py scenario` launches Playwright's own build and does not go
through `find_browser`, so a machine without that build cannot start those cases.
That is a limit of the machine, not a result for the cases.

Not planned until the stated need appears:

| Name | What it asks | Reopen when |
| --- | --- | --- |
| `real-os-matrix` | The same faults, permissions and files on Windows and macOS, beyond the Chromium offline and recovery already recorded, with real devices kept apart from emulation | a difference between operating systems is reported, or a release claims support for one |
| `android-lane` | The same CLI on Android Chrome or WebView | an Android device or emulator is available to the runner |
| `headed-lane` | A headed survey lane next to the headless default | a finding depends on the real window frame |
| `stagehand-lane` | An optional Stagehand lane calling the same operations | a consumer asks to drive these operations through Stagehand |
| `agent-transport` | A protocol transport on top of the CLI envelopes | the envelopes have stayed unchanged across two releases and a caller needs more than one command per process |

`check-png` now reports a missing or invalid file as one line on stderr with exit 1.
It was the only one of 19 `btk.py` entry points whose bad input ended in a traceback.
About 53 options across 11 subcommands still have no help text; add it when the
subcommand is next edited.
