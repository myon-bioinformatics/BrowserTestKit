#!/usr/bin/env python3
"""Record browser evidence as one looping GIF for a README: each shot is a page x viewport (x steps), captioned in Meiryo.

    uv run --group pages python tools/evidence_gif.py docs/evidence/browser.gif --serve . \\
        --shot "🏠 Top|/index.html|1280x800" --shot "📱 Top|/index.html|392x848"
    uv run --group pages python tools/evidence_gif.py docs/evidence/browser.gif --serve . --plan shots.json --force
    uv run --group pages python tools/evidence_gif.py runs/browser.gif --serve . --plan shots.json --git-commit --timestamp   # runs/browser-288cbed-20261113T021500Z.gif

A shot is ``label|url|WIDTHxHEIGHT`` or, in ``--plan`` JSON, ``{"label", "url", "viewport", "steps", "hold", "allow"}``. A step is
``{"click": sel}``, ``{"fill": [sel, text]}``, ``{"select": [sel, value]}``, ``{"press": key}``, ``{"scroll": y}``, ``{"wait": ms}``,
``{"eval": js}`` (each adds a frame showing what it did), and three that shape the GIF itself:

* 🔲 ``{"frame": sel}`` or ``{"frame": {"selector": sel, "shape": "rect", "color": "#e53935", "width": 4, "pad": 8, "keep": false}}``
  — one frame with the element marked by a coloured shape (``rect`` / ``ellipse`` / ``triangle`` around it, ``arrow``
  pointing at it; the same shape names as Ironmate's layers), so the viewer sees where the next step lands. The mark is
  an overlay that takes no clicks; it goes when the next step starts, or with ``keep`` stays until the next 🔲 / the shot's end.
  A mark also takes ``label`` (a callout), ``number`` (a badge) and ``spotlight`` (dim the rest); a list draws up to 8 marks at
  once, numbered. element_overlay.mark_svg() draws them (the same marks as ``element_overlay.py --format svg --shape``).

* ⏺ ``{"record": {"every": 200, "for": 2000, "clip": sel}}`` — frames every ``every`` ms for ``for`` ms (an animation, a
  transition); delays are the measured ones. With ``clip`` only that element's box is captured and laid on the last full
  screenshot, so a spinner costs its own few hundred bytes on the socket instead of the whole page.
* 🔍 ``{"zoom": sel}`` or ``{"zoom": {"selector": sel, "ms": 900, "steps": 6, "hold": 1200, "pad": 16, "scale": 2}}`` — the
  view eases onto the element (outlined) in ``steps`` frames over ``ms``, then holds; all of it is drawn from one capture.

For a served (``/``) shot every request leaving 127.0.0.1 is refused and listed under ``external`` (not a failure: the
evidence must not depend on the network); ``--map PREFIX=DIR`` answers a URL prefix (a pinned CDN asset) from a local
directory instead; ``"allow": ["text"]`` names page errors a fixture raises on purpose.

📦 Where the bytes go: Playwright takes each screenshot (PNG) in any engine (``--browser chromium|firefox|webkit``); a
screenshot byte-identical to the previous one is not sent again (the hash decides), and each shot's screenshots go to the
encoder page in one call per 4 MiB. That page is PySaiyanWeBoo's ``pysaiyanweboo.gif_recorder_document()``: the browser itself decodes,
fits, zooms and captions them on a canvas, and ``gif.js`` keeps only changed rectangles, picks one global palette, writes
delta frames and LZW — the GIF crosses the socket once, pixels never come back. Captions are drawn by the browser in Meiryo
(Japanese fallbacks), so no font file is bundled.

🎚️ Limits: ``--width/--height`` (canvas under a 32 px caption, multiples of 8), ``--colors`` (palette, ≤ 255),
``--max-bytes`` (e.g. ``800K``: fewer colours, then fewer ⏺ frames, until it fits; over budget is reported, not fatal),
``--max-frames`` (⏺ frames are cut first), ``--hold`` / ``--last-hold`` (ms per frame / on the last frame). GIF delays are
centiseconds, at least 2 (browsers stretch 0–1 cs to 10 cs), rounded on the real timeline so the total stays true.

Next to the GIF a receipt ``<name>.json`` records every shot's status, page errors and ``generator`` (the page's
``<meta name="generator">``), the encoder's report, what crossed the socket (``transfer``), the browser, and the plan itself
under ``plan`` — ``--plan <name>.json`` re-records the same GIF (the receipt is the recipe).

Exit codes: 0 every shot loaded without a page error or HTTP error; 1 some shot failed (the GIF is still written, ⛔ in its
caption, so the failure can be seen); 2 usage. Needs Playwright and PySaiyanWeBoo (``pysaiyanweboo``) (this repository's ``pages``
group); no Pillow, no image library.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import datetime
import functools
import hashlib
import http.server
import importlib.util
import json
import mimetypes
from pathlib import Path
import re
import sys
import threading
import time

CAPTION = 32
TOOLS = Path(__file__).resolve().parent


def _sibling(name: str):
    """A stdlib module next to this file (element_overlay, btk), loaded by path so the tool runs from anywhere."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader
    spec.loader.exec_module(module)
    return module


