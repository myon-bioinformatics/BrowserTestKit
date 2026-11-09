"""Child frames are listed by name, URL, and origin; their contents are never entered."""
import functools, json, subprocess, sys, threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer; from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import btk  # noqa: E402

SCRIPT = ROOT / "scripts" / "btk.py"

PAGE = """<!doctype html><meta charset="utf-8"><title>frames fixture</title>
<iframe name="inner" srcdoc="&lt;button&gt;inside&lt;/button&gt;"></iframe>
<iframe name="outer" src="child.html"></iframe>
"""
CHILD = """<!doctype html><meta charset="utf-8"><title>child</title>
<iframe name="deep" srcdoc="&lt;a href='#'&gt;deep&lt;/a&gt;"></iframe>
"""


def test_frame_rows_are_document_order_without_userinfo_or_ids():
    tree = {"frame": {"url": "http://127.0.0.1/"}, "childFrames": [
        {"frame": {"name": "a", "url": "http://u:p@127.0.0.1/a?x=1", "securityOrigin": "http://127.0.0.1", "id": "X"},
         "childFrames": [{"frame": {"name": "deep", "url": "about:srcdoc"}}]},
        {"frame": {"name": "b", "url": "about:blank"}},
    ]}
    rows, truncated = btk.frame_rows(tree)
    assert truncated is False
    assert [row["name"] for row in rows] == ["a", "deep", "b"]
    assert rows[0] == {"name": "a", "url": "http://127.0.0.1/a", "origin": "http://127.0.0.1"}
    assert all("id" not in row for row in rows)


def test_frame_rows_report_truncation_instead_of_silently_dropping():
    tree = {"frame": {}, "childFrames": [{"frame": {"name": str(i)}} for i in range(4)]}
    rows, truncated = btk.frame_rows(tree, limit=2)
    assert len(rows) == 2 and truncated is True


@pytest.mark.browser
def test_observe_lists_child_frames_and_same_page_reads_same(tmp_path):
    (tmp_path / "index.html").write_text(PAGE, encoding="utf-8")
    (tmp_path / "child.html").write_text(CHILD, encoding="utf-8")
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/index.html"
        for name in ("one.json", "two.json"):
            done = subprocess.run([sys.executable, str(SCRIPT), "observe", url, "--out", str(tmp_path / name), "--json"],
                                  cwd=tmp_path, capture_output=True, text=True, timeout=180)
            assert done.returncode == 0, done.stderr
    finally:
        server.shutdown()
    first = json.loads((tmp_path / "one.json").read_text(encoding="utf-8"))
    section = first["sections"]["frames"]
    assert section["coverage"] == "complete" and section["contents"] == "not_entered"
    assert section["child_frames"] == 3
    assert [item["name"] for item in section["items"]] == ["inner", "outer", "deep"]
    assert section["items"][1]["url"].endswith("/child.html")
    assert all("id" not in item for item in section["items"])

    compared = subprocess.run([sys.executable, "-S", str(SCRIPT), "compare", str(tmp_path / "one.json"),
                               str(tmp_path / "two.json"), "--json"], cwd=tmp_path,
                              capture_output=True, text=True, timeout=60)
    assert json.loads(compared.stdout)["sections"]["frames"]["relation"] == "same"
