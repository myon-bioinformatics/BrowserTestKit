"""Page text and DOM capture: page_text, saved live regressions, documentation DOM, local Chromium capture, overlays, HTML and fonts."""
from __future__ import annotations

import functools
import importlib.util
import json
import os
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
import element_overlay


# ═══ · page text ═════════════════════════════════════════════════════════════════════════════════════════

SCRIPT = ROOT / "tools" / "page_text.py"
SAMPLE = ROOT / "fixtures" / "page_text" / "sample.html"
GOLDEN = ROOT / "fixtures" / "page_text" / "sample.expected.txt"
SAMPLE_URL = "https://example.test/docs/page.html"
spec = importlib.util.spec_from_file_location("page_text", SCRIPT)
page_text = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(page_text)


def text_of(html: str) -> str:
    return page_text.page_text(html)["text"]


def run_cli(*args: str, stdin: bytes = b"") -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, "-S", str(SCRIPT), *args], input=stdin, capture_output=True,
                          env=env, timeout=60)


# -- golden fixture -----------------------------------------------------------

def test_golden_sample_in_process(capsys) -> None:
    assert page_text.main([str(SAMPLE), "--url", SAMPLE_URL]) == 0
    captured = capsys.readouterr()
    assert captured.out == GOLDEN.read_text(encoding="utf-8")
    assert captured.err == ""


def test_golden_sample_runs_without_site_packages() -> None:
    result = run_cli(str(SAMPLE), "--url", SAMPLE_URL)
    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout.decode("utf-8") == GOLDEN.read_text(encoding="utf-8")


def test_golden_sample_prefers_main_and_drops_hidden_content() -> None:
    result = page_text.page_text(SAMPLE.read_text(encoding="utf-8"))
    assert result["title"] == "Sample & Page — テスト"
    assert result["source_element"] == "main"
    for absent in ("Home", "Footer text", "must not appear", "Enable JavaScript", "template text", "svg text",
                   "svg title", "iframe fallback", "script link"):
        assert absent not in result["text"]
    assert 'after it <tag> & "quotes" \U0001F9EA' in result["text"]


def test_file_output_reports_the_file_url() -> None:
    result = run_cli(str(SAMPLE))
    assert f"URL: {SAMPLE.resolve().as_uri()}\n" in result.stdout.decode("utf-8")


# -- line-break and whitespace rules (innerText for default display) ----------

@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ("<div>a</div><div>b</div>", "a\nb"),
        ("<p>a</p><p>b</p>", "a\n\nb"),
        ("<h2>T</h2><div>x</div><p>y</p>", "T\nx\n\ny"),
        ("<ul><li>a<ul><li>b</li></ul></li><li>c</li></ul>", "a\nb\nc"),
        ("<ul><li>a<li>b</ul>", "a\nb"),
        ("a<br><br><br><br>b", "a\n\nb"),
        ("a</br>b", "a\nb"),
        ("<span>a</span> <span>b</span>", "a b"),
        ("<div>\n  lots   of \n\t spaces  </div>", "lots of spaces"),
        ("<table><tr><th>a</th><th></th><th>c</th></tr>\n<tr><td> d </td></tr></table>", "a\t\tc\nd"),
        ("<pre>\n  x\n\n\n\ty  </pre>", "  x\n\n\n\ty  "),
        ("a<pre>\nx</pre>", "a\nx"),
        ("a<pre><code>\nx</code></pre>", "a\n\nx"),
        ("<p>x&nbsp;&nbsp;y　z</p>", "x\xa0\xa0y　z"),
        ("<p>&lt;a&gt; &amp;amp; &#169; &#x1F9EA;</p>", "<a> &amp; \xa9 \U0001F9EA"),
        ("<pre>a\r\nb\rc</pre>", "a\nb\nc"),
        ("a﷐b﷒c", "a�b�c"),
        ("<div>a<hr>b</div>", "a\nb"),
        ("<p>a</p>\n\n<p>b</p>\n", "a\n\nb"),
    ],
)
def test_line_break_rules(html: str, expected: str) -> None:
    assert text_of(html) == expected


def test_title_is_collapsed_and_svg_titles_are_ignored() -> None:
    html = "<svg><title>icon</title></svg><title>  A &amp;\n B </title><title>second</title><p>x</p>"
    assert page_text.page_text(html)["title"] == "A & B"


def test_excluded_elements_are_dropped_with_descendants() -> None:
    html = ("<main><script>s()</script><style>p{}</style><noscript><p>n</p></noscript>"
            "<template><p>tp</p></template><svg><text>sv</text></svg><iframe>if</iframe>"
            "<noembed>ne</noembed><noframes>nf</noframes>ok</main>")
    assert text_of(html) == "ok"


def test_unclosed_nested_excluded_element_recovers_at_the_outer_end_tag() -> None:
    assert text_of("<p>before</p><noscript><svg><text>x</text></noscript><p>after</p>") == "before\n\nafter"


# -- source element selection -------------------------------------------------

def test_body_fallback_when_there_is_no_main_or_article() -> None:
    html = "<html><head><title>T</title><style>x{}</style></head><body><div>only body</div></body></html>"
    result = page_text.page_text(html)
    assert (result["source_element"], result["text"], result["title"]) == ("body", "only body", "T")


def test_body_fallback_cli_line() -> None:
    result = run_cli("-", stdin=b"<title>T</title><p>Hi</p>")
    assert result.returncode == 0
    assert result.stdout.decode("utf-8") == "Title: T\nURL: -\nSource element: body\n---\nHi\n"


def test_text_in_head_without_body_tag_starts_the_body() -> None:
    assert text_of("<html><head><title>T</title><p>Hi") == "Hi"


def test_article_is_used_when_there_is_no_main() -> None:
    result = page_text.page_text("<nav>n</nav><article><h1>A</h1>text</article><footer>f</footer>")
    assert (result["source_element"], result["text"]) == ("article", "A\ntext")


def test_main_wins_over_an_earlier_article_and_only_the_first_main_counts() -> None:
    result = page_text.page_text("<article>a</article><main>m1</main><main>m2</main>")
    assert (result["source_element"], result["text"]) == ("main", "m1")


def test_nested_main_does_not_end_the_outer_main_early() -> None:
    assert text_of("<main><main>inner</main>tail</main>after") == "inner\ntail"


def test_empty_main_prints_a_static_extraction_note() -> None:
    result = run_cli("-", stdin=b'<main id="app"></main><script>render()</script>')
    assert result.returncode == 0
    assert result.stdout.decode("utf-8").endswith("Source element: main\n---\n")
    assert "PAGE_TEXT_AS_RENDERED_TEXT" in result.stderr.decode("utf-8")


# -- limits and JSON ----------------------------------------------------------

def test_max_chars_truncates_and_flags_it() -> None:
    result = page_text.page_text("<p>abcdefghij</p>", max_chars=4)
    assert (result["text"], result["truncated"], result["total_chars"]) == ("abcd", True, 10)
    assert page_text.page_text("<p>abcd</p>", max_chars=4)["truncated"] is False


def test_cli_json_fields_and_truncation() -> None:
    result = run_cli(str(SAMPLE), "--json", "--max-chars", "12")
    assert result.returncode == 0
    data = json.loads(result.stdout.decode("utf-8"))
    for key in ("title", "url", "source_element", "text", "truncated", "bytes", "fetched_at"):
        assert key in data
    assert data["text"] == "Main heading" and data["truncated"] is True
    assert data["bytes"] == SAMPLE.stat().st_size and data["byte_limit_hit"] is False
    assert "truncated to 12 of" in result.stderr.decode("utf-8")


