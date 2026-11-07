import importlib.util, json, sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = (ROOT / "fixtures" / "index.html").resolve().as_uri()
RESULTS = ROOT / "test-results" / "python"
BROWSERS = ("chromium", "firefox", "webkit")
PROFILES = (("desktop", None), ("mobile", "iPhone 13"))

_spec = importlib.util.spec_from_file_location("check_png", ROOT / "tools" / "check_png.py")
_check_png = importlib.util.module_from_spec(_spec)
assert _spec.loader
_spec.loader.exec_module(_check_png)

def build_context_args(pw, browser_name: str, device_name: str | None) -> dict:
    if not device_name:
        return {}
    args = dict(pw.devices[device_name])
    if browser_name == "firefox":
        # See DEVICE_DESCRIPTOR_CROSS_ENGINE in docs/antipatterns.md.
        args.pop("is_mobile", None)
    return args

def close_browser(browser, *, after_failure: bool) -> None:
    """Close the browser; after a test failure, report a close error instead of masking the failure."""
    try:
        browser.close()
    except Exception as close_exc:
        if not after_failure:
            raise
        # See EVIDENCE_MASKS_ROOT_FAILURE in docs/antipatterns.md.
        print(f"warning: browser.close() failed after the test failed: {type(close_exc).__name__}: {close_exc}", file=sys.stderr)

class _BrowserThatFailsToClose:
    def close(self) -> None:
        raise RuntimeError("close failed")

def test_close_browser_does_not_mask_an_earlier_failure(capsys) -> None:
    close_browser(_BrowserThatFailsToClose(), after_failure=True)
    assert "close failed" in capsys.readouterr().err
    with pytest.raises(RuntimeError, match="close failed"):
        close_browser(_BrowserThatFailsToClose(), after_failure=False)

@pytest.mark.browser
@pytest.mark.parametrize("browser_name", BROWSERS)
@pytest.mark.parametrize("profile,device_name", PROFILES)
def test_shared_fixture(browser_name: str, profile: str, device_name: str | None) -> None:
    from playwright.sync_api import sync_playwright
    RESULTS.mkdir(parents=True, exist_ok=True)
    png = RESULTS / f"{browser_name}-{profile}.png"
    meta = RESULTS / f"{browser_name}-{profile}.json"
    stage = "launch"
    page = None
    with sync_playwright() as pw:
        browser = None
        failed = False
        try:
            browser = getattr(pw, browser_name).launch()
            ctx_args = build_context_args(pw, browser_name, device_name)
            context = browser.new_context(**ctx_args)
            page = context.new_page()
            stage = "navigate"
            page.goto(FIXTURE)
            stage = "assert"
            assert page.locator("#unicode").inner_text() == "日本語 / Unicode ✓ / 🧪"
            stage = "interact"
            page.locator("#name").fill("Python")
            page.locator("#submit").click()
            stage = "assert"
            assert page.locator("#result").inner_text() == "Hello, Python!"
            stage = "screenshot"
            page.screenshot(path=png, full_page=True)
            stage = "artifact"
            width, height = _check_png.dimensions(png.read_bytes())
            # "artifact" is relative to the metadata file, as in the Node lane; see tools/check_evidence.py.
            meta.write_text(json.dumps({"runtime":"python","project":f"{browser_name}-{profile}","browser":browser_name,"profile":profile,"emulation":"partial" if browser_name=="firefox" and profile=="mobile" else "full","stage":"complete","artifact":png.name,"width":width,"height":height})+"\n", encoding="utf-8")
        except Exception as exc:
            failed = True
            emulation = "partial" if browser_name == "firefox" and profile == "mobile" else "full"
            meta.write_text(json.dumps({"runtime":"python","project":f"{browser_name}-{profile}","browser":browser_name,"profile":profile,"emulation":emulation,"stage":stage,"error":type(exc).__name__,"message":str(exc)[:500]})+"\n", encoding="utf-8")
            if page is not None:
                try:
                    page.screenshot(path=RESULTS / f"{browser_name}-{profile}-failure.png", full_page=True)
                except Exception as evidence_exc:
                    print(f"warning: failure screenshot unavailable: {type(evidence_exc).__name__}: {evidence_exc}", file=sys.stderr)
            raise
        finally:
            if browser is not None:
                close_browser(browser, after_failure=failed)
