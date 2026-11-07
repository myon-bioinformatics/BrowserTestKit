"""network and console come from Playwright events; user-agent shadow roots are not author roots."""
import functools, json, subprocess, sys, threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer; from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import btk  # noqa: E402

SCRIPT = ROOT / "scripts" / "btk.py"

PAGE = """<!doctype html><meta charset="utf-8"><title>events fixture</title>
<img src="http://example.invalid/blocked.png" alt="">
<script>
  console.log('hello', 1);
  console.warn('careful');
  fetch('missing.json').catch(() => {});
</script>
"""


def test_user_agent_roots_are_reported_apart_from_author_roots():
    tree = {"nodeType": 9, "children": [
        {"nodeType": 1, "shadowRoots": [{"shadowRootType": "user-agent", "children": []}]},
        {"nodeType": 1, "shadowRoots": [{"shadowRootType": "open", "children": []}]},
    ]}
    counts = btk.count_shadow_roots(tree)
    assert counts["user_agent"] == 1
    assert (counts["open"], counts["closed"], counts["count"]) == (1, 0, 1)


@pytest.mark.browser
def test_network_and_console_come_from_playwright_events(tmp_path):
    (tmp_path / "index.html").write_text(PAGE, encoding="utf-8")
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/index.html"
        done = subprocess.run([sys.executable, str(SCRIPT), "observe", url, "--json"],
                              capture_output=True, text=True, timeout=180)
    finally:
        server.shutdown()
    assert done.returncode == 0, done.stderr
    sections = json.loads(done.stdout)["sections"]
    network = sections["network"]
    assert network["source"] == "playwright:page.response"
    document = [item for item in network["items"] if item["url"].endswith("/index.html")]
    assert document and document[0]["status"] == 200 and document[0]["mime"] == "text/html"
    assert any(item["status"] == 404 and item["url"].endswith("/missing.json") for item in network["items"])
    failed = [item for item in network["items"] if item.get("failure")]
    assert failed and "example.invalid" in failed[0]["url"]
    console = sections["console"]
    assert console["source"] == "playwright:page.console"
    types = [(item["type"], item["argument_count"]) for item in console["items"]]
    assert ("log", 2) in types and ("warning", 1) in types
    assert all("argument_types" not in item for item in console["items"])
