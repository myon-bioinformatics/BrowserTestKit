# Platform matrix application adapters

`platform_probe.py collect` still defaults to the local SRI fixture. Its OS × engine ×
profile × mode workflow remains 42 environments (including optional Chrome/Edge).
No published-site request is added to that fixture lane.

The application lane is `.github/workflows/platform-apps.yml`. It observes the
published Flutter app separately on Chromium/Firefox/WebKit desktop plus Chromium
Pixel and WebKit iPhone **emulation**. These are not physical phones, and Playwright
WebKit is not Safari. Pull requests that touch adapters run it on Linux; a manual
run (`workflow_dispatch`) or the weekly schedule runs the same five browser/profile
lanes on Linux, macOS and Windows (15 jobs). The adapters cover representative pages:

| Adapter | Route | Layout scope | Real coordinate input and postcondition |
| --- | --- | --- | --- |
| `flutter-home` | `/` 🏠 | Shared title, Home, favorite and menu hit regions | Favorite text changes from `Favorite this page` to `Remove favorite` |
| `flutter-hub` | `/hub` 🗺️ | Same shared header | Same favorite transition |
| `flutter-clipboard-shelf` | `/core/clipboard-shelf` 📋 | Same shared header | Same favorite transition |
| `flutter-now-timeline` | `/tools/time/now-timeline` 🕒 | Same shared header | Same favorite transition |
| `flutter-photo-studio` | `/tools/media/photo-studio` 🖼️ | Same shared header | Same favorite transition |
| `flutter-http-request-draft` | `/tools/http/request-draft` 🌐 | Same shared header | Same favorite transition |
| `flutter-ui-showcase` | `/examples/ui-showcase` 🎨 | Same shared header | Same favorite transition |
| `flutter-screen1` | `/screen1` 🌍 (catalogue) | Same shared header | Same favorite transition |
| `flutter-counter` | `/examples/counter-playground` 👾 | Shared regions plus Increase/Decrease body buttons | Press Increase; Reset changes from disabled to enabled |

Routes and emojis follow flutter_navigation_basic's generated page identities
(`lib/shared/navigation/page_identities.g.dart`). Coordinates (📍) is left out because
it asks for geolocation permission. Screenshots are stored per page and environment
(`app-results/<adapter>/<OS>-<browser>-<profile>/`).

After the screenshot comparison (`tools/app_comparison.py` with the pinned Ironmate),
the report job adds web-ui's `tool/capture_matrix.py` at a pinned merged commit:
`screenshots/viewer.html` shows one section per page, opened from its emoji, with each
environment's tile, pass/fail and deployed build. Update the web-ui SHA together with its
test when the viewer changes.

For a quick review the report job also writes `platform-app-digest`, a separate small
artifact: `digest.html` is one self-contained page (every page x environment, thumbnail as
a data URI, pass/fail, failure reasons, deployed build), with thumbnails starting at 64x128
px and halved in each direction (a quarter of the pixels) until the page fits 1 MB, and
`summary.md`, a short table that also goes to the job summary. The full matrix artifact
stays for detailed checks. GitHub comment posting is not part of this workflow.

This is deliberately a selected-control contract, not a guarantee for every screen,
all painted text, or every app feature. Flutter's invisible semantics labels do not
have the same glyph metrics as its canvas. Their `scrollWidth` is not treated as
painted-text clipping; screenshots support visual inspection. No production Dart
changes, forced application clicks, or synthetic application state updates are used.
Flutter's opt-in accessibility placeholder is activated once: a centre tap/click
for Android's full-screen placeholder, or a DOM click for the off-screen desktop
placeholder. Polling never repeatedly dispatches activation events.
Application actions use measured integer centres with mouse or touch input and must
satisfy the app-specific postcondition. Flutter bypasses the ordinary DOM
`elementFromPoint` check because its semantics forward input to the canvas; its
real-input postcondition still fails when an overlay blocks the action.

```sh
python tools/platform_probe.py collect --browser chromium --profile desktop \
  --adapter adapters/flutter-counter.json --widths 320 384 \
  --out app-results --evidence-dir app-results/counter/screenshots
python -S tools/platform_probe.py merge app-results --out app-matrix/dataset.json
uv run --group pages python tools/platform_probe.py render app-matrix/dataset.json --out app-matrix/index.html
```

`--url https://.../#/route` overrides the adapter URL, for example for a locally
served pinned app build. Only HTTP(S) targets without embedded credentials are
accepted. Point adapters only at test actions that are appropriate to execute.
Use a distinct evidence directory per environment/adapter.

## Adapter contract

