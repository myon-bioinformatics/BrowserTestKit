"""evidence_gif: shots are checked before any browser starts; a recording in each engine is read back by a stdlib parser, the engine itself and Pillow."""
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


def gif_blocks(data: bytes) -> dict:
    """The GIF's structure, read here with the standard library (independent of the encoder): screen, loop, comment, frames."""
    assert data[:6] == b"GIF89a" and data[-1:] == b";"
    width, height, packed = int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little"), data[10]
    pos, frames, gce, loop, comment = 13 + (3 * (2 << (packed & 7)) if packed & 0x80 else 0), [], None, None, b""
    while data[pos] != 0x3B:
        kind = data[pos]
        if kind == 0x21:
            label, pos, blocks = data[pos + 1], pos + 2, []
            while data[pos]:
                blocks.append(data[pos + 1:pos + 1 + data[pos]])
                pos += 1 + data[pos]
            pos += 1
            if label == 0xF9:
                gce = {"delay": int.from_bytes(blocks[0][1:3], "little"), "transparent": bool(blocks[0][0] & 1), "disposal": (blocks[0][0] >> 2) & 7}
            elif label == 0xFF and blocks[0] == b"NETSCAPE2.0":
                loop = int.from_bytes(blocks[1][1:3], "little")
            elif label == 0xFE:
                comment = b"".join(blocks)
            continue
        assert kind == 0x2C, hex(kind)
        x, y, w, h = (int.from_bytes(data[pos + 1 + 2 * i:pos + 3 + 2 * i], "little") for i in range(4))
        assert x + w <= width and y + h <= height and data[pos + 9] & 0xC0 == 0
        pos += 11
        while data[pos]:
            pos += 1 + data[pos]
        pos += 1
        frames.append({"rect": (x, y, w, h), **(gce or {})})
        gce = None
    return {"size": (width, height), "loop": loop, "comment": comment.decode("latin1"), "frames": frames}


