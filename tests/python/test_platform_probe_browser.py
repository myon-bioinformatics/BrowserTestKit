"""platform-matrix in real browsers: every engine/profile passes, then the viewer's tabs show the differences."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("platform_probe", ROOT / "tools" / "platform_probe.py")
pp = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(pp)
RESULTS = ROOT / "test-results" / "platform-matrix"
CASES = [("chromium", "desktop"), ("firefox", "desktop"), ("webkit", "desktop"),
         ("chromium", "pixel"), ("webkit", "iphone"), ("firefox", "iphone")]


@pytest.mark.browser
@pytest.mark.parametrize(("browser_name", "profile"), CASES)
def test_environment_passes_and_records_facts(browser_name, profile):
    record = pp.collect(browser_name, profile)
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"{record['env']['id']}.json").write_text(pp.json.dumps(record, ensure_ascii=False), encoding="utf-8")
    assert record["verdict"] == "pass", (record["layout"], record["click"], record["libs"], record["facts"]["libs"])
    facts = record["facts"]
    assert facts["window"]["inner_width"] == facts["viewport"]["client_width"] + facts["viewport"]["scrollbar_width"]
    assert record["min_ok_width"] is not None and record["min_ok_width"] <= facts["window"]["inner_width"]
    # Reported on every engine: whether Meiryo renders (None when the generic fallbacks measure the same), and the declared stack.
    assert facts["fonts"]["meiryo"] in (True, False, None) and facts["fonts"]["body_family"]
    if profile != "desktop":
        assert record["click"]["input"] == "touch" and facts["window"]["inner_width"] < 500


@pytest.mark.browser
def test_viewer_tabs_highlight_what_changed(tmp_path):
    from playwright.sync_api import sync_playwright
    records = pp.load_records([str(RESULTS)]) if RESULTS.exists() else []
    if len(records) < 2:
        pytest.skip("run the environment tests first")
    page_path = tmp_path / "matrix.html"
    page_path.write_text(pp.render(records), encoding="utf-8")
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(error))
            page.goto(page_path.as_uri())
            first = page.locator("#head").inner_text()
            other = next(r for r in records if r["env"]["device"] != records[0]["env"]["device"]
                         and r["env"]["browser"] == records[0]["env"]["browser"])
            page.get_by_role("button", name=other["env"]["device"], exact=True).click()
            second = page.locator("#head").inner_text()
            changed = page.locator("td.ui-changed").count()
            rects = page.locator("#diagram rect").count()
        finally:
            browser.close()
    assert not errors and first != second and other["env"]["id"] in second
    assert changed > 0 and rects >= 3
