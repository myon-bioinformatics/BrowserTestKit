"""btk.py emoji routes: pages discovers same-origin pages, observe --trail and goto take emoji, and the trail grammar matches route_trail.py."""
import functools, importlib.util, json, subprocess, sys, threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer; from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "btk.py"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


btk = _load("btk_under_test", SCRIPT)
route_trail = _load("route_trail", ROOT / "scripts" / "route_trail.py")

INDEX = ('<!doctype html><meta charset="utf-8"><title>home</title>'
         '<a href="form.html">Form</a> <a href="result.html">Result</a> <a href="https://example.com/">away</a>\n')
FORM = '<!doctype html><meta charset="utf-8"><title>form</title><a id="go" href="result.html">Go</a>\n'
RESULT = '<!doctype html><meta charset="utf-8"><title>result</title><button id="back">Back</button>\n'


def run_btk(args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=ROOT,
                          capture_output=True, text=True, timeout=180)


def write_routes(tmp_path, data):
    path = tmp_path / "routes.json"
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return str(path)


def test_trail_grammar_matches_route_trail():
    for trail in ["🏠 → 🗺", "🏠 -> 🗺 -> 🌍", "🏠 ↔ 🗺", "🏠 <-> 🗺 → 🌍", "🏠 ↔ 🗺 ↔ 🌍 → 🏠"]:
        expected = [route_trail.norm(step) for step in route_trail.parse_trail(trail)]
        assert btk.parse_trail(trail) == expected, trail


def test_trail_must_alternate_pages_and_arrows():
    with pytest.raises(btk.InspectError) as no_start:
        btk.parse_trail("→ 🗺")
    assert "alternate" in str(no_start.value)
    with pytest.raises(btk.InspectError) as no_end:
        btk.parse_trail("🏠 →")
    assert "alternate" in str(no_end.value)


def test_routes_file_is_checked(tmp_path):
    assert btk.load_routes(write_routes(tmp_path, {"/": "🏠", "/form.html": "🗺️"})) == {"🏠": "/", "🗺": "/form.html"}
    with pytest.raises(btk.InspectError) as not_json:
        btk.load_routes(write_routes(tmp_path, "not json"))
    assert "not JSON" in str(not_json.value)
    with pytest.raises(btk.InspectError) as twice:
        btk.load_routes(write_routes(tmp_path, {"/": "🏠", "/a": "🏠"}))
    assert "used by two" in str(twice.value)
    with pytest.raises(btk.InspectError) as relative:
        btk.load_routes(write_routes(tmp_path, {"form.html": "🗺"}))
    assert "bad route" in str(relative.value)
    with pytest.raises(btk.InspectError) as empty:
        btk.load_routes(write_routes(tmp_path, {"/": " "}))
    assert "needs one emoji" in str(empty.value)
    with pytest.raises(btk.InspectError) as too_many:
        btk.load_routes(write_routes(tmp_path, {f"/p{n}": str(n) for n in range(len(btk.ROUTE_POOL) + 1)}))
    assert "1 to 30" in str(too_many.value)


def test_routes_and_trail_flags_are_checked_before_any_browser_launch(tmp_path):
    url = "http://127.0.0.1:9/index.html"
    routes = write_routes(tmp_path, {"/index.html": "🏠", "/form.html": "🗺"})
    no_routes = run_btk(["observe", url, "--trail", "🏠 → 🗺", "--out-dir", str(tmp_path)])
    assert no_routes.returncode == 2 and "needs --routes" in no_routes.stderr
    no_dir = run_btk(["observe", url, "--trail", "🏠 → 🗺", "--routes", routes])
    assert no_dir.returncode == 2 and "--out-dir" in no_dir.stderr
    mixed = run_btk(["observe", url, "--trail", "🏠 → 🗺", "--routes", routes, "--out-dir", str(tmp_path),
                     "--step", "press - Enter"])
    assert mixed.returncode == 2 and "cannot be combined" in mixed.stderr
    unknown = run_btk(["observe", url, "--trail", "🏠 → 🌍", "--routes", routes, "--out-dir", str(tmp_path)])
    assert unknown.returncode == 2 and "no route for emoji 🌍" in unknown.stderr
    no_routes_goto = run_btk(["act", url, "--action", "goto", "--text", "🗺", "--json"])
    assert no_routes_goto.returncode == 2 and "--routes" in no_routes_goto.stdout + no_routes_goto.stderr
    bad_goto = run_btk(["act", url, "--action", "goto", "--text", "🌍", "--routes", routes, "--json"])
    assert bad_goto.returncode == 2 and "no route for emoji" in bad_goto.stdout + bad_goto.stderr
    zero = run_btk(["pages", url, "--max-pages", "0"])
    assert zero.returncode == 2 and "at least 1" in zero.stderr


