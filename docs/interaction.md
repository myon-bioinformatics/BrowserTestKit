# Measured input and side-effect records

`tools/interaction.mjs` (Node) and `tools/interaction.py` (Python sync/async)
are small helpers for an existing Playwright Page. They have no dependency of
their own; the caller owns Playwright, the Page and the Locator. Both produce the
same JSON records, so results can be collected first and checked at the end.

## press: real input where the DOM node is not the hit target

Flutter Web paints controls on a canvas; the semantics DOM node can be covered by
another node, so `locator.click()` fails as "intercepts pointer events" while
`el.click()` reaches the node through a path no user can take
(`SYNTHETIC_DOM_CLICK`). `press` instead:

1. scrolls the target into view and waits for it to be visible;
2. requires `ratio` (default 0.9) of its box to be inside the viewport;
3. clicks or taps at the **integer** centre (`FRACTIONAL_HIT_COORDINATES`);
4. returns a `pointer-press/1` record, including which element was on top at the point.

```js
import {press} from './tools/interaction.mjs';
const record = await press(page, page.getByText('Photo Studio', {exact: true}).first(),
  {touch: testInfo.project.use.hasTouch});
```

```python
from interaction import press, apress
record = press(page, page.get_by_text("Photo Studio", exact=True).first, touch=False)
```

`touch` omitted means `navigator.maxTouchPoints > 0` decides (`input_source` says
which); this detection is asserted only in Chromium profiles, so pass `touch`
explicitly in WebKit/Firefox projects. `hit.self: false` is expected under a
canvas UI: it is an observation, not a failure. As a `route_trail` hook,
`navigate: (page, step) => press(page, page.getByText(step.title), {touch})`
tests the click path that direct `goto` does not.

## inventory: what the page exposes, before choosing what to press

`inventory(page)` lists each matching element's role, `aria-label`, text
(trimmed), identifier (`flt-semantics-identifier`, `id` or `data-testid`), box and
whether it is in the viewport, as one `element-inventory/1` record (`total`,
`truncated`). The default selector covers Flutter semantics nodes, `[role]` and
native controls; pass `selector` / `limit` to narrow it.

`aria_label` is the raw attribute, **not** the computed accessible name. A
`<button>Submit</button>`, `aria-labelledby`, a native `<label>` or an image `alt`
give a name to Playwright's `getByRole(role, {name})` while `aria_label` stays
`null`. `aria-labelledby` takes precedence over `aria-label`, so even a
non-null raw label may differ from the accessible name. `text` is only the
element's own text content: a labelled input can have empty `text` while its
accessible name comes from an external label. Let Playwright's role/name locator
do the actual matching. This record is for comparing boxes, not for re-implementing
accessible-name computation.

```js
import {inventory, press} from './tools/interaction.mjs';
const listed = await inventory(page, {selector: 'flt-semantics'});
```

```python
listed = inventory(page, selector="flt-semantics")
```

Measured on flutter_navigation_basic PR #182 (Door-to-Door journey): Flutter merged
the dropdown into its card as one button-role node about 122–130px tall, so a press
at that node's centre landed on the card, not the 40px field, and the menu never
opened. Changing the locator's role did not help; giving the dropdown its own
`Semantics` boundary and identifier, and pressing that, did (390px and 1280px).
`fixtures/interaction.html` reproduces it with `#door-card` around `#door-field`
(`MERGED_SEMANTICS_HIT_AREA`). Listing boxes first shows the mismatch before any
coordinate is chosen.

## watch: what a page did while input was handled

For tools that accept untrusted input (pasted HTML, files), record requests,
dialogs (dismissed), uncaught errors, popups and Web Storage changes:

```js
import {watch} from './tools/interaction.mjs';
const w = await watch(page);
await page.locator('#paste-load').click();
const report = await w.stop();   // side-effects/1, report.quiet === true when nothing happened
```

```python
w = watch(page); page.locator("#paste-load").click(); report = w.stop()
w = await awatch(page); ...; report = await w.stop()
```

Measured in CI (PR #61): Firefox records an `http://` beacon twice, once more as
`https://` (HTTPS-first upgrade), so assert membership (`toContain` / `in`), not an
exact request list. Chromium/WebKit/mobile profiles recorded one request, one
dialog, one page error and a new `localStorage` key on `file://`.

Storage is compared before/after the watch, not cleared. Each area records
`{keys, fingerprint}`: the key count and an FNV-1a hash of the sorted key/value
pairs, so overwriting an existing key with a same-length value is a change
(`#overwrite` in the fixture). Raw values are never put in the record. The hash
detects change; it is not a security or integrity proof. `null` means the area
could not be read (for example blocked storage). With
`dismissDialogs: false` / `dismiss_dialogs=False` the caller must handle dialogs.

## Assert late

```sh
python -S tools/interaction.py check records.json         # a record, a list, or JSON lines
python -S tools/interaction.py check records.json --json
```

Exit 0 when every side-effects record is quiet, 1 otherwise, 2 for invalid input.
Press records are summarized (input, point, visible ratio, top element).

## One-liners that need no helper

```js
// Download → parsed JSON (no file on disk)
const [d] = await Promise.all([page.waitForEvent('download'), page.locator('#download').click()]);
const json = JSON.parse(Buffer.concat(await (await d.createReadStream()).toArray()).toString('utf8'));
// No horizontal page scroll at the current viewport
expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
```

```sh
# Registered tests per project without launching browsers (HANDROLLED_SPEC_CENSUS)
npx playwright test --list --reporter=json > tests.json
```

## Source and adoption

Extracted from intermediate work that did not reach a shared place:

| Source | Pattern | Here |
| --- | --- | --- |
| flutter_navigation_basic `e2e/tests/shared_header.spec.ts` (two copies) and PR #182 `home_discovery_journeys.spec.ts` | measured `press`, fractional centre | `press` (integer centre, viewport ratio, hit record) |
| flutter_navigation_basic `e2e/utils/helpers.ts` `tapSemantics` | `element.evaluate(n => n.click())` | `SYNTHETIC_DOM_CLICK` |
| flutter_navigation_basic PR #182 `route-audit.json` | hand-written receipt after the full journey | existing `write_capture_evidence.py` / `check_capture_evidence.py` |
| myon-bioinformatics.github.io `tests/html-import.spec.js` | request/dialog/storage inertness, download → JSON, no horizontal scroll | `watch`, one-liners above |
| PR #182 verification script | TypeScript transpile + vm stub to count tests; `replace` with `$` | `HANDROLLED_SPEC_CENSUS`, `REPLACEMENT_STRING_SPECIALS` |

Dart/Flutter widget tests stay with Flutter; this kit covers the browser side.
Tests: `tests/python/test_interaction.py` (Chromium/Firefox/WebKit, sync and async)
and `tests/node/interaction.spec.ts` (all Node projects including mobile).

The shared `fixtures/inventory-names.json` cases compare Node, Python sync and
Python async results against Playwright role/name matching: button text,
`aria-labelledby` overriding a raw label, a native external label, and a
Flutter-shaped merged semantics node. This fixture models the DOM shape, not
the full Flutter runtime. The existing interaction fixture continues to verify
real mouse/touch input at the card centre and the smaller field.
