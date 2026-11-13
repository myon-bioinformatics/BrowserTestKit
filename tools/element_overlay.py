#!/usr/bin/env python3
"""Element annotation overlay, DOM highlight CSS, and TAP element assertions.

Absorbs patterns from SeleniumBase (highlight overlays), Callstack agent-device
& Midscene (bounding box target annotations and coordinate maps), and Poku/PuppetSharp
(TAP assertion formatting and element digests).

Stdlib-only; runs under ``python -S``::

    python -S tools/element_overlay.py --file dom.json --format svg --out overlay.svg
    python -S tools/element_overlay.py --file dom.json --find "submit" --format tap
    python -c "import element_overlay; print(element_overlay.build_highlight_css(['#submit-btn']))"
    python -S tools/element_overlay.py --file dom.json --format svg --shape ellipse --label-field name --spotlight

🔲 Marks for slides and README GIFs: ``mark_svg(marks, viewport)`` draws ``rect`` / ``ellipse`` / ``triangle`` around a box or an
``arrow`` at it, with an optional callout ``label`` (Meiryo), a number badge and a ``spotlight`` that dims everything else.
``tools/evidence_gif.py``'s 🔲 frame step and ``--format svg --shape`` here draw with this one function.

Exit codes: 0 OK; 1 assertion failure or no search match; 2 usage or input error.
"""
from __future__ import annotations

import argparse, html, json, math, re, sys
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


# ---- 🔲 marks: one drawer for evidence_gif's frame step and the svg overlay ----------------------------------------------

MARK_SHAPES = ("rect", "ellipse", "triangle", "arrow")
MARK_KEYS = ("shape", "color", "width", "pad", "label", "number", "spotlight")
MARK_FONT = "Meiryo, 'Hiragino Sans', 'Noto Sans CJK JP', 'Yu Gothic', sans-serif"
MARK_TEXT = 16                       # px: label and badge text; the label box is 32 high (8 above and below)


def check_mark(value: Dict[str, Any]) -> Dict[str, Any]:
    """Defaults and limits for one mark's look (``box`` and other keys are the caller's): raises ValueError."""
    mark = {"shape": value.get("shape", "rect"), "color": str(value.get("color", "#e53935")), "width": value.get("width", 4),
            "pad": value.get("pad", 8), "label": value.get("label"), "number": value.get("number"), "spotlight": value.get("spotlight", False)}
    colour = mark["color"]
    if (mark["shape"] not in MARK_SHAPES or not isinstance(mark["width"], int) or not 1 <= mark["width"] <= 32
            or not isinstance(mark["pad"], int) or not 0 <= mark["pad"] <= 64
            or len(colour) not in (4, 7) or colour[0] != "#" or any(c not in "0123456789abcdefABCDEF" for c in colour[1:])
            or type(mark["spotlight"]) is not bool
            or not (mark["label"] is None or isinstance(mark["label"], str) and 0 < len(mark["label"]) <= 80)
            or not (mark["number"] is None or type(mark["number"]) is int and 0 <= mark["number"] <= 99)):
        raise ValueError(f"a mark: shape {' / '.join(MARK_SHAPES)}, color #rgb or #rrggbb, width 1-32, pad 0-64, "
                         f"label 1-80 characters, number 0-99, spotlight true/false: {value!r}")
    return mark


def _text_width(text: str) -> float:
    """Estimated width of ``text`` at MARK_TEXT px: a wide (CJK, emoji) character is one em, others about half."""
    return sum(MARK_TEXT if ord(c) >= 0x2E80 else MARK_TEXT * 0.56 for c in text)