@pytest.fixture
def site(tmp_path):
    (tmp_path / "index.html").write_text(INDEX, encoding="utf-8")
    (tmp_path / "form.html").write_text(FORM, encoding="utf-8")
    (tmp_path / "result.html").write_text(RESULT, encoding="utf-8")
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", tmp_path
    finally:
        server.shutdown()


@pytest.mark.browser
def test_pages_then_trail_and_emoji_goto_drive_the_same_routes(site):
    base, work = site
    routes_file = work / "routes.json"
    found = run_btk(["pages", f"{base}/index.html", "--routes-out", str(routes_file), "--json"])
    assert found.returncode == 0, found.stderr
    payload = json.loads(found.stdout)
    assert payload["schema"] == "btk-page-routes/1"
    assert payload["routes"] == {"/index.html": "🏠", "/form.html": "🗺", "/result.html": "🌍"}
    assert payload["skipped_external_links"] == 1 and payload["truncated"] is False
    assert json.loads(routes_file.read_text(encoding="utf-8")) == payload["routes"]

    capped = json.loads(run_btk(["pages", f"{base}/index.html", "--max-pages", "1", "--json"]).stdout)
    assert list(capped["routes"]) == ["/index.html"] and capped["truncated"] is True

    out = work / "trail"
    out.mkdir()
    trail = run_btk(["observe", f"{base}/index.html", "--trail", "🏠 → 🗺 → 🌍",
                     "--routes", str(routes_file), "--out-dir", str(out), "--json"])
    assert trail.returncode == 0, trail.stderr
    summary = json.loads(trail.stdout)
    assert [Path(f).name for f in summary["files"]] == ["00-initial.json", "01-goto.json", "02-goto.json"]
    pages = [json.loads(Path(f).read_text(encoding="utf-8"))["url"].rsplit("/", 1)[1] for f in summary["files"]]
    assert pages == ["index.html", "form.html", "result.html"]

    acted = run_btk(["act", f"{base}/index.html", "--action", "goto", "--text", "🗺",
                     "--routes", str(routes_file), "--json"])
    assert acted.returncode == 0, acted.stderr
    assert json.loads(acted.stdout)["url"].endswith("/form.html")


@pytest.mark.browser
def test_pages_many_links_cli_is_bounded(site):
    base, work = site
    fixture = ROOT / "tests/fixtures/pages/many-links.html"
    (work / "many.html").write_bytes(fixture.read_bytes())
    result = run_btk(["pages", f"{base}/many.html", "--max-pages", "1", "--json"])
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["routes"] == {"/many.html": "🏠"}
    assert payload["links_inspected"] == btk.PAGE_LINK_LIMIT
    assert payload["queue_peak"] == 1
    assert payload["truncated"] is True
    assert "secret" not in result.stdout and "hidden" not in result.stdout


@pytest.mark.parametrize("max_pages", [1, 30])
def test_pages_link_and_queue_budgets_without_bulk_locators(monkeypatch, max_pages):
    class Anchor:
        def get_attribute(self, name):
            page.reads += 1
            return f"/p{page.reads}?private=secret#hidden"

    class Anchors:
        def count(self):
            return 5000

        def nth(self, index):
            assert index < btk.PAGE_LINK_LIMIT
            return Anchor()

        def all(self):
            pytest.fail("must not allocate all locators")

    class Page:
        reads = 0
        visits = []
        url = "http://127.0.0.1:8000/"

        def goto(self, url, **kwargs):
            self.url = url
            self.visits.append(url)

        def locator(self, selector):
            return Anchors()

    page = Page()
    monkeypatch.setattr(btk, "_with_browser", lambda *args: (None, None, page, False))
    monkeypatch.setattr(btk, "_finish", lambda pw, browser, failure, result: result)
    payload = btk.discover_pages(url=page.url, browser_name="chromium", profile="desktop",
                                 headed=False, channel=None, max_pages=max_pages)
    assert payload["links_inspected"] == min(max_pages * btk.PAGE_LINK_LIMIT, btk.TOTAL_LINK_LIMIT)
    assert page.reads == payload["links_inspected"]
    assert payload["queue_peak"] <= max_pages
    assert len(payload["routes"]) == max_pages
    assert payload["truncated"] is True
    assert list(payload["routes"])[0] == "/"
    assert "secret" not in json.dumps(payload)
    assert page.visits == ["http://127.0.0.1:8000/"] + [
        f"http://127.0.0.1:8000/p{i}" for i in range(1, max_pages)]
