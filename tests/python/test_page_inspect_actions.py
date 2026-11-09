"""Stage 1-2 browser actions: Playwright methods, redacted values, explicit unsupported steps."""
import functools, json, os, platform, subprocess, sys, threading, time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer; from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import btk as page_inspect  # noqa: E402

SCRIPT = ROOT / "scripts" / "btk.py"
BROWSERS = ("chromium", "firefox", "webkit")

PAGE = """<!doctype html>
<meta charset="utf-8">
<title>actions</title>
<style>#below { margin-top: 2400px; height: 40px; }</style>
<button id="marker" type="button">idle</button>
<button id="echo" type="button">echo</button>
<input id="search" type="search" aria-label="Search">
<button id="results" type="button" hidden>results-ready</button>
<button id="result-1" type="button">Result one</button>
<input id="remember" type="checkbox" aria-label="Remember">
<select id="color" aria-label="Color">
  <option value="red">Red</option>
  <option value="token">btk-choice-token</option>
</select>
<form id="form">
  <input id="name" aria-label="Name">
  <button type="submit">Go</button>
</form>
<input id="file" type="file" aria-label="Upload">
<div id="source" draggable="true">Source</div>
<div id="drop">Drop</div>
<button id="alert" type="button">Alert</button>
<button id="alert-value" type="button">Alert value</button>
<button id="ask" type="button">Ask</button>
<a id="popup" target="_blank" href="popup.html">Popup</a>
<button id="touch" type="button">Touch</button>
<a id="away" href="other.html">Away</a>
<div id="below">Below</div>
<script>
  const marker = document.getElementById("marker");
  const echo = document.getElementById("echo");
  const search = document.getElementById("search");
  const name = document.getElementById("name");
  search.addEventListener("focus", () => { marker.textContent = "focused"; });
  search.addEventListener("input", () => { echo.textContent = search.value || "echo"; });
  search.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      document.getElementById("results").hidden = false;
      marker.textContent = search.value ? "searched" : "empty-search";
    }
  });
  document.getElementById("alert-value").addEventListener("click", () => alert(search.value));
  document.getElementById("result-1").addEventListener("click", () => { marker.textContent = "clicked-result"; });
  document.getElementById("result-1").addEventListener("dblclick", () => { marker.textContent = "doubled"; });
  document.getElementById("result-1").addEventListener("contextmenu", (event) => {
    event.preventDefault();
    marker.textContent = "right";
  });
  document.getElementById("result-1").addEventListener("mouseover", () => { marker.textContent = "hovered"; });
  document.getElementById("remember").addEventListener("change", () => { marker.textContent = "checked"; });
  document.getElementById("color").addEventListener("change", () => {
    marker.textContent = "selected";
    const chosen = document.getElementById("color");
    echo.textContent = chosen.options[chosen.selectedIndex].text;
  });
  name.addEventListener("input", () => {
    marker.textContent = name.value ? "name-filled" : "name-empty";
    echo.textContent = name.value || "echo";
  });
  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && (event.key === "k" || event.key === "K")) {
      event.preventDefault();
      marker.textContent = event.metaKey ? "shortcut-meta" : "shortcut-control";
    }
  });
  const drop = document.getElementById("drop");
  drop.addEventListener("dragover", (event) => event.preventDefault());
  drop.addEventListener("drop", (event) => { event.preventDefault(); marker.textContent = "dropped"; });
  document.getElementById("source").addEventListener("dragstart", (event) => {
    event.dataTransfer.setData("text/plain", "source");
  });
  search.addEventListener("blur", () => { marker.textContent = "blurred"; });
  document.getElementById("form").addEventListener("submit", (event) => {
    event.preventDefault();
    marker.textContent = "submitted";
  });
  document.getElementById("file").addEventListener("change", () => {
    marker.textContent = document.getElementById("file").files.length ? "one-file" : "no-file";
  });
  document.getElementById("alert").addEventListener("click", () => alert("dialog-notice"));
  document.getElementById("ask").addEventListener("click", () => {
    marker.textContent = confirm("leave?") ? "accepted" : "dismissed";
  });
  document.getElementById("touch").addEventListener("pointerdown", (event) => {
    if (event.pointerType === "touch") marker.textContent = "touched";
  });
</script>
"""
OTHER = "<!doctype html><meta charset=utf-8><title>other</title><button id=other type=button>Other page</button>"
POPUP = "<!doctype html><meta charset=utf-8><title>popup</title><button id=pop type=button>Popup page</button>"


def names_of(snapshot):
    return {element["id"]: element["name"] for element in snapshot["elements"] if element.get("id")}


