"""observe --step: a sequence of declared actions, one snapshot per step, text never recorded."""
import functools, importlib.util, json, subprocess, sys, threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer; from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "btk.py"
_spec = importlib.util.spec_from_file_location("route_trail", ROOT / "scripts" / "route_trail.py")
route_trail = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(route_trail)
ROUTES = route_trail.load_registry((ROOT / "fixtures" / "page_inspect_routes.json").read_text(encoding="utf-8"))

FORM = """<!doctype html><meta charset="utf-8"><title>form</title>
<input id="q" type="text"> <a id="go" href="result.html">Go</a>
"""
RESULT = """<!doctype html><meta charset="utf-8"><title>result</title><button id="back">Back</button>
"""


def observe(args, cwd):
    return subprocess.run([sys.executable, str(SCRIPT), "observe", *args], cwd=cwd,
                          capture_output=True, text=True, timeout=180)


def test_step_arguments_are_checked_before_any_browser_launch(tmp_path):
    url = "http://127.0.0.1:9/form.html"
    no_dir = observe([url, "--step", "click id:go"], tmp_path)
    assert no_dir.returncode == 2 and "--out-dir" in no_dir.stderr
    both = observe([url, "--step", "click id:go", "--out-dir", str(tmp_path), "--after-click", "id:go",
                    "--after-out", str(tmp_path / "x.json"), "--out", str(tmp_path / "y.json")], tmp_path)
    assert both.returncode == 2 and "cannot be combined" in both.stderr
    unknown = observe([url, "--step", "drag id:go", "--out-dir", str(tmp_path)], tmp_path)
    assert unknown.returncode == 2 and "unknown action drag" in unknown.stderr
    no_text = observe([url, "--step", "fill id:q", "--out-dir", str(tmp_path)], tmp_path)
    assert no_text.returncode == 2 and "needs TEXT" in no_text.stderr
    bad_wait = observe([url, "--step", "wait_for_timeout - soon", "--out-dir", str(tmp_path)], tmp_path)
    assert bad_wait.returncode == 2 and "milliseconds" in bad_wait.stderr
    many = observe([url, "--out-dir", str(tmp_path)] + sum((["--step", "press - Enter"] for _ in range(21)), []), tmp_path)
    assert many.returncode == 2 and "at most 20" in many.stderr


@pytest.mark.browser
def test_fill_then_click_writes_one_snapshot_per_step_and_hides_the_text(tmp_path):
    (tmp_path / "form.html").write_text(FORM, encoding="utf-8")
    (tmp_path / "result.html").write_text(RESULT, encoding="utf-8")
    out = tmp_path / "steps"
    out.mkdir()
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/form.html"
        done = observe([url, "--step", "fill id:q secret-value-42", "--step", "click id:go",
                        "--out-dir", str(out), "--json"], tmp_path)
    finally:
        server.shutdown()
    assert done.returncode == 0, done.stderr
    summary = json.loads(done.stdout)
    assert summary["schema"] == "btk-page-observe-steps/1"
    assert [Path(f).name for f in summary["files"]] == ["00-initial.json", "01-fill.json", "02-click.json"]
    initial, filled, clicked = (json.loads(Path(f).read_text(encoding="utf-8")) for f in summary["files"])
    assert initial["url"].endswith("/form.html") and "step" not in initial
    assert filled["step"]["index"] == 2 and filled["step"]["action"] == "fill"
    assert clicked["step"]["index"] == 3 and clicked["step"]["url_before"].endswith("/form.html")
    assert clicked["url"].endswith("/result.html") and clicked["step"]["url_after"].endswith("/result.html")
    for path in summary["files"]:
        assert "secret-value-42" not in Path(path).read_text(encoding="utf-8")
    assert "text" not in filled["step"]


def test_goto_step_needs_a_loopback_url_and_no_locator(tmp_path):
    url = "http://127.0.0.1:9/form.html"
    no_url = observe([url, "--step", "goto -", "--out-dir", str(tmp_path)], tmp_path)
    assert no_url.returncode == 2 and "needs TEXT" in no_url.stderr
    remote = observe([url, "--step", "goto - http://example.com:80/", "--out-dir", str(tmp_path)], tmp_path)
    assert remote.returncode == 2 and "loopback" in remote.stderr
    direct = subprocess.run([sys.executable, str(SCRIPT), "act", url, "--action", "goto", "--text", "http://example.com:80/", "--json"],
                            cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert direct.returncode == 2 and "loopback" in direct.stdout + direct.stderr


@pytest.mark.browser
def test_goto_step_navigates_within_loopback(tmp_path):
    (tmp_path / "form.html").write_text(FORM, encoding="utf-8")
    (tmp_path / "result.html").write_text(RESULT, encoding="utf-8")
    out = tmp_path / "steps"
    out.mkdir()
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        done = observe([f"{base}/form.html", "--step", f"goto - {base}/result.html",
                        "--out-dir", str(out), "--json"], tmp_path)
    finally:
        server.shutdown()
    assert done.returncode == 0, done.stderr
    summary = json.loads(done.stdout)
    assert [Path(f).name for f in summary["files"]] == ["00-initial.json", "01-goto.json"]
    navigated = json.loads(Path(summary["files"][1]).read_text(encoding="utf-8"))
    assert navigated["step"]["action"] == "goto"
    assert navigated["url"].endswith("/result.html")


@pytest.mark.browser
def test_emoji_trail_drives_goto_steps(tmp_path):
    (tmp_path / "form.html").write_text(FORM, encoding="utf-8")
    (tmp_path / "result.html").write_text(RESULT, encoding="utf-8")
    out = tmp_path / "steps"
    out.mkdir()
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        trail = route_trail.resolve("🏠 → 🗺", ROUTES, base=base, hash_routing=False)
        start, *rest = trail
        steps = [f"goto - {row['url']}" for row in rest]
        done = observe([start["url"], "--step", *steps, "--out-dir", str(out), "--json"], tmp_path)
    finally:
        server.shutdown()
    assert done.returncode == 0, done.stderr
    summary = json.loads(done.stdout)
    assert [Path(f).name for f in summary["files"]] == ["00-initial.json", "01-goto.json"]
    final = json.loads(Path(summary["files"][-1]).read_text(encoding="utf-8"))
    assert final["url"] == trail[-1]["url"] and final["url"].endswith("/result.html")
