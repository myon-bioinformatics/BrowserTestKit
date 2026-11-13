"""page-inspect platform and fault scenarios: evidence contracts, offline boundaries and real Chromium CLI runs."""

import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import btk as api
import page_inspect_scenarios as scenario
import page_inspect_scenarios as scenarios


# ═══ · page inspect platform depth ═══════════════════════════════════════════════════════════════════════
# Platform/fault evidence contracts, independent of generic action/step PRs.

@pytest.mark.parametrize("url,allowed", [
    ("http://127.0.0.1:8765/api/probe?q=private", True),
    ("http://127.0.0.1:8766/api/probe", False),
    ("https://127.0.0.1:8765/api/probe", False),
    ("http://[::1]:8765/api/probe", False),
    ("http://localhost:8765/api/probe", False),
    ("http://user:secret@127.0.0.1:8765/api/probe", False),
    ("http://example.invalid:8765/api/probe", False),
])
def test_request_scope_is_exact_fixture_origin(url, allowed):
    assert scenarios.same_origin(url, "http://127.0.0.1:8765/page", api) is allowed


def test_event_cap_freezes_detached_evidence_and_never_claims_equality():
    window = scenarios.EventWindow()
    window.phase = "fault"
    for _ in range(scenarios.EVENT_LIMIT + 2):
        window.record("request_failed", "/api/probe")
    evidence = window.freeze(api)
    assert evidence["coverage"] == "truncated"
    assert evidence["truncated"] is True and evidence["omitted"] == 2
    assert len(evidence["items"]) == scenarios.EVENT_LIMIT
    assert all(row["status"] is None and row["phase"] == "fault" for row in evidence["items"])
    assert "digest" not in evidence
    assert api.evidence_relation(evidence, evidence) == "unknown"
    before = json.dumps(evidence, sort_keys=True)
    window.record("request_failed", "[blocked]")
    window.items[0]["phase"] = "recovery"
    assert json.dumps(evidence, sort_keys=True) == before


def test_exact_cap_is_complete_and_generation_rejects_stale_or_private_data():
    window = scenarios.EventWindow()
    for _ in range(scenarios.EVENT_LIMIT):
        window.record("http_error", "/api/probe", 503)
    evidence = window.freeze(api)
    assert evidence["coverage"] == "complete_within_window"
    assert evidence["truncated"] is False
    assert api.evidence_relation(evidence, evidence) == "same"
    page = SimpleNamespace(locator=lambda _: SimpleNamespace(get_attribute=lambda _: "private-token"))
    with pytest.raises(api.InspectError) as error:
        scenarios._probe(page, "#button", "#output", {"ok", "error"}, api)
    assert "private-token" not in str(error.value)
    with pytest.raises(api.InspectError) as error:
        scenarios._state(page, "#output", "data-state", {"ok", "error"}, api)
    assert "private-token" not in str(error.value)


def test_permission_unknown_is_not_success_and_clear_is_not_deny():
    body, coverage = scenarios.permission_result("notifications", "grant", "unsupported", "unsupported", "unsupported")
    assert coverage == "unavailable" and body["verdict"] == "unavailable"
    assert body["matches_requested"] is None and body["reset_matches_baseline"] is None
    body, coverage = scenarios.permission_result("geolocation", "grant", "prompt", "denied", "prompt")
    assert coverage == "complete" and body["verdict"] == "mismatch"
    assert body["matches_requested"] is False and body["reset_matches_baseline"] is True
    body, coverage = scenarios.permission_result("geolocation", "clear", "prompt", "prompt", "prompt")
    assert body["verdict"] == "pass" and body["reported_state"] == "prompt"
    body, coverage = scenarios.permission_result("geolocation", "grant", "prompt", "granted", "granted")
    assert body["matches_requested"] is True and body["reset_matches_baseline"] is False
    assert body["verdict"] == "mismatch"


