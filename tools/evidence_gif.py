#!/usr/bin/env python3
"""Record browser evidence as one looping GIF for a README: each shot is a page x viewport (x steps), captioned in Meiryo.

    python tools/evidence_gif.py docs/evidence/browser.gif --serve . \\
        --shot "🏠 Top|/index.html|1280x800" --shot "📱 Top|/index.html|392x848"
    python tools/evidence_gif.py docs/evidence/browser.gif --serve . --plan shots.json

A shot is ``label|url|WIDTHxHEIGHT`` or, in ``--plan`` JSON, ``{"label", "url", "viewport", "steps"}`` where each step is one of
``{"click": sel}``, ``{"fill": [sel, text]}``, ``{"select": [sel, value]}``, ``{"press": key}``, ``{"scroll": y}``, ``{"wait": ms}`` or ``{"eval": js}``; every
step adds a frame, so the GIF shows what the step did. For a served (``/``) shot every request leaving 127.0.0.1 is refused and
listed under ``external`` (not a failure: the evidence must not depend on the network); other page errors name their URL.
``--map PREFIX=DIR`` answers requests under a URL prefix (a pinned CDN asset such as jsDelivr's web-ui CSS) from a local
checkout instead, so the page is recorded styled as published without the network. ``"allow": ["text", ...]`` names page errors a fixture raises on purpose
(a deliberately wrong SRI hash): they stay in the receipt under ``allowed`` but do not fail the shot. A URL starting with ``/`` is served from ``--serve DIR`` (a local
http.server, never the network). Frames are fitted into one canvas (default 720x480, multiples of 8) under a 32 px caption
that the browser itself renders in Meiryo (with Japanese fallbacks), so no font file is bundled.

Encoding follows PySaiyanWeBoo's ``tool/record_demo.py``: one shared palette (no dither, so frames never flicker), delays in
10 ms units, looping forever, and frames written as deltas (``optimize=True``). Next to the GIF a receipt ``<name>.json``
records every shot's status and page errors, the GIF's sha256 and the browser version, and keeps the plan itself under ``plan``,
so ``--plan <name>.json`` re-records the same GIF (the receipt is the recipe).

Exit codes: 0 every shot loaded without a page error or HTTP error; 1 some shot failed (the GIF is still written, ⛔ in its
caption, so the failure can be seen); 2 usage. Needs Playwright (Chromium) and Pillow from the caller's environment
(`pip install pillow`, as PySaiyanWeBoo's recorder does): this repository's own lock stays Pillow-free on purpose.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import functools
import hashlib
import html
import http.server
import io
import json
import mimetypes
from pathlib import Path
import sys
import threading

CAPTION = 32
FONT = "Meiryo, 'メイリオ', 'Hiragino Sans', 'Noto Sans CJK JP', 'Yu Gothic', sans-serif"
SCHEMA = "btk-evidence-gif/1"
STEP_KEYS = ("click", "fill", "select", "press", "scroll", "wait", "eval")


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


def check_plan(shots: list) -> list[dict]:
    """Shots with their viewport parsed; raises ValueError on anything this tool would not run."""
    if not shots:
        raise ValueError("no shots (use --shot or --plan)")
    checked = []
    for shot in shots:
        if not isinstance(shot, dict) or not isinstance(shot.get("url"), str) or not shot["url"]:
            raise ValueError(f"shot needs a url: {shot!r}")
        steps = shot.get("steps", [])
        for step in steps:
            if not isinstance(step, dict) or len(step) != 1 or next(iter(step)) not in STEP_KEYS:
                raise ValueError(f"step must be one of {', '.join(STEP_KEYS)}: {step!r}")
        checked.append({"label": str(shot.get("label", shot["url"])), "url": shot["url"],
                        "viewport": parse_viewport(str(shot.get("viewport", "1280x800"))), "steps": steps,
                        "hold": int(shot.get("hold", 1200)), "allow": [str(a) for a in shot.get("allow", [])]})
    return checked


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
    kind, value = next(iter(step.items()))
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


def caption_png(browser, text: str, width: int, ok: bool) -> bytes:
    page = browser.new_page(viewport={"width": width, "height": CAPTION})
    try:
        color = "#1b5e20" if ok else "#b71c1c"
        page.set_content(f"<body style='margin:0'><div style=\"height:{CAPTION}px;line-height:{CAPTION}px;padding:0 8px;"
                         f"font:14px/{CAPTION}px {html.escape(FONT)};color:#fff;background:{color};white-space:nowrap;"
                         f"overflow:hidden;text-overflow:ellipsis\">{html.escape(text)}</div>")
        return page.screenshot(type="png")
    finally:
        page.close()


def compose(shot_png: bytes, caption: bytes, width: int, height: int):
    """The screenshot fitted (aspect kept, centred) into width x height under its caption."""
    from PIL import Image
    shot = Image.open(io.BytesIO(shot_png)).convert("RGB")
    scale = min(width / shot.width, height / shot.height, 1.0)
    shot = shot.resize((max(1, round(shot.width * scale)), max(1, round(shot.height * scale))), Image.Resampling.LANCZOS)
    frame = Image.new("RGB", (width, height + CAPTION), "#eceff1")
    frame.paste(Image.open(io.BytesIO(caption)).convert("RGB"), (0, 0))
    frame.paste(shot, ((width - shot.width) // 2, CAPTION + (height - shot.height) // 2))
    return frame


def encode(frames: list, delays: list[int]) -> bytes:
    """One GIF: shared palette from frames across the run, no dither, 10 ms delays, looping, delta frames."""
    from PIL import Image
    sample = frames[::max(1, len(frames) // 16)]
    sheet = Image.new("RGB", (sample[0].width, sample[0].height * len(sample)))
    for i, frame in enumerate(sample):
        sheet.paste(frame, (0, i * frame.height))
    palette = sheet.quantize(colors=255)
    encoded = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in frames]
    out = io.BytesIO()
    encoded[0].save(out, format="GIF", save_all=True, append_images=encoded[1:],
                    duration=[max(20, round(d / 10) * 10) for d in delays], loop=0, optimize=True)
    return out.getvalue()


def record(out: Path, shots: list[dict], *, serve_dir: Path | None = None, width: int = 720, height: int = 480,
           sha: str | None = None, plan: list | None = None, maps: list[tuple[str, Path]] | None = None) -> dict:
    """Run every shot in Chromium, write ``out`` (GIF) and ``out.with_suffix('.json')`` (receipt); returns the receipt."""
    from playwright.sync_api import sync_playwright
    frames, delays, results = [], [], []
    with serve(serve_dir) as origin, sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            total = len(shots)
            for index, shot in enumerate(shots, 1):
                w, h = shot["viewport"]
                context = browser.new_context(viewport={"width": w, "height": h}, has_touch=w < 600, is_mobile=w < 600)
                page = context.new_page()
                page.set_default_timeout(10000)            # a missing selector fails the shot in 10 s, not Playwright's 30 s
                errors: list[str] = []
                external: list[str] = []
                if shot["url"].startswith("/"):
                    context.route(lambda u: not is_local(u), route_handler(maps or [], external))
                page.on("pageerror", lambda exc, errs=errors: errs.append(f"pageerror: {exc}"[:300]))
                page.on("console", lambda msg, errs=errors, ext=external: console_error(msg, errs, ext))
                done, pngs = [], []
                try:
                    response = page.goto(resolve(shot["url"], origin), wait_until="load")
                    if response is not None and response.status >= 400:
                        errors.append(f"http {response.status}")
                    page.wait_for_timeout(600)
                    pngs.append(("", page.screenshot(type="png")))
                    for step in shot["steps"]:
                        done.append(run_step(page, step))
                        pngs.append((done[-1], page.screenshot(type="png")))
                except Exception as exc:  # noqa: BLE001 - the failure is the evidence; keep recording the other shots
                    errors.append(f"{type(exc).__name__}: {exc}".splitlines()[0][:300])
                    with contextlib.suppress(Exception):
                        pngs.append(("⛔", page.screenshot(type="png")))
                finally:
                    allowed = [e for e in errors if any(a in e for a in shot["allow"])]
                    errors = [e for e in errors if e not in allowed]
                    ok = not errors
                    for note, png in pngs:
                        text = f"{'✅' if ok else '⛔'} {index}/{total} {shot['label']} · {w}×{h}" + (f" · {note}" if note else "")
                        frames.append(compose(png, caption_png(browser, text, width, ok), width, height))
                        delays.append(shot["hold"])
                    context.close()
                results.append({"label": shot["label"], "url": shot["url"], "viewport": f"{w}x{h}", "steps": done,
                                "status": "pass" if ok else "fail", "errors": errors, "allowed": allowed,
                                "external": sorted(set(external))})
            version = browser.version
        finally:
            browser.close()
    if not frames:
        raise RuntimeError("no frame was captured")
    delays[-1] = max(delays[-1], 2000)
    data = encode(frames, delays)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    receipt = {"schema": SCHEMA, "gif": out.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
               "frames": len(frames), "canvas": f"{width}x{height + CAPTION}", "browser": f"chromium {version}",
               "recorded": datetime.date.today().isoformat(), "commit": sha,
               "mapped": [prefix for prefix, _ in maps or []],
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
    args = parser.parse_args(argv)
    try:
        raw = [parse_shot(s) for s in args.shot]
        if args.plan:
            loaded = json.loads(args.plan.read_text(encoding="utf-8"))
            raw += loaded["plan"] if isinstance(loaded, dict) else loaded     # a receipt is also a plan
        shots = check_plan(raw)
        maps = [parse_map(m) for m in args.map]
        if args.serve is not None and not args.serve.is_dir():
            raise ValueError(f"--serve is not a directory: {args.serve}")
    except (ValueError, OSError) as exc:
        parser.exit(2, f"⛔ {exc}\n")
    receipt = record(args.out, shots, serve_dir=args.serve, width=args.width, height=args.height, sha=args.sha, plan=raw, maps=maps)
    for shot in receipt["shots"]:
        print(f"{'✅' if shot['status'] == 'pass' else '⛔'} {shot['label']} {shot['viewport']}"
              + (f" 🌐 {len(shot['external'])}" if shot["external"] else "") + "".join(f"\n   ⚠️ {e}" for e in shot["errors"]))
    s = receipt["summary"]
    print(f"🎞️ {args.out} 🖼️ {receipt['frames']} 📦 {receipt['bytes'] // 1024} KiB ✅ {s['pass']} ⛔ {s['fail']}")
    return 1 if s["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