def test_primary_is_playwright_control_or_meta_and_not_an_os_matrix():
    assert page_inspect.resolve_key("Primary+k") == "ControlOrMeta+k"
    assert page_inspect.resolve_key("Ctrl+Shift+K") == "Control+Shift+K"
    assert page_inspect.resolve_key("Command+k") == "Meta+k"
    source = Path(page_inspect.resolve_key.__code__.co_filename).read_text(encoding="utf-8")
    body = source.split("def resolve_key", 1)[1].split("\ndef ", 1)[0]
    assert "Darwin" not in body
    assert "ControlOrMeta" in body
    for name in ("long-press", "swipe", "touch-scroll"):
        support, reason = page_inspect.action_support(name, has_touch=True)
        assert support == {"chromium": "supported", "firefox": "unsupported", "webkit": "unsupported"}
        assert "dispatchTapEvent" in reason
        assert "mouse" in reason
        denied_touch, denied_reason = page_inspect.action_support(name, has_touch=False)
        assert denied_touch == {browser: "unsupported" for browser in BROWSERS}
        assert "hasTouch" in denied_reason
        assert "mouse" in denied_reason
    held = page_inspect.parse_step("long-press:id:touch=700")
    assert held["duration_ms"] == 700
    assert page_inspect.parse_step("long-press:id:touch")["duration_ms"] == 700
    assert page_inspect.parse_step("swipe:id:source")["dx"] == 160
    assert page_inspect.parse_step("touch-scroll:id:below=0,400")["dy"] == 400
    with pytest.raises(page_inspect.InspectError):
        page_inspect.parse_step("long-press:id:touch=100")
    with pytest.raises(page_inspect.InspectError):
        page_inspect.parse_step("swipe:id:source=0,0")
    denied, _reason = page_inspect.action_support("tap", has_touch=False)
    allowed, _reason = page_inspect.action_support("tap", has_touch=True)
    assert denied == {browser: "unsupported" for browser in BROWSERS}
    assert allowed == {browser: "supported" for browser in BROWSERS}


def test_phone_profiles_are_emulation_and_viewport_is_only_a_size():
    assert page_inspect.emulation_label("iphone") == {
        "emulation": True, "emulated_device": "iPhone 13", "real_device": False,
    }
    assert page_inspect.emulation_label("pixel")["emulated_device"] == "Pixel 7"
    assert page_inspect.emulation_label("pixel")["real_device"] is False
    assert page_inspect.emulation_label("desktop")["emulation"] is False
    devices = {"iPhone 13": {
        "is_mobile": True, "has_touch": True, "viewport": {"width": 390, "height": 844},
        "default_browser_type": "webkit",
    }}
    sized = page_inspect.context_options(
        devices, "chromium", "desktop", False, viewport={"width": 390, "height": 844},
    )
    assert sized["viewport"] == {"width": 390, "height": 844}
    assert sized.get("has_touch") is not True
    touched = page_inspect.context_options(devices, "chromium", "desktop", False, touch=True)
    assert touched["has_touch"] is True and "is_mobile" not in touched
    assert page_inspect.viewport_source_for("desktop", False, {"width": 390, "height": 844}) == "flag"
    assert page_inspect.viewport_source_for("iphone", False, None) == "device_descriptor"


def test_steps_reject_ordinals_and_keep_values_out_of_the_record(monkeypatch, tmp_path):
    with pytest.raises(page_inspect.InspectError) as raised:
        page_inspect.parse_step("click:h:3")
    assert "ordinal" in raised.value.error
    with pytest.raises(page_inspect.InspectError):
        page_inspect.parse_step("dance:id:save")
    monkeypatch.delenv("BTK_PAGE_SECRET_MISSING", raising=False)
    with pytest.raises(page_inspect.InspectError) as missing:
        page_inspect.parse_step("type:id:search=env:BTK_PAGE_SECRET_MISSING")
    assert "not set" in missing.value.error
    secret = "btk-type-secret"
    monkeypatch.setenv("BTK_PAGE_SECRET", secret)
    step = page_inspect.parse_step("type:id:search=env:BTK_PAGE_SECRET")
    published = page_inspect.publish_step(
        step,
        {"status": "done", "page_js_called": True, "support": {"chromium": "supported"}, "input": "keyboard"},
        url_before="http://127.0.0.1:9/", url_after="http://127.0.0.1:9/", index=1,
    )
    raw = json.dumps(published)
    assert secret not in raw
    assert published["value_recorded"] is False
    assert published["value_source"] == "env"
    assert published["value_name"] == "BTK_PAGE_SECRET"
    assert published["value_fingerprint"] == page_inspect.storage_fingerprint(secret)["sha256"]
    assert "key" not in published
    shortcut = page_inspect.parse_step("shortcut:Primary+k")
    shown = page_inspect.publish_step(
        shortcut,
        {"status": "done", "page_js_called": False, "support": {}, "input": "keyboard", "key_resolved": "ControlOrMeta+k"},
        url_before="http://127.0.0.1:9/", url_after="http://127.0.0.1:9/", index=1,
    )
    assert shown["key"] == "ControlOrMeta+k" and shown["primary"] == "ControlOrMeta"
    assert shown["primary_owner"] == "playwright"
    path = tmp_path / "value.txt"
    path.write_text("btk-file-secret", encoding="utf-8")
    filled = page_inspect.parse_step(f"fill:id:name=file:{path}")
    assert filled["secret"] == "btk-file-secret" and filled["value_source"] == "file"
    assert "btk-file-secret" not in json.dumps({key: value for key, value in filled.items() if key != "secret"})