def _overlaps(a: tuple, b: tuple) -> bool:
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def mark_svg(marks: List[Dict[str, Any]], viewport: Union[tuple, list], *, attributes: str = "") -> str:
    """One viewport-sized SVG with every mark: ``{"box": {x, y, width, height}, **check_mark(...)}``.

    Spotlight marks cut holes in one dimming layer. An arrow comes from the first side with room (left, right, above, else
    below). A label takes the first free place (beyond an arrow's tail; else right, below, above, left of the padded box)
    inside the viewport that covers no other mark or label. No ``style`` attribute (a page's ``style-src 'self'`` would
    refuse it): the caller positions the SVG."""
    vw, vh = float(viewport[0]), float(viewport[1])
    num = _svg_number
    padded = [(m["box"]["x"] - m["pad"], m["box"]["y"] - m["pad"], m["box"]["width"] + 2 * m["pad"], m["box"]["height"] + 2 * m["pad"]) for m in marks]
    taken = list(padded)
    layers, holes = [], []
    for mark, (x, y, w, h) in zip(marks, padded):
        colour, line = mark["color"], mark["width"]
        cx, cy = x + w / 2, y + h / 2
        if mark.get("spotlight"):
            holes.append(f"M{num(x)} {num(y)}h{num(w)}v{num(h)}h{num(-w)}Z")
        stroke = f'fill="none" stroke="{colour}" stroke-width="{line}" stroke-linejoin="round"'
        places = []                                                   # where this mark's label may go, best first
        if mark["shape"] == "ellipse":                                # the ellipse through the padded box's corners
            layers.append(f'<ellipse cx="{num(cx)}" cy="{num(cy)}" rx="{num(w / 2 ** .5)}" ry="{num(h / 2 ** .5)}" {stroke}/>')
        elif mark["shape"] == "triangle":                             # apex one box height above: the box's top edge just fits
            layers.append(f'<polygon points="{num(cx)},{num(y - h)} {num(x - w / 2)},{num(y + h)} {num(x + w + w / 2)},{num(y + h)}" {stroke}/>')
        elif mark["shape"] == "arrow":                                # tip on the box's edge, from the side with room
            head, tail = 4 * line, 64
            room = {"left": x, "right": vw - x - w, "above": y, "below": vh - y - h}
            side = next((s for s in ("left", "right", "above") if room[s] >= tail), "below")
            if side in ("left", "right"):
                sign, tip = (-1, x) if side == "left" else (1, x + w)
                end = tip + sign * tail
                layers.append(f'<line x1="{num(end)}" y1="{num(cy)}" x2="{num(tip + sign * head)}" y2="{num(cy)}" stroke="{colour}" stroke-width="{line}"/>'
                              f'<polygon points="{num(tip)},{num(cy)} {num(tip + sign * head)},{num(cy - head / 2)} {num(tip + sign * head)},{num(cy + head / 2)}" fill="{colour}"/>')
                taken.append((min(end, tip), cy - head / 2, tail, head))
                places.append(lambda lw, lh, end=end, sign=sign: (end - 8 - lw if sign < 0 else end + 8, cy - lh / 2))
            else:
                sign, tip = (-1, y) if side == "above" else (1, y + h)
                end = tip + sign * tail
                layers.append(f'<line x1="{num(cx)}" y1="{num(end)}" x2="{num(cx)}" y2="{num(tip + sign * head)}" stroke="{colour}" stroke-width="{line}"/>'
                              f'<polygon points="{num(cx)},{num(tip)} {num(cx - head / 2)},{num(tip + sign * head)} {num(cx + head / 2)},{num(tip + sign * head)}" fill="{colour}"/>')
                taken.append((cx - head / 2, min(end, tip), head, tail))
                places.append(lambda lw, lh, end=end, sign=sign: (cx - lw / 2, end - 8 - lh if sign < 0 else end + 8))
        else:
            layers.append(f'<rect x="{num(x)}" y="{num(y)}" width="{num(w)}" height="{num(h)}" rx="4" {stroke}/>')
        text = f'font-family="{MARK_FONT}" font-size="{MARK_TEXT}" font-weight="bold" fill="#fff"'
        if mark.get("number") is not None:                            # a badge on the padded box's top-left corner
            bx, by = min(max(x, 16), vw - 16), min(max(y, 16), vh - 16)
            layers.append(f'<circle cx="{num(bx)}" cy="{num(by)}" r="14" fill="{colour}"/>'
                          f'<text x="{num(bx)}" y="{num(by + 6)}" text-anchor="middle" {text}>{mark["number"]}</text>')
        if mark.get("label"):
            lw, lh = _text_width(mark["label"]) + 16, 32
            places += [lambda lw, lh: (x + w + 8, cy - lh / 2), lambda lw, lh: (x, y + h + 8),
                       lambda lw, lh: (x, y - 8 - lh), lambda lw, lh: (x - 8 - lw, cy - lh / 2)]
            inside = [(lx, ly) for lx, ly in (place(lw, lh) for place in places) if 0 <= lx <= vw - lw and 0 <= ly <= vh - lh]
            free = [(lx, ly) for lx, ly in inside if not any(_overlaps((lx, ly, lw, lh), other) for other in taken)]
            lx, ly = (free or inside or [(min(max(x, 0), max(0, vw - lw)), min(y + h + 8, max(0, vh - lh)))])[0]
            taken.append((lx, ly, lw, lh))
            layers.append(f'<rect x="{num(lx)}" y="{num(ly)}" width="{num(lw)}" height="{lh}" rx="8" fill="{colour}"/>'
                          f'<text x="{num(lx + 8)}" y="{num(ly + 22)}" {text}>{html.escape(mark["label"])}</text>')
    dim = (f'<path d="M0 0H{num(vw)}V{num(vh)}H0Z{"".join(holes)}" fill="#000" fill-opacity=".48" fill-rule="evenodd"/>' if holes else "")
    return (f'<svg xmlns="http://www.w3.org/2000/svg"{attributes} width="{num(vw)}" height="{num(vh)}" '
            f'viewBox="0 0 {num(vw)} {num(vh)}">{dim}{"".join(layers)}</svg>')


def render_svg_overlay(
    annotation_data: Any,
    options: Optional[Dict[str, Any]] = None
) -> str:
    """Render an SVG string with element bounding boxes, center markers, and target index badges."""
    opts = options or {}
    if opts.get("shape"):                                             # 🔲 the marks evidence_gif draws, one per element
        data = annotate_elements(annotation_data, opts) if isinstance(annotation_data, (list, dict)) and "schema" not in annotation_data else annotation_data
        items = data.get("items", []) if isinstance(data, dict) else []
        view = data.get("viewport") if isinstance(data, dict) and data.get("viewport") else {"width": 800.0, "height": 600.0}
        look = check_mark({"shape": opts["shape"], "color": opts.get("color", "#e53935"), "spotlight": bool(opts.get("spotlight"))})
        field = opts.get("label_field")
        marks = [{**look, "box": item, "number": item["index"] if len(items) > 1 else None,
                  "label": (str(item.get(field) or "")[:80] or None) if field else None} for item in items]
        return mark_svg(marks, (float(opts.get("width", view["width"])), float(opts.get("height", view["height"]))))
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
    parser.add_argument("--shape", choices=MARK_SHAPES, help="svg: 🔲 marks (evidence_gif's shapes) instead of boxes and dots")
    parser.add_argument("--label-field", help="svg --shape: callout text from this element field (name, role, selector, ...)")
    parser.add_argument("--spotlight", action="store_true", help="svg --shape: dim everything outside the marks")
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
        "shape": args.shape,
        "label_field": args.label_field,
        "spotlight": args.spotlight,
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
