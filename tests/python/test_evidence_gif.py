"""evidence_gif: shots are checked before any browser starts, and a recorded GIF has one captioned frame per shot and step."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("evidence_gif", ROOT / "tools" / "evidence_gif.py")
eg = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(eg)


def test_shot_and_plan_checks():
    assert eg.parse_shot("🏠 Top|/index.html|1280x800") == {"label": "🏠 Top", "url": "/index.html", "viewport": "1280x800"}
    plan = eg.check_plan([{"url": "/a.html", "viewport": "392x848", "steps": [{"click": "#go"}, {"wait": 100}], "allow": ["sri"]}])
    assert plan[0]["viewport"] == (392, 848) and plan[0]["label"] == "/a.html" and plan[0]["hold"] == 1200
    assert plan[0]["allow"] == ["sri"]
    for bad in (["no-bars"], ["a|/x|12by8"]):
        with pytest.raises(ValueError):
            eg.check_plan([eg.parse_shot(s) for s in bad])
    with pytest.raises(ValueError):
        eg.check_plan([{"url": "/a.html", "steps": [{"hover": "#x"}]}])
    with pytest.raises(ValueError):
        eg.check_plan([])
    with pytest.raises(ValueError):
        eg.resolve("/a.html", None)
    with pytest.raises(ValueError):
        eg.parse_map("cdn.example.test=/")
    assert eg.resolve("https://example.test/", None) == "https://example.test/"


def test_cli_usage_errors_exit_2(capsys):
    with pytest.raises(SystemExit) as exit_info:
        eg.main(["out.gif"])
    assert exit_info.value.code == 2 and "no shots" in capsys.readouterr().err


@pytest.mark.browser
def test_records_frames_and_receipt(tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image, ImageSequence
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<h1>ok</h1><button id=b onclick=\"this.textContent='done'\">go</button><select id=s><option>a<option>b</select><img src='https://example.invalid/x.png'><link rel=stylesheet href='https://cdn.example.test/pin/site.css'>", encoding="utf-8")
    (site / "broken.html").write_text("<script>throw new Error('boom')</script>", encoding="utf-8")
    cdn = tmp_path / "cdn"
    cdn.mkdir()
    (cdn / "site.css").write_text("h1{color:rgb(1,2,3)}", encoding="utf-8")
    out = tmp_path / "evidence" / "browser.gif"
    code = eg.main([str(out), "--serve", str(site), "--sha", "0" * 40, "--map", f"https://cdn.example.test/pin={cdn}", "--shot", "🏠 Top|/index.html|392x848",
                    "--plan", str(_plan(tmp_path, [{"label": "🖱️ click", "url": "/index.html", "steps": [{"click": "#b"}, {"select": ["#s", "b"]}]},
                                                   {"label": "💥", "url": "/broken.html"}, {"label": "🚫", "url": "/missing.html"}]))])
    receipt = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert code == 1 and receipt["summary"] == {"pass": 2, "fail": 2}
    assert [s["status"] for s in receipt["shots"]] == ["pass", "pass", "fail", "fail"]
    assert any("boom" in e for e in receipt["shots"][2]["errors"]) and "http 404" in receipt["shots"][3]["errors"]
    assert receipt["shots"][1]["steps"] == ["click #b", "select #s → b"]
    assert receipt["shots"][0]["external"] == ["https://example.invalid/x.png"]       # refused, listed, not a failure
    assert receipt["mapped"] == ["https://cdn.example.test/pin/"]                      # mapped CSS: served locally, not external and receipt["commit"] == "0" * 40
    frames = list(ImageSequence.Iterator(Image.open(out)))
    assert len(frames) == receipt["frames"] == 6 and frames[0].size == (720, 512)
    assert receipt["plan"][0]["url"] == "/index.html" and len(receipt["plan"]) == 4
    again = tmp_path / "again.gif"                       # the receipt is the recipe: the same plan records the same shots
    assert eg.main([str(again), "--serve", str(site), "--plan", str(out.with_suffix(".json"))]) == 1
    assert json.loads(again.with_suffix(".json").read_text(encoding="utf-8"))["summary"] == receipt["summary"]


def _plan(tmp_path, shots):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(shots), encoding="utf-8")
    return path
