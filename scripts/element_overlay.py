#!/usr/bin/env python3
"""Element annotation overlay, DOM highlight CSS, and TAP element assertions.

Absorbs patterns from SeleniumBase (highlight overlays), Callstack agent-device
& Midscene (bounding box target annotations and coordinate maps), and Poku/PuppetSharp
(TAP assertion formatting and element digests).

Stdlib-only; runs under ``python -S``::

    python -S scripts/element_overlay.py --file dom.json --format svg --out overlay.svg
    python -S scripts/element_overlay.py --file dom.json --find "submit" --format tap
    python -c "import element_overlay; print(element_overlay.build_highlight_css(['#submit-btn']))"

Exit codes: 0 OK; 1 assertion failure or no search match; 2 usage or input error.
"""
from __future__ import annotations

import argparse, json, math, re, sys
from decimal import Decimal, localcontext, ROUND_HALF_UP; from typing import Any, Dict, List, Optional, Union

SCHEMA = "btk-element-overlay/1"


def _finite_number(value: Any) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _round_coordinate(value: float) -> float:
    # Match the Node twin's binary64 operations, including ties toward +infinity.
    if abs(value) >= 2 ** 52:
        return value  # Already integral; avoid overflowing the scale operation.
    return math.floor(value * 100 + 0.5) / 100


def _svg_number(value: float) -> str:
    """Round-trip binary64 text shared with Node's toExponential(16)."""
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("SVG numbers must be finite")
    if value.is_integer():
        return str(int(value))  # Also normalizes negative zero.
    with localcontext() as context:
        context.rounding = ROUND_HALF_UP
        mantissa, exponent = format(Decimal.from_float(value), ".16e").split("e")
    return mantissa.rstrip("0").rstrip(".") + "e" + str(int(exponent))