def test_max_bytes_limit_is_reported(tmp_path: Path) -> None:
    page = tmp_path / "big.html"
    page.write_text("<p>" + "x" * 500 + "</p>", encoding="utf-8")
    loaded = page_text.load(str(page), max_bytes=100)
    assert (loaded["bytes"], loaded["byte_limit_hit"]) == (100, True)


@pytest.mark.parametrize("option", ["--max-chars", "--max-bytes", "--limit", "--timeout"])
def test_non_positive_limits_are_usage_errors(option: str) -> None:
    with pytest.raises(SystemExit) as excinfo:
        page_text.main([str(SAMPLE), option, "0"])
    assert excinfo.value.code == 2


def test_missing_file_is_an_input_error(capsys) -> None:
    assert page_text.main(["does/not/exist.html"]) == 2
    assert "no such file" in capsys.readouterr().err


def test_file_url_source() -> None:
    assert page_text.load(SAMPLE.resolve().as_uri())["url"] == SAMPLE.resolve().as_uri()


@pytest.mark.parametrize(
    ("raw", "content_type", "expected"),
    [
        ("①テスト".encode("cp932"), "text/html; charset=Shift_JIS", "①テスト"),
        ('<meta charset="euc-jp">テスト'.encode("euc_jp"), "", '<meta charset="euc-jp">テスト'),
        ("’".encode("cp1252"), "text/html; charset=iso-8859-1", "’"),
        (b"\xef\xbb\xbfBOM", "text/html; charset=iso-8859-1", "BOM"),
        (b"ok", "text/html; charset=x-unknown", "ok"),
    ],
)
def test_decoding(raw: bytes, content_type: str, expected: str) -> None:
    assert page_text._decode(raw, content_type) == expected


# -- find ---------------------------------------------------------------------

def test_find_resolves_relative_hrefs_against_base_href() -> None:
    matches = page_text.find_elements(SAMPLE.read_text(encoding="utf-8"), "status page", url=SAMPLE_URL)
    assert matches == [{"kind": "link", "text": "status page", "href": "https://example.test/docs/status.html",
                        "index": matches[0]["index"], "aria_label": None, "title": "Status page"}]
    home = page_text.find_elements(SAMPLE.read_text(encoding="utf-8"), "home", url=SAMPLE_URL)
    assert home[0]["href"] == "https://example.test/"


def test_find_resolves_relative_hrefs_against_the_page_url() -> None:
    html = '<a href="../settings/security">Code security</a><a href=" #top ">Top</a>'
    matches = page_text.find_elements(html, "", url="https://github.com/o/r/settings/actions")
    assert [match["href"] for match in matches] == [
        "https://github.com/o/r/settings/security", "https://github.com/o/r/settings/actions#top"]


def test_find_matches_text_aria_label_and_title_case_insensitively() -> None:
    html = SAMPLE.read_text(encoding="utf-8")
    assert [(m["kind"], m["text"]) for m in page_text.find_elements(html, "CLOSE DIALOG")] == [("button", "×")]
    assert [(m["kind"], m["text"]) for m in page_text.find_elements(html, "refresh data")] == [
        ("button", "↻ Refresh")]
    assert [(m["kind"], m["text"]) for m in page_text.find_elements(html, "main heading")] == [
        ("heading", "Main heading")]


def test_find_candidates_and_non_candidates() -> None:
    html = ('<input type="submit" value="Save changes"><input type="text" value="Save draft">'
            '<span role="link">Save link</span><a>Save anchor without href</a><p>Save paragraph</p>'
            '<button>Save<br>all</button>')
    assert [(m["kind"], m["text"]) for m in page_text.find_elements(html, "save")] == [
        ("button", "Save changes"), ("link", "Save link"), ("button", "Save all")]


def test_find_never_matches_script_style_or_template_text() -> None:
    html = ("<style>.status-page{}</style><script>var a = '<a href=x>status page</a>';</script>"
            "<template><a href=y>status page</a></template><noscript><a href=z>status page</a></noscript>")
    assert page_text.find_elements(html, "status") == []


def test_find_nbsp_and_nested_elements() -> None:
    html = '<div role="button"><div>status</div>&nbsp;page</div><a href="/x"><b>status</b>\n page</a>'
    assert [m["text"] for m in page_text.find_elements(html, "status page")] == ["status \xa0page", "status page"]


def test_find_an_unclosed_link_ends_at_the_next_link() -> None:
    html = '<a href="/1">one<a href="/2">two</a>'
    assert [m["text"] for m in page_text.find_elements(html, "")] == ["one", "two"]


def test_find_limit_and_index_order() -> None:
    html = "".join(f'<a href="/{n}">item {n}</a>' for n in range(5))
    matches = page_text.find_elements(html, "item", limit=2)
    assert [(m["text"], m["index"]) for m in matches] == [("item 0", 0), ("item 1", 1)]


def test_find_cli_output_and_exit_codes() -> None:
    found = run_cli(str(SAMPLE), "--url", SAMPLE_URL, "--find", "status page")
    assert found.returncode == 0
    assert found.stdout.decode("utf-8") == (
        'Found 1 match(es) for "status page":\n'
        '- link "status page" href=https://example.test/docs/status.html\n')
    labelled = run_cli(str(SAMPLE), "--find", "close")
    assert labelled.stdout.decode("utf-8").endswith('- button "×" aria-label="Close dialog"\n')
    missing = run_cli(str(SAMPLE), "--find", "script link")
    assert missing.returncode == 1
    assert missing.stdout.decode("utf-8") == 'No matches for "script link".\n'
    as_json = run_cli(str(SAMPLE), "--find", "nothing here", "--json")
    assert (as_json.returncode, json.loads(as_json.stdout)) == (1, [])


def test_existing_browser_fixture_is_readable() -> None:
    result = run_cli(str(ROOT / "fixtures" / "index.html"), "--find", "反映")
    assert result.returncode == 0
    assert result.stdout.decode("utf-8").endswith('- button "反映"\n')
    text = page_text.page_text((ROOT / "fixtures" / "index.html").read_text(encoding="utf-8"))["text"]
    assert "addEventListener" not in text and "日本語 / Unicode ✓ / 🧪" in text


# -- HTTP (local server only; EXTERNAL_SITE_AS_CORE_FIXTURE) -------------------

ROUTES = {
    "/old": (302, {"Location": "/new"}, b""),
    "/new": (200, {"Content-Type": "text/html; charset=Shift_JIS"},
             "<title>新</title><main><p>①移動後</p><a href='next'>次へ</a></main>".encode("cp932")),
    "/missing": (404, {"Content-Type": "text/html"}, b"<title>Page not found</title><main>Not Found</main>"),
    "/forbidden": (403, {"Content-Type": "text/html"}, b"<p>Forbidden</p>"),
    "/image.png": (200, {"Content-Type": "image/png"}, b"\x89PNG\r\n\x1a\n"),
}


@pytest.fixture()
def server(monkeypatch):
    for name in ("no_proxy", "NO_PROXY"):
        monkeypatch.setenv(name, "127.0.0.1,localhost")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - http.server API
            status, headers, body = ROUTES[self.path]
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_http_redirect_reports_final_url_and_decodes_header_charset(server, capsys) -> None:
    assert page_text.main([f"{server}/old"]) == 0
    out = capsys.readouterr().out
    assert out == f"Title: 新\nURL: {server}/new\nSource element: main\n---\n①移動後\n\n次へ\n"
    assert page_text.main([f"{server}/old", "--find", "次"]) == 0
    assert f"href={server}/next" in capsys.readouterr().out


