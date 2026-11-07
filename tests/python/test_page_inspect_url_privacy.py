"""URL evidence drops values without changing the URLs sent to the browser."""
import functools, json, os, shutil, subprocess, sys, threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer; from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import btk

QUERY = "private-query-42"
FRAGMENT = "private-fragment-73"
SUFFIX = f"?q={QUERY}#{FRAGMENT}"


def assert_private_absent(text):
    assert QUERY not in text
    assert FRAGMENT not in text


@pytest.mark.parametrize("url,expected", [
    (f"http://user:password@127.0.0.1:8765/path{SUFFIX}", "http://127.0.0.1:8765/path"),
    (f"https://[::1]:8765/a%20b{SUFFIX}", "https://[::1]:8765/a%20b"),
    (f"http://127.0.0.1:8765/{SUFFIX}", "http://127.0.0.1:8765/"),
    (f"about:blank{SUFFIX}", "about:blank"),
    ("", ""),
    (f"http://[broken/path{SUFFIX}", "[invalid-url]"),
    (f"http://127.0.0.1:bad/path{SUFFIX}", "[invalid-url]"),
])
def test_evidence_url_omits_values_and_keeps_path(url, expected):
    assert btk.strip_userinfo(url) == expected


def test_navigation_validation_and_step_parsing_preserve_values():
    url = "http://127.0.0.1:8765/path" + SUFFIX
    assert btk.loopback_target(url) == url
    assert btk._parse_step("goto - " + url, 1)["text"] == url


@pytest.mark.parametrize("entrypoint", ["python", "node"])
def test_compare_legacy_urls_does_not_republish_values(tmp_path, entrypoint):
    left = {"schema": btk.SCHEMA_OBSERVE, "elements": [],
            "url": "http://127.0.0.1:8765/start" + SUFFIX}
    right = dict(left, url="http://127.0.0.1:8765/result" + SUFFIX)
    for name, body in (("left", left), ("right", right)):
        (tmp_path / f"{name}.json").write_text(json.dumps(body), encoding="utf-8")
    done = run_cli(entrypoint, ["compare", str(tmp_path / "left.json"), str(tmp_path / "right.json"), "--json"])
    assert done.returncode == 0, done.stdout + done.stderr
    assert_private_absent(done.stdout + done.stderr)
    payload = json.loads(done.stdout)
    assert payload["left_url"].endswith("/start")
    assert payload["right_url"].endswith("/result")


def test_navigation_error_call_logs_omit_arbitrary_url_values():
    exc = btk.InspectError('Page.goto: navigating to "http://127.0.0.1:8765/path' + SUFFIX + '"', "retry")
    payload = btk.error_envelope("observe", exc)
    assert_private_absent(btk.dumps(payload) + btk.error_text(payload))
    assert "http://127.0.0.1:8765/path" in payload["error"]


def run_cli(entrypoint, args):
    if entrypoint == "node":
        node = shutil.which("node")
        assert node, "Node 26+ is required for the URL privacy parity checks"
        command = [node, str(ROOT / "vendor" / "py_launch.cjs"), "--env-var", "BTK_PYTHON", "--site",
                   str(ROOT / "scripts" / "btk.py")]
    else:
        command = [sys.executable, str(ROOT / "scripts" / "btk.py")]
    return subprocess.run(command + args, capture_output=True, text=True, timeout=120,
                          env={**os.environ, "PYTHON": sys.executable, "BTK_PYTHON": sys.executable})