overlay = _sibling("element_overlay")       # 🔲 marks: the same drawer as element_overlay --format svg --shape
SCHEMA = "btk-evidence-gif/2"
STEP_KEYS = ("click", "fill", "select", "press", "scroll", "wait", "eval", "record", "zoom", "frame")
FRAME_MARK = "data-btk-frame"         # the overlay's attribute: what clear_frame removes
ENGINES = ("chromium", "firefox", "webkit")
BATCH_BYTES = 4 << 20                 # base64 per call to the encoder page: one shot is usually one call
MIN_EVERY = 20                        # ms: a GIF frame cannot be shorter (2 cs) without browsers stretching it
WEB_UI_HINT = ("needs PySaiyanWeBoo (pysaiyanweboo): run with `uv run --group pages` here, or install "
               "'pysaiyanweboo @ git+https://github.com/myon-bioinformatics/PySaiyanWeBoo@<commit>'")


def parse_viewport(text: str) -> tuple[int, int]:
    width, sep, height = text.lower().partition("x")
    if not sep or not width.isdigit() or not height.isdigit() or int(width) < 1 or int(height) < 1:
        raise ValueError(f"viewport must be WIDTHxHEIGHT: {text!r}")
    return int(width), int(height)


def parse_shot(text: str) -> dict:
    parts = text.split("|")
    if len(parts) != 3 or not parts[1]:
        raise ValueError(f"shot must be 'label|url|WIDTHxHEIGHT': {text!r}")
    return {"label": parts[0], "url": parts[1], "viewport": parts[2]}


def check_plan(shots: list, *, hold: int = 1200) -> list[dict]:
    """Shots with their viewport parsed; raises ValueError on anything this tool would not run."""
    if not shots:
        raise ValueError("no shots (use --shot or --plan)")
    checked = []
    for shot in shots:
        if not isinstance(shot, dict) or not isinstance(shot.get("url"), str) or not shot["url"]:
            raise ValueError(f"shot needs a url: {shot!r}")
        steps = [check_step(step) for step in shot.get("steps", [])]
        checked.append({"label": str(shot.get("label", shot["url"])), "url": shot["url"],
                        "viewport": parse_viewport(str(shot.get("viewport", "1280x800"))), "steps": steps,
                        "hold": int(shot.get("hold", hold)), "allow": [str(a) for a in shot.get("allow", [])]})
    return checked


def check_step(step) -> dict:
    """One step, with ⏺ record / 🔍 zoom options filled in and bounded; raises ValueError otherwise."""
    if not isinstance(step, dict) or len(step) != 1 or next(iter(step)) not in STEP_KEYS:
        raise ValueError(f"step must be one of {', '.join(STEP_KEYS)}: {step!r}")
    kind, value = next(iter(step.items()))
    if kind == "record":
        value = {} if value is None else value
        if not isinstance(value, dict) or set(value) - {"every", "for", "clip"}:
            raise ValueError(f"record takes every, for and clip: {step!r}")
        every, span = int(value.get("every", 200)), int(value.get("for", 2000))
        if every < MIN_EVERY or not 0 < span <= 60000 or (value.get("clip") is not None and not isinstance(value["clip"], str)):
            raise ValueError(f"record: every >= {MIN_EVERY} ms, 0 < for <= 60000 ms, clip a selector: {step!r}")
        return {"record": {"every": every, "for": span, "clip": value.get("clip")}}
    if kind == "zoom":
        value = {"selector": value} if isinstance(value, str) else value
        if not isinstance(value, dict) or not isinstance(value.get("selector"), str) or set(value) - {"selector", "ms", "steps", "hold", "pad", "scale"}:
            raise ValueError(f"zoom takes a selector (and ms, steps, hold, pad, scale): {step!r}")
        zoom = {"selector": value["selector"], "ms": int(value.get("ms", 900)), "steps": int(value.get("steps", 6)),
                "hold": int(value.get("hold", 1200)), "pad": int(value.get("pad", 16)), "scale": float(value.get("scale", 2))}
        if not (1 <= zoom["steps"] <= 30 and 0 <= zoom["ms"] <= 10000 and 0 <= zoom["hold"] <= 60000 and 1 <= zoom["scale"] <= 8):
            raise ValueError(f"zoom: 1-30 steps, ms <= 10000, hold <= 60000, scale 1-8: {step!r}")
        return {"zoom": zoom}
    if kind == "frame":
        items = value if isinstance(value, list) else [value]
        if not 1 <= len(items) <= 8:
            raise ValueError(f"frame takes 1-8 marks: {step!r}")
        marks = []
        for item in items:
            item = {"selector": item} if isinstance(item, str) else item
            if not isinstance(item, dict) or not isinstance(item.get("selector"), str) or set(item) - {"selector", "keep", *overlay.MARK_KEYS}:
                raise ValueError(f"a frame mark takes a selector (and keep, {', '.join(overlay.MARK_KEYS)}): {step!r}")
            if type(item.get("keep", False)) is not bool:
                raise ValueError(f"frame: keep true/false: {step!r}")
            marks.append({"selector": item["selector"], **overlay.check_mark(item), "keep": item.get("keep", False)})
        if len(marks) > 1:                                             # several marks: ① ② … unless a number is given
            for index, mark in enumerate(marks, 1):
                mark["number"] = index if mark["number"] is None else mark["number"]
        return {"frame": {"marks": marks, "keep": any(m.pop("keep") for m in marks)}}
    return step