def test_http_404_still_prints_the_page_and_warns_about_login_walls(server, capsys) -> None:
    assert page_text.main([f"{server}/missing"]) == 1
    captured = capsys.readouterr()
    assert "Not Found" in captured.out
    assert "HTTP 404" in captured.err and "LOGIN_WALL_AS_404" in captured.err
    assert page_text.main([f"{server}/forbidden", "--json"]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["status"] == 403
    assert "HTTP 403" in captured.err and "LOGIN_WALL_AS_404" not in captured.err


def test_http_non_html_content_type_warns(server, capsys) -> None:
    page_text.main([f"{server}/image.png"])
    assert "Content-Type is image/png, not HTML" in capsys.readouterr().err


def test_network_failure_is_exit_2(monkeypatch, capsys) -> None:
    for name in ("no_proxy", "NO_PROXY"):
        monkeypatch.setenv(name, "127.0.0.1,localhost")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    assert page_text.main([f"http://127.0.0.1:{port}/", "--timeout", "5"]) == 2
    assert "fetch failed" in capsys.readouterr().err


def test_textarea_content_is_not_page_text() -> None:
    assert text_of("<p>a</p><textarea>pre-filled\n  value</textarea><p>b</p>") == "a\n\nb"


def test_meta_utf16_label_is_read_as_utf8() -> None:
    assert page_text._decode(b'<meta charset="utf-16"><p>hi \xe2\x9c\x93</p>') == '<meta charset="utf-16"><p>hi ✓</p>'
    assert page_text._decode("x".encode("utf-16-le"), "text/html; charset=utf-16le") == "x"


# -- compare_rendered_text unit & CLI tests ----------------------------------

def test_compare_rendered_text_equal() -> None:
    text = "Hello world\nSecond line"
    res = page_text.compare_rendered_text(text, text)
    assert res["classification"] == "EQUAL"
    assert res["similarity_ratio"] == 1.0
    assert res["only_static"] == []
    assert res["only_rendered"] == []


def test_compare_rendered_text_js_added() -> None:
    static = "Static title"
    rendered = "Static title\nDynamic SPA component"
    res = page_text.compare_rendered_text(static, rendered)
    assert res["classification"] == "JS_RENDERED_ADDED"
    assert res["only_static"] == []
    assert res["only_rendered"] == ["Dynamic SPA component"]


def test_compare_rendered_text_css_hidden() -> None:
    static = "Header\nHidden element\nFooter"
    rendered = "Header\nFooter"
    res = page_text.compare_rendered_text(static, rendered)
    assert res["classification"] == "CSS_HIDDEN_REMOVED"
    assert res["only_static"] == ["Hidden element"]
    assert res["only_rendered"] == []


def test_compare_rendered_text_mutated() -> None:
    static = "Old text\nCommon line"
    rendered = "Common line\nNew text"
    res = page_text.compare_rendered_text(static, rendered)
    assert res["classification"] == "MUTATED_TEXT"
    assert res["only_static"] == ["Old text"]
    assert res["only_rendered"] == ["New text"]


def test_compare_rendered_cli_equal(tmp_path: Path) -> None:
    static_text = page_text.page_text(SAMPLE.read_text(encoding="utf-8"))["text"]
    rendered_file = tmp_path / "rendered.txt"
    rendered_file.write_text(static_text, encoding="utf-8")
    result = run_cli(str(SAMPLE), "--compare-rendered", str(rendered_file))
    assert result.returncode == 0
    assert "Classification: EQUAL" in result.stdout.decode("utf-8")


def test_compare_rendered_cli_diff_and_json(tmp_path: Path) -> None:
    static_text = page_text.page_text(SAMPLE.read_text(encoding="utf-8"))["text"]
    rendered_file = tmp_path / "rendered.txt"
    rendered_file.write_text(static_text + "\nExtra dynamic line", encoding="utf-8")
    result = run_cli(str(SAMPLE), "--compare-rendered", str(rendered_file))
    assert result.returncode == 1
    out = result.stdout.decode("utf-8")
    assert "Classification: JS_RENDERED_ADDED" in out
    assert "+ Extra dynamic line" in out

    json_result = run_cli(str(SAMPLE), "--compare-rendered", str(rendered_file), "--json")
    assert json_result.returncode == 1
    data = json.loads(json_result.stdout.decode("utf-8"))
    assert data["comparison"]["classification"] == "JS_RENDERED_ADDED"
    assert "Extra dynamic line" in data["comparison"]["only_rendered"]


def test_compare_rendered_cli_stdin_conflict() -> None:
    result = run_cli("-", "--compare-rendered", "-", stdin=b"<p>test</p>")
    assert result.returncode == 2
    assert "cannot use stdin (-) for both" in result.stderr.decode("utf-8")


def test_compare_rendered_cli_missing_file() -> None:
    result = run_cli(str(SAMPLE), "--compare-rendered", "nonexistent/file.txt")
    assert result.returncode == 2
    assert "no such file" in result.stderr.decode("utf-8")


def test_rendered_input_reads_dom_capture_visible_only() -> None:
    capture = json.dumps({"schema": "btk-browser-dom/1", "selector": "main", "html": "<p>Hidden</p>",
                          "text": "Hidden", "visible": "Shown"})
    assert page_text.rendered_input(capture) == ("Shown", {"kind": "dom-json", "schema": "btk-browser-dom/1",
                                                          "selector": "main"})
    assert page_text.rendered_input("plain\ntext")[1]["kind"] == "text"
    assert page_text.rendered_input("{not json")[0] == "{not json"
    assert page_text.rendered_input('{"text": "x"}')[1]["kind"] == "text"  # textContent is not rendered text
    with pytest.raises(ValueError):
        page_text.rendered_input(json.dumps({"visible": None}))


def test_compare_rendered_normalizes_whitespace_on_both_sides() -> None:
    assert page_text.compare_rendered_text("a b", "a    b\n\n\n")["classification"] == "EQUAL"


def test_compare_rendered_cli_dom_json(tmp_path: Path) -> None:
    html_file = tmp_path / "page.html"
    html_file.write_text("<main><h1>Title</h1><p>Static body</p></main>", encoding="utf-8")
    capture = tmp_path / "page.json"
    capture.write_text(json.dumps({"schema": "btk-browser-dom/1", "selector": "body",
                                   "visible": "Title\nStatic body\nDynamic JS badge"}), encoding="utf-8")
    result = run_cli(str(html_file), "--compare-rendered", str(capture), "--json")
    assert result.returncode == 1
    data = json.loads(result.stdout.decode("utf-8"))
    assert data["comparison"]["classification"] == "JS_RENDERED_ADDED"
    assert data["comparison"]["only_rendered"] == ["Dynamic JS badge"]
    assert data["rendered_input"]["kind"] == "dom-json"
    assert "captured from selector 'body'" in result.stderr.decode("utf-8")

    capture.write_text(json.dumps({"visible": None}), encoding="utf-8")
    assert run_cli(str(html_file), "--compare-rendered", str(capture)).returncode == 2


def test_compare_rendered_cli_refuses_truncated_or_oversized(tmp_path: Path) -> None:
    rendered = tmp_path / "rendered.txt"
    rendered.write_text("x" * 50, encoding="utf-8")
    truncated = run_cli(str(SAMPLE), "--compare-rendered", str(rendered), "--max-chars", "5")
    assert truncated.returncode == 2 and "raise --max-chars" in truncated.stderr.decode("utf-8")
    big = tmp_path / "big.txt"
    big.write_text("y" * (SAMPLE.stat().st_size + 10), encoding="utf-8")
    oversized = run_cli(str(SAMPLE), "--compare-rendered", str(big), "--max-bytes", str(SAMPLE.stat().st_size + 1))
    assert oversized.returncode == 2 and "exceeds --max-bytes" in oversized.stderr.decode("utf-8")


def test_compare_rendered_cli_reads_stdin_and_reports_os_error(tmp_path: Path) -> None:
    static_text = page_text.page_text(SAMPLE.read_text(encoding="utf-8"))["text"]
    ok = run_cli(str(SAMPLE), "--compare-rendered", "-", stdin=static_text.encode("utf-8"))
    assert ok.returncode == 0 and "Classification: EQUAL" in ok.stdout.decode("utf-8")
    bad = tmp_path / "bad.txt"
    bad.write_text("x", encoding="utf-8")
    bad.chmod(0)
    if os.access(bad, os.R_OK):
        pytest.skip("running as a user that can read mode-0 files")
    denied = run_cli(str(SAMPLE), "--compare-rendered", str(bad))
    assert denied.returncode == 2 and "error reading --compare-rendered input" in denied.stderr.decode("utf-8")


@pytest.mark.parametrize('source', ['file', 'stdin'])
def test_rendered_input_limits_binary_read_before_decoding(tmp_path, monkeypatch, capsys, source):
    import io
    import types
    html_file = tmp_path / 'page.html'
    html_file.write_text('<p>x</p>', encoding='utf-8')
    rendered = tmp_path / 'rendered.txt'
    rendered.write_bytes(b'not used')
    reads = []

    class BoundedInput(io.BytesIO):
        def read(self, size=-1):
            reads.append(size)
            assert size == 11  # max_bytes + 1, never an unbounded text read
            return super().read(size)

    stream = BoundedInput(b'\xff' * 100)
    if source == 'stdin':
        monkeypatch.setattr(page_text.sys, 'stdin', types.SimpleNamespace(buffer=stream))
        target = '-'
    else:
        old_open = Path.open
        monkeypatch.setattr(Path, 'open', lambda path, *a, **k: stream if path == rendered else old_open(path, *a, **k))
        target = str(rendered)
    assert page_text.main([str(html_file), '--compare-rendered', target, '--max-bytes', '10']) == 2
    assert reads == [11]
    assert 'exceeds --max-bytes' in capsys.readouterr().err


def test_rendered_byte_limit_counts_raw_bytes_not_replacement_encoding(tmp_path):
    html_file = tmp_path / 'page.html'
    html_file.write_text('<p>x</p>', encoding='utf-8')
    rendered = tmp_path / 'rendered.txt'
    rendered.write_bytes(b'\xff' * 9)
    result = run_cli(str(html_file), '--compare-rendered', str(rendered), '--max-bytes', '10')
    assert result.returncode == 1
    assert b'exceeds --max-bytes' not in result.stderr


def test_compare_rendered_refuses_byte_truncated_static_html(tmp_path):
    html_file = tmp_path / 'large.html'
    html_file.write_text('<p>x</p>' + ' ' * 100, encoding='utf-8')
    rendered = tmp_path / 'rendered.txt'
    rendered.write_text('x', encoding='utf-8')
    result = run_cli(str(html_file), '--compare-rendered', str(rendered), '--max-bytes', '10')
    assert result.returncode == 2 and b'static HTML was truncated by --max-bytes' in result.stderr


# ═══ · page text live regressions ════════════════════════════════════════════════════════════════════════
# Saved live DOM evidence; no upstream access in regression tests.

FIXTURES = Path(__file__).resolve().parents[2] / 'fixtures/page_text'


def test_alpine_hidden_copy_is_not_duplicated():
    sample = json.loads((FIXTURES / 'alpine-code-dom.json').read_text())
    assert text_of(sample['html']) == sample['visible_text']


def test_deno_pre_keeps_terminal_newline():
    sample = json.loads((FIXTURES / 'deno-doc-dom.json').read_text())['samples'][0]
    assert text_of(sample['dom_html']) == sample['inner_text']


@pytest.mark.parametrize('attributes', ['hidden', 'hidden="false"',
    'style="DISPLAY: none !important"', 'style="display:block;display:none"'])
def test_hidden_nested_and_void_elements_do_not_leak_or_swallow_siblings(attributes):
    assert text_of(f'<main><div {attributes}><div>copy</div><a href="/x">secret</a></div>'
                   f'<input {attributes}>kept</main>') == 'kept'
    assert not page_text.find_elements(f'<a {attributes} href="/x">secret</a>', 'secret')


def test_aria_hidden_is_accessibility_not_visual_hiding():
    assert text_of('<span aria-hidden="true">visible</span>') == 'visible'
    assert text_of('<span style="display:none;display:inline">visible</span>') == 'visible'
    assert text_of('<span style="display:none!important;display:inline">hidden</span>') == ''


@pytest.mark.parametrize('code', ['x\n', 'x\n\n\n', 'x  \n\t y  ', '\n\nx\n'])
def test_pre_preserves_content_not_print_formatting(code):
    assert text_of('<pre><code>' + code + '</code></pre>') == code


def test_pre_br_is_a_preserved_line_break():
    assert text_of('<pre><code>x<br>y<br></code></pre>') == 'x\ny\n'


@pytest.mark.parametrize('style', [
    '--sample:";display:none;"', "--sample:';display:none;'",
    '--sample:fn(;display:none;)', '/* ;display:none; */display:block',
    'display:none;display:/* restored */inline',
    'display:none;display:',
])
def test_css_data_and_comments_do_not_become_hiding_declarations(style):
    from html import escape
    expected = '' if style == 'display:none;display:' else 'visible'
    assert text_of('<span style="'+escape(style, quote=True)+'">visible</span>') == expected


@pytest.mark.parametrize('sample', json.loads(
    (FIXTURES / 'media-code-dom.json').read_text())['samples'], ids=lambda s: s['id'])
def test_selected_media_code_preserves_context_and_excludes_controls(sample):
    import hashlib
    assert hashlib.sha256(sample['pre_outer_html'].encode()).hexdigest() == sample['pre_sha256']
    assert hashlib.sha256(sample['code_outer_html'].encode()).hexdigest() == sample['code_sha256']
    assert sample['code_outer_html'] in sample['pre_outer_html']
    assert '<button' in sample['pre_outer_html']
    assert '<button' not in sample['code_outer_html']
    # Explicit context model for the selected code, not the original complete pre.
    assert text_of('<pre>'+sample['code_outer_html']+'</pre>') == sample['code_inner_text']
    assert 'Copy' not in sample['code_inner_text']
    assert 'MyComposition.tsx' not in sample['code_inner_text']
    assert 'const frame: number' not in sample['code_inner_text']


@pytest.mark.parametrize('name', ['ffmpeg', 'pillow'])
def test_previously_recorded_media_newlines_now_match(name):
    samples = json.loads((FIXTURES / 'media-doc-dom-survey.json').read_text())
    # The historical comparisons stay unchanged; the current reader is checked here.
    sample = next(s for s in samples if s['name'] == name)
    assert text_of(sample['fragment_html']) == sample['fragment_inner_text']


# ═══ · documentation dom ═════════════════════════════════════════════════════════════════════════════════
# Static documentation extraction; browser CSS difference remains explicit.

spec_documentation_dom=importlib.util.spec_from_file_location('documentation_page_text', ROOT/'tools/page_text.py')
module=importlib.util.module_from_spec(spec_documentation_dom);spec_documentation_dom.loader.exec_module(module)
module=importlib.util.module_from_spec(spec_documentation_dom);spec_documentation_dom.loader.exec_module(module)


def test_python_method_definition():
    html=(ROOT/'fixtures/page_text/python-method.html').read_text(encoding='utf-8')
    observed=module.page_text(html,url='https://docs.python.org/3/library/html.parser.html')
    assert observed['truncated'] is False
    words=observed['text'].split()
    assert words[0]=='HTMLParser.feed(data)¶'
    assert ' '.join(words[1:])=='Feed some text to the parser. It is processed insofar as it consists of complete elements; incomplete data is buffered until more data is fed or close() is called. data must be str.'
    links=module.find_elements(html,'close',url='https://docs.python.org/3/library/html.parser.html')
    assert any(link.get('href')=='https://docs.python.org/3/library/html.parser.html#html.parser.HTMLParser.close' for link in links)


# ═══ · browser dom local ═════════════════════════════════════════════════════════════════════════════════
# Exercise real Chromium against a deterministic local HTTP fixture.

@pytest.mark.browser
def test_dom_capture_local_page(tmp_path):
    (tmp_path/'index.html').write_text('<!doctype html><meta charset="utf-8"><main id="body"><h1>日本語</h1><p>Body &amp; text</p></main>',encoding='utf-8')
    handler=functools.partial(SimpleHTTPRequestHandler,directory=str(tmp_path))
    server=ThreadingHTTPServer(('127.0.0.1',0),handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        script=Path(__file__).resolve().parents[2]/'examples/capture_browser_dom.py'
        output=tmp_path/'capture.json'
        args=[sys.executable,str(script),f'http://127.0.0.1:{server.server_port}/index.html',
              '--selector','#body','--output',str(output)]
        run=subprocess.run(args,capture_output=True,text=True,timeout=90)
        assert run.returncode==0,run.stderr
        result=json.loads(output.read_text())
        assert result['schema']=='btk-browser-dom/1'
        assert 'Body &amp; text' in result['html']
        assert result['visible'].split()==['日本語','Body','&','text']
        assert result['content_verified'] is False
        # A repeated capture must preserve the original evidence byte for byte.
        original = output.read_bytes()
        repeat = subprocess.run(args, capture_output=True, text=True, timeout=10)
        assert repeat.returncode == 2
        assert 'output already exists' in repeat.stderr
        assert output.read_bytes() == original
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)


@pytest.mark.browser
@pytest.mark.parametrize('selector,code,error', [
    ('#missing', 2, 'Timeout'),
    ('p', 1, 'ambiguous_selector'),
])
def test_selector_mismatch_does_not_save(tmp_path, selector, code, error):
    (tmp_path / 'index.html').write_text('<main><p>one</p><p>two</p></main>', encoding='utf-8')
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        script = Path(__file__).resolve().parents[2] / 'examples/capture_browser_dom.py'
        output = tmp_path / 'capture.json'
        run = subprocess.run(
            [sys.executable, str(script), f'http://127.0.0.1:{server.server_port}/index.html',
             '--selector', selector, '--output', str(output), '--timeout-ms', '3000'],
            capture_output=True, text=True, timeout=30)
        assert run.returncode == code, run.stderr
        assert error in run.stderr
        assert not output.exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.browser
def test_aria_snapshot_and_same_origin_only(tmp_path):
    (tmp_path/'index.html').write_text(
        '<!doctype html><meta charset="utf-8"><style>a::before{content:"["}a::after{content:"]"}</style>'
        '<main id="body"><a href="/x">GitHub</a><button aria-label="Delete">\U0001F5D1\ufe0f</button>'
        '<img src="https://example.invalid/pixel.png" alt="Logo"></main>', encoding='utf-8')
    handler=functools.partial(SimpleHTTPRequestHandler,directory=str(tmp_path))
    server=ThreadingHTTPServer(('127.0.0.1',0),handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        script=Path(__file__).resolve().parents[2]/'examples/capture_browser_dom.py'
        output=tmp_path/'capture.json'
        run=subprocess.run([sys.executable,str(script),f'http://127.0.0.1:{server.server_port}/index.html',
                            '--selector','#body','--output',str(output),'--aria-snapshot','--same-origin-only'],
                           capture_output=True,text=True,timeout=90)
        assert run.returncode==0,run.stderr
        result=json.loads(output.read_text(encoding='utf-8'))
        # CSS generated content is part of the browser's accessible name, not of the HTML.
        assert '- link "[GitHub]"' in result['aria_snapshot']
        assert '- button "Delete"' in result['aria_snapshot'] and '- img "Logo"' in result['aria_snapshot']
        assert result['blocked_requests']==['https://example.invalid/pixel.png']
        assert result['browser_version']
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)


def _capture_module():
    import importlib.util
    path = Path(__file__).resolve().parents[2] / 'examples/capture_browser_dom.py'
    spec = importlib.util.spec_from_file_location('capture_browser_dom', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('a, b, same', [
    ('http://LOCALHOST:8000/a', 'http://localhost:8000/b', True),
    ('http://example.test:80/', 'http://example.test/x', True),
    ('https://Example.Test:443/', 'https://example.test/', True),
    ('http://example.test/', 'https://example.test/', False),
    ('http://example.test:8080/', 'http://example.test/', False),
    ('http://example.test/', 'http://example.test.evil/', False),
    ('http://example.test/', 'http://example.test@evil.test/', False),
])
def test_same_origin_key_uses_scheme_host_and_effective_port(a, b, same):
    key = _capture_module().same_origin_key
    assert (key(a) == key(b)) is same
    assert key(a) is not None


def test_same_origin_key_without_host_or_with_bad_port():
    key = _capture_module().same_origin_key
    assert key('data:text/plain,x') is None
    assert key('http://example.test:99999/') is None


# ═══ · browser capture cli ═══════════════════════════════════════════════════════════════════════════════
# Dependency-free CLI contract checks, not browser execution certification.

BROWSER_CAPTURE_CLI_SCRIPT=Path(__file__).resolve().parents[2]/'examples/capture_browser_dom.py'

@pytest.mark.parametrize('args,code',[(['--help'],0),(['file:///etc/passwd','--output','unused.json'],2),(['https://example.com','--output','unused.json','--timeout-ms','0'],2)])
def test_cli_preflight_without_browser(args,code,tmp_path):
    run=subprocess.run([sys.executable,str(BROWSER_CAPTURE_CLI_SCRIPT),*args],cwd=tmp_path,capture_output=True,text=True)
    assert run.returncode==code
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('kind', ['file', 'directory', 'dangling_symlink', 'missing_parent'])
def test_unusable_output_rejected_before_browser_import_or_network(tmp_path, kind):
    import os
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading

    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'<main>must not be fetched</main>')

        def log_message(self, *args):
            pass

    output = tmp_path / 'capture.json'
    if kind == 'file':
        output.write_bytes(b'existing evidence')
    elif kind == 'directory':
        output.mkdir()
    elif kind == 'dangling_symlink':
        output.symlink_to(tmp_path / 'missing.json')
    else:
        output = tmp_path / 'missing' / 'capture.json'
    # The guard must run before even importing Playwright; no installation needed.
    package = tmp_path / 'playwright'
    package.mkdir()
    marker = tmp_path / 'browser-imported'
    (package / '__init__.py').write_text(
        'from pathlib import Path\nPath(' + repr(str(marker)) + ').touch()\n'
        'raise AssertionError("Playwright must not be imported")\n', encoding='utf-8')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        run = subprocess.run([sys.executable, '-S', str(BROWSER_CAPTURE_CLI_SCRIPT),
                              f'http://127.0.0.1:{server.server_port}/', '--output', str(output)],
                             env={**os.environ, 'PYTHONPATH': str(tmp_path)},
                             capture_output=True, text=True, timeout=10)
        assert run.returncode == 2
        assert ('output already exists' if kind != 'missing_parent' else 'output parent is not a directory') in run.stderr
        assert not marker.exists()
        assert requests == []
        if kind == 'file':
            assert output.read_bytes() == b'existing evidence'
        elif kind == 'directory':
            assert output.is_dir() and not list(output.iterdir())
        elif kind == 'dangling_symlink':
            assert output.is_symlink() and not output.exists()
        else:
            assert not output.parent.exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# ═══ · element overlay ═══════════════════════════════════════════════════════════════════════════════════

SCRIPTS = Path(__file__).resolve().parents[2] / "tools"


def test_annotate_elements_basic():
    elements = [
        {"x": 10, "y": 20, "width": 100, "height": 40, "name": "Login", "role": "button", "id": "btn-login"},
        {"x": 120, "y": 20, "w": 80, "h": 40, "label": "Cancel", "role": "button", "selector": "#btn-cancel"},
        {"x": 0, "y": 0, "width": 0, "height": 10},  # invalid width
        "not a dict",
    ]
    res = element_overlay.annotate_elements(elements, {"viewport": "800x600"})
    assert res["schema"] == "btk-element-overlay/1"
    assert res["count"] == 2
    assert len(res["items"]) == 2

    e1 = res["items"][0]
    assert e1["index"] == 1
    assert e1["name"] == "Login"
    assert e1["selector"] == "#btn-login"
    assert e1["cx"] == 60.0
    assert e1["cy"] == 40.0
    assert e1["clipped"] is False


def test_annotate_elements_find_filter_and_clipped():
    elements = [
        {"x": 10, "y": 20, "width": 100, "height": 40, "name": "Submit Form", "role": "button"},
        {"x": 900, "y": 20, "width": 50, "height": 40, "name": "Offscreen", "role": "link"},
    ]
    res = element_overlay.annotate_elements(elements, {"find": "submit", "viewport": "800x600"})
    assert res["count"] == 1
    assert res["items"][0]["name"] == "Submit Form"

    res_off = element_overlay.annotate_elements(elements, {"viewport": {"width": 800, "height": 600}})
    assert res_off["count"] == 2
    assert res_off["items"][1]["clipped"] is True


def test_build_highlight_css():
    css = element_overlay.build_highlight_css(["#submit-btn", {"selector": ".cancel-btn"}], color="#00ff00", outline_width=3)
    assert "#submit-btn, .cancel-btn {" in css
    assert "outline: 3px solid #00ff00 !important;" in css
    assert element_overlay.build_highlight_css([]) == ""


def test_render_svg_overlay():
    elements = [
        {"x": 10, "y": 20, "width": 100, "height": 40, "name": "Login", "role": "button"},
    ]
    svg = element_overlay.render_svg_overlay(elements, {"width": 400, "height": 300, "color": "#0000ff"})
    assert '<svg xmlns="http://www.w3.org/2000/svg"' in svg
    assert 'width="400"' in svg
    assert 'stroke="#0000ff"' in svg
    assert '<circle cx="60" cy="40" r="3"' in svg


def test_assert_elements_tap():
    elements = [
        {"x": 10, "y": 20, "width": 100, "height": 40, "name": "Login", "role": "button"},
    ]
    res = element_overlay.assert_elements(elements, {"min_count": 1, "assert_role": "button"})
    assert res["ok"] is True
    assert res["passed"] == 2
    assert "TAP version 13" in res["tap"]
    assert "ok 1 - element count (1) >= min_count (1)" in res["tap"]
    assert "ok 2 - at least one element has role 'button'" in res["tap"]

    failing = element_overlay.assert_elements(elements, {"assert_role": "textbox"})
    assert failing["ok"] is False
    assert failing["failed"] == 1
    assert "not ok 2 - at least one element has role 'textbox'" in failing["tap"]


def test_cli_main_file_and_format(tmp_path: Path):
    in_file = tmp_path / "elements.json"
    out_svg = tmp_path / "out.svg"

    data = [{"x": 10, "y": 20, "width": 100, "height": 40, "name": "Submit", "role": "button", "id": "btn"}]
    in_file.write_text(json.dumps(data), encoding="utf-8")

    code = element_overlay.main(["--file", str(in_file), "--format", "svg", "--out", str(out_svg)])
    assert code == 0
    assert "<svg" in out_svg.read_text(encoding="utf-8")

    code_tap = element_overlay.main(["--file", str(in_file), "--format", "tap", "--assert-role", "button"])
    assert code_tap == 0

    code_fail = element_overlay.main(["--file", str(in_file), "--format", "tap", "--assert-role", "dialog"])
    assert code_fail == 1


def test_cli_main_python_m(tmp_path: Path):
    env = {"PYTHONPATH": str(SCRIPTS)}
    done = subprocess.run(
        [sys.executable, "-S", "-m", "element_overlay", "--format", "css", "--color", "#123456"],
        input='[{"selector": "#a"}]', capture_output=True, text=True, env=env, timeout=10
    )
    assert done.returncode == 0
    assert "#a {" in done.stdout
    assert "#123456" in done.stdout


def test_mark_svg_shapes_labels_numbers_and_spotlight():
    box = {"x": 100, "y": 50, "width": 40, "height": 20}
    def draw(**look):
        return element_overlay.mark_svg([{**element_overlay.check_mark(look), "box": box}], (392, 848), attributes=' data-x="1"')
    rect = draw(color="#1565c0", width=8, pad=0)
    assert '<rect x="100" y="50" width="40" height="20"' in rect and 'stroke="#1565c0" stroke-width="8"' in rect
    assert rect.startswith('<svg xmlns="http://www.w3.org/2000/svg" data-x="1" width="392" height="848"') and "style=" not in rect
    assert '<ellipse cx="120" cy="60"' in draw(shape="ellipse") and '<polygon points="120,6 64,78 176,78"' in draw(shape="triangle")
    assert 'x1="28" y1="60" x2="76" y2="60"' in draw(shape="arrow")                    # room on the left: it comes from there
    edge = element_overlay.mark_svg([{**element_overlay.check_mark({"shape": "arrow", "pad": 0}), "box": {**box, "x": 0}}], (392, 848))
    assert 'x1="104" ' in edge                                                          # no room left: from the right
    label = draw(label="ここ <b>", pad=0)
    assert "ここ &lt;b&gt;</text>" in label and 'font-family="Meiryo,' in label and '<rect x="148" y="44" width="' in label  # right of the box
    def label_at(box, viewport, **look):
        svg = element_overlay.mark_svg([{**element_overlay.check_mark({"label": "ラベル", "pad": 0, **look}), "box": box}], viewport)
        return svg.split('rx="8"')[0].rsplit("<rect ", 1)[1]
    assert label_at({**box, "x": 300}, (392, 848)).startswith('x="300" y="78"')       # no room right: below
    assert label_at({**box, "x": 300, "y": 820}, (392, 848)).startswith('x="300" y="780"')   # nor below: above
    two = element_overlay.mark_svg([{**element_overlay.check_mark({"label": "あ", "pad": 0}), "box": box},
                                    {**element_overlay.check_mark({"pad": 0}), "box": {**box, "x": 148}}], (392, 848))
    assert '<rect x="100" y="78"' in two                                                # right is another mark: below
    wide = element_overlay.mark_svg([{**element_overlay.check_mark({"shape": "arrow", "pad": 0, "label": "結果"}), "box": {"x": 32, "y": 200, "width": 336, "height": 20}}], (392, 848))
    assert 'x1="200" y1="136" x2="200" y2="184"' in wide and '<rect x="176" y="96"' in wide   # no side room: from above, label at the tail
    assert '<circle cx="92" cy="42" r="14"' in draw(number=3) and ">3</text>" in draw(number=3)
    lit = draw(spotlight=True, pad=0)
    assert 'fill-rule="evenodd"' in lit and "M100 50h40v20h-40Z" in lit and 'fill-rule' not in draw()
    for bad in ({"shape": "star"}, {"color": "red"}, {"width": 0}, {"pad": 65}, {"label": ""}, {"number": 100}, {"spotlight": "yes"}, {"spotlight": 1}, {"number": True}):
        try:
            element_overlay.check_mark(bad)
        except ValueError:
            continue
        raise AssertionError(bad)


def test_svg_overlay_shape_uses_the_mark_drawer():
    elements = [{"x": 10, "y": 20, "width": 100, "height": 40, "name": "Login"}, {"x": 120, "y": 20, "width": 80, "height": 40, "name": "Cancel"}]
    svg = element_overlay.render_svg_overlay(elements, {"shape": "ellipse", "label_field": "name", "spotlight": True, "viewport": "400x300"})
    assert svg.count("<ellipse") == 2 and ">Login</text>" in svg and ">2</text>" in svg and 'fill-rule="evenodd"' in svg
    run = subprocess.run([sys.executable, "-S", str(SCRIPTS / "element_overlay.py"), "--format", "svg", "--shape", "rect", "--viewport", "400x300"],
                         input=json.dumps(elements[:1]), capture_output=True, text=True, check=True)
    assert run.stdout.count("<rect") == 1 and "<circle" not in run.stdout              # one element: no number badge


# ═══ · html builder ══════════════════════════════════════════════════════════════════════════════════════

HTML_BUILDER_SCRIPT = ROOT / "tools" / "html_builder.py"
spec_html_builder = importlib.util.spec_from_file_location("html_builder", HTML_BUILDER_SCRIPT)
html_builder = importlib.util.module_from_spec(spec_html_builder)
assert spec_html_builder.loader
spec_html_builder.loader.exec_module(html_builder)
h = html_builder.h
page = html_builder.page
Raw = html_builder.Raw


# --- h(): children ----------------------------------------------------------

def test_text_child_is_escaped() -> None:
    assert h("p", "<script>alert('x')</script>") == "<p>&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;</p>"


def test_numbers_render_via_str_without_escaping() -> None:
    assert h("p", 0, 1, 3.5) == "<p>013.5</p>"


def test_none_and_false_children_are_skipped() -> None:
    assert h("p", None, False, "x") == "<p>x</p>"


def test_lists_and_tuples_are_flattened_recursively() -> None:
    assert h("div", ["a", ("b", [None, "c"])], "d") == "<div>abcd</div>"


def test_raw_is_inserted_unescaped() -> None:
    assert h("p", Raw("<b>bold</b>")) == "<p><b>bold</b></p>"


def test_h_output_composes_without_double_escaping() -> None:
    """h() itself returns Raw, so nesting h() calls must not re-escape."""
    inner = h("span", "<x>")
    assert inner == "<span>&lt;x&gt;</span>"
    assert h("div", inner) == "<div><span>&lt;x&gt;</span></div>"
    assert h("ul", h("li", "a"), h("li", "b")) == "<ul><li>a</li><li>b</li></ul>"
    assert isinstance(h("p", "x"), str)


def test_no_children_renders_empty_element() -> None:
    assert h("div") == "<div></div>"


# --- h(): attributes ---------------------------------------------------------

def test_trailing_underscore_is_stripped() -> None:
    assert h("label", for_="name") == '<label for="name"></label>'
    assert h("div", class_="box") == '<div class="box"></div>'


def test_internal_underscores_become_hyphens() -> None:
    assert h("div", data_id="7", aria_label="hi") == '<div data-id="7" aria-label="hi"></div>'


def test_true_is_a_bare_attribute() -> None:
    assert h("input", disabled=True) == "<input disabled>"


def test_false_and_none_attributes_are_omitted() -> None:
    assert h("input", disabled=False, value=None, type="text") == '<input type="text">'


def test_attribute_values_are_escaped_and_quoted() -> None:
    assert h("a", href='x?a=1&b="2"') == '<a href="x?a=1&amp;b=&quot;2&quot;"></a>'


def test_attribute_order_is_preserved() -> None:
    assert h("input", c="3", a="1", b="2") == '<input c="3" a="1" b="2">'


# --- h(): void elements -------------------------------------------------------

@pytest.mark.parametrize("tag", ["area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
                                 "source", "track", "wbr"])
def test_void_elements_have_no_closing_tag(tag: str) -> None:
    assert h(tag) == f"<{tag}>"


def test_void_element_with_children_raises() -> None:
    with pytest.raises(ValueError, match="void"):
        h("br", "text")


def test_void_element_with_only_falsy_children_does_not_raise() -> None:
    assert h("br", None, False) == "<br>"


def test_non_void_element_keeps_closing_tag() -> None:
    assert h("div", "x") == "<div>x</div>"


# --- page() -------------------------------------------------------------------

def test_page_wraps_body_in_full_document_with_utf8_meta() -> None:
    out = page("My Title", h("p", "hi"))
    assert out == ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
                   "<title>My Title</title></head><body><p>hi</p></body></html>")


