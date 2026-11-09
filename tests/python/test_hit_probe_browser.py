"""Map each fixture target's real hit area with few probes and check it against clicks.

The probe is the browser's own hit test (document.elementFromPoint); each cell's score
is the fraction of its 2x2 integer sub-points that land on the target, a 0..1 value like a Jev
confidence. Observations are collected first and asserted at the end (assert-late).
"""
import importlib.util, json, os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = (ROOT / "fixtures" / "hit_probe.html").resolve().as_uri()
RESULTS = ROOT / "test-results" / "hit-probe"
BROWSERS = ("chromium", "firefox", "webkit")
CELL, PAD = 2, 8

spec = importlib.util.spec_from_file_location("hit_probe", ROOT / "scripts" / "hit_probe.py")
hit_probe = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(hit_probe)

SCORE_JS = """([id, pts, sub]) => { const t = document.getElementById(id);
  return pts.map(([x, y]) => { let hit = 0;
    for (const [dx, dy] of sub) { const e = document.elementFromPoint(x + dx, y + dy);
      if (e && (e === t || t.contains(e))) hit++; }
    return hit / sub.length; }); }"""
# Integer sub-points: engines round fractional hit-test coordinates differently.
SUB = [[0, 0], [1, 0], [0, 1], [1, 1]]