def clear_frame(page, *, kept: bool = False) -> None:
    """Remove the 🔲 overlay before the next step acts (``kept``: also one drawn with ``keep``)."""
    page.evaluate("([mark, kept]) => document.querySelectorAll(kept ? `[${mark}]` : `[${mark}=once]`).forEach(e => e.remove())", [FRAME_MARK, kept])


def draw_frame(page, frame: dict) -> str:
    """Mark the elements (the first scrolled into view) with element_overlay's marks; returns the step's note."""
    page.locator(frame["marks"][0]["selector"]).first.scroll_into_view_if_needed(timeout=5000)
    marks = []
    for mark in frame["marks"]:
        box = page.locator(mark["selector"]).first.bounding_box()
        if not box:
            raise ValueError(f"frame: {mark['selector']} has no box (hidden?)")
        marks.append({**mark, "box": box})
    clear_frame(page, kept=True)
    size = page.evaluate("() => [document.documentElement.clientWidth, document.documentElement.clientHeight]")
    svg = overlay.mark_svg(marks, size, attributes=f' {FRAME_MARK}="{"keep" if frame["keep"] else "once"}"')
    page.evaluate("""svg => { document.documentElement.insertAdjacentHTML('beforeend', svg);
        Object.assign(document.documentElement.lastElementChild.style, { position: 'fixed', left: '0', top: '0', zIndex: '2147483647',
                                                                         pointerEvents: 'none', overflow: 'visible' }); }""", svg)
    return "🔲 " + " ".join(m.get("label") or m["selector"] for m in frame["marks"])


def parse_bytes(text: str | int | None) -> int | None:
    """``800K`` / ``2M`` / ``900000`` → bytes; None or 0 → no budget."""
    if text in (None, "", 0, "0"):
        return None
    raw = str(text).strip().upper()
    scale = {"K": 1024, "M": 1024 * 1024}.get(raw[-1:], 1)
    number = raw[:-1] if scale > 1 else raw
    if not number.replace(".", "", 1).isdigit() or float(number) <= 0:
        raise ValueError(f"--max-bytes must be a size like 800K or 2M: {text!r}")
    return int(float(number) * scale)


@contextlib.contextmanager
def serve(directory: Path | None):
    """``http://127.0.0.1:<port>`` for ``directory`` (quiet, threaded), or None without a directory."""
    if directory is None:
        yield None
        return

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(directory)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def is_local(url: str) -> bool:
    return url.startswith(("http://127.0.0.1", "http://localhost", "data:", "blob:", "about:")) or not url


def parse_map(text: str) -> tuple[str, Path]:
    prefix, sep, directory = text.partition("=")
    if not sep or not prefix.startswith(("http://", "https://")) or not Path(directory).is_dir():
        raise ValueError(f"--map must be URL_PREFIX=DIR with an existing DIR: {text!r}")
    return prefix.rstrip("/") + "/", Path(directory).resolve()


def answer_mapped(route, maps: list[tuple[str, Path]], external: list[str]) -> None:
    """A mapped URL from its local file (404 if missing or outside DIR); any other non-local URL is refused and listed."""
    url = route.request.url
    for prefix, directory in maps:
        if url.startswith(prefix):
            target = (directory / url[len(prefix):].split("?", 1)[0].split("#", 1)[0]).resolve()
            if target.is_relative_to(directory) and target.is_file():
                route.fulfill(status=200, body=target.read_bytes(),
                              content_type=mimetypes.guess_type(target.name)[0] or "application/octet-stream")
            else:
                route.fulfill(status=404, body=b"")
            return
    external.append(url[:200])
    route.abort()


def route_handler(maps: list[tuple[str, Path]], external: list[str]):
    """One-argument route handler bound to this shot's lists (Playwright passes the request too to two-argument ones)."""
    return lambda route: answer_mapped(route, maps, external)


def resolve(url: str, origin: str | None) -> str:
    if url.startswith("/"):
        if origin is None:
            raise ValueError(f"{url!r} needs --serve DIR")
        return origin + url
    return url


def run_step(page, step: dict) -> str:
    """Do one interaction step and wait for it to settle; returns its caption note (⏺ / 🔍 are captured by capture_step)."""
    kind, value = next(iter(step.items()))
    if kind == "frame":
        return draw_frame(page, value)
    if kind == "click":
        page.click(value)
    elif kind == "fill":
        page.fill(value[0], value[1])
    elif kind == "select":
        page.select_option(value[0], value[1])
    elif kind == "press":
        page.keyboard.press(value)
    elif kind == "scroll":
        page.evaluate("y => window.scrollTo(0, y)", value)
    elif kind == "eval":
        page.evaluate(value)
    page.wait_for_timeout(int(value) if kind == "wait" else 400)
    if kind == "select":
        return f"select {value[0]} → {value[1]}"
    if kind == "eval":
        return "eval ⚙️"                                       # the script itself is in the plan, not the caption
    return f"fill {value[0]}" if kind == "fill" else f"{kind} {value}"