def test_redaction_covers_accessibility_text_and_encoded_urls():
    secret = "hello world"
    with page_inspect.bound_secrets([secret]):
        payload = {
            "url": "http://127.0.0.1:9/q?q=hello+world",
            "elements": [{
                "name": "Result hello world",
                "href": "http://127.0.0.1:9/" + "hello%20world",
            }],
            "dialogs": [{"message": "alert hello world"}],
        }
        cleaned = page_inspect.redact_obj(payload)
        clipped = page_inspect.clip("PREFIX-" + secret + "-" + ("x" * 200), 80)
    raw = json.dumps(cleaned)
    assert secret not in raw
    assert "hello%20world" not in raw
    assert "hello+world" not in raw
    assert cleaned["elements"][0]["name"] == "Result [redacted]"
    assert cleaned["dialogs"][0]["message"] == "alert [redacted]"
    assert secret not in clipped
    assert "[redacted]" in clipped


def test_known_secrets_are_removed_in_full_before_stagehand_key_patterns():
    secret = "sk-abcdefSECRETVALUE"
    with page_inspect.bound_secrets([secret]):
        cleaned = page_inspect.redact_text(f"typed {secret} stays gone")
    assert secret not in cleaned
    assert cleaned == "typed [redacted] stays gone"
    leaked = page_inspect.redact_text(
        "boom ?api_key=supersecret&ok=1 sk-abcdefMOREVALUE Bearer abcdefghijkl "
        "bb_live_abcdREST AIza" + ("A" * 30)
    )
    assert "supersecret" not in leaked
    assert "api_key=[redacted]" in leaked
    assert "sk-abcdef[redacted]" in leaked
    assert "Bearer [redacted]" in leaked
    assert "bb_live_abcd[redacted]" in leaked
    assert "AIza[redacted]" in leaked
    assert "MOREVALUE" not in leaked
    envelope = page_inspect.error_envelope(
        "observe",
        page_inspect.InspectError("failed sk-abcdefLEFTOVERKEY", "hint Bearer abcdefghijkl"),
    )
    raw = json.dumps(envelope)
    assert "LEFTOVERKEY" not in raw
    assert "abcdefghijkl" not in raw


def test_frame_rows_keep_name_url_origin_in_document_order_without_ids():
    tree = {"childFrames": [
        {"frame": {"id": "1", "name": "outer", "url": "http://user:pw@127.0.0.1:9/outer", "securityOrigin": "http://127.0.0.1:9"},
         "childFrames": [
             {"frame": {"id": "2", "name": "inner", "url": "http://127.0.0.1:9/inner", "securityOrigin": "http://127.0.0.1:9"}},
         ]},
        {"frame": {"id": "3", "name": "sibling", "url": "http://127.0.0.1:9/sibling", "securityOrigin": "http://127.0.0.1:9"}},
    ]}
    rows, truncated = page_inspect.frame_rows(tree)
    assert truncated is False
    assert rows == [
        {"name": "outer", "url": "http://127.0.0.1:9/outer", "origin": "http://127.0.0.1:9"},
        {"name": "inner", "url": "http://127.0.0.1:9/inner", "origin": "http://127.0.0.1:9"},
        {"name": "sibling", "url": "http://127.0.0.1:9/sibling", "origin": "http://127.0.0.1:9"},
    ]
    assert "id" not in json.dumps(rows)
    assert "user:pw" not in json.dumps(rows)


