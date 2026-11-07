"""Measured pointer/touch input and side-effect observation for an existing Playwright Page.

Library (the caller owns Playwright, Page and Locator; nothing is imported from it here):

    from interaction import press, apress, watch, awatch, inventory, ainventory
    record = press(page, page.get_by_text("Photo Studio"), touch=False)
    w = watch(page); ...; report = w.stop()          # sync API
    w = await awatch(page); ...; report = await w.stop()  # async API
    listed = inventory(page)                         # role/aria-label/text/id/box per element

press() scrolls the target into view, requires `ratio` of its box inside the
viewport, and sends real mouse/touch input at the integer centre. Use it where
the DOM node is not the hit target (Flutter Web, canvas UIs, forwarding
overlays). watch() records requests, dialogs (dismissed), uncaught errors,
popups and Web Storage changes (key count plus a fingerprint of the sorted
key/value pairs, so overwriting an existing key counts; raw values are not
recorded) while untrusted input is handled.
Node twin: scripts/interaction.mjs (same records).

CLI (offline, assert-late): check saved `pointer-press/1` / `side-effects/1` records.

    python -S scripts/interaction.py check records.json      # one record, a list, or JSON lines

Exit codes: 0 all records OK, 1 a side-effects record is not quiet, 2 input error.
"""
from __future__ import annotations

import argparse, json, math, sys

# Same snapshot function text as storage() in interaction.mjs.
_STORAGE = """() => {
  // Per area: key count and an FNV-1a fingerprint of the sorted key/value pairs,
  // so an overwrite of an existing key is detected without exposing raw values.
  const snap = (name) => {
    try {
      const store = window[name], entries = [];
      for (let i = 0; i < store.length; i++) { const k = store.key(i); entries.push([k, store.getItem(k)]); }
      entries.sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
      const text = JSON.stringify(entries);
      let h = 0x811c9dc5;
      for (let i = 0; i < text.length; i++) { h ^= text.charCodeAt(i); h = Math.imul(h, 0x01000193); }
      return {keys: entries.length, fingerprint: 'fnv1a32:' + (h >>> 0).toString(16).padStart(8, '0')};
    } catch (e) { return null; }
  };
  return {local: snap('localStorage'), session: snap('sessionStorage')};
}"""
INVENTORY_SELECTOR = "flt-semantics, [role], button, a, input, select, textarea"
# Same function text as inventory() in interaction.mjs.
_INVENTORY = r"""([selector, limit]) => {
  // Role, aria-label, text, identifier and box of each matching element:
  // shows when a framework merged a control into a larger container.
  const all = document.querySelectorAll(selector), elements = [];
  for (const el of all) {
    if (elements.length >= limit) break;
    const r = el.getBoundingClientRect();
    const text = (el.textContent || '').replace(/\s+/g, ' ').trim();
    elements.push({
      tag: el.tagName.toLowerCase(),
      role: el.getAttribute('role'),
      aria_label: el.getAttribute('aria-label'),
      text: text.length > 120 ? text.slice(0, 119) + '…' : text,
      id: el.getAttribute('flt-semantics-identifier') || el.id || el.getAttribute('data-testid') || null,
      box: {x: Math.round(r.x), y: Math.round(r.y), width: Math.round(r.width), height: Math.round(r.height)},
      in_viewport: r.width > 0 && r.height > 0 && r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth,
    });
  }
  return {total: all.length, elements};
}"""
_HIT = """(el, [x, y]) => { const top = document.elementFromPoint(x, y);
  return {tag: top ? top.tagName.toLowerCase() : null, self: !!top && (top === el || el.contains(top))}; }"""


def _round(value):
    # Same float operations as interaction.mjs: round() ties to even, Math.round ties up
    # (9/16 gave 0.562 here and 0.563 in Node).
    return math.floor(value * 1000 + 0.5) / 1000


def press_point(box, viewport, ratio=0.9):
    """Integer centre of `box` if at least `ratio` of it is inside `viewport`."""
    if not 0 < ratio <= 1:
        raise ValueError("ratio must be in (0, 1]")
    if not box or not (box["width"] >= 1 and box["height"] >= 1):
        raise ValueError("press target has no layout box")
    w = max(0, min(box["x"] + box["width"], viewport["width"]) - max(box["x"], 0))
    h = max(0, min(box["y"] + box["height"], viewport["height"]) - max(box["y"], 0))
    visible = (w * h) / (box["width"] * box["height"])
    if visible < ratio:
        raise ValueError(f"press target is {visible:.2f} in the viewport; need {ratio}")
    return {"x": math.floor(box["x"] + box["width"] / 2), "y": math.floor(box["y"] + box["height"] / 2),
            "visible_ratio": _round(visible)}


def _record(box, point, touch, source, hit):
    return {"schema": "pointer-press/1", "input": "touch" if touch else "mouse", "input_source": source,
            "x": point["x"], "y": point["y"], "visible_ratio": point["visible_ratio"],
            "box": {k: _round(box[k]) for k in ("x", "y", "width", "height")}, "hit": hit}


def press(page, target, *, touch=None, ratio=0.9, timeout=10000):
    target.scroll_into_view_if_needed(timeout=timeout)
    target.wait_for(state="visible", timeout=timeout)
    viewport = page.viewport_size or page.evaluate("() => ({width: innerWidth, height: innerHeight})")
    box = target.bounding_box()
    point = press_point(box, viewport, ratio)
    hit = target.evaluate(_HIT, [point["x"], point["y"]])
    source = "navigator.maxTouchPoints" if touch is None else "explicit"
    if touch is None:
        touch = page.evaluate("() => navigator.maxTouchPoints > 0")
    (page.touchscreen.tap if touch else page.mouse.click)(point["x"], point["y"])
    return _record(box, point, touch, source, hit)