def console_error(msg, errors: list, external: list) -> None:
    """A console error, with the resource URL Chromium attaches; a refused external request was already listed."""
    if msg.type != "error":
        return
    where = (msg.location or {}).get("url", "")
    if msg.text.startswith("Failed to load resource") and not is_local(where):
        if where not in external:
            external.append(where[:200])
        return
    errors.append(f"console: {msg.text}" + (f" @ {where}" if where else "")[:300])


def page_generator(page) -> str | None:
    """``<meta name="generator">`` of the page just loaded (PySaiyanWeBoo's web_ui writes ``web_ui <commit>``), so the
    receipt says which generator drew what the GIF shows; None when the page has none."""
    with contextlib.suppress(Exception):
        content = page.evaluate("() => document.querySelector('meta[name=generator]')?.content ?? null")
        return content[:120] if isinstance(content, str) else None
    return None


class Captures:
    """One shot's screenshots, held until the shot ends (the caption shows the final ✅/⛔), sent without repeats."""

    def __init__(self, transfer: dict, last: list):
        self.items: list[dict] = []
        self.transfer = transfer
        self.last = last                      # [hash of the last full screenshot sent], shared across shots

    def full(self, page, width: int, note: str, delay: int, group: int = 0, **extra) -> bytes:
        png = page.screenshot(type="png")
        self.transfer["screenshots"] += 1
        self.transfer["png_bytes"] += len(png)
        digest = hashlib.sha256(png).digest()
        item = {"note": note, "delay": delay, "group": group, "cssWidth": width, **extra}
        if digest == self.last[0]:
            self.transfer["identical"] += 1  # the encoder page still has it: send nothing
        else:
            item["png"] = png
            self.last[0] = digest
        self.items.append(item)
        return png

    def patch(self, page, box: dict, note: str, delay: int, group: int) -> None:
        png = page.screenshot(type="png", clip=box)
        self.transfer["screenshots"] += 1
        self.transfer["png_bytes"] += len(png)
        self.transfer["patches"] += 1
        self.items.append({"note": note, "delay": delay, "group": group, "patch": {"png": png, "x": box["x"], "y": box["y"]}})


