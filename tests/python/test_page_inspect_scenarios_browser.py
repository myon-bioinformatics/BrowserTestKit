"""Real Chromium CLI tests with a private loopback HTTP server."""
import json, subprocess, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer; from pathlib import Path

import pytest

pytestmark = pytest.mark.browser
ROOT = Path(__file__).resolve().parents[2]
FIXTURE = (ROOT / "fixtures/page_inspect_scenarios.html").read_bytes()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/scenario.txt"):
            body, mime = b"fixture", "text/plain"
            self.send_response(200)
            self.send_header("Content-Disposition", 'attachment; filename="scenario.txt"')
        elif self.path.startswith("/api/probe"):
            body, mime = b"ok", "text/plain"
            self.send_response(200)
        else:
            body, mime = FIXTURE, "text/html; charset=utf-8"
            self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


@pytest.fixture(scope="module")
def url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:%d/scenarios?token=redact-me" % server.server_port
    finally:
        server.shutdown()
        server.server_close()


def run(url, *flags):
    proc = subprocess.run([sys.executable, "scripts/btk.py", "scenario", url,
                           "--browser", "chromium", *flags, "--json"],
                          cwd=ROOT, capture_output=True, text=True, timeout=90)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "redact-me" not in proc.stdout
    return json.loads(proc.stdout)


@pytest.mark.parametrize("case,key,value", [
    ("shortcut", "select_all_replaced", True),
    ("offline", "fetch_result", "error"),
    ("http-error", "fetch_result", "error"),
    ("latency", "fetch_result", "ok"),
    ("upload", "upload_accepted", True),
    ("download", "completed", True),
])
def test_real_chromium_scenarios(url, case, key, value):
    evidence = run(url, "--case", case)
    assert evidence["schema"] == "btk-page-observe/1"
    assert evidence["sections"]["scenario"][key] == value
    assert evidence["sections"]["network"]["coverage"] == "complete_within_window"
    if case == "http-error":
        assert any(item["kind"] == "http_error" and item["path"] == "/api/probe" and item["status"] == 503
                   and item["phase"] == "fault" for item in evidence["sections"]["network"]["items"])
    if case == "offline":
        assert any(item["kind"] == "request_failed" for item in evidence["sections"]["network"]["items"])
        assert evidence["sections"]["scenario"]["recovered"] is True


def test_display_and_permissions(url):
    evidence = run(url, "--case", "display", "--viewport", "390x844",
                   "--color-scheme", "dark", "--reduced-motion", "reduce",
                   "--locale", "ja-JP", "--timezone", "Asia/Tokyo",
                   "--device-scale-factor", "2")
    observed = evidence["sections"]["scenario"]["computed"]
    assert observed["color_scheme"] == "dark"
    assert observed["reduced_motion"] == "reduce"
    assert observed["locale"] == "ja-JP"
    assert observed["timezone"] == "Asia/Tokyo"
    assert float(observed["dpr"]) == 2
    assert evidence["environment"]["viewport"] == {"width": 390, "height": 844}
    assert evidence["environment"]["device_claim"] == "emulation_only"
    granted = run(url, "--case", "permission", "--permission", "notifications", "--permission-state", "grant")
    section = granted["sections"]["scenario"]
    assert section["reported_state"] in ("granted", "denied", "prompt", "unsupported")
    assert section["configured_state"] == "grant"
    assert section["matches_requested"] == (None if section["coverage"] == "unavailable" else section["reported_state"] == "granted")
    geo = run(url, "--case", "permission", "--permission", "geolocation", "--permission-state", "grant")
    assert geo["sections"]["scenario"]["reported_state"] == "granted"
    assert geo["sections"]["scenario"]["matches_requested"] is True
    assert geo["sections"]["scenario"]["reset_matches_baseline"] is True
    assert "latitude" not in json.dumps(geo)


@pytest.mark.parametrize("profile", ["desktop", "iphone", "pixel"])
@pytest.mark.parametrize("explicit", [False, True])
def test_profile_environment_matches_context(url, profile, explicit):
    flags = ["--profile", profile, "--case", "display"]
    if explicit:
        flags += ["--viewport", "800x600"]
    evidence = run(url, *flags)
    environment = evidence["environment"]
    settings = evidence["sections"]["environment_settings"]
    expected_source = "flag" if explicit else ("desktop_default" if profile == "desktop" else "device_descriptor")
    assert environment["has_touch"] == (profile != "desktop")
    assert environment["viewport_source"] == expected_source
    for key in ("has_touch", "viewport_source", "viewport", "device_scale_factor"):
        assert settings[key] == environment[key]
    assert evidence["sections"]["scenario"]["computed"]["dpr"] == environment["device_scale_factor"]
    if explicit:
        assert environment["viewport"] == {"width": 800, "height": 600}


@pytest.mark.browser
@pytest.mark.parametrize("explicit", [False, True])
def test_native_window_configuration_is_accepted(explicit):
    # Headless launch also validates the headed context kwargs without an X server.
    sys.path.insert(0, str(ROOT / "scripts"))
    from types import SimpleNamespace
    from playwright.sync_api import sync_playwright
    import btk as api
    import page_inspect_scenarios as scenario
    args = SimpleNamespace(browser="chromium", profile="desktop", headed=True,
                           color_scheme="light", reduced_motion="no-preference",
                           locale="en-US", timezone="UTC", device_scale_factor=2)
    viewport = {"width": 800, "height": 600} if explicit else None
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            config = scenario.context_configuration(args, api, playwright.devices, viewport)
            context = browser.new_context(**config)
            page = context.new_page()
            assert page.viewport_size == viewport
            if explicit:
                assert page.evaluate("devicePixelRatio") == 2
            else:
                assert "device_scale_factor" not in config
        finally:
            browser.close()