async def apress(page, target, *, touch=None, ratio=0.9, timeout=10000):
    await target.scroll_into_view_if_needed(timeout=timeout)
    await target.wait_for(state="visible", timeout=timeout)
    viewport = page.viewport_size or await page.evaluate("() => ({width: innerWidth, height: innerHeight})")
    box = await target.bounding_box()
    point = press_point(box, viewport, ratio)
    hit = await target.evaluate(_HIT, [point["x"], point["y"]])
    source = "navigator.maxTouchPoints" if touch is None else "explicit"
    if touch is None:
        touch = await page.evaluate("() => navigator.maxTouchPoints > 0")
    await (page.touchscreen.tap if touch else page.mouse.click)(point["x"], point["y"])
    return _record(box, point, touch, source, hit)


def _inventory_record(selector, data):
    return {"schema": "element-inventory/1", "selector": selector, "total": data["total"],
            "truncated": data["total"] > len(data["elements"]), "elements": data["elements"]}


def inventory(page, *, selector=INVENTORY_SELECTOR, limit=200):
    """Role, aria-label, text, identifier and box of matching elements (see interaction.mjs).

    `aria_label` is the raw attribute, not the computed accessible name: a
    `<button>Submit</button>` has `aria_label: None` and `text: "Submit"`.
    """
    return _inventory_record(selector, page.evaluate(_INVENTORY, [selector, limit]))


async def ainventory(page, *, selector=INVENTORY_SELECTOR, limit=200):
    return _inventory_record(selector, await page.evaluate(_INVENTORY, [selector, limit]))


class _Watch:
    def __init__(self, page, dismiss):
        self.page, self.dismiss = page, dismiss
        self.seen = {"requests": [], "dialogs": [], "page_errors": [], "popups": []}
        self.before = None
        self.handlers = {
            "request": lambda r: self.seen["requests"].append(r.url),
            "dialog": self._dialog,
            "pageerror": lambda e: self.seen["page_errors"].append(str(getattr(e, "message", e))),
            "popup": lambda p: self.seen["popups"].append(p.url),
        }

    def _note(self, dialog):
        self.seen["dialogs"].append({"type": dialog.type, "message": dialog.message})
        return dialog.dismiss() if self.dismiss else None

    def _start(self):
        for name, fn in self.handlers.items():
            self.page.on(name, fn)

    def _finish(self, after):
        for name, fn in self.handlers.items():
            self.page.remove_listener(name, fn)
        quiet = all(not v for v in self.seen.values()) and after == self.before
        return {"schema": "side-effects/1", "quiet": quiet, **self.seen,
                "storage": {"before": self.before, "after": after}}


class Watch(_Watch):
    def _dialog(self, dialog):
        self._note(dialog)

    def stop(self):
        return self._finish(self.page.evaluate(_STORAGE))


class AsyncWatch(_Watch):
    async def _dialog(self, dialog):
        pending = self._note(dialog)
        if pending is not None:
            await pending

    async def stop(self):
        return self._finish(await self.page.evaluate(_STORAGE))


def watch(page, *, dismiss_dialogs=True):
    w = Watch(page, dismiss_dialogs)
    w.before = page.evaluate(_STORAGE)
    w._start()
    return w


async def awatch(page, *, dismiss_dialogs=True):
    w = AsyncWatch(page, dismiss_dialogs)
    w.before = await page.evaluate(_STORAGE)
    w._start()
    return w


def check(records):
    """Return (ok, lines) for a list of pointer-press/1 and side-effects/1 records."""
    lines, ok = [], True
    for i, record in enumerate(records):
        schema = record.get("schema") if isinstance(record, dict) else None
        if schema == "pointer-press/1":
            hit = record.get("hit") or {}
            lines.append(f"{i} press {record['input']} at {record['x']},{record['y']} "
                         f"visible={record['visible_ratio']} top={hit.get('tag')} self={hit.get('self')}")
        elif schema == "side-effects/1":
            noisy = [k for k in ("requests", "dialogs", "page_errors", "popups") if record.get(k)]
            if record["storage"]["before"] != record["storage"]["after"]:
                noisy.append("storage")
            ok = ok and record.get("quiet") is True and not noisy
            lines.append(f"{i} side-effects {'quiet' if not noisy else 'NOT QUIET: ' + ', '.join(noisy)}")
        else:
            raise ValueError(f"record {i}: unknown schema {schema!r}")
    return ok, lines


def _load(text):
    text = text.strip()
    try:
        data = json.loads(text)
    except ValueError:
        data = [json.loads(line) for line in text.splitlines() if line.strip()]
    return data if isinstance(data, list) else [data]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check saved pointer-press/1 and side-effects/1 records.")
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check", help="summarize records; exit 1 if a side-effects record is not quiet")
    c.add_argument("file", help="JSON file (a record, a list, or JSON lines); - for stdin")
    c.add_argument("--json", action="store_true", help="print a JSON summary")
    args = parser.parse_args(argv)
    try:
        text = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
        ok, lines = check(_load(text))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"ok": ok, "records": len(lines), "lines": lines}, ensure_ascii=False))
    else:
        print("\n".join(lines + [f"{'PASS' if ok else 'FAIL'} {len(lines)} records"]))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