@pytest.fixture
def server():
    fixture = (ROOT / "fixtures/page_inspect_scenarios.html").read_bytes()
    state = {"fixture": fixture, "requests": []}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state["requests"].append(self.path)
            body, mime = (b"fixture", "text/plain") if self.path.startswith(("/api/probe", "/scenario.txt")) else (state["fixture"], "text/html")
            self.send_response(200)
            if self.path.startswith("/scenario.txt"):
                self.send_header("Content-Disposition", 'attachment; filename="scenario.txt"')
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            return

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:%d/scenarios?token=PRIVATE" % http.server_port, state
    finally:
        http.shutdown()
        http.server_close()
        thread.join(timeout=2)


def run(url, case, browser="chromium", *extra, host="python"):
    prefix = [sys.executable, "tools/btk.py"] if host == "python" else [shutil.which("node"), "vendor/py_launch.cjs", "--env-var", "BTK_PYTHON", "--site", "tools/btk.py"]
    done = subprocess.run([*prefix, "scenario", url, "--browser", browser, "--case", case, *extra, "--json"],
                          cwd=ROOT, capture_output=True, text=True, timeout=90,
                          env={**os.environ, "PYTHON": sys.executable, "BTK_PYTHON": sys.executable})
    assert done.returncode == 0, done.stdout + done.stderr
    assert "PRIVATE" not in done.stdout + done.stderr
    return json.loads(done.stdout)


@pytest.mark.browser
@pytest.mark.parametrize("browser", ["chromium", "firefox", "webkit"])
def test_offline_is_observed_and_restored_in_each_engine(server, browser):
    url, requests = server
    evidence = run(url, "offline", browser)
    outcome = evidence["sections"]["scenario"]
    assert outcome["phases"] == [{"phase": "baseline", "result": "ok"}, {"phase": "fault", "result": "error"}, {"phase": "recovery", "result": "ok"}]
    assert outcome["recovered"] is True and outcome["verdict"] == "pass"
    assert sum(path.startswith("/api/probe") for path in requests["requests"]) == 2
    assert evidence["limits"]["http_scope"] == "fixture_origin_only"
    assert any(row["phase"] == "fault" and row["status"] is None for row in evidence["sections"]["network"]["items"])


@pytest.mark.browser
@pytest.mark.parametrize("case", ["http-error", "latency"])
def test_injected_fault_has_baseline_and_recovery(server, case):
    url, _ = server
    evidence = run(url, case)
    outcome = evidence["sections"]["scenario"]
    assert outcome["verdict"] == "pass" and outcome["recovered"] is True
    if case == "latency":
        assert outcome["delay_at_least_requested"] is True
        assert evidence["measurements"]["injected_delay_ms"] >= 100
        assert "injected_delay_ms" not in json.dumps(evidence["sections"])
    else:
        assert any(row["status"] == 503 and row["phase"] == "fault" for row in evidence["sections"]["network"]["items"])


@pytest.mark.browser
@pytest.mark.parametrize("browser", ["chromium", "firefox", "webkit"])
def test_geolocation_permission_grant_and_reset_are_real(server, browser):
    url, _ = server
    outcome = run(url, "permission", browser, "--permission", "geolocation")["sections"]["scenario"]
    if outcome["coverage"] == "unavailable":
        assert outcome["matches_requested"] is None and "digest" not in outcome
        assert browser != "chromium", "Chromium geolocation permission must be observed, not skipped"
    else:
        assert outcome["states"]["configured"] == "granted"
        assert outcome["states"]["reset"] == outcome["states"]["baseline"]
        assert outcome["verdict"] == "pass"


@pytest.mark.browser
def test_upload_reset_download_cleanup_and_node_bridge(server):
    url, _ = server
    assert run(url, "upload")["sections"]["scenario"]["cleared"] is True
    assert run(url, "download")["sections"]["scenario"]["temporary_artifact"] == "deleted"
    node = shutil.which("node")
    if not node:
        pytest.skip("Node runtime is unavailable; file assertions already ran")
    version = subprocess.check_output([node, "-p", "process.versions.node"], text=True).strip()
    if int(version.split(".")[0]) < 26:
        pytest.skip("Node bridge needs 26+, found " + version)
    py = run(url, "offline")
    js = run(url, "offline", host="node")
    for key in ("scenario", "environment_settings", "network"):
        assert api.evidence_relation(py["sections"][key], js["sections"][key]) == "same"