def test_page_escapes_title_and_honors_lang() -> None:
    out = page("A & B", lang="fr")
    assert "<title>A &amp; B</title>" in out
    assert out.startswith('<!doctype html>\n<html lang="fr">')


# --- CLI ------------------------------------------------------------------

def run_cli_html_builder(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-S", str(HTML_BUILDER_SCRIPT), *args], capture_output=True, text=True,
                          encoding="utf-8")


def test_cli_renders_tags_and_folds_consecutive_li_into_one_ul() -> None:
    done = run_cli_html_builder("h1:Title", "p:Hello world", "br", "li:a", "li:b")
    assert done.returncode == 0, done.stderr
    assert done.stdout == "<h1>Title</h1>\n<p>Hello world</p>\n<br>\n<ul><li>a</li><li>b</li></ul>\n"


def test_cli_bare_tag_with_no_colon_has_no_text() -> None:
    done = run_cli_html_builder("hr", "p")
    assert done.returncode == 0, done.stderr
    assert done.stdout == "<hr>\n<p></p>\n"


def test_cli_page_flag_wraps_output() -> None:
    done = run_cli_html_builder("h1:Title", "p:Body", "--page", "My Page")
    assert done.returncode == 0, done.stderr
    assert done.stdout == ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
                           "<title>My Page</title></head><body><h1>Title</h1>\n"
                           "<p>Body</p></body></html>\n")


