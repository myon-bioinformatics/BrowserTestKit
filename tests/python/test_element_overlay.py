import json, subprocess, sys
from pathlib import Path

import element_overlay

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
