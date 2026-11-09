"""observe --after-click: one click, then a second snapshot written to its own file."""
import functools, json, subprocess, sys, threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer; from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "btk.py"

FIRST = """<!doctype html><meta charset="utf-8"><title>first</title>
<a id="next" href="second.html">Next</a>
"""
SECOND = """<!doctype html><meta charset="utf-8"><title>second</title>
<button id="done">Done</button>
"""


def run(args, cwd):
    # observe needs Playwright from site-packages, so it runs without -S (unlike offline commands)
    return subprocess.run([sys.executable, str(SCRIPT), "observe", *args], cwd=cwd,
                          capture_output=True, text=True, timeout=180)


def test_sequence_arguments_are_checked_before_any_browser_launch(tmp_path):
    url = "http://127.0.0.1:9/index.html"
    missing_after = run([url, "--after-click", "id:next", "--out", str(tmp_path / "a.json")], tmp_path)
    assert missing_after.returncode == 2 and "--after-out" in missing_after.stderr
    missing_out = run([url, "--after-click", "id:next", "--after-out", str(tmp_path / "b.json")], tmp_path)
    assert missing_out.returncode == 2 and "--out" in missing_out.stderr
    ordinal = run([url, "--after-click", "h:2", "--out", str(tmp_path / "a.json"),
                   "--after-out", str(tmp_path / "b.json")], tmp_path)
    assert ordinal.returncode == 2 and "ordinal" in ordinal.stderr
    same = run([url, "--after-click", "id:next", "--out", str(tmp_path / "a.json"),
                "--after-out", str(tmp_path / "a.json")], tmp_path)
    assert same.returncode == 2 and "different files" in same.stderr
    no_dir = run([url, "--after-click", "id:next", "--out", str(tmp_path / "missing" / "a.json"),
                  "--after-out", str(tmp_path / "b.json")], tmp_path)
    assert no_dir.returncode == 2 and "directory does not exist" in no_dir.stderr
    # observe writes an error envelope to --out, as every command does; the second file is never written
    assert json.loads((tmp_path / "a.json").read_text(encoding="utf-8"))["ok"] is False
    assert not (tmp_path / "b.json").exists()


@pytest.mark.browser
def test_click_then_second_snapshot_in_its_own_file(tmp_path):
    (tmp_path / "first.html").write_text(FIRST, encoding="utf-8")
    (tmp_path / "second.html").write_text(SECOND, encoding="utf-8")
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/first.html"
        done = run([url, "--after-click", "id:next", "--out", str(tmp_path / "before.json"),
                    "--after-out", str(tmp_path / "after.json"), "--json"], tmp_path)
    finally:
        server.shutdown()
    assert done.returncode == 0, done.stderr
    before = json.loads((tmp_path / "before.json").read_text(encoding="utf-8"))
    after = json.loads((tmp_path / "after.json").read_text(encoding="utf-8"))
    assert before["url"].endswith("/first.html") and "step" not in before
    assert after["url"].endswith("/second.html")
    assert after["step"]["index"] == 2 and after["step"]["action"] == "click"
    assert after["step"]["url_before"].endswith("/first.html") and after["step"]["url_after"].endswith("/second.html")
    assert after["limits"]["click_runs_page_scripts"] is True
    assert json.loads(done.stdout)["url"] == before["url"]

    compared = subprocess.run([sys.executable, "-S", str(SCRIPT), "compare", str(tmp_path / "before.json"),
                               str(tmp_path / "after.json"), "--json"], cwd=tmp_path,
                              capture_output=True, text=True, timeout=60)
    assert compared.returncode == 0
    sections = json.loads(compared.stdout)["sections"]
    for name in ("dom", "accessibility", "resources"):
        assert sections[name]["relation"] == "different", name
    report = json.loads(compared.stdout)
    assert report["left_url"].endswith("/first.html") and report["right_url"].endswith("/second.html")


@pytest.mark.browser
def test_same_page_observed_twice_reads_same_for_identity_sections(tmp_path):
    (tmp_path / "first.html").write_text(FIRST, encoding="utf-8")
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/first.html"
        for name in ("one.json", "two.json"):
            done = run([url, "--out", str(tmp_path / name), "--json"], tmp_path)
            assert done.returncode == 0, done.stderr
    finally:
        server.shutdown()
    compared = subprocess.run([sys.executable, "-S", str(SCRIPT), "compare", str(tmp_path / "one.json"),
                               str(tmp_path / "two.json"), "--json"], cwd=tmp_path,
                              capture_output=True, text=True, timeout=60)
    sections = json.loads(compared.stdout)["sections"]
    for name in ("dom", "accessibility", "resources"):
        assert sections[name]["relation"] == "same", name
