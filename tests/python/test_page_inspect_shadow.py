"""Shadow roots are counted (open and closed), never entered, and frames are not descended."""
import functools, json, subprocess, sys, threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer; from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import btk  # noqa: E402

PAGE = """<!doctype html><meta charset="utf-8"><title>shadow fixture</title>
<div id="open-host"></div><div id="closed-host"></div><div id="nested-host"></div>
<iframe srcdoc="&lt;div id='f'&gt;&lt;/div&gt;"></iframe>
<script>
  const open = document.getElementById('open-host').attachShadow({mode: 'open'});
  open.innerHTML = '<button>open</button>';
  document.getElementById('closed-host').attachShadow({mode: 'closed'}).innerHTML = '<button>closed</button>';
  const nested = document.getElementById('nested-host').attachShadow({mode: 'open'});
  nested.innerHTML = '<div id="inner"></div>';
  nested.getElementById('inner').attachShadow({mode: 'open'}).innerHTML = '<a href="#">deep</a>';
</script>
"""


def test_counts_open_closed_and_nested_roots_without_frames():
    tree = {"nodeType": 9, "children": [
        {"nodeType": 1, "shadowRoots": [{"shadowRootType": "open", "children": [
            {"nodeType": 1, "shadowRoots": [{"shadowRootType": "closed", "children": []}]}]}]},
        {"nodeType": 1, "contentDocument": {"nodeType": 9, "shadowRoots": [{"shadowRootType": "open"}]}},
    ]}
    counts = btk.count_shadow_roots(tree)
    assert counts == {"open": 1, "closed": 1, "count": 2, "user_agent": 0, "truncated": False}


def test_truncation_is_reported_not_silent():
    deep = {"nodeType": 1, "children": []}
    for _ in range(5):
        deep = {"nodeType": 1, "children": [deep]}
    counts = btk.count_shadow_roots(deep, limit=3)
    assert counts["truncated"] is True


@pytest.mark.browser
def test_observe_reports_shadow_root_section(tmp_path):
    (tmp_path / "index.html").write_text(PAGE, encoding="utf-8")
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/index.html"
        run = subprocess.run([sys.executable, str(ROOT / "scripts" / "btk.py"), "observe", url, "--json"],
                             capture_output=True, text=True, timeout=180)
    finally:
        server.shutdown()
    assert run.returncode == 0, run.stderr
    section = json.loads(run.stdout)["sections"]["shadow_roots"]
    assert section["coverage"] == "complete"
    # open-host, nested-host and the inner root inside nested-host are open; one closed root.
    assert (section["open"], section["closed"], section["count"]) == (3, 1, 4)
    assert section["contents"] == "not_entered"
