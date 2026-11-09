# Shared browser execution ownership

`browser_execution.mjs` extracts the generic machinery first exercised by Flutter
PR #206. It imports Node standard libraries and the existing `interaction.mjs`
real-pointer helper; it does not launch/install a browser or add a dependency.

```js
import {workerBudget, explore} from './tools/browser_execution.mjs';
// Playwright config: workers: workerBudget({override: process.env.MY_WORKERS})
const receipt = await explore(page, {
  route: '/sample', seed: 'recorded-seed', steps: 3,
  buttonName: /^(Add|Reset)$/,
  values: ['', '0', '日本語🙂'],
  ready: async page => { /* application readiness assertion */ },
});
```

Automatic worker count is min(4, available CPUs, reported/constrained memory per
2 GiB), minimum one. An explicit override accepts 1..32. Environment variable
names belong to callers; Playwright's own `--workers` still overrides its config.
Healthy worker/browser reuse, context teardown, failure replacement and queueing
remain Playwright responsibilities. No duplicate process supervisor is provided.

Exploration performs at most 20 operations, re-inventories visible controls each
step, and derives choices from seed + route + step independently of worker order.
`buttonName` defaults to no button presses. Only the caller's allowed buttons are
pressed. File/password inputs are excluded; unsupported native input types are
recorded as skipped. The caller supplies input values, including any xprobe
corpus it chooses to adapt. Numeric fields retain the initial conservative zero
input. No exhaustive coverage or business-output correctness is implied.

Receipts contain seed, route, step budget, action/value/label/URL and uncaught page
errors. `ExplorationError` retains the original cause and a partial `.receipt`;
listeners are removed on success and failure. Applications own screenshots,
attachments, JUnit annotations, build identity and assertion policy.

Candidate enumeration now bounds each editable/enabled state check to 250 ms
(`candidateTimeout`, integer 1..1000). A single step stops collecting candidates
after a one-second budget, plus at most one in-flight state check. This budget is
not a hard page-operation deadline. Initial zero counts, invisible/disabled
controls, availability timeouts after inventory, truncated scans and exhausted
budgets are distinct in `inventories`. An availability timeout does not establish
why a candidate became unavailable and does not claim that it was operated.
Tab/Escape/scroll remain fallback choices. No retry is added.

Only candidate-availability `TimeoutError` is treated this way. Closed pages,
other browser errors, uncaught app errors, application-readiness failures and
failures of an already selected action still fail with the original cause and
receipt. Selected action label/type reads have explicit one-second bounds;
pointer/fill operations retain their existing three-second bounds. A seed
reproduces choices given the same inventory, not browser event timing.

This addresses the actual Flutter #207 observations: seed `39036084029:1` on
`/tools/time/now-timeline` timed out checking button eligibility; seed
`39036904077:1` on `/screen91` timed out checking input editability. Both happened
between counting/visibility and a later live locator state query. Preserve those
original failures as historical evidence; no claim is made that a new seed alone
fixes them. New browser regressions remove each candidate at that exact boundary.

## Reusing Aoi's stdlib min distributions

Aoi PR #57 at `aaae53d9d0ba9748b494e62f31f416ee8f25b335` provides five
`dist/*_min.py` bundles. They are not in Aoi main as of this inspection. No
unmerged bundle is silently adopted into this repository's vendor lock.

| Bundle | Relevant existing API | Boundary |
| --- | --- | --- |
| BlueProbe | `new_report`, `inspect`, saved-snapshot adapters | Offline reparsing and unknown counts; no live DOM waiter |
| QueRyu | `Cache`, `CacheIntegrityError`, verified body/snapshot | Acquisition/cache integrity; cached success is not fresh UI execution |
| PythDRagoras | `counts_metrics`, `threshold_curve`, provenance audits | Measured boundary/threshold analysis; no UI click or renderer |

`examples/replay_dom_with_blueprobe_min.py` connects a supplied trusted BlueProbe
min to the existing `page_text.find_elements` extractor. It verifies the saved
DOM hash and minpy content digest, records both file identities and uses
BlueProbe's report/inspection/unknown aggregation. No parser, browser, download or
new package is added. Empty static matches remain unknown for live presence.

```sh
python -S examples/replay_dom_with_blueprobe_min.py saved-dom.json --blueprobe-min /path/to/blueprobe_min.py --find Add
```

The input comes from the existing `examples/capture_browser_dom.py`. Output is
historical static evidence, not browser visibility, operability or native OS
verification. CSS/layout/coordinate input remains in the real-browser lane.
QueRyu is not used to fabricate an HTTP 200 acquisition from an arbitrary saved
DOM fragment; its acquisition/cache API belongs at the actual retrieval stage.

Local optional integration used the exact PR #57 BlueProbe bundle with file
SHA-256 `3d09c9d9bbe876021df29199994cf449a4b2241ed0389617b5ec3198da7c4d8d`.
Under `python -S`, a saved fixture with an Add button produced one static hit;
query `missing-123` produced zero hits and one retained unknown. This exercises
the actual supplied min API, not a substitute parser or a live browser claim.

## Existing boundaries

| Owner | Responsibility |
| --- | --- |
| browser-test-kit | Worker budget, page interaction/inventory, seeded exploration, screenshots/evidence, `route_trail.py` emoji identities and ordered navigation |
| xprobe | Existing reproducible edge/factor corpora and JUnit failure-identity import; importing identity does not recreate inputs or execute the browser |
| BlueProbe (Aoi) | Existing saved-HTML extraction/comparison adapters and acquisition provenance; its `HtmlSource` explicitly does not own network acquisition |
| Flutter consumer | Its route catalogue, allowed buttons/values, Flutter readiness, expected business results and app/build evidence |

Do not add another emoji parser or route walker here. `route_trail.py` already
handles variation selectors, full ZWJ/skin-tone/flag/keycap identities, collisions
and ordered trails. `explore` operates on the current page; its route is an opaque
seed identity. Normalization and route selection remain with the existing tools.

## Verification ownership

`tests/browserless/browser_execution.spec.ts` checks CPU/memory caps, deterministic
choices, actual browserless Playwright worker overlap/replacement/remaining-case
completion, JUnit seed properties and partial-receipt/error cleanup. It uses the
existing browserless config and produces the existing lane's JUnit, without an
engine. `tests/node/browser_execution.spec.ts` checks real pointer and Unicode
input against HTML in the normal five-project lane. Existing emoji trail tests
remain the authority for identities; consumers do not duplicate these suites.

Consumers pin the exact upstream commit + blob SHA + SHA-256 and reuse the MIT
license. A checked-in source copy is immutable vendor input, not a second
implementation. Update upstream first and refresh the consumer lock. Flutter's
consumer PR depends on the upstream change in browser-test-kit PR #84.