An adapter is trusted JSON with `schema: btk-platform-adapter/1`, a stable `id`, a
`url`, disjoint CSS `elements`, and a `click` object. Every layout element must be
unique, have a nonzero visible box, fit inside the viewport, and not intersect any
other listed element. Ordinary DOM controls must also be unclipped and unobscured.
Do not list a container together with its child (intentional nesting is not overlap).
The rules are property checks rather than cross-engine pixel equality.

`click.target` must be listed in `elements`. `click.observe`, `attribute`, `before`
and `after` describe an application-state transition. `attribute: textContent`
observes exact text; other values observe a DOM attribute. JSON `null` means an
absent attribute. Before and after must differ. Missing or ambiguous elements fail
closed. Optional `bindings` map alphanumeric names to `{selector, text}` and resolve
exact visible semantics text to `[data-btk-probe="name"]` markers; markers are
refreshed after layout changes. This supports Flutter controls without stable ids.
`flutter_semantics: true` waits for Flutter's accessibility tree before binding.
Optional `provenance: {path, field}` names a same-site JSON file and the key path of
the build identity in it; the Flutter adapters read
`assets/assets/diagnostics/build_metadata.json` → `repository.revision.sha`, the commit
the Pages deployment was built from. The record keeps it as
`target.provenance` (`url`, `status`, `value`, `error`) and the summary prints
`build=<sha>`. It is reported, never gating: a missing file or field gives
`value: null` with the reason. Only relative paths without `..` are accepted.