def _extract_element_list(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        if "elements" in data and isinstance(data["elements"], list):
            return [item for item in data["elements"] if isinstance(item, dict)]
        if "items" in data and isinstance(data["items"], list):
            return [item for item in data["items"] if isinstance(item, dict)]
        # Single element dictionary
        if any(k in data for k in ("x", "y", "width", "w", "height", "h", "selector", "id")):
            return [data]
    return []


def _parse_viewport(viewport: Any) -> Optional[Dict[str, float]]:
    if isinstance(viewport, dict):
        w = viewport.get("width", viewport.get("w"))
        h = viewport.get("height", viewport.get("h"))
        if _finite_number(w) and _finite_number(h) and w > 0 and h > 0:
            return {"width": float(w), "height": float(h)}
    elif isinstance(viewport, str):
        match = re.match(r"^([0-9]+(?:\.[0-9]+)?)x([0-9]+(?:\.[0-9]+)?)$", viewport.strip(" \t\n\r\v\f"), re.IGNORECASE)
        if match:
            w, h = float(match.group(1)), float(match.group(2))
            if _finite_number(w) and _finite_number(h) and w > 0 and h > 0:
                return {"width": w, "height": h}
    return None


def annotate_elements(
    elements: Any,
    options: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Calculate target coordinates, index badges [1], [2], and bounds for elements."""
    opts = options or {}
    raw_list = _extract_element_list(elements)
    viewport = _parse_viewport(opts.get("viewport"))

    query = str(opts.get("find") or "").strip().lower()

    items: List[Dict[str, Any]] = []
    index = 1

    for raw in raw_list:
        x = raw.get("x", raw.get("left"))
        y = raw.get("y", raw.get("top"))
        w = raw.get("width", raw.get("w"))
        h = raw.get("height", raw.get("h"))

        # Skip elements missing numeric position or non-positive dimensions
        if not all(_finite_number(value) for value in (x, y, w, h)):
            continue

        fx, fy, fw, fh = float(x), float(y), float(w), float(h)
        if fw <= 0 or fh <= 0:
            continue

        name = str(raw.get("name") or raw.get("label") or raw.get("text") or raw.get("innerText") or "").strip()
        role = str(raw.get("role") or "").strip()
        selector = str(raw.get("selector") or (f"#{raw['id']}" if raw.get("id") else "")).strip()

        if query:
            searchable = f"{name} {role} {selector}".lower()
            if query not in searchable:
                continue

        if not all(math.isfinite(value) for value in (fx + fw, fy + fh, fx + fw / 2, fy + fh / 2)):
            continue
        cx = _round_coordinate(fx + fw / 2.0)
        cy = _round_coordinate(fy + fh / 2.0)

        clipped = False
        if viewport:
            vw, vh = viewport["width"], viewport["height"]
            if fx < 0 or fy < 0 or (fx + fw) > vw or (fy + fh) > vh:
                clipped = True

        items.append({
            "index": index,
            "label": str(index),
            "name": name,
            "role": role,
            "selector": selector,
            "x": _round_coordinate(fx),
            "y": _round_coordinate(fy),
            "width": _round_coordinate(fw),
            "height": _round_coordinate(fh),
            "cx": cx,
            "cy": cy,
            "clipped": clipped,
        })
        index += 1

    return {
        "schema": SCHEMA,
        "count": len(items),
        "viewport": viewport,
        "items": items,
    }


def build_highlight_css(
    selectors_or_elements: Any,
    color: str = "#ff0055",
    outline_width: Union[int, float] = 2
) -> str:
    """Generate CSS rules to highlight elements in the DOM before taking a screenshot."""
    raw_list = selectors_or_elements if isinstance(selectors_or_elements, list) else [selectors_or_elements]
    selectors: List[str] = []

    for item in raw_list:
        if isinstance(item, str):
            s = item.strip()
            if s:
                selectors.append(s)
        elif isinstance(item, dict):
            sel = item.get("selector") or (f"#{item['id']}" if item.get("id") else "")
            if sel:
                selectors.append(str(sel).strip())

    if not selectors:
        return ""

    joined = ", ".join(selectors)
    w = f"{outline_width}px" if isinstance(outline_width, (int, float)) else str(outline_width)
    return (
        f"{joined} {{\n"
        f"  outline: {w} solid {color} !important;\n"
        f"  outline-offset: 2px !important;\n"
        f"  box-shadow: 0 0 8px {color} !important;\n"
        f"  transition: outline 0.2s ease-in-out !important;\n"
        f"}}"
    )


def render_svg_overlay(
    annotation_data: Any,
    options: Optional[Dict[str, Any]] = None
) -> str:
    """Render an SVG string with element bounding boxes, center markers, and target index badges."""
    opts = options or {}
    data = annotate_elements(annotation_data, opts) if isinstance(annotation_data, (list, dict)) and "schema" not in annotation_data else annotation_data
    items = data.get("items", []) if isinstance(data, dict) else []

    vw, vh = 800.0, 600.0
    if isinstance(data, dict) and data.get("viewport"):
        vw = float(data["viewport"]["width"])
        vh = float(data["viewport"]["height"])
    elif items:
        max_x = max((item["x"] + item["width"] for item in items), default=780.0)
        max_y = max((item["y"] + item["height"] for item in items), default=580.0)
        vw = max(800.0, max_x + 20.0)
        vh = max(600.0, max_y + 20.0)

    width = float(opts.get("width", vw))
    height = float(opts.get("height", vh))
    color = str(opts.get("color", opts.get("stroke_color", "#ff0055"))).strip()
    bg_opacity = float(opts.get("fill_opacity", 0.15))

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_svg_number(width)} {_svg_number(height)}" width="{_svg_number(width)}" height="{_svg_number(height)}">',
        f'  <style>.btk-badge {{ font-family: sans-serif; font-size: 12px; font-weight: bold; fill: #ffffff; }}</style>',
    ]

    for item in items:
        x, y, w, h = item["x"], item["y"], item["width"], item["height"]
        cx, cy = item["cx"], item["cy"]
        idx = item["index"]
        name = item.get("name") or item.get("role") or f"Target {idx}"

        # Bounding box
        lines.append(f'  <rect x="{_svg_number(x)}" y="{_svg_number(y)}" width="{_svg_number(w)}" height="{_svg_number(h)}" fill="{color}" fill-opacity="{_svg_number(bg_opacity)}" stroke="{color}" stroke-width="2" rx="3" />')
        # Center dot
        lines.append(f'  <circle cx="{_svg_number(cx)}" cy="{_svg_number(cy)}" r="3" fill="{color}" />')
        # Badge background
        badge_w = max(20.0, 10.0 + len(str(idx)) * 8.0)
        badge_x = max(0.0, x)
        badge_y = max(0.0, y - 18.0) if y >= 18.0 else y
        lines.append(f'  <rect x="{_svg_number(badge_x)}" y="{_svg_number(badge_y)}" width="{_svg_number(badge_w)}" height="16" fill="{color}" rx="3" />')
        lines.append(f'  <text x="{_svg_number(badge_x + 5)}" y="{_svg_number(badge_y + 12)}" class="btk-badge">{idx}</text>')

    lines.append('</svg>')
    return "\n".join(lines)


def assert_elements(
    elements: Any,
    conditions: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Poku-style zero-dependency TAP element assertions."""
    conds = conditions or {}
    ann = annotate_elements(elements, conds)
    items = ann["items"]

    min_count = int(conds.get("min_count", conds.get("assert_min_count", 1)))
    required_role = str(conds.get("assert_role", conds.get("role", ""))).strip().lower()
    required_find = str(conds.get("find", "")).strip().lower()

    assertions = []

    # Check 1: min_count
    c1_ok = len(items) >= min_count
    assertions.append({
        "ok": c1_ok,
        "description": f"element count ({len(items)}) >= min_count ({min_count})"
    })

    # Check 2: required_role if specified
    if required_role:
        found_role = any(item.get("role", "").lower() == required_role for item in items)
        assertions.append({
            "ok": found_role,
            "description": f"at least one element has role '{required_role}'"
        })

    # Check 3: search query match if specified
    if required_find:
        found_match = len(items) > 0
        assertions.append({
            "ok": found_match,
            "description": f"found elements matching search query '{required_find}'"
        })

    passed = sum(1 for a in assertions if a["ok"])
    total = len(assertions)
    all_ok = (passed == total)

    tap_lines = ["TAP version 13", f"1..{total}"]
    for idx, a in enumerate(assertions, 1):
        status = "ok" if a["ok"] else "not ok"
        tap_lines.append(f"{status} {idx} - {a['description']}")

    return {
        "ok": all_ok,
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "count": len(items),
        "items": items,
        "tap": "\n".join(tap_lines) + "\n"
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--file", help="JSON file containing elements or DOM capture (default: stdin)")
    parser.add_argument("--format", choices=["json", "svg", "tap", "css"], default="json", help="Output format")
    parser.add_argument("--find", help="Filter elements by label, name, role, or selector")
    parser.add_argument("--assert-role", help="Assert presence of element with given role")
    parser.add_argument("--assert-min-count", type=int, default=1, help="Assert minimum element count")
    parser.add_argument("--viewport", help="Viewport dimensions WxH (e.g. 1280x720)")
    parser.add_argument("--color", default="#ff0055", help="Color for CSS highlight or SVG overlay")
    parser.add_argument("--out", help="Write output to file instead of stdout")
    args = parser.parse_args(argv)

    try:
        if args.file and args.file != "-":
            with open(args.file, "r", encoding="utf-8") as f:
                content = json.load(f)
        else:
            if sys.stdin.isatty():
                content = []
            else:
                raw_in = sys.stdin.read().strip()
                content = json.loads(raw_in) if raw_in else []
    except Exception as err:
        print(f"element_overlay: error reading input: {err}", file=sys.stderr)
        return 2

    options = {
        "find": args.find,
        "assert_role": args.assert_role,
        "assert_min_count": args.assert_min_count,
        "viewport": args.viewport,
        "color": args.color,
    }

    if args.format == "css":
        output_text = build_highlight_css(content, color=args.color)
        exit_code = 0
    elif args.format == "svg":
        output_text = render_svg_overlay(content, options)
        exit_code = 0
    elif args.format == "tap":
        assert_res = assert_elements(content, options)
        output_text = assert_res["tap"]
        exit_code = 0 if assert_res["ok"] else 1
    else:  # json
        if args.assert_role or args.find or args.assert_min_count > 1:
            res = assert_elements(content, options)
            output_text = json.dumps(res, indent=2)
            exit_code = 0 if res["ok"] else 1
        else:
            ann = annotate_elements(content, options)
            output_text = json.dumps(ann, indent=2)
            exit_code = 0 if ann["count"] > 0 or not args.find else 1

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(output_text)
            if not output_text.endswith("\n"):
                f.write("\n")
    else:
        sys.stdout.write(output_text)
        if not output_text.endswith("\n"):
            sys.stdout.write("\n")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