@pytest.mark.browser
def test_unknown_fixture_state_fails_without_leaking_it(server):
    url, state = server
    state["fixture"] = state["fixture"].replace(b'</script>', b'display.dataset.scheme = "PRIVATE";</script>')
    done = subprocess.run([sys.executable, "tools/btk.py", "scenario", url, "--case", "display", "--json"], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert done.returncode == 2
    assert "PRIVATE" not in done.stdout + done.stderr
    assert json.loads(done.stdout)["stage"] == "inspect"


# ═══ · page inspect scenarios unit ═══════════════════════════════════════════════════════════════════════
# Offline boundary tests for the platform and fault scenario command.

def options(**updates):
    values = dict(target="http://127.0.0.1:8765/test?token=hidden", case="display",
                  browser="chromium", profile="desktop", channel=None, headed=False,
                  viewport="390x844", locale="ja-JP", timezone="Asia/Tokyo",
                  color_scheme="dark", reduced_motion="reduce",
                  device_scale_factor=2.0, latency_ms=100,
                  permission="notifications", permission_state="grant")
    values.update(updates)
    return SimpleNamespace(**values)


def test_validation_and_network_privacy():
    assert scenario.validate(options(), api) == {"width": 390, "height": 844}
    for changes in ({"viewport": "9999x9999"}, {"viewport": "bad"},
                    {"channel": "arbitrary"}, {"browser": "webkit", "channel": "chrome"},
                    {"target": "https://example.com/"}, {"locale": "en_US"},
                    {"timezone": "../../secret"}, {"device_scale_factor": float("nan")},
                    {"latency_ms": 2001}):
        with pytest.raises(api.InspectError):
            scenario.validate(options(**changes), api)
    assert scenario.safe_path("http://127.0.0.1:9/api/probe?token=SECRET", api) == "/api/probe"
    assert scenario.safe_path("http://127.0.0.1:9/private/secret", api) == "[other_loopback]"
    assert scenario.safe_path("http://example.com/secret", api) == "[blocked]"
    assert scenario.EVENT_LIMIT == 30


def test_coverage_relations_and_cli_bad_flags():
    a = api._section("test", "complete_within_window", {"case": "offline"})
    assert api.evidence_relation(a, api._section("test", "complete_within_window", {"case": "offline"})) == "same"
    assert api.evidence_relation(a, api._section("test", "complete", {"case": "latency"})) == "different"
    assert api.evidence_relation(a, api._section("test", "truncated", {"case": "offline"})) == "unknown"
    cmd = [sys.executable, "-S", str(ROOT / "tools/btk.py"), "scenario",
           "http://127.0.0.1:9/page", "--case", "display", "--viewport", "9999x9999", "--json"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 2
    assert json.loads(proc.stdout)["stage"] == "input"


@pytest.mark.parametrize("profile", ["desktop", "iphone", "pixel"])
@pytest.mark.parametrize("headed", [False, True])
@pytest.mark.parametrize("viewport", [None, {"width": 800, "height": 600}])
@pytest.mark.parametrize("browser", ["chromium", "firefox", "webkit"])
def test_context_configuration(profile, headed, viewport, browser):
    devices = {name: {"viewport": {"width": 390, "height": 844},
                      "has_touch": True, "is_mobile": True, "device_scale_factor": 3,
                      "default_browser_type": "webkit"} for name in api.PROFILES.values() if name}
    args = options(profile=profile, headed=headed, browser=browser)
    config = scenario.context_configuration(args, api, devices, viewport)
    native = profile == "desktop" and headed and viewport is None
    assert bool(config.get("no_viewport")) == native
    assert ("device_scale_factor" not in config) if native else config["device_scale_factor"] == 2
    assert bool(config.get("has_touch")) == (profile != "desktop")
    assert "default_browser_type" not in config
    if browser == "firefox":
        assert "is_mobile" not in config
    if viewport is not None:
        assert config["viewport"] == viewport


# ═══ · page inspect scenarios browser ════════════════════════════════════════════════════════════════════
# Real Chromium CLI tests with a private loopback HTTP server.

PAGE_INSPECT_SCENARIOS_BROWSER_MARK = pytest.mark.browser
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


def run_page_inspect_scenarios_browser(url, *flags):
    proc = subprocess.run([sys.executable, "tools/btk.py", "scenario", url,
                           "--browser", "chromium", *flags, "--json"],
                          cwd=ROOT, capture_output=True, text=True, timeout=90)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "redact-me" not in proc.stdout
    return json.loads(proc.stdout)


@PAGE_INSPECT_SCENARIOS_BROWSER_MARK
@pytest.mark.parametrize("case,key,value", [
    ("shortcut", "select_all_replaced", True),
    ("offline", "fetch_result", "error"),
    ("http-error", "fetch_result", "error"),
    ("latency", "fetch_result", "ok"),
    ("upload", "upload_accepted", True),
    ("download", "completed", True),
])
def test_real_chromium_scenarios(url, case, key, value):
    evidence = run_page_inspect_scenarios_browser(url, "--case", case)
    assert evidence["schema"] == "btk-page-observe/1"
    assert evidence["sections"]["scenario"][key] == value
    assert evidence["sections"]["network"]["coverage"] == "complete_within_window"
    if case == "http-error":
        assert any(item["kind"] == "http_error" and item["path"] == "/api/probe" and item["status"] == 503
                   and item["phase"] == "fault" for item in evidence["sections"]["network"]["items"])
    if case == "offline":
        assert any(item["kind"] == "request_failed" for item in evidence["sections"]["network"]["items"])
        assert evidence["sections"]["scenario"]["recovered"] is True


@PAGE_INSPECT_SCENARIOS_BROWSER_MARK
def test_display_and_permissions(url):
    evidence = run_page_inspect_scenarios_browser(url, "--case", "display", "--viewport", "390x844",
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
    granted = run_page_inspect_scenarios_browser(url, "--case", "permission", "--permission", "notifications", "--permission-state", "grant")
    section = granted["sections"]["scenario"]
    assert section["reported_state"] in ("granted", "denied", "prompt", "unsupported")
    assert section["configured_state"] == "grant"
    assert section["matches_requested"] == (None if section["coverage"] == "unavailable" else section["reported_state"] == "granted")
    geo = run_page_inspect_scenarios_browser(url, "--case", "permission", "--permission", "geolocation", "--permission-state", "grant")
    assert geo["sections"]["scenario"]["reported_state"] == "granted"
    assert geo["sections"]["scenario"]["matches_requested"] is True
    assert geo["sections"]["scenario"]["reset_matches_baseline"] is True
    assert "latitude" not in json.dumps(geo)


@PAGE_INSPECT_SCENARIOS_BROWSER_MARK
@pytest.mark.parametrize("profile", ["desktop", "iphone", "pixel"])
@pytest.mark.parametrize("explicit", [False, True])
def test_profile_environment_matches_context(url, profile, explicit):
    flags = ["--profile", profile, "--case", "display"]
    if explicit:
        flags += ["--viewport", "800x600"]
    evidence = run_page_inspect_scenarios_browser(url, *flags)
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


@PAGE_INSPECT_SCENARIOS_BROWSER_MARK
@pytest.mark.browser
@pytest.mark.parametrize("explicit", [False, True])
def test_native_window_configuration_is_accepted(explicit):
    # Headless launch also validates the headed context kwargs without an X server.
    sys.path.insert(0, str(ROOT / "tools"))
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