def test_settle_is_chromium_only_and_step_files_are_observe_slices():
    support, reason = page_inspect.action_support("settle", has_touch=False)
    assert support == {"chromium": "supported", "firefox": "unsupported", "webkit": "unsupported"}
    assert "CDP" in reason and "page script" in reason
    assert page_inspect.parse_step("settle")["action"] == "settle"
    with pytest.raises(page_inspect.InspectError):
        page_inspect.parse_step("settle:id:x")
    published = page_inspect.publish_step(
        page_inspect.parse_step("settle"),
        {"status": "done", "page_js_called": False, "support": support, "input": "wait", "quiet": False},
        url_before="http://127.0.0.1:9/", url_after="http://127.0.0.1:9/", index=1,
    )
    assert published["quiet"] is False and published["status"] == "done"
    names = [
        page_inspect.observation_file_name(0, {"schema": "btk-page-observe/1"}),
        page_inspect.observation_file_name(1, {"schema": "btk-page-observe/1", "step": {"action": "settle"}}),
        page_inspect.observation_file_name(2, {"schema": "btk-page-observe/1", "step": {"action": "click"}}),
    ]
    assert names == ["00-initial.json", "01-settle.json", "02-click.json"]
    source = Path(page_inspect.__file__).read_text(encoding="utf-8")
    assert ".evaluate(" not in source
    # Whitespace steps retain their older envelope; colon steps use SCHEMA_RUN.
    assert page_inspect.SCHEMA_RUN == "btk-page-observe-run/1"
    assert "page.keyboard.press(resolved)" in source
    assert "keyboard.press(resolved, timeout" not in source


def test_bad_steps_fail_before_a_browser_launch(tmp_path):
    env = os.environ.copy()
    env.pop("BTK_PAGE_SECRET_MISSING", None)

    def run(args):
        return subprocess.run(
            [sys.executable, "-S", str(SCRIPT), "observe", "http://127.0.0.1:9/missing", *args, "--json"],
            cwd=tmp_path, capture_output=True, text=True, timeout=30, env=env,
        )

    missing = run(["--step", "type:id:search=env:BTK_PAGE_SECRET_MISSING"])
    assert missing.returncode == 2
    body = json.loads(missing.stdout)
    assert body["ok"] is False and "not set" in body["error"]
    ordinal = run(["--step", "click:h:1"])
    assert ordinal.returncode == 2 and "ordinal" in json.loads(ordinal.stdout)["error"]
    both = run(["--step", "click:id:save", "--after-click", "id:save", "--after-out", str(tmp_path / "b.json"), "--out", str(tmp_path / "a.json")])
    assert both.returncode == 2 and "--step" in json.loads(both.stdout)["error"]
    out_dir = tmp_path / "slices"
    out_dir.mkdir()
    bare = run(["--out-dir", str(out_dir)])
    assert bare.returncode == 2 and "out-dir" in json.loads(bare.stdout)["error"]


def _serve(directory: Path):
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(directory))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _run(args, env=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT, capture_output=True, text=True, timeout=180, env=env,
    )


