import importlib.util, json, os, socket, subprocess, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer; from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
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
