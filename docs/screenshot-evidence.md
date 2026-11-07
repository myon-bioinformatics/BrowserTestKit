# Screenshot evidence across repositories

<img src="assets/brand/camera-validation.png" alt="Camera Validation: a camera with a validation check" width="156" height="170">


This is the shared screenshot knowledge entry point for browser-test-kit consumers.
Keep portable procedures and validators here; keep routes, selectors, app bootstrap,
and framework scroll adapters in the owning application.

## Existing implementation and limits

- `tools/check_png.py`: stdlib-only PNG signature, IHDR dimensions and terminal
  IEND checks. This is a structural check, not a full image decoder or visual review.
- `tools/check_evidence.py`: requires exact project identities with at least one
  `stage: "complete"` receipt and validates every complete PNG record, including
  retries. Optional recorded dimensions must match the PNG.
- `tests/node/smoke.spec.ts`, `tests/python/test_smoke.py` and
  `.github/workflows/playwright.yml`: working Node/Python reference lanes.
- [btk-event/1](evidence-events.md): optional event stream and evidence board.
  Its stage/status vocabulary is separate from the screenshot receipt's
  `stage: "complete"`; do not interchange those formats.

`tools/check_capture_evidence.py` separately handles multi-image `captures[]`
receipts. It requires a complete named capture set per project, validates every
complete retry, checks checkout SHA, PNG byte counts/hashes and optional dimensions,
and can compare canonical timestamps and run identity. The single-image validator
keeps its existing contract. Neither validator proves the visible screen's meaning;
application assertions and visual review remain necessary.

## Capture, validate, publish

1. Define the required project × scenario identities before execution. Record
   intentional skips and known blockers; they do not count as verified coverage.
2. Build the intended entrypoint and route at an explicit checkout. Record repository,
   reviewed head SHA, tested checkout SHA, CI run ID/attempt, Playwright/runtime version,
   engine/version, device profile and viewport. PR merge SHA and PR head SHA may differ.
3. Open the correct route, complete app bootstrap, perform the scenario, and assert
   its terminal state using current locators. For canonical metadata, compare the
   producer JSON, embedded asset and displayed values; preserve unknown values rather
   than substituting the device clock or a second Git collector.
4. Bring the intended content into the viewport using an operation supported by the
   engine/profile and the application's actual scroll container. A locator existing
   or passing a visibility check alone does not establish that it appears in the PNG.
5. Capture named success screenshots explicitly after the assertions. Failure-only
   screenshot configuration does not produce success evidence. Disable animations
   where appropriate and wait for deterministic state rather than an arbitrary delay.
6. Write receipts only after the required assertions and capture checks succeed.
   Record the reached stage and original error separately on failure; do not mark a
   failure screenshot as a complete success receipt.
7. Validate project/scenario identity, each PNG and any consumer-specific receipt
   fields. Fail a required evidence lane when required success evidence is missing.
   Preserve the original test failure if validation or cleanup also fails.
8. Upload available PNGs, receipts, report, trace/logs and canonical source JSON even
   after failure, using `if: always()`. Check upload results and publish a run/artifact
   link plus retention in the job summary. An uploaded failure bundle proves
   preservation, not success.

Use the existing single-capture validator where its receipt fits:

```sh
python -S tools/check_evidence.py test-results \
  --pattern '**/evidence.json' \
  --expect chromium,firefox,webkit,mobile-webkit
```

This is an example matrix. Use the consumer's measured projects and keep CI browser
installation, Playwright config and expected identities in parity.

For multi-capture receipts such as Flutter's Home/Build diagnostics format:

```sh
python -S tools/check_capture_evidence.py test-results \
  --expect chromium,firefox,webkit,mobile-webkit \
  --captures home.png,build-diagnostics.png \
  --sha "$TESTED_SHA" --canonical build/metadata.json
```

Each receipt requires `project`, `sha`, and a nonempty `captures` list with
`file`, integer `bytes`, and lowercase `sha256` for each PNG. Files must stay
inside the receipt directory, including after symlink resolution. Optional capture
`width`/`height` must match. `--canonical` expects `head.sha`, `head.timestamp`
and `generated_at`; receipts must match them as `sha`, `committed_at` and
`generated_at`. Pass the actual producer JSON path; the path above is illustrative.
Receipt metadata equality does not establish equality with embedded/displayed values.

An explicit `stage` other than `complete` is ignored as incomplete. An absent stage
supports Flutter's legacy success-only receipt: the producer must write it only after
assertions and all captures succeed. Partial receipts from different retries cannot
combine into a successful scenario set. Extra projects/captures are permitted but
their complete receipts and images are also validated.

Use `--run-id "$GITHUB_RUN_ID" --run-attempt "$GITHUB_RUN_ATTEMPT"` when receipts
record these exact strings as `run_id` and `run_attempt`. Without both flags the tool
prints a note and cannot reject old evidence from the same SHA; clean the output
directory before the attempt. Exit codes are 0 for valid evidence, 1 for failed
validation, and 2 for invalid CLI arguments. Output is capped at 20 notes/errors each.

## Receipts, retries and screenshot meaning