@pytest.mark.browser
def test_steps_record_observations_and_redact_values(tmp_path):
    (tmp_path / "index.html").write_text(PAGE, encoding="utf-8")
    (tmp_path / "other.html").write_text(OTHER, encoding="utf-8")
    (tmp_path / "popup.html").write_text(POPUP, encoding="utf-8")
    secret_path = tmp_path / "secret.txt"
    secret_path.write_text("btk-file-secret", encoding="utf-8")
    upload = tmp_path / "btk-upload-token.txt"
    upload.write_bytes(b"upload")
    server = _serve(tmp_path)
    port = server.server_address[1]
    base = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env["BTK_PAGE_SECRET"] = "btk-type-secret"
    out = tmp_path / "obs.json"
    shortcut = "shortcut-meta" if platform.system() == "Darwin" else "shortcut-control"
    steps = [
        "focus:id:search",
        "type:id:search=env:BTK_PAGE_SECRET",
        "press:Enter",
        "wait:id:results",
        "click:id:alert-value",
        "click:id:result-1",
        "check:id:remember",
        "select:id:color=btk-choice-token",
        f"fill:id:name=file:{secret_path}",
        "clear:id:name",
        "shortcut:Primary+k",
        "hover:id:result-1",
        "dblclick:id:result-1",
        "rightclick:id:result-1",
        "drag:id:source=id:drop",
        "scroll-into-view:id:below",
        "focus:id:search",
        "blur:id:search",
        "submit:id:form",
        f"set-files:id:file=file:{upload}",
        "click:id:alert",
        "click:id:ask",
        "click:id:popup",
        "resize:500x400",
        f"goto:{base}/other.html",
        "back",
        "forward",
        "reload",
        "wait:timeout:20",
        "tap:id:touch",
        "long-press:id:touch",
        "swipe:id:source",
        "touch-scroll:id:below",
    ]
    command = ["observe", f"{base}/index.html", "--viewport", "390x844", "--json", "--out", str(out)]
    for step in steps:
        command.extend(["--step", step])
    try:
        done = _run(command, env=env)
    finally:
        server.shutdown()
    assert done.returncode == 0, done.stderr + done.stdout[:2000]
    raw = out.read_text(encoding="utf-8")
    for token in ("btk-type-secret", "btk-file-secret", "btk-choice-token", "btk-upload-token"):
        assert token not in raw
    assert "btk-type-secret" not in done.stdout
    payload = json.loads(raw)
    assert payload["schema"] == "btk-page-observe-run/1"
    assert payload["ok"] is True
    assert payload["dialog_policy"] == "dismiss"
    assert len(payload["observations"]) == len(steps) + 1
    assert len(payload["comparisons"]) == len(steps)
    assert payload["comparisons"][0]["schema"] == "btk-page-compare/1"
    first = payload["observations"][0]
    assert first["environment"]["viewport"] == {"width": 390, "height": 844}
    assert first["environment"]["viewport_source"] == "flag"
    assert first["environment"]["emulation"] is False
    assert first["environment"]["real_device"] is False
    assert first["environment"]["has_touch"] is False
    assert "step" not in first
    by_action = {step["action"]: (index, step) for index, step in enumerate(payload["steps"], start=1)}

    def marker_after(action):
        index, _step = by_action[action]
        return names_of(payload["observations"][index]).get("marker")

    assert marker_after("focus") == "focused"
    _index, typed = by_action["type"]
    assert typed["value_source"] == "env" and typed["value_recorded"] is False
    assert typed["value_fingerprint"] == page_inspect.storage_fingerprint("btk-type-secret")["sha256"]
    assert names_of(payload["observations"][by_action["type"][0]]).get("echo") == "[redacted]"
    assert marker_after("press") == "searched"
    results_wait = next(
        index for index, step in enumerate(payload["steps"], start=1)
        if step["action"] == "wait" and step.get("locator") == "id:results"
    )
    assert names_of(payload["observations"][results_wait]).get("results") == "results-ready"
    def observation_after(*, action, locator=None):
        for index, step in enumerate(payload["steps"], start=1):
            if step["action"] != action:
                continue
            if locator is not None and step.get("locator") != locator:
                continue
            return payload["observations"][index]
        raise AssertionError(f"missing {action} {locator or ''}")

    alert_obs = observation_after(action="click", locator="id:alert-value")
    dialogs = alert_obs["sections"]["dialogs"]["items"]
    assert dialogs and dialogs[0]["type"] == "alert" and dialogs[0]["action"] == "dismiss"
    assert dialogs[0]["message"] == "[redacted]"
    assert marker_after("check") == "checked"
    assert marker_after("select") == "selected"
    assert names_of(payload["observations"][by_action["select"][0]]).get("echo") == "[redacted]"
    assert marker_after("fill") == "name-filled"
    assert marker_after("clear") == "name-empty"
    assert marker_after("shortcut") == shortcut
    assert by_action["shortcut"][1]["key"] == "ControlOrMeta+k"
    assert by_action["shortcut"][1]["primary_owner"] == "playwright"
    assert marker_after("hover") == "hovered"
    assert marker_after("dblclick") == "doubled"
    assert marker_after("rightclick") == "right"
    assert marker_after("drag") == "dropped"
    _index, scrolled = by_action["scroll-into-view"]
    assert scrolled["status"] == "done"
    assert scrolled["support"]["chromium"] == "supported"
    assert scrolled["page_js_called"] is True
    assert marker_after("blur") == "blurred"
    assert marker_after("submit") == "submitted"
    assert marker_after("set-files") == "one-file"
    notice = observation_after(action="click", locator="id:alert")
    assert notice["sections"]["dialogs"]["items"][0]["message"] == "dialog-notice"
    assert notice["sections"]["dialogs"]["items"][0]["action"] == "dismiss"
    ask = observation_after(action="click", locator="id:ask")
    assert names_of(ask).get("marker") == "dismissed"
    popup = observation_after(action="click", locator="id:popup")
    opened = popup["sections"]["popups"]
    assert opened["opened"] == 1 and opened["contents"] == "not_entered"
    assert opened["items"][0]["url"].endswith("/popup.html")
    assert opened["items"][0]["contents"] == "not_entered"
    resized = observation_after(action="resize")
    assert resized["environment"]["viewport"] == {"width": 500, "height": 400}
    assert resized["environment"]["emulation"] is False
    assert observation_after(action="goto")["url"].endswith("/other.html")
    assert observation_after(action="back")["url"].endswith("/index.html")
    assert observation_after(action="forward")["url"].endswith("/other.html")
    assert observation_after(action="reload")["url"].endswith("/other.html")
    for action in ("tap", "long-press", "swipe", "touch-scroll"):
        _index, step = by_action[action]
        assert step["status"] == "unsupported"
        assert step["support"] == {browser: "unsupported" for browser in BROWSERS}
        assert step["page_js_called"] is False
    assert "touched" not in raw