Browser/OS facts live in `tools/platform_facts.js` and are used by both lanes:
screen, window, viewport, DPR, safe areas, input/media preferences, platform and font
widths. Two font facts answer "does this page really show Meiryo here": `fonts.body_family` is the
stack the page declares (the body's computed `font-family`), and `fonts.meiryo` is whether the OS has
Meiryo (`"Meiryo", monospace` and `"Meiryo", serif` measure the same only when Meiryo itself draws the text;
`null` when the two generic fallbacks cannot be told apart). The summary prints `meiryo=yes|no|unknown`.
It is reported, never gating: most Linux and macOS machines lack Meiryo, and no font file can be bundled.
What the generated files ask for is checked offline, by exit code:
`python -S tools/check_font_family.py out/` fails on a `font-family`, `font:` shorthand or Plotly `family`
that does not start with Meiryo (or `inherit`, `var(...)`, a monospace stack) and on a Plotly figure with no family. A shadow-isolated measuring probe avoids application CSS contaminating the
classic scrollbar measurement. Fixture-only library/SRI checks stay in the fixture.

## Width search, evidence and failures

The fixture retains its original monotonic binary search. Application layouts can
have passing islands around responsive breakpoints, so adapters scan widths in 8 px
steps from 256px to the initial viewport width, stopping at the first layout pass.
### Pixel values: an 8 px default, not a rule

Widths this kit picks on its own default to multiples of 8: the search grid
(`--width-step 8` from 256px, the first grid width above Windows WebKit's 250px floor)
and the click widths 320/384. That keeps runs, Ironmate tiles (160x320, 8px gap) and
the web-ui viewer on one grid, halves stay integral, and it matches Material's 8 dp
grid (its 4 dp half-step and the Material 3 window classes at 600/840 are on it too).

It is a convenience, so every value can be switched when the question needs it:

| Question | Use |
| --- | --- |
| Compare runs, tools and environments side by side | defaults: `--width-step 8`, `--widths 320 384` |
| Does a real device width work? | device widths, e.g. `--widths 390 412` (any width from 250) |
| Where exactly does a breakpoint flip? | `--width-step 1` (search from 250) and widths either side, e.g. `--widths 419 420` |

flutter_navigation_basic itself branches at 420, 560, 620, 720, 760, 900, 1000 and
1180 px; half of those (420, 620, 900, 1180) are not multiples of 8, so the 8 px grid
reports only the first passing sample and can miss a passing interval between grid
points. Use step 1 when an integer-pixel edge matters. Device
profiles always keep their real viewports (iPhone 390, Pixel 412). Manual runs of the
workflow take `width_step` and `widths` inputs; pull requests and the weekly schedule
use the defaults. Records keep the grid used in `width_search.step` and `low`. The screenshot comparison
and digest use the last requested click width (384px by default), so custom device
widths do not leave the report searching for an uncaptured 384px image.

`min_ok_width` is the **first sampled passing width**, not an exact minimum for an
8px search. It is **layout-only**, scoped to those bounds, the search grid and the current
viewport height, with the tested failures retained in `width_search.observations`. A
result at the search floor (256px, or 250px with step 1) is lower-bound-censored: it says
nothing about narrower widths (Windows WebKit refuses viewports below 250). The first passing width and explicit
`--widths` also receive independent real clicks; a click failure
makes the record fail, without relabelling the layout threshold as a click threshold.
Each click sample uses a **fresh browser context**, isolating cookies and persistent
browser storage. Records contain per-width boxes, failure reasons and click before/
after values. Screenshots capture initial and tested widths.

The facts schema remains `btk-platform-facts/1` with additive target/sample fields;
old fixture records are still readable. Environment ids remain unchanged. File and
merge keys additionally include the adapter config hash and URL, so multiple pages
on one environment cannot overwrite each other. The viewer has a Target axis and
shows per-width results. The config snapshot and observation time accompany live
observations. A public deployment can change between runs: it is not a pinned app
artifact. Network failures or missing app controls are errors, never a passing
measurement. The application workflow's failing collect job remains failed even
when the report job can render other completed observations.

Offline regressions exercise a different DOM without fixture hooks, nonmonotonic
width rules, touch and mouse postconditions, persistent-storage isolation, overlap,
occlusion and missing elements. They use a loopback server and no public network.

## Screenshot comparison artifact

The report job also checks out a full-commit-pinned Ironmate and calls
`tools/app_comparison.py`. `platform-app-matrix/screenshots/` contains `sheet.png`,
a labeled `index.html`, `tiles/` and `report.json`. Open `screenshots/index.html` for
the grid and independent capture evidence, or the parent `index.html` for all
platform facts and per-width layout/click details. Capture job failures remain failures.

```sh
python -S tools/app_comparison.py app-results --ironmate ../Ironmate \
  --runners-json '["ubuntu-24.04","macos-latest","windows-latest"]' \
  --out app-matrix/screenshots
```

Use a trusted Ironmate checkout containing the planned-axis `browser_matrix.build`
API. The report reuses Ironmate's PNG decoding, DPR-normalized tiles and existing
`image_compare.metrics` MAE/RMSE. The OS plan and nine adapter files define expected
coverage, so even a completely absent page or environment appears as missing. No
PNG at all is an error. Output must be new/empty and must not be a symlink; a report
rerun uses a fresh output directory. Parent directories are trusted, matching Ironmate.

The JSON retains original screenshot hashes/sizes, tile crop/pad rows, independent
observation verdicts, per-shot layout/click evidence, DPR, browser version, capture
time and deployment provenance. A missing observation or unknown/different build
is explicit. A pixel score never changes an observation verdict. Missing PNGs are
coverage gaps, not measured UI failures; a passing observation does not certify the
whole UI. OS fonts, engine rendering, DPR, different viewport heights, animation and
capture timing can all cause differences. Width fitting crops/pads content, and
cropped content is outside the metric. Inspect the original PNG and relevant
layout/click failure before attributing a difference to an application defect.

## Select a comparison or digest plan

```sh
python -S tools/app_comparison.py app-results --runners-json '["ubuntu-24.04"]' --page 'portal-*' --env '*-chromium-*' --list
uv run --group pages python tools/app_digest.py app-results --runners-json '["ubuntu-24.04"]' --page 'flutter-home' --env 'Linux-*' --ironmate .ironmate --out digest
```

Both commands accept repeatable case-sensitive `--page` adapter filename-stem
and `--env` planned-environment globs. Rules are ORed within each group; groups
intersect. The default page rule remains `flutter-*`; explicit rules allow
other valid adapters. Repeated matches do not duplicate targets, while duplicate
selected adapter ids are errors. Missing planned cells remain missing, and the
coverage denominator is the selected plan. Empty selections are input errors.

`--list` lists the selected pages/environments without importing Ironmate,
loading result JSON or decoding PNGs. It needs a results directory, adapters and
runner plan, but neither `--ironmate` nor `--out`; either output argument with
`--list` is rejected. Generation keeps the existing strict JSON result loader,
selects observation records before joining them, and filters screenshots before
duplicate detection or decoding. Unrelated malformed JSON still causes the
existing input error; page filtering is not permission to hide invalid receipts.

Generation requires Ironmate's page/environment-aware `find_shots` API. The
explicit workflow checkout now uses merged commit
`e77d2a3f7b66f37c899832b261450b2bcd29f3dc`; use that revision for a local `.ironmate`
checkout too. Vendor locks, workflow permissions and the web-ui source pin are
unchanged. The selected comparison retains platform verdicts and provenance.

Validation: 64 focused tests and actionlint passed. A real stdlib-only integration
with that Ironmate implementation selected a non-Flutter adapter and one Linux
lane, produced comparison and digest coverage of 1/1, and ignored corrupt PNGs
and duplicate screenshot keys outside the selected plan.
