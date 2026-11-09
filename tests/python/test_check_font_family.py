"""check_font_family: Meiryo first in CSS and Plotly, monospace and inherit allowed, bundled plotly.js skipped."""
import importlib.util, json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("check_font_family", ROOT / "tools" / "check_font_family.py")
cf = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(cf)

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
    spec = {"data": [], "layout": {"title": "t"}}
    assert "Open Sans" in scan(json.dumps(spec), ".json")[1][0][1]
    spec["layout"]["font"] = {"family": MEIRYO}
    assert scan(json.dumps(spec), ".json")[1] == []
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
    spec = {"data": [{"type": "scatter", "textfont": {"family": "Meiryo"}}],
            "layout": layout}
    text = json.dumps(spec)
    _, problems = scan(text, ".json")
    assert any("no layout.font.family" in why for _, why in problems)
    path = tmp_path / "figure.json"
    path.write_text(text, encoding="utf-8")
    assert cf.main([str(path)]) == 1
    assert "no layout.font.family" in capsys.readouterr().out


def test_plotly_json_without_layout_does_not_use_trace_family():
    spec = {"data": [{"textfont": {"family": "Meiryo"}}]}
    assert "no layout.font.family" in scan(json.dumps(spec), ".json")[1][0][1]


def test_plotly_json_layout_and_trace_families_are_both_checked():
    spec = {"data": [{"textfont": {"family": "Meiryo"}}],
            "layout": {"font": {"family": MEIRYO}}}
    assert scan(json.dumps(spec), ".json") == (2, [])
    spec["layout"]["font"]["family"] = "Arial"
    assert "Arial" in scan(json.dumps(spec), ".json")[1][0][1]
    spec["layout"]["font"]["family"] = MEIRYO
    spec["data"][0]["textfont"]["family"] = "Arial"
    assert "Arial" in scan(json.dumps(spec), ".json")[1][0][1]


def test_plotly_json_escaped_keys_and_values_use_decoded_family():
    text = r'{"data":[{"textfont":{"family":"Meiryo"}}],"layout":{"font":{"fami\u006cy":"Arial"}}}'
    assert "Arial" in scan(text, ".json")[1][0][1]
    assert scan(text.replace("Arial", r"Meir\u0079o"), ".json") == (2, [])