@pytest.mark.browser
def test_touch_requires_a_touch_context_and_phones_are_emulation(tmp_path):
    (tmp_path / "index.html").write_text(PAGE, encoding="utf-8")
    server = _serve(tmp_path)
    url = f"http://127.0.0.1:{server.server_address[1]}/index.html"
    try:
        desktop = _run(["observe", url, "--touch", "--viewport", "390x844", "--step", "tap:id:touch", "--json"])
        phone = _run(["observe", url, "--profile", "iphone", "--browser", "chromium", "--step", "tap:id:touch", "--json"])
        accepted = _run([
            "observe", url, "--dialog-policy", "accept", "--step", "click:id:ask", "--json",
        ])
    finally:
        server.shutdown()
    assert desktop.returncode == 0, desktop.stderr
    touched = json.loads(desktop.stdout)
    first = touched["observations"][0]
    assert first["environment"]["has_touch"] is True
    assert first["environment"]["emulation"] is False
    assert first["environment"]["real_device"] is False
    assert first["environment"]["emulated_device"] is None
    assert touched["steps"][0]["status"] == "done"
    assert names_of(touched["observations"][1]).get("marker") == "touched"
    assert phone.returncode == 0, phone.stderr
    emulated = json.loads(phone.stdout)
    env = emulated["observations"][0]["environment"]
    assert env["emulation"] is True
    assert env["emulated_device"] == "iPhone 13"
    assert env["real_device"] is False
    assert env["has_touch"] is True
    assert "physical device" in emulated["observations"][0]["hint"]
    assert emulated["steps"][0]["status"] == "done"
    assert names_of(emulated["observations"][1]).get("marker") == "touched"
    assert accepted.returncode == 0, accepted.stderr
    agreed = json.loads(accepted.stdout)
    assert names_of(agreed["observations"][1]).get("marker") == "accepted"
    dialog = agreed["observations"][1]["sections"]["dialogs"]["items"][0]
    assert dialog["type"] == "confirm" and dialog["action"] == "accept"


GESTURE = """<!doctype html><meta charset=utf-8>
<style>body{height:4000px;margin:0;} #pad{position:absolute;left:20px;top:120px;width:300px;height:180px;}</style>
<button id=marker type=button>idle</button>
<button id=moved type=button>still</button>
<div id=pad>pad</div>
<script>
const marker = document.getElementById("marker");
let t0 = 0;
document.getElementById("pad").addEventListener("pointerdown", (event) => {
  t0 = performance.now();
  marker.textContent = "down-" + event.pointerType;
});
document.getElementById("pad").addEventListener("pointerup", (event) => {
  marker.textContent = "up-" + event.pointerType + "-" + Math.round(performance.now() - t0);
});
document.addEventListener("touchmove", (event) => {
  const touch = event.touches[0];
  if (!touch) return;
  marker.dataset.x = String(Math.round(touch.clientX));
}, {passive: true});
document.addEventListener("touchend", () => {
  if (marker.textContent.startsWith("down-")) marker.textContent = "swiped-" + (marker.dataset.x || "");
});
window.addEventListener("scroll", () => {
  document.getElementById("moved").textContent = "scrolled-" + Math.round(window.scrollY);
});
</script>
"""


