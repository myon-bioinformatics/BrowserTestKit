# Element Annotation Overlay, Highlight CSS, and TAP Assertions

`tools/element_overlay.py` (Python stdlib-only) and `tools/element_overlay.mjs` (Node ES module twin) provide target coordinate calculation, annotation overlay rendering, DOM highlight CSS generation, and Poku-style zero-dependency TAP element assertions.

It absorbs key capabilities from:
- **SeleniumBase & Midscene**: DOM element highlight CSS rules (`build_highlight_css`) for pre-screenshot visual indication.
- **Callstack agent-device & Midscene**: Bounding box target annotations (`[1]`, `[2]`, label badges, `x, y, w, h`, role, selector) and SVG overlay generation (`render_svg_overlay`).
- **Poku & PuppetSharp**: Zero-dependency TAP assertion formatting (`assert_elements`) and element trace digests.

## Python Command-Line Usage

```sh
# Annotate element coordinates and badges into JSON
python -S tools/element_overlay.py --file elements.json --viewport 1280x720

# Filter elements by search query and generate SVG overlay diagram
python -S tools/element_overlay.py --file elements.json --find "submit" --format svg --out overlay.svg

# Run zero-dependency TAP element assertions
python -S tools/element_overlay.py --file elements.json --assert-role button --assert-min-count 1 --format tap

# Generate CSS highlight rules for pre-screenshot DOM injection
python -S tools/element_overlay.py --file elements.json --format css --color "#ff0055"
```

## Python One-Liners (`python -m` / `python -c`)

```sh
# One-liner module CLI after installation
python -m element_overlay --file elements.json --format tap

# Python one-liner code snippet
python -c "import element_overlay; print(element_overlay.build_highlight_css(['#submit-btn', '.btn-primary']))"
```

## Node ES Module Usage (`tools/element_overlay.mjs`)

```js
import { annotateElements, buildHighlightCss, renderSvgOverlay, assertElements } from './tools/element_overlay.mjs';

const annotation = annotateElements(elements, { viewport: { width: 1280, height: 720 } });
const highlightCss = buildHighlightCss(['#btn-1', '#btn-2'], '#00ff00');
const svg = renderSvgOverlay(elements, { width: 800, height: 600 });
const tapResult = assertElements(elements, { min_count: 1, assert_role: 'button' });
```

## Evidence Schema (`btk-element-overlay/1`)

Both twins accept finite numeric coordinates, excluding booleans. Elements with
missing or invalid coordinates, non-positive dimensions, or overflowing bounds
are skipped; invalid viewport dimensions are ignored. An explicitly supplied
coordinate takes precedence over its alias even when invalid.

Coordinates and centers use the same binary64 operations in both languages:
`floor(value * 100 + 0.5) / 100`, with half ties toward positive infinity
(`0.125` → `0.13`, `-0.125` → `-0.12`). Values with magnitude at least `2**52`
are already integral and bypass scaling. This is a binary floating-point rule,
not exact decimal arithmetic.

Output data adheres to schema `btk-element-overlay/1`:

```json
{
  "schema": "btk-element-overlay/1",
  "count": 1,
  "viewport": { "width": 1280, "height": 720 },
  "items": [
    {
      "index": 1,
      "label": "1",
      "name": "Submit Form",
      "role": "button",
      "selector": "#submit-btn",
      "x": 100.0,
      "y": 200.0,
      "width": 120.0,
      "height": 40.0,
      "cx": 160.0,
      "cy": 220.0,
      "clipped": false
    }
  ]
}
```

### Numeric and output parity

- Geometry uses binary64 numbers. Booleans, strings, null, NaN and infinities
  are invalid coordinates. Width and height must be strictly positive before
  rounding; a tiny positive dimension can therefore be reported as zero.
- An invalid element is skipped without consuming an index. Invalid viewports
  become `null`. Viewport strings use ASCII digits in `WxH` form (case-insensitive
  `x`) with optional surrounding ASCII whitespace; viewport objects use positive finite dimensions without rounding.
- Clipping compares the original, unrounded bounds with the viewport. Touching
  its edge is allowed; any negative origin or right/bottom bound beyond the
  edge is clipped. No viewport means `clipped: false`.
- JSON parity means equal records parsed as binary64, including every numeric value;
  whitespace and representations such as `1` versus `1.0` may differ.
- SVG parity means identical strings. Integral values use exact decimal text;
  fractional values use 17 significant digits, half-away-from-zero formatting,
  and normalized exponents. This preserves binary64 precision instead of
  truncating geometry to six significant digits. Negative zero renders as `0`.
- Text metadata (`name`, `label`, `text`, `innerText`, `role`, `selector`, `id`)
  is expected to contain strings. Empty names/selectors fall back to the next
  name field or element ID in both twins.
- `--format svg --shape rect|ellipse|triangle|arrow` (with `--label-field`, `--spotlight`) draws the 🔲 marks
  `tools/evidence_gif.py` puts on screenshots instead of boxes and dots: both go through `mark_svg()`. (Python
  only; the Node twin keeps the box overlay.)
