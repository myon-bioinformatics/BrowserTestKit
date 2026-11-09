"""Platform/fault evidence contracts, independent of generic action/step PRs."""
import json, os, shutil, subprocess, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer; from pathlib import Path; from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import btk as api
import page_inspect_scenarios as scenarios


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
    prefix = [sys.executable, "scripts/btk.py"] if host == "python" else [shutil.which("node"), "vendor/py_launch.cjs", "--env-var", "BTK_PYTHON", "--site", "scripts/btk.py"]
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
    done = subprocess.run([sys.executable, "scripts/btk.py", "scenario", url, "--case", "display", "--json"], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert done.returncode == 2
    assert "PRIVATE" not in done.stdout + done.stderr
    assert json.loads(done.stdout)["stage"] == "inspect"