@pytest.mark.browser
def test_chromium_touch_gestures_use_touch_input(tmp_path):
    (tmp_path / "index.html").write_text(GESTURE, encoding="utf-8")
    server = _serve(tmp_path)
    url = f"http://127.0.0.1:{server.server_address[1]}/index.html"
    try:
        done = _run([
            "observe", url, "--touch", "--viewport", "390x700",
            "--step", "long-press:id:pad=700",
            "--step", "swipe:id:pad=160,0",
            "--step", "touch-scroll:id:pad=0,400",
            "--json",
        ])
    finally:
        server.shutdown()
    assert done.returncode == 0, done.stderr + done.stdout[:2000]
    payload = json.loads(done.stdout)
    assert payload["ok"] is True
    assert payload["observations"][0]["environment"]["has_touch"] is True
    by_action = {step["action"]: step for step in payload["steps"]}
    held = by_action["long-press"]
    assert held["status"] == "done"
    assert held["duration_ms"] == 700
    assert held["page_js_called"] is True
    assert held["support"] == {"chromium": "supported", "firefox": "unsupported", "webkit": "unsupported"}
    assert "dispatchTapEvent" in held["unsupported_reason"]
    hold = names_of(payload["observations"][1]).get("marker", "")
    assert hold.startswith("up-touch-"), hold
    assert 600 <= int(hold.rsplit("-", 1)[1]) <= 1100
    swiped = by_action["swipe"]
    assert swiped["status"] == "done" and swiped["dx"] == 160 and swiped["input"] == "touch"
    swipe_name = names_of(payload["observations"][2]).get("marker", "")
    assert swipe_name.startswith("swiped-"), swipe_name
    assert int(swipe_name.split("-", 1)[1]) >= 280
    scrolled = by_action["touch-scroll"]
    assert scrolled["status"] == "done" and scrolled["dy"] == 400
    scroll_name = names_of(payload["observations"][3]).get("moved", "")
    scroll_y = payload["observations"][3]["limits"].get("scroll_y")
    assert scroll_name.startswith("scrolled-"), (scroll_name, scroll_y, scrolled)
    assert 300 <= scroll_y <= 480, (scroll_y, scroll_name)
    raw = done.stdout
    assert "mouse" in held["unsupported_reason"]


@pytest.mark.browser
@pytest.mark.parametrize("browser_name", ("firefox", "webkit"))
def test_other_engines_keep_gestures_unsupported_with_touch(tmp_path, browser_name):
    (tmp_path / "index.html").write_text(PAGE, encoding="utf-8")
    server = _serve(tmp_path)
    url = f"http://127.0.0.1:{server.server_address[1]}/index.html"
    try:
        done = _run([
            "observe", url, "--browser", browser_name, "--touch", "--viewport", "390x700",
            "--step", "tap:id:touch",
            "--step", "long-press:id:touch",
            "--step", "swipe:id:source",
            "--step", "touch-scroll:id:below",
            "--json",
        ])
    finally:
        server.shutdown()
    assert done.returncode == 0, done.stderr
    payload = json.loads(done.stdout)
    assert payload["steps"][0]["status"] == "done"
    assert payload["steps"][0]["support"][browser_name] == "supported"
    for step in payload["steps"][1:]:
        assert step["status"] == "unsupported"
        assert step["support"]["chromium"] == "supported"
        assert step["support"][browser_name] == "unsupported"
        assert "dispatchTapEvent" in step["reason"]
        assert "mouse" in step["reason"]
        assert step["page_js_called"] is False


@pytest.mark.browser
@pytest.mark.parametrize("browser_name", ("firefox", "webkit"))
def test_other_browsers_run_click_and_mark_swipe_unsupported(tmp_path, browser_name):
    (tmp_path / "index.html").write_text(PAGE, encoding="utf-8")
    server = _serve(tmp_path)
    url = f"http://127.0.0.1:{server.server_address[1]}/index.html"
    try:
        done = _run([
            "observe", url, "--browser", browser_name, "--step", "click:id:result-1",
            "--step", "swipe:id:source", "--json",
        ])
    finally:
        server.shutdown()
    assert done.returncode == 0, done.stderr
    payload = json.loads(done.stdout)
    assert payload["steps"][0]["status"] == "done"
    assert payload["steps"][0]["support"][browser_name] == "supported"
    swipe = payload["steps"][1]
    assert swipe["status"] == "unsupported"
    assert swipe["support"] == {browser: "unsupported" for browser in BROWSERS}
    snapshot = payload["observations"][1]
    assert snapshot["sections"]["dom"]["coverage"] == "unavailable"
    assert snapshot["elements"] == []
    assert snapshot["environment"]["real_device"] is False
    assert "Saved" not in done.stdout


FRAMES = """<!doctype html><meta charset=utf-8><title>frames</title>
<iframe name="outer" src="outer.html"></iframe>
"""
OUTER = """<!doctype html><meta charset=utf-8><title>outer</title>
<iframe name="inner" src="inner.html"></iframe>
"""
INNER = """<!doctype html><meta charset=utf-8><title>inner</title><p>nested-body</p>"""
QUIET = """<!doctype html><meta charset=utf-8><title>quiet</title><button id=stay type=button>still</button>"""
BUSY = """<!doctype html><meta charset=utf-8><title>busy</title><div id=log></div>
<script>setInterval(() => { const node = document.createElement("span"); node.textContent = "x"; document.getElementById("log").appendChild(node); }, 40);</script>
"""