def test_cli_requires_at_least_one_item() -> None:
    done = run_cli_html_builder()
    assert done.returncode == 2
    assert "usage" in done.stderr.lower()


def test_cli_help_exits_zero_with_usage_on_stdout() -> None:
    done = run_cli_html_builder("--help")
    assert done.returncode == 0
    assert done.stdout.lower().startswith("usage")


def test_module_help_via_dash_m_exits_zero_with_usage_on_stdout() -> None:
    done = subprocess.run([sys.executable, "-S", "-m", "html_builder", "--help"], capture_output=True, text=True,
                          encoding="utf-8", cwd=str(HTML_BUILDER_SCRIPT.parent))
    assert done.returncode == 0, done.stderr
    assert done.stdout.lower().startswith("usage")


# ═══ · check font family ═════════════════════════════════════════════════════════════════════════════════
# check_font_family: Meiryo first in CSS and Plotly, monospace and inherit allowed, bundled plotly.js skipped.

spec_check_font_family = importlib.util.spec_from_file_location("check_font_family", ROOT / "tools" / "check_font_family.py")
cf = importlib.util.module_from_spec(spec_check_font_family)
assert spec_check_font_family.loader
spec_check_font_family.loader.exec_module(cf)

MEIRYO = "Meiryo, メイリオ, sans-serif"


