"""Real observe and act runs through the CLI on a loopback fixture."""
import json, os, subprocess, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer; from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = (ROOT / "fixtures" / "page_inspect.html").read_bytes()
SCRIPT = ROOT / "scripts" / "btk.py"
sys.path.insert(0, str(ROOT / "scripts"))
RESULTS = ROOT / "test-results" / "page-inspect"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(FIXTURE)))
        self.end_headers()
        self.wfile.write(FIXTURE)

    def log_message(self, fmt, *args):
        return


def notice(capsys, title, text):
    if os.environ.get("GITHUB_ACTIONS"):
        with capsys.disabled():
            print(f"\n::notice title={title}::{text[:900]}", flush=True)


def run(args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=ROOT, capture_output=True, text=True, timeout=120)


def loopback():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/page"
    server.shutdown()


loopback = pytest.fixture(scope="module")(loopback)


def names(survey):
    return [element["name"] for element in survey["elements"]]


def test_chromium_observe_is_passive_and_fingerprints_storage(loopback, capsys):
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / "chromium-observe.json"
    done = run([
        "observe", loopback, "--browser", "chromium", "--json", "--out", str(out),
        "--include-storage-keys", "--include-storage-fingerprints",
    ])
    assert done.returncode == 0, done.stderr
    payload = json.loads(done.stdout)
    assert payload["schema"] == "btk-page-observe/1" and payload["ok"] is True
    assert payload["limits"]["page_js_called"] is False
    assert "Runtime.evaluate" not in payload["limits"]["cdp_methods"]
    # Only _wait_quiet (settle, after a driven page) may call page.evaluate; observe_page must not.
    import inspect
    import btk
    assert ".evaluate(" not in inspect.getsource(btk.observe_page)
    found = names(payload)
    for expected in ("Save draft", "Cancel", "Documentation", "Search", "Remember me", "Unavailable"):
        assert expected in found, found
    assert found.count("Cancel") == 2
    assert "Secret" not in found and "Child frame" not in found
    assert payload["child_frames"] >= 1
    frames = payload["sections"]["frames"]
    assert frames["coverage"] == "complete"
    assert frames["contents"] == "not_entered"
    assert frames["items"]
    for row in frames["items"]:
        assert set(row) == {"name", "url", "origin"}
        assert "Child frame" not in json.dumps(row)
    assert payload["sections"]["network"]["coverage"] == "attach_limited"
    save = next(element for element in payload["elements"] if element["name"] == "Save draft")
    assert save["address"] == "id:save-draft"
    unavailable = next(element for element in payload["elements"] if element["name"] == "Unavailable")
    assert unavailable["enabled"] is False
    raw = done.stdout
    assert "secret-value" not in raw
    areas = payload["sections"]["storage"]["areas"]
    local = next(area for area in areas if area["area"] == "local")
    assert local["items"][0]["key"] == "draft-token"
    assert "sha256" in local["items"][0] and "value" not in local["items"][0]
    hidden = run(["observe", loopback, "--browser", "chromium", "--include-hidden", "--json"])
    assert hidden.returncode == 0, hidden.stderr
    assert "Secret" in names(json.loads(hidden.stdout))
    notice(capsys, "page-observe chromium", f"elements={payload['total']} save={save['address']}")


def test_firefox_and_webkit_observe_do_not_invent_a_page(loopback, browser_name, capsys):
    done = run(["observe", loopback, "--browser", browser_name, "--profile", "iphone", "--json"])
    assert done.returncode == 0, done.stderr
    payload = json.loads(done.stdout)
    assert payload["limits"]["page_js_called"] is False
    assert payload["limits"]["reason"] == "cdp_session_unavailable"
    assert payload["sections"]["dom"]["coverage"] == "unavailable"
    assert payload["elements"] == []
    assert payload["environment"]["viewport"]["width"] == 390
    notice(capsys, f"page-observe {browser_name}", "cdp unavailable; no page call")


def test_act_requeries_and_records_the_click(loopback, capsys):
    clicked = run([
        "act", loopback, "--browser", "chromium", "--action", "click", "--locator", "id:save-draft", "--json",
    ])
    assert clicked.returncode == 0, clicked.stderr
    payload = json.loads(clicked.stdout)
    assert payload["limits"]["page_js_called"] is True
    assert payload["limits"]["bounded_to"] == ["click"]
    assert payload["sections"]["dom"]["coverage"] == "not_requested"
    assert "Saved" in payload["sections"]["accessibility"]["names"]
    ordinal = run(["act", loopback, "--action", "click", "--locator", "h:0", "--json"])
    assert ordinal.returncode == 2
    assert json.loads(ordinal.stdout)["ok"] is False
    notice(capsys, "page-act", "click re-queried id:save-draft")


# Firefox step split out of the Chromium click test: it used to run inside that test
# whenever BTK_BROWSER_ENGINES included firefox, so a host without Firefox failed the
# Chromium test. As a browser_name parameter, conftest's engine selection applies.
def test_act_wait_does_not_call_page_js(loopback, browser_name, capsys):
    waited = run(["act", loopback, "--browser", browser_name, "--action", "wait_for_timeout", "--timeout-ms", "50", "--json"])
    assert waited.returncode == 0, waited.stderr
    body = json.loads(waited.stdout)
    assert body["limits"]["page_js_called"] is False
    assert body["sections"]["accessibility"]["coverage"] == "not_requested"
    notice(capsys, f"page-act {browser_name}", "wait without page call")


test_firefox_and_webkit_observe_do_not_invent_a_page = pytest.mark.browser(
    pytest.mark.parametrize("browser_name", ("firefox", "webkit"))(test_firefox_and_webkit_observe_do_not_invent_a_page)
)
test_chromium_observe_is_passive_and_fingerprints_storage = pytest.mark.browser(
    test_chromium_observe_is_passive_and_fingerprints_storage
)
test_act_requeries_and_records_the_click = pytest.mark.browser(test_act_requeries_and_records_the_click)
test_act_wait_does_not_call_page_js = pytest.mark.browser(
    pytest.mark.parametrize("browser_name", ("firefox",))(test_act_wait_does_not_call_page_js)
)