def observe(page, target):
    box = page.locator("#" + target).bounding_box()
    # Integer origin: engines round fractional hit-test and click coordinates differently.
    x0, y0 = int(box["x"] - PAD), int(box["y"] - PAD)
    width, height = int((box["width"] + 2 * PAD) // CELL), int((box["height"] + 2 * PAD) // CELL)
    calls = []

    def probe(points):
        calls.append(len(points))
        return page.evaluate(SCORE_JS, [target, [[x0 + x * CELL, y0 + y * CELL] for x, y in points], SUB])

    result = hit_probe.refine(width, height, probe, stride=8, margin=0.25)
    reference = hit_probe.exhaustive(width, height, probe)
    centre = lambda cell: (x0 + cell[0] * CELL + 1, y0 + cell[1] * CELL + 1)
    clicks = []
    for inside, outside in hit_probe.boundary_pairs(result["rows"], width, limit=6):
        pts = [centre(inside), centre(outside)]
        predicted = page.evaluate(SCORE_JS, [target, [list(p) for p in pts], [[0, 0]]])
        if predicted != [1.0, 0.0]:
            continue  # the cell centre itself sits on the other side; not a clean edge pair
        for point, expect in zip(pts, (1, 0)):
            before = int(page.locator("#" + target).get_attribute("data-clicks"))
            hit_before = page.evaluate("([x,y]) => { const e=document.elementFromPoint(x,y); return e && (e.id || e.tagName); }", list(point))
            page.mouse.click(*point)
            hit_after = page.evaluate("([x,y]) => { const e=document.elementFromPoint(x,y); return e && (e.id || e.tagName); }", list(point))
            after = int(page.locator("#" + target).get_attribute("data-clicks"))
            clicks.append({"point": point, "predicted": expect, "clicked": after - before,
                           "hit_before": hit_before, "hit_after": hit_after})
    return {"target": target, "evidence": hit_probe.to_json(result, rank=3), "matches_exhaustive":
            result["rows"] == reference, "batches": len(calls) - 1, "clicks": clicks}


def annotate(capsys, browser_name, text):
    """One GitHub Actions notice per browser, so CI shows the numbers without downloading artifacts."""
    if os.environ.get("GITHUB_ACTIONS"):
        with capsys.disabled():
            print(f"\n::notice title=hit-probe {browser_name}::{text[:900]}", flush=True)


def summary(observed):
    rows = []
    for target, obs in observed.items():
        ev = obs["evidence"]
        good = sum(c["clicked"] == c["predicted"] for c in obs["clicks"])
        rows.append(f"{target}: probes={ev['probes']}/{ev['cells']} exact={obs['matches_exhaustive']} "
                    f"clicks={good}/{len(obs['clicks'])} factors={len(ev['factors'])} residual={ev['residual']}")
    return "; ".join(rows)


@pytest.mark.browser
@pytest.mark.parametrize("browser_name", BROWSERS)
def test_hit_maps_need_few_probes_and_agree_with_clicks(browser_name, capsys):
    from playwright.sync_api import sync_playwright
    RESULTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = getattr(pw, browser_name).launch()
        try:
            page = browser.new_page(viewport={"width": 480, "height": 320})
            page.goto(FIXTURE)
            observed = {t: observe(page, t) for t in ("plain", "rounded", "covered", "rotated")}
        except Exception as exc:
            annotate(capsys, browser_name, f"observe failed: {type(exc).__name__}: {exc}")
            raise
        finally:
            browser.close()
    (RESULTS / f"{browser_name}.json").write_text(json.dumps(observed, indent=1) + "\n", encoding="utf-8")
    annotate(capsys, browser_name, summary(observed))

    for target, obs in observed.items():
        ev = obs["evidence"]
        assert obs["matches_exhaustive"], target
        assert ev["probes"] < ev["cells"] * 0.5, (target, ev["probes"], ev["cells"])
        assert obs["clicks"], f"{target}: no clean boundary pair to click"
        assert all(c["clicked"] == c["predicted"] for c in obs["clicks"]), (target, obs["clicks"])
    rank1 = {t: hit_probe.factorize(hit_probe._rows_from_map(o["evidence"]["map"])[0], rank=1)["residual"]
             for t, o in observed.items()}
    assert rank1["plain"] == 0  # a plain box is one rectangle
    assert rank1["rounded"] > 0 and rank1["rotated"] > 0  # corners / rotation leave a residual
    covered = observed["covered"]["evidence"]
    # The blocker takes the right part of the button away; the pointer-events:none ghost does not.
    assert covered["ones"] < observed["plain"]["evidence"]["ones"]
    assert covered["map"][PAD // CELL + 2][PAD // CELL + 2] == "1"


EDGE_JS = """([x, y]) => { const e = document.elementFromPoint(x, y); return e ? (e.id || e.tagName) : null; }"""


@pytest.mark.browser
@pytest.mark.parametrize("browser_name", BROWSERS)
def test_integer_edges_agree_and_fractional_edges_are_recorded(browser_name, capsys):
    """FRACTIONAL_HIT_COORDINATES in docs/antipatterns.md.

    At integer pixels around the plain box's left edge, elementFromPoint and a real click agree on
    every engine (asserted). At fractional pixels engines may round differently, so those results
    are only recorded, per engine, as evidence.
    """
    from playwright.sync_api import sync_playwright
    rows = []
    with sync_playwright() as pw:
        browser = getattr(pw, browser_name).launch()
        try:
            page = browser.new_page(viewport={"width": 480, "height": 320})
            page.goto(FIXTURE)
            box = page.locator("#plain").bounding_box()
            edge, y = int(box["x"]), int(box["y"] + box["height"] / 2)
            for x in (edge - 1, edge - 0.75, edge - 0.5, edge - 0.25, edge):
                hit = page.evaluate(EDGE_JS, [x, y]) == "plain"
                before = int(page.locator("#plain").get_attribute("data-clicks"))
                page.mouse.click(x, y)
                clicked = int(page.locator("#plain").get_attribute("data-clicks")) - before
                rows.append({"x": x, "hit": hit, "clicked": clicked})
        finally:
            browser.close()
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"{browser_name}-fractional-edge.json").write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")
    annotate(capsys, browser_name + " fractional edge",
             " ".join(f"x={r['x']}:hit={int(r['hit'])},click={r['clicked']}" for r in rows))
    integer = [r for r in rows if float(r["x"]).is_integer()]
    assert [(r["hit"], r["clicked"]) for r in integer] == [(False, 0), (True, 1)], integer