def capture_step(page, step: dict, captures: Captures, width: int, hold: int, budget: list, group: int) -> str:
    """⏺ record or 🔍 zoom: the frames this step adds, within the shared frame budget; returns the step's note."""
    kind, value = next(iter(step.items()))
    if kind == "zoom":
        target = page.locator(value["selector"]).first
        target.scroll_into_view_if_needed(timeout=5000)
        box = target.bounding_box()
        if not box:
            raise ValueError(f"zoom: {value['selector']} has no box (hidden?)")
        note = f"🔍 {value['selector']}"
        captures.full(page, width, note, value["hold"], zoom={"x": box["x"], "y": box["y"], "w": box["width"], "h": box["height"]},
                      steps=value["steps"], ms=value["ms"], hold=value["hold"], pad=value["pad"], maxScale=value["scale"])
        return note                                                     # its frames are in static_frames already
    every, span, clip = value["every"], value["for"], value["clip"]
    planned = max(1, min(span // every, budget[0] + 1))               # the first frame is in static_frames already
    box = None
    if clip:
        box = page.locator(clip).first.bounding_box()
        if not box:
            raise ValueError(f"record: {clip} has no box (hidden?)")
        box = {k: round(box[k]) for k in ("x", "y", "width", "height")}
    note = "⏺ " + (clip or "") + f" {every} ms"
    captures.full(page, width, f"{note} 1/{planned}", every, group)    # the base every patch is laid on
    start = previous = time.monotonic()
    for index in range(2, planned + 1):
        wait = start + (index - 1) * every / 1000 - time.monotonic()
        if wait > 0:
            page.wait_for_timeout(wait * 1000)
        now = time.monotonic()
        captures.items[-1]["delay"] = max(MIN_EVERY, round((now - previous) * 1000))   # what the viewer waited, measured
        previous = now
        if box:
            captures.patch(page, box, f"{note} {index}/{planned}", every, group)
        else:
            captures.full(page, width, f"{note} {index}/{planned}", every, group)
    captures.items[-1]["delay"] = max(captures.items[-1]["delay"], hold)
    budget[0] -= planned - 1
    return f"{note} ×{planned}"


def send(kernel, items: list[dict], transfer: dict) -> None:
    """Captioned items to the encoder page, base64, in calls of at most BATCH_BYTES."""
    batch, size = [], 0
    for item in items:
        wire = dict(item)
        for key in ("png",):
            if key in wire:
                wire[key] = base64.b64encode(wire[key]).decode("ascii")
                size += len(wire[key])
        if "patch" in wire:
            wire["patch"] = {**wire["patch"], "png": base64.b64encode(wire["patch"]["png"]).decode("ascii")}
            size += len(wire["patch"]["png"])
        batch.append(wire)
        if size >= BATCH_BYTES:
            kernel.evaluate("items => rec.add(items)", batch)
            transfer["calls"] += 1
            transfer["sent_bytes"] += size
            batch, size = [], 0
    if batch:
        kernel.evaluate("items => rec.add(items)", batch)
        transfer["calls"] += 1
        transfer["sent_bytes"] += size


def slug(text: str, limit: int = 32) -> str:
    """A file-name part that still says what it is: letters and digits of any script, the rest (emoji, marks, /) as one dash."""
    return re.sub(r"[\W_]+", "-", text).strip("-").lower()[:limit].strip("-") or "x"


def still_stem(shot: int, label: str, viewport: tuple[int, int], number: int, what: str) -> str:
    """``01-top-1280x800-02-save``: shot number and label, viewport, the still's number in the shot (00 = the page as loaded)
    and what is marked, so a name read alone says where it came from (at most 64 characters, PySaiyanWeBoo's name-part limit)."""
    return f"{shot:02d}-{slug(label, 24)}-{viewport[0]}x{viewport[1]}-{number:02d}-{slug(what, 16)}"


def frame_what(frame: dict) -> str:
    return " ".join(m.get("label") or m["selector"] for m in frame["marks"])


def output_names(out: Path, shots: list[dict], *, stills: Path | None = None, stills_prefix: str = "", stills_suffix: str = "",
                 run_details: tuple = (), force: bool = False, now=None) -> tuple[Path, dict]:
    """🏷️ Every name this run writes, decided before the browser starts: ``(gif, {(shot, number): still})``.

    PySaiyanWeBoo's output-name rules, the same as its demo recorder: ``run_details`` (``timestamp`` / ``git-commit``, in the order
    given) are appended to the GIF's stem and to every still (one time and commit for the whole run), and an existing file is
    refused unless ``force``. Without PySaiyanWeBoo (🪶 stills only) the names are ``<prefix><stem><suffix>.png`` and run details
    are refused."""
    stems = {}
    for index, shot in enumerate(shots, 1):
        frames = [frame_what(step["frame"]) for step in shot["steps"] if "frame" in step]
        for number, what in enumerate(["page", *frames]):
            stems[(index, number)] = still_stem(index, shot["label"], shot["viewport"], number, what)
    try:
        import pysaiyanweboo as pswb
    except ImportError:
        pswb = None
    if pswb is None:
        if run_details:
            raise ValueError("--timestamp / --git-commit use PySaiyanWeBoo's naming rules: install pysaiyanweboo (`uv run --group pages`)")
        paths = {key: stills / f"{stills_prefix}{stem}{stills_suffix}.png" for key, stem in stems.items()} if stills is not None else {}
        taken = [str(p) for p in paths.values() if p.exists()]
        if taken and not force:
            raise FileExistsError("refusing to overwrite " + ", ".join(taken) + " (add --force to replace them)")
        return out, paths
    now = now or datetime.datetime.now(datetime.timezone.utc)                # one time for the GIF and every still
    gif = pswb.gif_targets(out.parent, [out.stem], run_details=run_details, force=force, now=now, ext=out.suffix or ".gif")[out.stem]
    if stills is None:
        return gif, {}
    named = pswb.gif_targets(stills, list(stems.values()), prefix=stills_prefix, suffix=stills_suffix, run_details=run_details,
                             force=force, now=now, ext=".png")
    return gif, {key: named[stem] for key, stem in stems.items()}


def seal_stills(directory: Path, written: list[dict], engine: str, sha: str | None, *, ok: bool) -> dict:
    """📸 The stills as a capture bundle ``btk.py check-capture-evidence`` reads: ``evidence-<engine>.json`` next to them, ``project`` = the
    engine, ``stage`` complete only when every shot passed and ``sha`` (``--sha``) is a full commit; then checked here by that validator.

    ``python -S tools/btk.py check-capture-evidence DIR --pattern 'evidence-*.json' --expect chromium --captures A.png,B.png --sha SHA``"""
    kit = _sibling("btk")
    name = f"evidence-{engine}.json"
    target = directory / name
    target.unlink(missing_ok=True)                                     # an old complete receipt must not cover this run
    directory.mkdir(parents=True, exist_ok=True)
    captures = [{key: still[key] for key in ("file", "bytes", "sha256", "width", "height")} for still in written]
    full = isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40}", sha) is not None
    receipt = {"stage": "complete" if ok and full and captures else "failed", "project": engine, "sha": sha,
               "generator": "BrowserTestKit evidence_gif --stills", "captures": captures}
    if not full:
        receipt["error"] = "--sha is not a full lowercase commit: the stills are not a checkable bundle"
    elif not ok:
        receipt["error"] = "a shot failed: failed bundles cannot cover success"
    target.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if receipt["stage"] == "complete":
        passed, _, problems = kit.check_capture_evidence(directory, name, [engine], [c["file"] for c in captures], sha)
        if not passed:                                                 # never leave a complete receipt the validator rejects
            receipt.update(stage="failed", error="; ".join(problems))
            target.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"receipt": name, "stage": receipt["stage"], **({"error": receipt["error"]} if "error" in receipt else {})}