@pytest.mark.browser
@pytest.mark.parametrize("browser_name", ["chromium", "firefox", "webkit"])
def test_records_frames_and_receipt(tmp_path, browser_name):
    pytest.importorskip("web_ui")
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<meta name=generator content='web_ui " + "a" * 40 + "'><h1>ok</h1><button id=b onclick=\"this.textContent='done'\">go</button><select id=s><option>a<option>b</select><img src='https://example.invalid/x.png'><link rel=stylesheet href='https://cdn.example.test/pin/site.css'>"
                                     "<div id=bar style='width:10px;height:16px;background:#c62828'></div><script>let w=10;setInterval(()=>{w=(w+23)%300;bar.style.width=w+'px'},60)</script>", encoding="utf-8")
    (site / "broken.html").write_text("<script>throw new Error('boom')</script>", encoding="utf-8")
    cdn = tmp_path / "cdn"
    cdn.mkdir()
    (cdn / "site.css").write_text("h1{color:rgb(1,2,3)}", encoding="utf-8")
    out = tmp_path / "evidence" / "browser.gif"
    code = eg.main([str(out), "--browser", browser_name, "--serve", str(site), "--sha", "0" * 40, "--map", f"https://cdn.example.test/pin={cdn}",
                    "--shot", "🏠 Top|/index.html|392x848",
                    "--plan", str(_plan(tmp_path, [{"label": "🖱️ click", "url": "/index.html", "steps": [
                        {"click": "#b"}, {"select": ["#s", "b"]}, {"record": {"every": 100, "for": 600, "clip": "#bar"}},
                        {"zoom": {"selector": "#b", "steps": 4, "ms": 400, "hold": 600}}]},
                        {"label": "💥", "url": "/broken.html", "steps": [{"wait": 100}]}, {"label": "🚫", "url": "/missing.html"}]))])
    receipt = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert code == 1 and receipt["summary"] == {"pass": 2, "fail": 2} and receipt["schema"] == "btk-evidence-gif/2"
    assert [s["status"] for s in receipt["shots"]] == ["pass", "pass", "fail", "fail"] and receipt["browser"].startswith(browser_name)
    assert any("boom" in e for e in receipt["shots"][2]["errors"]) and "http 404" in receipt["shots"][3]["errors"]
    steps = receipt["shots"][1]["steps"]
    assert steps[:2] == ["click #b", "select #s → b"] and steps[2].startswith("⏺ #bar 100 ms ×") and steps[3] == "🔍 #b"
    assert receipt["shots"][0]["external"] == ["https://example.invalid/x.png"]       # refused, listed, not a failure
    assert receipt["shots"][0]["generator"] == "web_ui " + "a" * 40 and receipt["shots"][2]["generator"] is None
    assert receipt["mapped"] == ["https://cdn.example.test/pin/"] and receipt["commit"] == "0" * 40
    x, e = receipt["transfer"], receipt["encoder"]
    assert x["patches"] >= 3 and x["identical"] >= 1                                  # the clip and the quiet wait cost no full screenshot
    assert x["gif_bytes"] == receipt["bytes"] == out.stat().st_size and x["calls"] >= 4
    assert x["sent_bytes"] < x["png_bytes"] * 4 / 3 + 4 * x["calls"]                  # each screenshot went over at most once
    assert e["synthesized"] == 3 and e["colors"] <= 255 and not e["overBudget"]
    gif = gif_blocks(out.read_bytes())                                                 # the GIF, read without the encoder
    assert gif["size"] == (720, 512) and gif["loop"] == 0 and gif["comment"] == "BrowserTestKit evidence_gif " + "0" * 40
    assert len(gif["frames"]) == receipt["frames"] and gif["frames"][0]["rect"] == (0, 0, 720, 512)
    assert all(f["delay"] >= 2 and f["disposal"] == 1 for f in gif["frames"]) and any(f["transparent"] for f in gif["frames"])
    assert gif["frames"][-1]["delay"] >= 200                                           # the last frame holds before the loop
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:                                                      # the same engine opens it
        browser = getattr(pw, browser_name).launch()
        page = browser.new_page()
        size = page.evaluate("async s => { const i = new Image(); i.src = s; await i.decode(); return [i.naturalWidth, i.naturalHeight]; }",
                             "data:image/gif;base64," + __import__("base64").b64encode(out.read_bytes()).decode())
        browser.close()
    assert size == [720, 512]
    try:
        from PIL import Image                                                          # and Pillow, where it is installed
    except ImportError:
        pass
    else:
        assert Image.open(out).n_frames == receipt["frames"]
    assert receipt["plan"][0]["url"] == "/index.html" and len(receipt["plan"]) == 4
    small = tmp_path / "small.gif"                       # the receipt is the recipe; a budget trades colours, then ⏺ frames
    assert eg.main([str(small), "--browser", browser_name, "--serve", str(site), "--plan", str(out.with_suffix(".json")), "--max-bytes", "20K"]) == 1
    tight = json.loads(small.with_suffix(".json").read_text(encoding="utf-8"))
    assert tight["summary"] == receipt["summary"] and tight["limits"]["max_bytes"] == 20480
    assert tight["encoder"]["colors"] < e["colors"] and (tight["bytes"] <= 20480 or tight["encoder"]["overBudget"])


def test_step_and_budget_checks():
    assert eg.check_step({"record": {"every": 50}}) == {"record": {"every": 50, "for": 2000, "clip": None}}
    assert eg.check_step({"zoom": "#x"})["zoom"] == {"selector": "#x", "ms": 900, "steps": 6, "hold": 1200, "pad": 16, "scale": 2.0}
    for bad in ({"record": {"every": 10}}, {"record": {"for": 0}}, {"record": {"fps": 3}}, {"zoom": {"ms": 1}}, {"zoom": {"selector": "#x", "steps": 99}}):
        with pytest.raises(ValueError):
            eg.check_step(bad)
    assert [eg.parse_bytes(v) for v in ("800K", "2M", "1000", None, "0")] == [819200, 2097152, 1000, None, None]
    with pytest.raises(ValueError):
        eg.parse_bytes("lots")
    shots = eg.check_plan([{"url": "/a", "steps": [{"click": "#a"}, {"zoom": "#a"}, {"record": {}}]}], hold=900)
    assert shots[0]["hold"] == 900 and eg.static_frames(shots) == 1 + 1 + 6 + 1


def _plan(tmp_path, shots):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(shots), encoding="utf-8")
    return path