@pytest.mark.parametrize("stack,ok", [
    (MEIRYO, True),
    ("'Meiryo',sans-serif", True),
    ("メイリオ, Meiryo", True),
    ("Meiryo, メイリオ, sans-serif !important", True),
    ("inherit", True),
    ("var(--ui-font)", True),
    ('ui-monospace, "Courier New", monospace', True),
    ("system-ui, sans-serif", False),
    ("-apple-system, BlinkMacSystemFont, 'Segoe UI'", False),
    ("sans-serif", False),
    ("initial", False),
    ("Yu Gothic, Meiryo", False),
])
def test_reason_accepts_meiryo_first_inherit_var_and_monospace_only(stack, ok):
    assert (cf.reason(stack) is None) is ok


def scan(text, suffix=".html"):
    return cf.scan(text, suffix)


def test_css_declaration_and_shorthand_are_both_read_with_line_numbers():
    text = f"<style>\nbody{{font:16px/1.65 {MEIRYO}}}\nh1{{font-family:system-ui}}\n</style>"
    seen, problems = scan(text)
    assert seen == 2
    assert [line for line, _ in problems] == [3] and "system-ui" in problems[0][1]


def test_custom_property_names_and_font_size_are_not_declarations():
    assert scan("a{--ui-font:system-ui;font-size:16px;font-weight:700}") == (0, [])