def static_frames(shots: list[dict]) -> int:
    """Frames every shot needs whatever happens: the page, each step, each zoom's steps (⏺ frames come out of the rest)."""
    return sum(1 + sum(next(iter(s.values()))["steps"] if "zoom" in s else 1 for s in shot["steps"]) for shot in shots)


def record(out: Path, shots: list[dict], *, serve_dir: Path | None = None, width: int = 720, height: int = 480,
           sha: str | None = None, plan: list | None = None, maps: list[tuple[str, Path]] | None = None,
           browser_name: str = "chromium", colors: int = 255, max_bytes: int | None = None, max_frames: int = 300,
           last_hold: int = 2000, stills: Path | None = None, stills_prefix: str = "", stills_suffix: str = "",
           executable: str = "auto", still_paths: dict | None = None) -> dict:
    """Run every shot in ``browser_name``, write ``out`` (GIF) and ``out.with_suffix('.json')`` (receipt); returns the receipt.

    📸 ``stills``: also write the page as loaded and every 🔲 frame of each shot as a PNG there (``still_paths`` from output_names; computed
    here when not given), sealed as a
    capture bundle (seal_stills) that ``btk.py check-capture-evidence`` validates.
    🪶 Without PySaiyanWeBoo (a nano environment) only the stills and the receipt are written, and ``stills`` is required.
    🧭 ``executable``: ``auto`` launches Playwright's build, or one already on disk when that revision is missing (btk.find_browser);
    ``bundled`` only Playwright's; a path that browser."""
    try:
        import pysaiyanweboo as pswb
    except ImportError as exc:
        if stills is None:
            raise RuntimeError(WEB_UI_HINT + "; without it, --stills DIR records PNG stills only") from exc
        pswb = None
    from playwright.sync_api import sync_playwright
    if browser_name not in ENGINES:
        raise ValueError(f"--browser must be one of {', '.join(ENGINES)}")
    written: list[dict] = []
    if stills is not None and still_paths is None:
        out, still_paths = output_names(out, shots, stills=stills, stills_prefix=stills_prefix, stills_suffix=stills_suffix, force=True)
    results = []
    transfer = {"screenshots": 0, "png_bytes": 0, "identical": 0, "patches": 0, "calls": 0, "sent_bytes": 0, "gif_bytes": 0}
    budget = [max(0, max_frames - static_frames(shots))]               # frames ⏺ steps may still take
    last = [b""]
    started = time.monotonic()
    with serve(serve_dir) as origin, sync_playwright() as pw:
        engine = getattr(pw, browser_name)
        if executable == "auto":
            found = _sibling("btk").find_browser(browser_name, engine.executable_path)
        elif executable == "bundled":
            found = {"source": "playwright", "path": None}
        else:
            found = {"source": "given", "path": str(Path(executable))}
        browser = engine.launch(**({"executable_path": found["path"]} if found["path"] else {}))
        try:
            kernel = None
            if pswb is not None:
                kernel = browser.new_context().new_page()                # its own context: no routes, no network
                kernel.set_content(pswb.gif_recorder_document())
                kernel.evaluate("o => { window.rec = new UiGif.Recorder(o); }", {"width": width, "height": height, "caption": CAPTION})
            total = len(shots)
            for index, shot in enumerate(shots, 1):
                w, h = shot["viewport"]
                context = browser.new_context(viewport={"width": w, "height": h}, has_touch=w < 600, is_mobile=w < 600 and browser_name != "firefox")
                page = context.new_page()
                page.set_default_timeout(10000)            # a missing selector fails the shot in 10 s, not Playwright's 30 s
                errors: list[str] = []
                external: list[str] = []
                generator = None
                if shot["url"].startswith("/"):
                    context.route(lambda u: not is_local(u), route_handler(maps or [], external))
                page.on("pageerror", lambda exc, errs=errors: errs.append(f"pageerror: {exc}"[:300]))
                page.on("console", lambda msg, errs=errors, ext=external: console_error(msg, errs, ext))
                captures, done = Captures(transfer, last), []

                def keep_still(png: bytes, what: str, shot=index) -> None:
                    if stills is None:
                        return
                    stills.mkdir(parents=True, exist_ok=True)
                    number = sum(1 for x in written if x["shot"] == shot)
                    target = still_paths[(shot, number)]
                    target.write_bytes(png)
                    name = target.name
                    width, height = _sibling("btk").png_dimensions(png)
                    written.append({"file": name, "shot": shot, "what": what, "bytes": len(png), "sha256": hashlib.sha256(png).hexdigest(),
                                    "width": width, "height": height})
                try:
                    response = page.goto(resolve(shot["url"], origin), wait_until="load")
                    if response is not None and response.status >= 400:
                        errors.append(f"http {response.status}")
                    page.wait_for_timeout(600)
                    generator = page_generator(page)
                    keep_still(captures.full(page, w, "", shot["hold"]), "page")
                    for group, step in enumerate(shot["steps"], 1):
                        clear_frame(page)                              # a 🔲 mark shows on its own frame only (unless keep)
                        if "record" in step or "zoom" in step:
                            done.append(capture_step(page, step, captures, w, shot["hold"], budget, group + 1000 * index))
                        else:
                            done.append(run_step(page, step))
                            png = captures.full(page, w, done[-1], shot["hold"])
                            if "frame" in step:                        # 📸 a marked view is what a slide wants
                                keep_still(png, frame_what(step["frame"]))
                except Exception as exc:  # noqa: BLE001 - the failure is the evidence; keep recording the other shots
                    errors.append(f"{type(exc).__name__}: {exc}".splitlines()[0][:300])
                    with contextlib.suppress(Exception):
                        captures.full(page, w, "⛔", shot["hold"])
                finally:
                    allowed = [e for e in errors if any(a in e for a in shot["allow"])]
                    errors = [e for e in errors if e not in allowed]
                    ok = not errors
                    for item in captures.items:
                        note = item.pop("note")
                        item["caption"] = f"{'✅' if ok else '⛔'} {index}/{total} {shot['label']} · {w}×{h}" + (f" · {note}" if note else "")
                        item["ok"] = ok
                    if index == total and captures.items:
                        captures.items[-1]["delay"] = max(captures.items[-1]["delay"], last_hold)
                    if captures.items and kernel is not None:
                        send(kernel, captures.items, transfer)
                    context.close()
                results.append({"label": shot["label"], "url": shot["url"], "viewport": f"{w}x{h}", "steps": done,
                                "status": "pass" if ok else "fail", "errors": errors, "allowed": allowed,
                                "external": sorted(set(external)), "generator": generator})
            captured = time.monotonic()
            encoded = None
            if kernel is not None:
                encoded = kernel.evaluate("o => rec.encodeBase64(o)", {"colors": colors, "maxBytes": max_bytes or 0,
                                                                         "comment": f"BrowserTestKit evidence_gif {sha or ''}".strip()})
            version = browser.version
        finally:
            browser.close()
    out.parent.mkdir(parents=True, exist_ok=True)
    if encoded is not None:
        data = base64.b64decode(encoded["gif"])
        transfer["gif_bytes"] = len(data)
        report = encoded["report"]
        report["capture_s"] = round(captured - started, 1)
        out.write_bytes(data)
        gif = {"gif": out.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "frames": report["frames"]}
        encoder = {"module": "PySaiyanWeBoo gif.js", "web_ui": pswb.installed_commit() or "checkout", **report}   # "web_ui": the key fleet records read
    else:                                                              # 🪶 no PySaiyanWeBoo: the stills are the evidence
        gif = {"gif": None, "bytes": 0, "sha256": None, "frames": 0}
        encoder = {"module": None, "skipped": "PySaiyanWeBoo is not installed: stills only",
                   "colors": 0, "overBudget": False, "capture_s": round(captured - started, 1)}
    bundle = seal_stills(stills, written, browser_name, sha, ok=all(r["status"] == "pass" for r in results)) if stills is not None else None
    receipt = {"schema": SCHEMA, **gif, "canvas": f"{width}x{height + CAPTION}", "browser": f"{browser_name} {version}",
               "executable": found, "recorded": datetime.date.today().isoformat(), "commit": sha,
               "stills": {"dir": str(stills) if stills is not None else None, "files": written, **(bundle or {})},
               "encoder": encoder,
               "limits": {"colors": colors, "max_bytes": max_bytes, "max_frames": max_frames, "last_hold_ms": last_hold},
               "transfer": transfer, "mapped": [prefix for prefix, _ in maps or []],
               "summary": {"pass": sum(r["status"] == "pass" for r in results), "fail": sum(r["status"] == "fail" for r in results)},
               "shots": results, "plan": plan if plan is not None else []}
    out.with_suffix(".json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog=__doc__.split("\n\n", 1)[1])
    parser.add_argument("out", type=Path, help="GIF to write; the receipt goes next to it as .json")
    parser.add_argument("--shot", action="append", default=[], metavar="LABEL|URL|WxH", help="one shot (repeatable)")
    parser.add_argument("--plan", type=Path, help="JSON list of shots (with steps)")
    parser.add_argument("--serve", type=Path, metavar="DIR", help="serve DIR on 127.0.0.1 for URLs starting with /")
    parser.add_argument("--map", action="append", default=[], metavar="PREFIX=DIR",
                        help="answer URLs under PREFIX from DIR (a pinned CDN asset served locally; repeatable)")
    parser.add_argument("--width", type=int, default=720, help="canvas width (default 720)")
    parser.add_argument("--height", type=int, default=480, help="canvas height below the caption (default 480)")
    parser.add_argument("--sha", help="tested commit, recorded in the receipt")
    parser.add_argument("--browser", choices=ENGINES, default="chromium", help="engine that takes the screenshots and encodes (default chromium)")
    parser.add_argument("--colors", type=int, default=255, help="palette size, 2-255 (default 255)")
    parser.add_argument("--max-bytes", help="GIF size budget such as 800K or 2M: fewer colours, then fewer ⏺ frames")
    parser.add_argument("--max-frames", type=int, default=300, help="frame cap; ⏺ record steps get what the other frames leave (default 300)")
    parser.add_argument("--hold", type=int, default=1200, help="ms each frame stays (default 1200; a shot's \"hold\" wins)")
    parser.add_argument("--stills", type=Path, metavar="DIR", help="📸 also write the page as loaded and every 🔲 frame as PNG here "
                        "(the only output when PySaiyanWeBoo is not installed)")
    parser.add_argument("--stills-prefix", default="", help="📸 text before every still's name, e.g. a run or a repository")
    parser.add_argument("--stills-suffix", default="", help="📸 text after every still's name, before .png, e.g. -dark")
    parser.add_argument("--timestamp", action="append_const", const="timestamp", dest="run_details", default=[],
                        help="🏷️ add when this run started (UTC, -20261113T021500Z) to the GIF's and every still's name, so each run keeps its files")
    parser.add_argument("--git-commit", action="append_const", const="git-commit", dest="run_details",
                        help="🏷️ add the current directory's git commit (-288cbed, -288cbed-dirty); run details follow the order given")
    parser.add_argument("--force", action="store_true", help="🏷️ replace an existing GIF or still (otherwise refused, as PySaiyanWeBoo's recorder does)")
    parser.add_argument("--executable", default="auto", metavar="auto|bundled|PATH",
                        help="🧭 auto (default): Playwright's browser, or one already on disk when its revision is missing; "
                             "bundled: Playwright's only; PATH: that browser")
    parser.add_argument("--last-hold", type=int, default=2000, help="ms the last frame stays before the loop restarts (default 2000)")
    args = parser.parse_args(argv)
    try:
        raw = [parse_shot(s) for s in args.shot]
        if args.plan:
            loaded = json.loads(args.plan.read_text(encoding="utf-8"))
            raw += loaded["plan"] if isinstance(loaded, dict) else loaded     # a receipt is also a plan
        shots = check_plan(raw, hold=args.hold)
        maps = [parse_map(m) for m in args.map]
        max_bytes = parse_bytes(args.max_bytes)
        if args.serve is not None and not args.serve.is_dir():
            raise ValueError(f"--serve is not a directory: {args.serve}")
        if not 2 <= args.colors <= 255 or args.max_frames < len(shots) or args.width % 8 or args.height % 8:
            raise ValueError("--colors 2-255, --max-frames at least one per shot, --width/--height multiples of 8")
        out, still_paths = output_names(args.out, shots, stills=args.stills, stills_prefix=args.stills_prefix, stills_suffix=args.stills_suffix,
                                        run_details=tuple(dict.fromkeys(args.run_details)), force=args.force)
    except (ValueError, OSError, argparse.ArgumentTypeError) as exc:
        parser.exit(2, f"⛔ {exc}\n")
    try:
        receipt = record(out, shots, serve_dir=args.serve, width=args.width, height=args.height, sha=args.sha, plan=raw, maps=maps,
                         browser_name=args.browser, colors=args.colors, max_bytes=max_bytes, max_frames=args.max_frames, last_hold=args.last_hold,
                         stills=args.stills, stills_prefix=args.stills_prefix, stills_suffix=args.stills_suffix, executable=args.executable,
                         still_paths=still_paths)
    except RuntimeError as exc:
        parser.exit(2, f"⛔ {exc}\n")
    for shot in receipt["shots"]:
        print(f"{'✅' if shot['status'] == 'pass' else '⛔'} {shot['label']} {shot['viewport']}"
              + (f" 🌐 {len(shot['external'])}" if shot["external"] else "") + "".join(f"\n   ⚠️ {e}" for e in shot["errors"]))
    s, e, x = receipt["summary"], receipt["encoder"], receipt["transfer"]
    if receipt["stills"]["files"]:
        bundle = receipt["stills"]
        print(f"📸 {bundle['dir']} × {len(bundle['files'])} 🧾 {bundle['receipt']} {'✅' if bundle['stage'] == 'complete' else '⚠️'} {bundle['stage']}"
              + (f" ({bundle['error']})" if "error" in bundle else ""))
    if receipt["executable"]["path"]:
        print(f"🧭 {receipt['executable']['source']} {receipt['executable']['path']}")
    if receipt["gif"] is None:
        print(f"🪶 {e['skipped']} ✅ {s['pass']} ⛔ {s['fail']}")
        return 1 if s["fail"] else 0
    print(f"🎞️ {out} 🖼️ {receipt['frames']} 📦 {receipt['bytes'] // 1024} KiB 🎨 {e['colors']} ✅ {s['pass']} ⛔ {s['fail']}"
          + (" ⚠️ over --max-bytes" if e["overBudget"] else ""))
    print(f"🔌 📸 {x['screenshots']} ({x['png_bytes'] // 1024} KiB) ♻️ {x['identical']} ✂️ {x['patches']} 📤 {x['sent_bytes'] // 1024} KiB / {x['calls']} calls 📥 {x['gif_bytes'] // 1024} KiB")
    return 1 if s["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