@pytest.mark.browser
def test_chromium_lists_nested_frames_and_writes_step_slices(tmp_path):
    (tmp_path / "index.html").write_text(FRAMES, encoding="utf-8")
    (tmp_path / "outer.html").write_text(OUTER, encoding="utf-8")
    (tmp_path / "inner.html").write_text(INNER, encoding="utf-8")
    out_dir = tmp_path / "slices"
    out_dir.mkdir()
    out = tmp_path / "run.json"
    server = _serve(tmp_path)
    url = f"http://127.0.0.1:{server.server_address[1]}/index.html"
    try:
        done = _run([
            "observe", url, "--step", "wait:timeout:1", "--out", str(out), "--out-dir", str(out_dir), "--json",
        ])
    finally:
        server.shutdown()
    assert done.returncode == 0, done.stderr
    payload = json.loads(done.stdout)
    assert payload["schema"] == "btk-page-observe-run/1"
    assert "btk-page-observe-steps/1" not in done.stdout
    initial = payload["observations"][0]
    rows = initial["sections"]["frames"]["items"]
    assert [row["name"] for row in rows] == ["outer", "inner"]
    assert rows[0]["url"].endswith("/outer.html")
    assert rows[1]["url"].endswith("/inner.html")
    assert initial["sections"]["frames"]["contents"] == "not_entered"
    assert "nested-body" not in json.dumps(initial["elements"])
    assert all(set(row) == {"name", "url", "origin"} for row in rows)
    files = payload["files"]
    assert [Path(path).name for path in files] == ["00-initial.json", "01-wait.json"]
    for path, observation in zip(files, payload["observations"]):
        assert json.loads(Path(path).read_text(encoding="utf-8")) == observation
    disk = json.loads(out.read_text(encoding="utf-8"))
    assert disk["schema"] == "btk-page-observe-run/1"
    assert disk["files"] == files


@pytest.mark.browser
def test_chromium_settle_is_quiet_without_page_script_and_busy_pages_time_out(tmp_path):
    (tmp_path / "quiet.html").write_text(QUIET, encoding="utf-8")
    (tmp_path / "busy.html").write_text(BUSY, encoding="utf-8")
    server = _serve(tmp_path)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        started = time.monotonic()
        quiet = _run([
            "observe", f"{base}/quiet.html", "--timeout-ms", "8000", "--step", "settle", "--json",
        ])
        elapsed = time.monotonic() - started
        busy = _run([
            "observe", f"{base}/busy.html", "--timeout-ms", "900", "--step", "settle", "--json",
        ])
    finally:
        server.shutdown()
    assert quiet.returncode == 0, quiet.stderr
    quiet_payload = json.loads(quiet.stdout)
    step = quiet_payload["steps"][0]
    assert step["status"] == "done" and step["quiet"] is True
    assert step["page_js_called"] is False
    assert step["support"]["firefox"] == "unsupported"
    assert step["support"]["webkit"] == "unsupported"
    methods = quiet_payload["observations"][1]["limits"]["cdp_methods"]
    assert "Runtime.evaluate" not in methods
    assert "Page.setLifecycleEventsEnabled" in methods
    assert "DOM.getDocument" in methods
    assert quiet_payload["observations"][1]["limits"]["quiet"] is True
    assert elapsed < 6, elapsed
    assert busy.returncode == 0, busy.stderr
    busy_payload = json.loads(busy.stdout)
    busy_step = busy_payload["steps"][0]
    assert busy_step["status"] == "done" and busy_step["quiet"] is False
    assert busy_payload["ok"] is True
    assert ".evaluate(" not in (ROOT / "scripts" / "btk.py").read_text(encoding="utf-8")


@pytest.mark.browser
@pytest.mark.parametrize("browser_name", ("firefox", "webkit"))
def test_other_engines_report_settle_unsupported(tmp_path, browser_name):
    (tmp_path / "quiet.html").write_text(QUIET, encoding="utf-8")
    server = _serve(tmp_path)
    url = f"http://127.0.0.1:{server.server_address[1]}/quiet.html"
    try:
        done = _run(["observe", url, "--browser", browser_name, "--step", "settle", "--json"])
    finally:
        server.shutdown()
    assert done.returncode == 0, done.stderr
    payload = json.loads(done.stdout)
    step = payload["steps"][0]
    assert step["status"] == "unsupported"
    assert step["page_js_called"] is False
    assert "CDP" in step["reason"] and "page script" in step["reason"]
    assert "quiet" not in step