| Concern | Rule |
| --- | --- |
| Project and scenario | Check each required identity, not only a total file count. |
| Multiple captures | Declare required scenario file names and validate every image with `check_capture_evidence.py`, or use separate single-image receipts. |
| Retry/run attempt | Keep attempt identity and output directories distinct. Extra retry artifacts must not cause a false count failure, and old runs must not satisfy current-run coverage. |
| Provenance | Record reviewed SHA and tested SHA explicitly; validate source-derived values against the build actually exercised. |
| Image integrity | Check file presence, PNG structure and positive dimensions. When a receipt records size/hash, recompute them from the saved bytes. |
| Semantic proof | Assert expected state and viewport intersection before success capture; inspect images when layout or canvas content matters. A hash alone cannot prove the intended screen is shown. |
| Different screenshots | Different hashes can help detect accidental duplicate captures but do not prove that the correct scenario was captured. |
| Pixel regression | Stabilize capture candidates before making pixel baselines blocking. |
| Mobile/WebKit | Record emulation explicitly. Playwright WebKit evidence is not physical iOS/Safari evidence. |
| Workflow green | A skipped/untriggered capture lane does not establish screenshot coverage. Verify the required evidence job and artifact on the relevant SHA. |

A focused screenshot lane can run automatically on relevant UI/metadata changes.
Keep its triggers explicit and its build/scenarios bounded. Full E2E may remain
manual; the job summary must say which lane ran and which coverage remains unmeasured.

## Where knowledge and binaries live

Commit procedures, incident records, schemas/fixtures and reusable validator code.
Store run-specific screenshots, traces and reports in CI artifacts with declared
retention. A deliberately selected visual baseline may be versioned separately;
do not turn every diagnostic run into repository binary history.

Link source runs and preserve compact textual facts in incident records. CI artifacts
expire, so an artifact URL alone is not durable knowledge.

## Adoption

1. Link this guide from a consumer's testing documentation.
2. Record its required scenarios, engine/profile matrix, entrypoint and receipt mapping.
3. Reuse kit validators with an explicit upstream commit and the consumer's provenance
   convention. Do not copy validators silently or assume the kit is imported everywhere.
4. Keep application interactions local until repeated measured evidence justifies a
   portable adapter. Share a small deterministic fixture and tests when extracting one.
5. Feed new failures back as incident records with source head/run and verification status.

| Consumer | Existing evidence pattern | Next use of this guide |
| --- | --- | --- |
| browser-test-kit | Node/Python matrix and single-capture validator | Shared reference and future portable fixtures. |
| flutter_navigation_basic | PR #128 focused Home/Build diagnostics lane, four projects, local multi-capture receipts | Keep Flutter scrolling/bootstrap local; resolve and measure the current incident before claiming portable interaction. |
| Ironmate | CLI captures of stub and consumer examples | Map named scenarios and metadata to receipts if adopting the stronger contract. |
| mcp-toolcall-lab | Stub/Pages and frontend evidence lanes | Apply the same success/failure/provenance rules to each actual capture lane. |

This table identifies integration points; it does not claim those repositories already
consume this guide or the same receipt schema.

## Incident index

- [Flutter diagnostics #128: scroll and failure preservation](incidents/flutter-diagnostics-128.md)
- [Stable anti-pattern catalogue](antipatterns.md)
- [Reference lane contract](fast-init.md)

## Sealing existing CLI capture lanes

`write_capture_evidence.py` adapts existing PNG capture jobs to multi-image receipts.
Pass an explicit project/bundle identity, required filenames, tested SHA, run ID,
attempt and `--stage complete|failed|skipped`. Clear success images/receipts before
capture and call the writer after application assertions/capture outcome is known.
Only complete bundles pass `check_capture_evidence.py`; failed/skipped receipts are
preserved but never cover required success. The writer checks all required inputs,
records byte/hash/dimensions, and saves a failed receipt on invalid/missing input.
It cannot prove fresh capture or visible semantics merely by hashing a PNG.

`probe_capture_rejections.py` first validates an actual CI bundle, then mutates
isolated temporary copies (missing image, wrong hash, stale run ID, failed receipt)
and requires each to fail the same validator. It never modifies the actual evidence.
Use the same expect/captures/SHA/run/attempt and receipt pattern as the real lane.
A mixed-engine bundle must be named honestly; it is not an individual engine project.

## README GIF evidence (`tools/evidence_gif.py`)

A README wants one picture a reviewer can watch, not a bundle to download. `tools/evidence_gif.py`
records a plan of shots (page × viewport × steps: `click`, `fill`, `select`, `press`, `scroll`, `wait`,
`eval`) in Chromium and writes `docs/evidence/browser.gif` plus `docs/evidence/browser.json`.

- 🖼️ Every shot and every step is one frame, fitted into a 720×480 canvas (multiples of 8) under a 32 px
  caption that Chromium renders in Meiryo with Japanese fallbacks; ✅/⛔ in the caption is the shot status.
- 🎨 One shared palette, no dither, delta frames (the encoding PySaiyanWeBoo's `tool/record_demo.py` uses).
- 🌐 For served (`/…`) shots every request leaving 127.0.0.1 is refused and listed under `external`; it
  does not fail the shot, so the evidence never depends on the network. `--map PREFIX=DIR` answers a pinned
  CDN prefix (jsDelivr web-ui CSS, cdn.plot.ly) from a local checkout so the page is recorded styled.
- ⛔ A page error, console error (with its URL) or HTTP ≥ 400 fails the shot; `"allow": ["text"]` keeps
  errors a fixture raises on purpose (a deliberately wrong SRI hash) under `allowed` instead. Exit 1 still
  writes the GIF so the failure can be seen.
- 🧾 The receipt holds the GIF's sha256, frames, browser version, tested commit, per-shot results and the
  plan itself, so `--plan docs/evidence/browser.json` re-records the same GIF.

It needs Pillow from the caller's environment (this lock stays Pillow-free; the recording test skips without it).
It is review evidence, not a pixel baseline: GIF bytes depend on the Chromium build and fonts.