def test_inline_style_attribute_and_quoted_names():
    seen, problems = scan("""<p style="font-family:'Courier New', monospace">x</p><p style="font-family: Arial">y</p>""")
    assert seen == 2 and len(problems) == 1 and "Arial" in problems[0][1]
    seen, problems = scan('a{font-family:"Segoe UI", Meiryo}')  # a quoted name with a space is one name
    assert seen == 1 and "Segoe UI" in problems[0][1]


def test_plotly_family_in_json_and_in_script_text():
    seen, problems = scan('{"layout": {"font": {"family": "Arial"}}} x = {font:{family:\'%s\'}}' % MEIRYO, ".json")
    assert seen == 2 and len(problems) == 1 and "Arial" in problems[0][1]


def test_plotly_call_without_any_figure_family_fails_because_plotly_defaults_to_open_sans():
    page = "<script>Plotly.newPlot('f', [], {});</script><style>body{font-family:%s}</style>" % MEIRYO
    _, problems = scan(page)
    assert len(problems) == 1 and "Open Sans" in problems[0][1]
    ok = "<script>Plotly.newPlot('f', [], {font:{family:'%s'}});</script>" % MEIRYO
    assert scan(ok)[1] == []


def test_plotly_json_spec_needs_layout_font_family():
    spec_check_font_family = {"data": [], "layout": {"title": "t"}}
    assert "Open Sans" in scan(json.dumps(spec_check_font_family), ".json")[1][0][1]
    spec_check_font_family["layout"]["font"] = {"family": MEIRYO}
    assert scan(json.dumps(spec_check_font_family), ".json")[1] == []
    assert scan(json.dumps({"rows": []}), ".json") == (0, [])  # not a figure


