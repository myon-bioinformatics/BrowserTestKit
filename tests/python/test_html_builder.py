from __future__ import annotations

import importlib.util, subprocess, sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "html_builder.py"
spec = importlib.util.spec_from_file_location("html_builder", SCRIPT)
html_builder = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(html_builder)
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

def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-S", str(SCRIPT), *args], capture_output=True, text=True,
                          encoding="utf-8")


def test_cli_renders_tags_and_folds_consecutive_li_into_one_ul() -> None:
    done = run_cli("h1:Title", "p:Hello world", "br", "li:a", "li:b")
    assert done.returncode == 0, done.stderr
    assert done.stdout == "<h1>Title</h1>\n<p>Hello world</p>\n<br>\n<ul><li>a</li><li>b</li></ul>\n"


def test_cli_bare_tag_with_no_colon_has_no_text() -> None:
    done = run_cli("hr", "p")
    assert done.returncode == 0, done.stderr
    assert done.stdout == "<hr>\n<p></p>\n"


def test_cli_page_flag_wraps_output() -> None:
    done = run_cli("h1:Title", "p:Body", "--page", "My Page")
    assert done.returncode == 0, done.stderr
    assert done.stdout == ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
                           "<title>My Page</title></head><body><h1>Title</h1>\n"
                           "<p>Body</p></body></html>\n")


def test_cli_requires_at_least_one_item() -> None:
    done = run_cli()
    assert done.returncode == 2
    assert "usage" in done.stderr.lower()


def test_cli_help_exits_zero_with_usage_on_stdout() -> None:
    done = run_cli("--help")
    assert done.returncode == 0
    assert done.stdout.lower().startswith("usage")


def test_module_help_via_dash_m_exits_zero_with_usage_on_stdout() -> None:
    done = subprocess.run([sys.executable, "-S", "-m", "html_builder", "--help"], capture_output=True, text=True,
                          encoding="utf-8", cwd=str(SCRIPT.parent))
    assert done.returncode == 0, done.stderr
    assert done.stdout.lower().startswith("usage")