@pytest.fixture
def private_site(tmp_path):
    # The script reports only a boolean result. The server also records the received
    # query in memory; neither probe prints the original location into page content.
    html = f'''<!doctype html><title>pending</title><button id="ok">Ready</button>
    <script src="/asset.js{SUFFIX}"></script>
    <img src="http://localhost:9/fail.svg{SUFFIX}">
    <iframe src="/frame.html{SUFFIX}"></iframe>'''
    for name in ("start.html", "result.html"):
        (tmp_path / name).write_text(html, encoding="utf-8")
    (tmp_path / "asset.js").write_text(
        f'document.title = location.search === "?q={QUERY}" && location.hash === "#{FRAGMENT}" '
        '? "navigation-preserved" : "navigation-broken";', encoding="utf-8")
    (tmp_path / "frame.html").write_text("<!doctype html><title>frame</title>", encoding="utf-8")
    received = []

    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            received.append(self.path)
            super().do_GET()

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(tmp_path)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", received
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.browser
@pytest.mark.parametrize("entrypoint", ["python", "node"])
def test_initial_goto_network_steps_and_compare_hide_values(tmp_path, private_site, entrypoint):
    base, received = private_site
    out = tmp_path / "snapshots"
    out.mkdir()
    done = run_cli(entrypoint, ["observe", base + "/start.html" + SUFFIX,
                              "--step", "goto - " + base + "/result.html" + SUFFIX,
                              "--out-dir", str(out), "--json"])
    assert done.returncode == 0, done.stdout + done.stderr
    assert_private_absent(done.stdout + done.stderr)
    summary = json.loads(done.stdout)
    snapshots = [json.loads(Path(path).read_text(encoding="utf-8")) for path in summary["files"]]
    initial, final = snapshots
    for path in summary["files"]:
        assert_private_absent(Path(path).read_text(encoding="utf-8"))
    for snapshot, path in ((initial, "/start.html"), (final, "/result.html")):
        assert snapshot["url"] == base + path
        assert snapshot["title"] == "navigation-preserved"  # query AND fragment reached page script
        assert snapshot["sections"]["dom"]["document_url"] == base + path
        assert snapshot["sections"]["resources"]["document_url"] == base + path
        assert any(row["url"] == base + "/asset.js" for row in snapshot["sections"]["scripts"]["items"])
        assert any(row["url"] == base + "/frame.html" for row in snapshot["sections"]["frames"]["items"])
    network = initial["sections"]["network"]["items"]
    assert any(row["url"] == base + "/start.html" and row["status"] == 200 for row in network)
    assert any(row["url"] == "http://localhost:9/fail.svg" and row.get("failure") for row in network)
    assert final["step"]["url_before"] == base + "/start.html"
    assert final["step"]["url_after"] == base + "/result.html"
    assert "/start.html?q=" + QUERY in received
    assert "/result.html?q=" + QUERY in received
    compared = run_cli(entrypoint, ["compare", *summary["files"], "--json"])
    assert compared.returncode == 0, compared.stdout + compared.stderr
    assert_private_absent(compared.stdout + compared.stderr)
    payload = json.loads(compared.stdout)
    assert payload["left_url"] == base + "/start.html"
    assert payload["right_url"] == base + "/result.html"


@pytest.mark.browser
@pytest.mark.parametrize("entrypoint", ["python", "node"])
def test_act_goto_url_section_hides_values(tmp_path, private_site, entrypoint):
    base, received = private_site
    artifact = tmp_path / "act.json"
    done = run_cli(entrypoint, ["act", base + "/start.html" + SUFFIX, "--action", "goto",
                              "--text", base + "/result.html" + SUFFIX, "--json", "--out", str(artifact)])
    assert done.returncode == 0, done.stdout + done.stderr
    assert_private_absent(done.stdout + done.stderr + artifact.read_text(encoding="utf-8"))
    payload = json.loads(done.stdout)
    assert payload["url"] == base + "/result.html"
    assert payload["sections"]["url"]["url"] == base + "/result.html"
    assert "/result.html?q=" + QUERY in received


def test_colon_step_publication_hides_urls_without_changing_navigation():
    url = "http://127.0.0.1:8765/path" + SUFFIX
    step = btk.parse_step("goto:" + url)
    assert step["url"] == url
    public = btk.publish_step(step, {"status": "done"}, url_before=url, url_after=url, index=1)
    assert_private_absent(json.dumps(public))
    assert public["url_before"] == public["url_after"] == "http://127.0.0.1:8765/path"