def test_inlined_plotly_bundle_is_skipped_but_the_page_around_it_is_checked():
    bundle = "/**\n* plotly.js v4.1.2\n*/\nvar css='font-family:\"Open Sans\",verdana,arial,sans-serif';\nvar f={family:'Open Sans'};"
    page = f"<script>{bundle}</script>\n<style>body{{font-family:{MEIRYO}}}</style>\n<script>Plotly.newPlot('a',[],{{font:{{family:'{MEIRYO}'}}}})</script>"
    seen, problems = scan(page)
    assert (seen, problems) == (2, [])
    broken = page.replace(MEIRYO, "system-ui", 1)  # the style line follows the 5-line bundle
    assert [line for line, _ in scan(broken)[1]] == [6]


def test_cli_exit_codes_and_bounded_output(tmp_path, capsys):
    good = tmp_path / "good.html"
    good.write_text(f"<style>body{{font:16px/1.6 {MEIRYO}}}</style>")
    assert cf.main([str(good)]) == 0 and "pass 1 files, 1 font declarations, 0 problems" in capsys.readouterr().out
    bad = tmp_path / "sub" / "bad.css"
    bad.parent.mkdir()
    bad.write_text("".join(f"p{i}{{font-family:Arial}}\n" for i in range(cf.MAX_SHOWN + 5)))
    assert cf.main([str(tmp_path)]) == 1  # a directory is searched
    out = capsys.readouterr().out.splitlines()
    assert len(out) == cf.MAX_SHOWN + 2 and out[-2] == "... 5 more" and out[-1].startswith("fail 2 files")
    assert f"bad.css:1: font-family: Arial" in out[0]


def test_nothing_declared_is_a_failure_and_bad_input_is_exit_2(tmp_path, capsys):
    empty = tmp_path / "empty.html"
    empty.write_text("<p>no fonts</p>")
    assert cf.main([str(empty)]) == 1 and "nothing asks for Meiryo" in capsys.readouterr().out
    assert cf.main([]) == 2
    assert cf.main([str(tmp_path / "missing.html")]) == 2 and "no such file" in capsys.readouterr().err
    assert cf.main([str(tmp_path / "sub")]) == 2
    assert cf.main(["--help"]) == 0


@pytest.mark.parametrize("layout", [
    {}, {"font": {}}, {"font": None}, {"font": []},
    {"font": {"family": None}}, {"font": {"family": ""}},
    {"font": {"family": "  "}}, {"font": {"family": 123}},
    {"font": {"family": ["Meiryo"]}}, None, [],
])
def test_trace_family_does_not_replace_layout_family(layout, tmp_path, capsys):
    spec_check_font_family = {"data": [{"type": "scatter", "textfont": {"family": "Meiryo"}}],
            "layout": layout}
    text = json.dumps(spec_check_font_family)
    _, problems = scan(text, ".json")
    assert any("no layout.font.family" in why for _, why in problems)
    path = tmp_path / "figure.json"
    path.write_text(text, encoding="utf-8")
    assert cf.main([str(path)]) == 1
    assert "no layout.font.family" in capsys.readouterr().out


def test_plotly_json_without_layout_does_not_use_trace_family():
    spec_check_font_family = {"data": [{"textfont": {"family": "Meiryo"}}]}
    assert "no layout.font.family" in scan(json.dumps(spec_check_font_family), ".json")[1][0][1]


def test_plotly_json_layout_and_trace_families_are_both_checked():
    spec_check_font_family = {"data": [{"textfont": {"family": "Meiryo"}}],
            "layout": {"font": {"family": MEIRYO}}}
    assert scan(json.dumps(spec_check_font_family), ".json") == (2, [])
    spec_check_font_family["layout"]["font"]["family"] = "Arial"
    assert "Arial" in scan(json.dumps(spec_check_font_family), ".json")[1][0][1]
    spec_check_font_family["layout"]["font"]["family"] = MEIRYO
    spec_check_font_family["data"][0]["textfont"]["family"] = "Arial"
    assert "Arial" in scan(json.dumps(spec_check_font_family), ".json")[1][0][1]


def test_plotly_json_escaped_keys_and_values_use_decoded_family():
    text = r'{"data":[{"textfont":{"family":"Meiryo"}}],"layout":{"font":{"fami\u006cy":"Arial"}}}'
    assert "Arial" in scan(text, ".json")[1][0][1]
    assert scan(text.replace("Arial", r"Meir\u0079o"), ".json") == (2, [])
