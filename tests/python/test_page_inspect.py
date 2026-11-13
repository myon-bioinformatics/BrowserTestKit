"""page-inspect: offline operations, the CLI, browser actions, events, frames, shadow roots, settle, steps and URL privacy."""

import functools
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import btk
import btk as page_inspect


# ═══ · page inspect ══════════════════════════════════════════════════════════════════════════════════════
# Offline page-inspect operations: scores, addresses, pages, and the CLI.

spec = importlib.util.spec_from_file_location("btk", ROOT / "tools" / "btk.py")
btk_page_inspect = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(btk_page_inspect)


def element(name, **extra):
    raw = {
        "tag": "button", "role": "button", "name": name, "text": name, "id": "", "testid": "",
        "href": "", "visible": True, "enabled": True, "in_viewport": True, "obscured": False,
        "box": {"x": 1, "y": 2, "width": 30, "height": 16}, "parent_tag": "body", "css": "button",
        "id_unique": False, "testid_unique": False, "css_unique": False,
    }
    raw.update(extra)
    return raw


def snapshot_of(*items, total=None):
    raw = {"title": "Fixture", "child_frames": 0, "total": total if total is not None else len(items),
           "truncated": False, "elements": list(items)}
    return btk_page_inspect.assemble_observe(
        raw, environment={"os": "Linux", "browser": "chromium", "version": "0", "channel": None,
                          "headless": True, "profile": "desktop", "viewport": {"width": 1280, "height": 800}},
        url="http://127.0.0.1:9/fixture", start=0, limit=50,
        sections={"dom": {"source": "test", "coverage": "not_requested"}},
        limits={"page_js_called": False},
    )


def test_similarity_is_the_shorter_string_ratio():
    assert btk_page_inspect.lcs_length("save drft", "save draft") == 7
    assert btk_page_inspect.lcs_length("", "abc") == 0
    assert btk_page_inspect.lcs_length("abc", "xyz") == 0
    assert btk_page_inspect.similarity("Save", "save draft") == 1
    assert btk_page_inspect.similarity("save drft", "save draft") == btk_page_inspect.round6(7 / 9)
    assert btk_page_inspect.similarity("", "save") == 0
    assert btk_page_inspect.lcs_length("a\U0001f600b", "a\U0001f600b") == 4
    assert btk_page_inspect.similarity("\U0001f600", "x\U0001f600y") == 1


def test_addresses_prefer_stable_ids_and_number_duplicate_roles():
    survey = snapshot_of(
        element("Save draft", id="save-draft", testid="save-draft", id_unique=True, testid_unique=True, css="#save-draft", css_unique=True),
        element("Cancel"),
        element("Cancel"),
        element("Go", role="link", tag="a", href="https://example.test/docs"),
    )
    save, first, second, link = survey["elements"]
    assert save["address"] == "id:save-draft"
    assert save["locators"][0] == {"kind": "id", "selector": "#save-draft", "unique": True, "nth": None}
    assert first["address"] == "role:button/Cancel#0"
    assert second["address"] == "role:button/Cancel#1"
    assert link["address"].startswith("role:link/")
    assert "h:N" in survey["hint"]


def test_locate_pages_ranks_and_refuses_a_shared_top_score():
    elements = snapshot_of(
        element("Save draft", id="save-draft", id_unique=True, css="#save-draft", css_unique=True),
        element("Saved items", role="link", tag="a"),
        element("Search", role="searchbox", tag="input", id="q", id_unique=True, css="#q", css_unique=True),
    )["elements"]
    unique = btk_page_inspect.locate(elements, query="Save draft", unique=True)
    assert unique["ok"] is True and unique["items"][0]["address"] == "id:save-draft"
    shared = btk_page_inspect.locate(elements, query="Save", unique=True)
    assert shared["ok"] is False and shared["error"].startswith("more than one")
    assert {item["name"] for item in shared["suggestions"]} == {"Save draft", "Saved items"}
    missed = btk_page_inspect.locate(elements, query="Save drft")
    assert missed["ok"] is False and missed["suggestions"][0]["name"] == "Save draft"
    page = btk_page_inspect.locate(elements, query="", start=1, limit=1)
    assert page["pagination"] == {"start": 1, "limit": 1, "returned": 1, "total": 3, "next_start": 2}
    counted = btk_page_inspect.count_elements(elements, role="button")
    assert counted["total"] == 1 and counted["by_role"] == {"button": 1}


def test_describe_accepts_common_target_forms_and_rejects_memory_addresses():
    elements = snapshot_of(
        element("Save draft", id="save-draft", testid="save-draft", id_unique=True, testid_unique=True, css="#save-draft", css_unique=True),
        element("Cancel"),
        element("Cancel"),
    )["elements"]
    for target in ("id:save-draft", "#save-draft", "save-draft", 'role=button[name="Save draft"]',
                   'getByRole("button", { name: "Save draft" })', 'button "Save draft"', "h:0"):
        found = btk_page_inspect.describe_element(elements, target)
        assert found["ok"] is True and found["item"]["id"] == "save-draft", target
    second = btk_page_inspect.describe_element(elements, "role:button/Cancel#1")
    assert second["ok"] is True and second["item"]["handle"] == "h:2"
    ambiguous = btk_page_inspect.describe_element(elements, 'button "Cancel"')
    assert ambiguous["ok"] is False and len(ambiguous["suggestions"]) == 2
    with pytest.raises(btk_page_inspect.InspectError) as raised:
        btk_page_inspect.describe_element(elements, "0x401000")
    assert "memory addresses" in raised.value.error
    missing = btk_page_inspect.describe_element(elements, "zzzz")
    assert missing["ok"] is False and missing["suggestions"] == []
    spaced = snapshot_of(element("Save draft"), element("Go \U0001f600", role="link", tag="a"))["elements"]
    assert spaced[0]["address"] == "role:button/Save%20draft#0"
    emoji = btk_page_inspect.describe_element(spaced, spaced[1]["address"])
    assert emoji["ok"] is True and emoji["item"]["handle"] == "h:1"
    paged = btk_page_inspect.normalize_elements(
        {"elements": [element("One"), element("Two"), element("Three")], "total": 3}, start=1, limit=1,
    )
    assert paged["truncated"] is True and paged["elements"][0]["handle"] == "h:1"


def test_option_names_are_not_abbreviated_and_do_not_swallow_the_next_flag():
    parser = btk_page_inspect.build_parser()
    args = parser.parse_args(["observe", "http://127.0.0.1:9/page", "--browser", "firefox", "--json", "--limit", "2"])
    assert args.browser == "firefox" and args.json is True and args.limit == 2
    attached = parser.parse_args(["observe", "http://127.0.0.1:9/page", "--browser=webkit"])
    assert attached.browser == "webkit"
    with pytest.raises(SystemExit) as raised:
        parser.parse_args(["observe", "http://127.0.0.1:9/page", "--brow", "firefox"])
    assert raised.value.code == 2


def test_context_options_drop_is_mobile_only_for_firefox():
    devices = {"iPhone 13": {"is_mobile": True, "has_touch": True, "viewport": {"width": 390, "height": 844}, "default_browser_type": "webkit"}}
    firefox = btk_page_inspect.context_options(devices, "firefox", "iphone", False)
    webkit = btk_page_inspect.context_options(devices, "webkit", "iphone", False)
    assert "is_mobile" not in firefox and firefox["has_touch"] is True
    assert webkit["is_mobile"] is True and "default_browser_type" not in webkit
    desktop = btk_page_inspect.context_options(devices, "chromium", "desktop", False)
    assert desktop["viewport"] == {"width": 1280, "height": 800}


def test_cli_offline_commands_match_the_library(tmp_path: Path):
    survey = snapshot_of(
        element("Save draft", id="save-draft", id_unique=True, css="#save-draft", css_unique=True),
        element("Saved items", role="link", tag="a"),
    )
    path = tmp_path / "survey.json"
    path.write_text(btk_page_inspect.dumps(survey), encoding="utf-8")
    script = ROOT / "tools" / "btk.py"

    def run(args):
        return subprocess.run([sys.executable, "-S", str(script), *args], cwd=tmp_path, capture_output=True, text=True, timeout=30)

    listed = run(["operations"])
    assert listed.returncode == 0 and "locate:" in listed.stdout
    found = run(["locate", str(path), "--query", "Save draft", "--unique", "--json"])
    assert found.returncode == 0
    assert json.loads(found.stdout)["items"][0]["address"] == "id:save-draft"
    shared = run(["locate", str(path), "--query", "Save", "--unique"])
    assert shared.returncode == 1 and "suggest:" in shared.stderr and shared.stdout == ""
    described = run(["describe", str(path), "--target", "id:save-draft", "--json"])
    body = json.loads(described.stdout)
    assert described.returncode == 0 and body["resolved_as"] == "id" and body["context"][0].startswith("* ")
    memory = run(["describe", str(path), "--target", "0x10", "--json"])
    assert memory.returncode == 2 and json.loads(memory.stdout)["stage"] == "input"
    missing = run(["locate", str(tmp_path / "missing.json"), "--query", "Save"])
    assert missing.returncode == 2 and "observe --out" in missing.stderr
    help_text = run(["--help"])
    assert help_text.returncode == 0 and "usage" in help_text.stdout.lower()


def test_loopback_policy_and_evidence_do_not_treat_missing_as_same():
    assert btk_page_inspect.loopback_target("http://127.0.0.1:9/a") == "http://127.0.0.1:9/a"
    assert btk_page_inspect.loopback_target("http://[::1]:80/a") == "http://[::1]:80/a"
    assert btk_page_inspect.loopback_target("http://127.0.0.1:8081/") == "http://127.0.0.1:8081/"
    for url in (
        "http://localhost:9/",
        "http://127.0.0.1/a",
        "file:///tmp/page.html",
        "javascript:alert(1)",
        "https://example.test:9/",
        "http://user:secret" + chr(64) + "127.0.0.1:9/",
    ):
        with pytest.raises(btk_page_inspect.InspectError):
            btk_page_inspect.loopback_target(url)
    missing = {"coverage": "not_requested"}
    assert btk_page_inspect.evidence_relation(missing, missing) == "unknown"
    same = {"coverage": "complete", "digest": "abc"}
    assert btk_page_inspect.evidence_relation(same, dict(same)) == "same"
    assert btk_page_inspect.evidence_relation(same, {"coverage": "complete", "digest": "def"}) == "different"
    limited = {"coverage": "attach_limited", "digest": "abc"}
    assert btk_page_inspect.evidence_relation(limited, dict(limited)) == "unknown"
    finger = btk_page_inspect.storage_fingerprint("secret-value")
    assert finger["fingerprint_version"] == 2 and "secret-value" not in json.dumps(finger)
    # Passive observation and Chromium settle use CDP without page evaluation.
    source = (ROOT / "tools" / "btk.py").read_text(encoding="utf-8")
    assert ".evaluate(" not in source


def test_act_rejects_ordinals_before_launch():
    with pytest.raises(btk_page_inspect.InspectError) as raised:
        btk_page_inspect.act_page(
            url="http://127.0.0.1:9/", browser_name="chromium", profile="desktop", headed=False,
            channel=None, action="click", locator="h:0", text=None, timeout_ms=500,
        )
    assert "ordinal" in raised.value.error
    with pytest.raises(btk_page_inspect.InspectError):
        btk_page_inspect.act_page(
            url="http://127.0.0.1:9/", browser_name="chromium", profile="desktop", headed=False,
            channel=None, action="dance", locator="id:save", text=None, timeout_ms=500,
        )


def test_request_boundary_rejects_redirects_and_external_resources():
    from unittest.mock import Mock
    rejected = "http://localhost:9/"
    route = Mock()
    route.request.url = rejected
    btk_page_inspect.restrict_request(route)
    route.abort.assert_called_once_with("blockedbyclient")
    route.fetch.assert_not_called()
    for status in (200, 302):
        route = Mock()
        route.request.url = "http://127.0.0.1:9/"
        route.fetch.return_value.status = status
        btk_page_inspect.restrict_request(route)
        route.fetch.assert_called_once_with(max_redirects=0)
        if status == 302:
            route.abort.assert_called_once_with("blockedbyclient")
        else:
            route.fulfill.assert_called_once_with(response=route.fetch.return_value)


@pytest.mark.browser
def test_observe_cdp_never_sends_runtime_evaluate_with_after_action(tmp_path, monkeypatch):
    import functools
    import threading
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    from playwright.sync_api import BrowserContext

    (tmp_path / "page.html").write_text("<!doctype html><style>body{height:3000px}</style>", encoding="utf-8")
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    cdp_methods: list[str] = []
    original = BrowserContext.new_cdp_session

    def tracking_new_cdp_session(self, page):
        client = original(self, page)
        real_send = client.send

        def send(method, params=None, **kwargs):
            cdp_methods.append(method)
            if method in ("Runtime.evaluate", "Runtime.callFunctionOn"):
                raise AssertionError(f"forbidden CDP method: {method}")
            if kwargs:
                return real_send(method, params, **kwargs)
            return real_send(method, params or {})

        client.send = send
        return client

    monkeypatch.setattr(BrowserContext, "new_cdp_session", tracking_new_cdp_session)
    try:
        url = f"http://127.0.0.1:{server.server_port}/page.html"
        session = btk_page_inspect.observe_page(
            url=url,
            browser_name="chromium",
            profile="desktop",
            headed=False,
            channel=None,
            include_hidden=False,
            include_storage_keys=False,
            include_storage_fingerprints=False,
            role="",
            start=0,
            limit=50,
            steps=[btk_page_inspect.parse_step("scroll:0,800")],
        )
    finally:
        server.shutdown()
    results = session["observations"]
    assert len(results) == 2
    after = results[1]
    assert after["limits"]["page_js_called"] is False
    assert 650 <= after["limits"]["scroll_y"] <= 950
    assert "Runtime.evaluate" not in cdp_methods
    assert "Page.getLayoutMetrics" in cdp_methods


# ═══ · page inspect actions ══════════════════════════════════════════════════════════════════════════════
# Stage 1-2 browser actions: Playwright methods, redacted values, explicit unsupported steps.

SCRIPT = ROOT / "tools" / "btk.py"
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
    assert ".evaluate(" not in (ROOT / "tools" / "btk.py").read_text(encoding="utf-8")


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


# ═══ · page inspect browser ══════════════════════════════════════════════════════════════════════════════
# Real observe and act runs through the CLI on a loopback fixture.

FIXTURE = (ROOT / "fixtures" / "page_inspect.html").read_bytes()
RESULTS = ROOT / "test-results" / "page-inspect"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(FIXTURE)))
        self.end_headers()
        self.wfile.write(FIXTURE)

    def log_message(self, fmt, *args):
        return


def notice(capsys, title, text):
    if os.environ.get("GITHUB_ACTIONS"):
        with capsys.disabled():
            print(f"\n::notice title={title}::{text[:900]}", flush=True)


def run(args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=ROOT, capture_output=True, text=True, timeout=120)


def loopback():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/page"
    server.shutdown()


loopback = pytest.fixture(scope="module")(loopback)


def names(survey):
    return [element["name"] for element in survey["elements"]]


def test_chromium_observe_is_passive_and_fingerprints_storage(loopback, capsys):
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / "chromium-observe.json"
    done = run([
        "observe", loopback, "--browser", "chromium", "--json", "--out", str(out),
        "--include-storage-keys", "--include-storage-fingerprints",
    ])
    assert done.returncode == 0, done.stderr
    payload = json.loads(done.stdout)
    assert payload["schema"] == "btk-page-observe/1" and payload["ok"] is True
    assert payload["limits"]["page_js_called"] is False
    assert "Runtime.evaluate" not in payload["limits"]["cdp_methods"]
    # Only _wait_quiet (settle, after a driven page) may call page.evaluate; observe_page must not.
    import inspect
    import btk
    assert ".evaluate(" not in inspect.getsource(btk.observe_page)
    found = names(payload)
    for expected in ("Save draft", "Cancel", "Documentation", "Search", "Remember me", "Unavailable"):
        assert expected in found, found
    assert found.count("Cancel") == 2
    assert "Secret" not in found and "Child frame" not in found
    assert payload["child_frames"] >= 1
    frames = payload["sections"]["frames"]
    assert frames["coverage"] == "complete"
    assert frames["contents"] == "not_entered"
    assert frames["items"]
    for row in frames["items"]:
        assert set(row) == {"name", "url", "origin"}
        assert "Child frame" not in json.dumps(row)
    assert payload["sections"]["network"]["coverage"] == "attach_limited"
    save = next(element for element in payload["elements"] if element["name"] == "Save draft")
    assert save["address"] == "id:save-draft"
    unavailable = next(element for element in payload["elements"] if element["name"] == "Unavailable")
    assert unavailable["enabled"] is False
    raw = done.stdout
    assert "secret-value" not in raw
    areas = payload["sections"]["storage"]["areas"]
    local = next(area for area in areas if area["area"] == "local")
    assert local["items"][0]["key"] == "draft-token"
    assert "sha256" in local["items"][0] and "value" not in local["items"][0]
    hidden = run(["observe", loopback, "--browser", "chromium", "--include-hidden", "--json"])
    assert hidden.returncode == 0, hidden.stderr
    assert "Secret" in names(json.loads(hidden.stdout))
    notice(capsys, "page-observe chromium", f"elements={payload['total']} save={save['address']}")


def test_firefox_and_webkit_observe_do_not_invent_a_page(loopback, browser_name, capsys):
    done = run(["observe", loopback, "--browser", browser_name, "--profile", "iphone", "--json"])
    assert done.returncode == 0, done.stderr
    payload = json.loads(done.stdout)
    assert payload["limits"]["page_js_called"] is False
    assert payload["limits"]["reason"] == "cdp_session_unavailable"
    assert payload["sections"]["dom"]["coverage"] == "unavailable"
    assert payload["elements"] == []
    assert payload["environment"]["viewport"]["width"] == 390
    notice(capsys, f"page-observe {browser_name}", "cdp unavailable; no page call")


def test_act_requeries_and_records_the_click(loopback, capsys):
    clicked = run([
        "act", loopback, "--browser", "chromium", "--action", "click", "--locator", "id:save-draft", "--json",
    ])
    assert clicked.returncode == 0, clicked.stderr
    payload = json.loads(clicked.stdout)
    assert payload["limits"]["page_js_called"] is True
    assert payload["limits"]["bounded_to"] == ["click"]
    assert payload["sections"]["dom"]["coverage"] == "not_requested"
    assert "Saved" in payload["sections"]["accessibility"]["names"]
    ordinal = run(["act", loopback, "--action", "click", "--locator", "h:0", "--json"])
    assert ordinal.returncode == 2
    assert json.loads(ordinal.stdout)["ok"] is False
    notice(capsys, "page-act", "click re-queried id:save-draft")


# Firefox step split out of the Chromium click test: it used to run inside that test
# whenever BTK_BROWSER_ENGINES included firefox, so a host without Firefox failed the
# Chromium test. As a browser_name parameter, conftest's engine selection applies.
def test_act_wait_does_not_call_page_js(loopback, browser_name, capsys):
    waited = run(["act", loopback, "--browser", browser_name, "--action", "wait_for_timeout", "--timeout-ms", "50", "--json"])
    assert waited.returncode == 0, waited.stderr
    body = json.loads(waited.stdout)
    assert body["limits"]["page_js_called"] is False
    assert body["sections"]["accessibility"]["coverage"] == "not_requested"
    notice(capsys, f"page-act {browser_name}", "wait without page call")


test_firefox_and_webkit_observe_do_not_invent_a_page = pytest.mark.browser(
    pytest.mark.parametrize("browser_name", ("firefox", "webkit"))(test_firefox_and_webkit_observe_do_not_invent_a_page)
)
test_chromium_observe_is_passive_and_fingerprints_storage = pytest.mark.browser(
    test_chromium_observe_is_passive_and_fingerprints_storage
)
test_act_requeries_and_records_the_click = pytest.mark.browser(test_act_requeries_and_records_the_click)
test_act_wait_does_not_call_page_js = pytest.mark.browser(
    pytest.mark.parametrize("browser_name", ("firefox",))(test_act_wait_does_not_call_page_js)
)


# ═══ · page inspect compare ══════════════════════════════════════════════════════════════════════════════
# Offline compare of two observe snapshots: section relations and counts, never browser-driven.

ENV = {"os": "linux", "browser": "chromium", "profile": "desktop", "headless": True}


def snapshot(*, shadow=None, env=None, total=2, frames=0, extra=None):
    sections = {"child_frames": btk._section("cdp:Page.getFrameTree", "complete", {"count": frames})}
    if shadow is not None:
        sections["shadow_roots"] = btk._section(
            "cdp:DOM.getDocument pierce", shadow.get("coverage", "complete"),
            {"open": shadow["open"], "closed": shadow["closed"], "count": shadow["count"], "contents": "not_entered"},
        )
    if extra is not None:
        sections.update(extra)
    return {
        "schema": btk.SCHEMA_OBSERVE,
        "url": "http://127.0.0.1:8765/page",
        "environment": dict(ENV if env is None else env),
        "sections": sections,
        "child_frames": frames,
        "total": total,
        "elements": [],
    }


SHADOW = {"open": 3, "closed": 1, "count": 4}


def test_identical_snapshots_are_same_and_counts_have_zero_delta():
    payload = btk.compare_snapshots(snapshot(shadow=SHADOW), snapshot(shadow=SHADOW))
    assert payload["schema"] == btk.SCHEMA_COMPARE and payload["ok"] is True
    assert payload["sections"]["shadow_roots"]["relation"] == "same"
    assert payload["sections"]["child_frames"]["relation"] == "same"
    assert payload["shadow_roots"]["count"] == {"left": 4, "right": 4, "delta": 0}
    assert payload["same_environment"] is True


def test_changed_shadow_count_is_different_with_signed_delta():
    right = snapshot(shadow={"open": 4, "closed": 1, "count": 5})
    payload = btk.compare_snapshots(snapshot(shadow=SHADOW), right)
    assert payload["sections"]["shadow_roots"]["relation"] == "different"
    assert payload["shadow_roots"]["open"]["delta"] == 1
    assert payload["shadow_roots"]["count"] == {"left": 4, "right": 5, "delta": 1}


def test_missing_section_is_unknown_not_same_and_counts_are_null():
    payload = btk.compare_snapshots(snapshot(shadow=SHADOW), snapshot(shadow=None))
    assert payload["sections"]["shadow_roots"] == {"relation": "unknown", "left": "complete", "right": None}
    assert payload["shadow_roots"]["count"] == {"left": 4, "right": None, "delta": None}


def test_truncated_coverage_is_never_same_even_with_equal_digests():
    left = snapshot(shadow=SHADOW)
    right = snapshot(shadow=dict(SHADOW, coverage="truncated"))
    payload = btk.compare_snapshots(left, right)
    assert payload["sections"]["shadow_roots"]["relation"] == "unknown"
    assert payload["shadow_roots"]["count"]["delta"] is None


def test_environment_mismatch_is_reported_with_a_hint():
    other = dict(ENV, browser="firefox")
    payload = btk.compare_snapshots(snapshot(shadow=SHADOW), snapshot(shadow=SHADOW, env=other))
    assert payload["same_environment"] is False
    assert payload["environment"]["browser"] == ["chromium", "firefox"]
    assert "environments differ" in payload["hint"]


def test_element_and_frame_counts_report_deltas():
    payload = btk.compare_snapshots(snapshot(total=2, frames=0), snapshot(total=5, frames=1))
    assert payload["elements"] == {"left": 2, "right": 5, "delta": 3}
    assert payload["child_frames"] == {"left": 0, "right": 1, "delta": 1}


def test_compare_text_lists_relations_and_deltas():
    payload = btk.compare_snapshots(snapshot(shadow=SHADOW), snapshot(shadow={"open": 4, "closed": 1, "count": 5}))
    text = btk.compare_text(payload)
    assert "section shadow_roots=different left=complete right=complete" in text
    assert "shadow_roots count left=4 right=5 delta=1" in text
    assert text.endswith("\n")


def test_cli_compare_round_trip_and_input_errors(tmp_path):
    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    left.write_text(btk.dumps(snapshot(shadow=SHADOW)), encoding="utf-8")
    right.write_text(btk.dumps(snapshot(shadow=SHADOW)), encoding="utf-8")

    def run(args):
        return subprocess.run([sys.executable, "-S", str(SCRIPT), *args], cwd=tmp_path, capture_output=True, text=True, timeout=30)

    done = run(["compare", str(left), str(right), "--json"])
    body = json.loads(done.stdout)
    assert done.returncode == 0 and body["schema"] == "btk-page-compare/1"
    assert body["sections"]["shadow_roots"]["relation"] == "same"

    text = run(["compare", str(left), str(right)])
    assert text.returncode == 0 and text.stdout.startswith("btk-page-compare/1 same_environment=true")

    not_observe = tmp_path / "locate.json"
    not_observe.write_text(json.dumps({"schema": "btk-page-inspect/1", "elements": []}), encoding="utf-8")
    bad = run(["compare", str(left), str(not_observe), "--json"])
    assert bad.returncode == 2 and json.loads(bad.stdout)["ok"] is False

    missing = run(["compare", str(left), str(tmp_path / "missing.json")])
    assert missing.returncode == 2 and "observe --out" in missing.stderr


def test_operations_lists_compare():
    names = [item["name"] for item in btk.operations()["items"]]
    assert "compare" in names


# ═══ · page inspect events ═══════════════════════════════════════════════════════════════════════════════
# network and console come from Playwright events; user-agent shadow roots are not author roots.

PAGE_INSPECT_EVENTS_PAGE = """<!doctype html><meta charset="utf-8"><title>events fixture</title>
<img src="http://example.invalid/blocked.png" alt="">
<script>
  console.log('hello', 1);
  console.warn('careful');
  fetch('missing.json').catch(() => {});
</script>
"""


def test_user_agent_roots_are_reported_apart_from_author_roots():
    tree = {"nodeType": 9, "children": [
        {"nodeType": 1, "shadowRoots": [{"shadowRootType": "user-agent", "children": []}]},
        {"nodeType": 1, "shadowRoots": [{"shadowRootType": "open", "children": []}]},
    ]}
    counts = btk.count_shadow_roots(tree)
    assert counts["user_agent"] == 1
    assert (counts["open"], counts["closed"], counts["count"]) == (1, 0, 1)


@pytest.mark.browser
def test_network_and_console_come_from_playwright_events(tmp_path):
    (tmp_path / "index.html").write_text(PAGE_INSPECT_EVENTS_PAGE, encoding="utf-8")
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/index.html"
        done = subprocess.run([sys.executable, str(SCRIPT), "observe", url, "--json"],
                              capture_output=True, text=True, timeout=180)
    finally:
        server.shutdown()
    assert done.returncode == 0, done.stderr
    sections = json.loads(done.stdout)["sections"]
    network = sections["network"]
    assert network["source"] == "playwright:page.response"
    document = [item for item in network["items"] if item["url"].endswith("/index.html")]
    assert document and document[0]["status"] == 200 and document[0]["mime"] == "text/html"
    assert any(item["status"] == 404 and item["url"].endswith("/missing.json") for item in network["items"])
    failed = [item for item in network["items"] if item.get("failure")]
    assert failed and "example.invalid" in failed[0]["url"]
    console = sections["console"]
    assert console["source"] == "playwright:page.console"
    types = [(item["type"], item["argument_count"]) for item in console["items"]]
    assert ("log", 2) in types and ("warning", 1) in types
    assert all("argument_types" not in item for item in console["items"])


# ═══ · page inspect frames ═══════════════════════════════════════════════════════════════════════════════
# Child frames are listed by name, URL, and origin; their contents are never entered.

PAGE_INSPECT_FRAMES_PAGE = """<!doctype html><meta charset="utf-8"><title>frames fixture</title>
<iframe name="inner" srcdoc="&lt;button&gt;inside&lt;/button&gt;"></iframe>
<iframe name="outer" src="child.html"></iframe>
"""
CHILD = """<!doctype html><meta charset="utf-8"><title>child</title>
<iframe name="deep" srcdoc="&lt;a href='#'&gt;deep&lt;/a&gt;"></iframe>
"""


def test_frame_rows_are_document_order_without_userinfo_or_ids():
    tree = {"frame": {"url": "http://127.0.0.1/"}, "childFrames": [
        {"frame": {"name": "a", "url": "http://u:p@127.0.0.1/a?x=1", "securityOrigin": "http://127.0.0.1", "id": "X"},
         "childFrames": [{"frame": {"name": "deep", "url": "about:srcdoc"}}]},
        {"frame": {"name": "b", "url": "about:blank"}},
    ]}
    rows, truncated = btk.frame_rows(tree)
    assert truncated is False
    assert [row["name"] for row in rows] == ["a", "deep", "b"]
    assert rows[0] == {"name": "a", "url": "http://127.0.0.1/a", "origin": "http://127.0.0.1"}
    assert all("id" not in row for row in rows)


def test_frame_rows_report_truncation_instead_of_silently_dropping():
    tree = {"frame": {}, "childFrames": [{"frame": {"name": str(i)}} for i in range(4)]}
    rows, truncated = btk.frame_rows(tree, limit=2)
    assert len(rows) == 2 and truncated is True


@pytest.mark.browser
def test_observe_lists_child_frames_and_same_page_reads_same(tmp_path):
    (tmp_path / "index.html").write_text(PAGE_INSPECT_FRAMES_PAGE, encoding="utf-8")
    (tmp_path / "child.html").write_text(CHILD, encoding="utf-8")
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/index.html"
        for name in ("one.json", "two.json"):
            done = subprocess.run([sys.executable, str(SCRIPT), "observe", url, "--out", str(tmp_path / name), "--json"],
                                  cwd=tmp_path, capture_output=True, text=True, timeout=180)
            assert done.returncode == 0, done.stderr
    finally:
        server.shutdown()
    first = json.loads((tmp_path / "one.json").read_text(encoding="utf-8"))
    section = first["sections"]["frames"]
    assert section["coverage"] == "complete" and section["contents"] == "not_entered"
    assert section["child_frames"] == 3
    assert [item["name"] for item in section["items"]] == ["inner", "outer", "deep"]
    assert section["items"][1]["url"].endswith("/child.html")
    assert all("id" not in item for item in section["items"])

    compared = subprocess.run([sys.executable, "-S", str(SCRIPT), "compare", str(tmp_path / "one.json"),
                               str(tmp_path / "two.json"), "--json"], cwd=tmp_path,
                              capture_output=True, text=True, timeout=60)
    assert json.loads(compared.stdout)["sections"]["frames"]["relation"] == "same"


# ═══ · page inspect keys ═════════════════════════════════════════════════════════════════════════════════
# ControlOrMeta resolution, ported from Playwright's resolveSmartModifierString (offline).

def test_control_or_meta_is_control_off_macos_and_meta_on_macos():
    assert btk.resolve_smart_key("ControlOrMeta", "linux") == "Control"
    assert btk.resolve_smart_key("ControlOrMeta", "win32") == "Control"
    assert btk.resolve_smart_key("ControlOrMeta", "darwin") == "Meta"


def test_chords_resolve_each_part_and_leave_other_keys_alone():
    assert btk.resolve_smart_key("ControlOrMeta+A", "linux") == "Control+A"
    assert btk.resolve_smart_key("Shift+ControlOrMeta+Enter", "darwin") == "Shift+Meta+Enter"
    assert btk.resolve_smart_key("Enter", "linux") == "Enter"
    assert btk.resolve_smart_key("+", "linux") == "+"
    assert btk.resolve_smart_key("Control+A", "darwin") == "Control+A"


def test_default_platform_follows_sys_platform():
    expected = "Meta" if sys.platform == "darwin" else "Control"
    assert btk.resolve_smart_key("ControlOrMeta") == expected


def test_page_keyboard_press_does_not_take_a_timeout():
    source = (ROOT / "tools" / "btk.py").read_text(encoding="utf-8")
    assert "page.keyboard.press(text, timeout=" not in source
    assert "page.keyboard.press(resolved)" in source
    assert "target().press(resolved, timeout=timeout_ms)" in source


# ═══ · page inspect routes ═══════════════════════════════════════════════════════════════════════════════
# btk.py emoji routes: pages discovers same-origin pages, observe --trail and goto take emoji, and the trail grammar matches route_trail.py.

def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


btk_page_inspect_routes = _load("btk_under_test", SCRIPT)
route_trail = _load("route_trail", ROOT / "tools" / "route_trail.py")

INDEX = ('<!doctype html><meta charset="utf-8"><title>home</title>'
         '<a href="form.html">Form</a> <a href="result.html">Result</a> <a href="https://example.com/">away</a>\n')
FORM = '<!doctype html><meta charset="utf-8"><title>form</title><a id="go" href="result.html">Go</a>\n'
RESULT = '<!doctype html><meta charset="utf-8"><title>result</title><button id="back">Back</button>\n'


def run_btk(args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=ROOT,
                          capture_output=True, text=True, timeout=180)


def write_routes(tmp_path, data):
    path = tmp_path / "routes.json"
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return str(path)


def test_trail_grammar_matches_route_trail():
    for trail in ["🏠 → 🗺", "🏠 -> 🗺 -> 🌍", "🏠 ↔ 🗺", "🏠 <-> 🗺 → 🌍", "🏠 ↔ 🗺 ↔ 🌍 → 🏠"]:
        expected = [route_trail.norm(step) for step in route_trail.parse_trail(trail)]
        assert btk_page_inspect_routes.parse_trail(trail) == expected, trail


def test_trail_must_alternate_pages_and_arrows():
    with pytest.raises(btk_page_inspect_routes.InspectError) as no_start:
        btk_page_inspect_routes.parse_trail("→ 🗺")
    assert "alternate" in str(no_start.value)
    with pytest.raises(btk_page_inspect_routes.InspectError) as no_end:
        btk_page_inspect_routes.parse_trail("🏠 →")
    assert "alternate" in str(no_end.value)


def test_routes_file_is_checked(tmp_path):
    assert btk_page_inspect_routes.load_routes(write_routes(tmp_path, {"/": "🏠", "/form.html": "🗺️"})) == {"🏠": "/", "🗺": "/form.html"}
    with pytest.raises(btk_page_inspect_routes.InspectError) as not_json:
        btk_page_inspect_routes.load_routes(write_routes(tmp_path, "not json"))
    assert "not JSON" in str(not_json.value)
    with pytest.raises(btk_page_inspect_routes.InspectError) as twice:
        btk_page_inspect_routes.load_routes(write_routes(tmp_path, {"/": "🏠", "/a": "🏠"}))
    assert "used by two" in str(twice.value)
    with pytest.raises(btk_page_inspect_routes.InspectError) as relative:
        btk_page_inspect_routes.load_routes(write_routes(tmp_path, {"form.html": "🗺"}))
    assert "bad route" in str(relative.value)
    with pytest.raises(btk_page_inspect_routes.InspectError) as empty:
        btk_page_inspect_routes.load_routes(write_routes(tmp_path, {"/": " "}))
    assert "needs one emoji" in str(empty.value)
    with pytest.raises(btk_page_inspect_routes.InspectError) as too_many:
        btk_page_inspect_routes.load_routes(write_routes(tmp_path, {f"/p{n}": str(n) for n in range(len(btk_page_inspect_routes.ROUTE_POOL) + 1)}))
    assert "1 to 30" in str(too_many.value)


def test_routes_and_trail_flags_are_checked_before_any_browser_launch(tmp_path):
    url = "http://127.0.0.1:9/index.html"
    routes = write_routes(tmp_path, {"/index.html": "🏠", "/form.html": "🗺"})
    no_routes = run_btk(["observe", url, "--trail", "🏠 → 🗺", "--out-dir", str(tmp_path)])
    assert no_routes.returncode == 2 and "needs --routes" in no_routes.stderr
    no_dir = run_btk(["observe", url, "--trail", "🏠 → 🗺", "--routes", routes])
    assert no_dir.returncode == 2 and "--out-dir" in no_dir.stderr
    mixed = run_btk(["observe", url, "--trail", "🏠 → 🗺", "--routes", routes, "--out-dir", str(tmp_path),
                     "--step", "press - Enter"])
    assert mixed.returncode == 2 and "cannot be combined" in mixed.stderr
    unknown = run_btk(["observe", url, "--trail", "🏠 → 🌍", "--routes", routes, "--out-dir", str(tmp_path)])
    assert unknown.returncode == 2 and "no route for emoji 🌍" in unknown.stderr
    no_routes_goto = run_btk(["act", url, "--action", "goto", "--text", "🗺", "--json"])
    assert no_routes_goto.returncode == 2 and "--routes" in no_routes_goto.stdout + no_routes_goto.stderr
    bad_goto = run_btk(["act", url, "--action", "goto", "--text", "🌍", "--routes", routes, "--json"])
    assert bad_goto.returncode == 2 and "no route for emoji" in bad_goto.stdout + bad_goto.stderr
    zero = run_btk(["pages", url, "--max-pages", "0"])
    assert zero.returncode == 2 and "at least 1" in zero.stderr


@pytest.fixture
def site(tmp_path):
    (tmp_path / "index.html").write_text(INDEX, encoding="utf-8")
    (tmp_path / "form.html").write_text(FORM, encoding="utf-8")
    (tmp_path / "result.html").write_text(RESULT, encoding="utf-8")
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", tmp_path
    finally:
        server.shutdown()


@pytest.mark.browser
def test_pages_then_trail_and_emoji_goto_drive_the_same_routes(site):
    base, work = site
    routes_file = work / "routes.json"
    found = run_btk(["pages", f"{base}/index.html", "--routes-out", str(routes_file), "--json"])
    assert found.returncode == 0, found.stderr
    payload = json.loads(found.stdout)
    assert payload["schema"] == "btk-page-routes/1"
    assert payload["routes"] == {"/index.html": "🏠", "/form.html": "🗺", "/result.html": "🌍"}
    assert payload["skipped_external_links"] == 1 and payload["truncated"] is False
    assert json.loads(routes_file.read_text(encoding="utf-8")) == payload["routes"]

    capped = json.loads(run_btk(["pages", f"{base}/index.html", "--max-pages", "1", "--json"]).stdout)
    assert list(capped["routes"]) == ["/index.html"] and capped["truncated"] is True

    out = work / "trail"
    out.mkdir()
    trail = run_btk(["observe", f"{base}/index.html", "--trail", "🏠 → 🗺 → 🌍",
                     "--routes", str(routes_file), "--out-dir", str(out), "--json"])
    assert trail.returncode == 0, trail.stderr
    summary = json.loads(trail.stdout)
    assert [Path(f).name for f in summary["files"]] == ["00-initial.json", "01-goto.json", "02-goto.json"]
    pages = [json.loads(Path(f).read_text(encoding="utf-8"))["url"].rsplit("/", 1)[1] for f in summary["files"]]
    assert pages == ["index.html", "form.html", "result.html"]

    acted = run_btk(["act", f"{base}/index.html", "--action", "goto", "--text", "🗺",
                     "--routes", str(routes_file), "--json"])
    assert acted.returncode == 0, acted.stderr
    assert json.loads(acted.stdout)["url"].endswith("/form.html")


@pytest.mark.browser
def test_pages_many_links_cli_is_bounded(site):
    base, work = site
    fixture = ROOT / "tests/fixtures/pages/many-links.html"
    (work / "many.html").write_bytes(fixture.read_bytes())
    result = run_btk(["pages", f"{base}/many.html", "--max-pages", "1", "--json"])
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["routes"] == {"/many.html": "🏠"}
    assert payload["links_inspected"] == btk_page_inspect_routes.PAGE_LINK_LIMIT
    assert payload["queue_peak"] == 1
    assert payload["truncated"] is True
    assert "secret" not in result.stdout and "hidden" not in result.stdout


@pytest.mark.parametrize("max_pages", [1, 30])
def test_pages_link_and_queue_budgets_without_bulk_locators(monkeypatch, max_pages):
    class Anchor:
        def get_attribute(self, name):
            page.reads += 1
            return f"/p{page.reads}?private=secret#hidden"

    class Anchors:
        def count(self):
            return 5000

        def nth(self, index):
            assert index < btk_page_inspect_routes.PAGE_LINK_LIMIT
            return Anchor()

        def all(self):
            pytest.fail("must not allocate all locators")

    class Page:
        reads = 0
        visits = []
        url = "http://127.0.0.1:8000/"

        def goto(self, url, **kwargs):
            self.url = url
            self.visits.append(url)

        def locator(self, selector):
            return Anchors()

    page = Page()
    monkeypatch.setattr(btk_page_inspect_routes, "_with_browser", lambda *args: (None, None, page, False))
    monkeypatch.setattr(btk_page_inspect_routes, "_finish", lambda pw, browser, failure, result: result)
    payload = btk_page_inspect_routes.discover_pages(url=page.url, browser_name="chromium", profile="desktop",
                                 headed=False, channel=None, max_pages=max_pages)
    assert payload["links_inspected"] == min(max_pages * btk_page_inspect_routes.PAGE_LINK_LIMIT, btk_page_inspect_routes.TOTAL_LINK_LIMIT)
    assert page.reads == payload["links_inspected"]
    assert payload["queue_peak"] <= max_pages
    assert len(payload["routes"]) == max_pages
    assert payload["truncated"] is True
    assert list(payload["routes"])[0] == "/"
    assert "secret" not in json.dumps(payload)
    assert page.visits == ["http://127.0.0.1:8000/"] + [
        f"http://127.0.0.1:8000/p{i}" for i in range(1, max_pages)]


# ═══ · page inspect sequence ═════════════════════════════════════════════════════════════════════════════
# observe --after-click: one click, then a second snapshot written to its own file.

FIRST = """<!doctype html><meta charset="utf-8"><title>first</title>
<a id="next" href="second.html">Next</a>
"""
SECOND = """<!doctype html><meta charset="utf-8"><title>second</title>
<button id="done">Done</button>
"""


def run_page_inspect_sequence(args, cwd):
    # observe needs Playwright from site-packages, so it runs without -S (unlike offline commands)
    return subprocess.run([sys.executable, str(SCRIPT), "observe", *args], cwd=cwd,
                          capture_output=True, text=True, timeout=180)


def test_sequence_arguments_are_checked_before_any_browser_launch(tmp_path):
    url = "http://127.0.0.1:9/index.html"
    missing_after = run_page_inspect_sequence([url, "--after-click", "id:next", "--out", str(tmp_path / "a.json")], tmp_path)
    assert missing_after.returncode == 2 and "--after-out" in missing_after.stderr
    missing_out = run_page_inspect_sequence([url, "--after-click", "id:next", "--after-out", str(tmp_path / "b.json")], tmp_path)
    assert missing_out.returncode == 2 and "--out" in missing_out.stderr
    ordinal = run_page_inspect_sequence([url, "--after-click", "h:2", "--out", str(tmp_path / "a.json"),
                   "--after-out", str(tmp_path / "b.json")], tmp_path)
    assert ordinal.returncode == 2 and "ordinal" in ordinal.stderr
    same = run_page_inspect_sequence([url, "--after-click", "id:next", "--out", str(tmp_path / "a.json"),
                "--after-out", str(tmp_path / "a.json")], tmp_path)
    assert same.returncode == 2 and "different files" in same.stderr
    no_dir = run_page_inspect_sequence([url, "--after-click", "id:next", "--out", str(tmp_path / "missing" / "a.json"),
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
        done = run_page_inspect_sequence([url, "--after-click", "id:next", "--out", str(tmp_path / "before.json"),
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
            done = run_page_inspect_sequence([url, "--out", str(tmp_path / name), "--json"], tmp_path)
            assert done.returncode == 0, done.stderr
    finally:
        server.shutdown()
    compared = subprocess.run([sys.executable, "-S", str(SCRIPT), "compare", str(tmp_path / "one.json"),
                               str(tmp_path / "two.json"), "--json"], cwd=tmp_path,
                              capture_output=True, text=True, timeout=60)
    sections = json.loads(compared.stdout)["sections"]
    for name in ("dom", "accessibility", "resources"):
        assert sections[name]["relation"] == "same", name


# ═══ · page inspect settle ═══════════════════════════════════════════════════════════════════════════════
# settle: a quiet window without an LLM (Stagehand quietWait rule); redaction ported from Stagehand.

SETTLES = """<!doctype html><meta charset="utf-8"><title>settles</title><p id="p">0</p>
<script>
  let n = 0;
  const t = setInterval(() => { document.getElementById('p').textContent = String(++n); if (n === 5) clearInterval(t); }, 80);
</script>
"""
NEVER = """<!doctype html><meta charset="utf-8"><title>never</title><p id="p">0</p>
<script>
  let n = 0;
  setInterval(() => { document.getElementById('p').textContent = String(++n); }, 50);
</script>
"""


def test_redaction_removes_keys_tokens_and_bearer_values():
    text = ("nav failed https://h/x?token=abcd1234efgh&a=1 Bearer abcdefghijkl "
            "sk-abcdefghij1234 AIza" + "x" * 32)
    cleaned = btk.redact_text(text)
    assert "abcd1234efgh" not in cleaned and "abcdefghijkl" not in cleaned
    assert "sk-abcdef[redacted]" in cleaned and "AIza[redacted]" in cleaned
    assert "a=1" not in cleaned  # query values are omitted regardless of key name
    envelope = btk.error_envelope("act", btk.InspectError(
        "bad https://h/?apiKey=zzzzzzzz", "retry with Bearer abcdefghijk"))
    assert "zzzzzzzz" not in json.dumps(envelope) and "abcdefghijk" not in json.dumps(envelope)


def test_settle_needs_no_locator_and_is_declared_as_an_action(tmp_path):
    assert "settle" in btk.ACT_ACTIONS
    assert btk._parse_step("settle - -", 1)["action"] == "settle"


def serve(directory: Path):
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(directory))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def act_settle(url, cwd, timeout_ms="5000"):
    return subprocess.run([sys.executable, str(SCRIPT), "act", url, "--action", "settle",
                           "--timeout-ms", timeout_ms, "--json"], cwd=cwd, capture_output=True, text=True, timeout=180)


@pytest.mark.browser
def test_settle_reports_quiet_when_mutations_stop_and_not_quiet_on_timeout(tmp_path):
    (tmp_path / "settles.html").write_text(SETTLES, encoding="utf-8")
    (tmp_path / "never.html").write_text(NEVER, encoding="utf-8")
    server = serve(tmp_path)
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        quiet = act_settle(f"{base}/settles.html", tmp_path)
        busy = act_settle(f"{base}/never.html", tmp_path, timeout_ms="1500")
    finally:
        server.shutdown()
    assert quiet.returncode == 0, quiet.stderr
    assert json.loads(quiet.stdout)["limits"]["quiet"] is True
    assert busy.returncode == 0, busy.stderr
    assert json.loads(busy.stdout)["limits"]["quiet"] is False


# ═══ · page inspect shadow ═══════════════════════════════════════════════════════════════════════════════
# Shadow roots are counted (open and closed), never entered, and frames are not descended.

PAGE_INSPECT_SHADOW_PAGE = """<!doctype html><meta charset="utf-8"><title>shadow fixture</title>
<div id="open-host"></div><div id="closed-host"></div><div id="nested-host"></div>
<iframe srcdoc="&lt;div id='f'&gt;&lt;/div&gt;"></iframe>
<script>
  const open = document.getElementById('open-host').attachShadow({mode: 'open'});
  open.innerHTML = '<button>open</button>';
  document.getElementById('closed-host').attachShadow({mode: 'closed'}).innerHTML = '<button>closed</button>';
  const nested = document.getElementById('nested-host').attachShadow({mode: 'open'});
  nested.innerHTML = '<div id="inner"></div>';
  nested.getElementById('inner').attachShadow({mode: 'open'}).innerHTML = '<a href="#">deep</a>';
</script>
"""


def test_counts_open_closed_and_nested_roots_without_frames():
    tree = {"nodeType": 9, "children": [
        {"nodeType": 1, "shadowRoots": [{"shadowRootType": "open", "children": [
            {"nodeType": 1, "shadowRoots": [{"shadowRootType": "closed", "children": []}]}]}]},
        {"nodeType": 1, "contentDocument": {"nodeType": 9, "shadowRoots": [{"shadowRootType": "open"}]}},
    ]}
    counts = btk.count_shadow_roots(tree)
    assert counts == {"open": 1, "closed": 1, "count": 2, "user_agent": 0, "truncated": False}


def test_truncation_is_reported_not_silent():
    deep = {"nodeType": 1, "children": []}
    for _ in range(5):
        deep = {"nodeType": 1, "children": [deep]}
    counts = btk.count_shadow_roots(deep, limit=3)
    assert counts["truncated"] is True


@pytest.mark.browser
def test_observe_reports_shadow_root_section(tmp_path):
    (tmp_path / "index.html").write_text(PAGE_INSPECT_SHADOW_PAGE, encoding="utf-8")
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/index.html"
        run = subprocess.run([sys.executable, str(ROOT / "tools" / "btk.py"), "observe", url, "--json"],
                             capture_output=True, text=True, timeout=180)
    finally:
        server.shutdown()
    assert run.returncode == 0, run.stderr
    section = json.loads(run.stdout)["sections"]["shadow_roots"]
    assert section["coverage"] == "complete"
    # open-host, nested-host and the inner root inside nested-host are open; one closed root.
    assert (section["open"], section["closed"], section["count"]) == (3, 1, 4)
    assert section["contents"] == "not_entered"


# ═══ · page inspect steps ════════════════════════════════════════════════════════════════════════════════
# observe --step: a sequence of declared actions, one snapshot per step, text never recorded.

_spec = importlib.util.spec_from_file_location("route_trail", ROOT / "tools" / "route_trail.py")
route_trail_page_inspect_steps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(route_trail_page_inspect_steps)
ROUTES = route_trail_page_inspect_steps.load_registry((ROOT / "fixtures" / "page_inspect_routes.json").read_text(encoding="utf-8"))

PAGE_INSPECT_STEPS_FORM = """<!doctype html><meta charset="utf-8"><title>form</title>
<input id="q" type="text"> <a id="go" href="result.html">Go</a>
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
    (tmp_path / "form.html").write_text(PAGE_INSPECT_STEPS_FORM, encoding="utf-8")
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
    (tmp_path / "form.html").write_text(PAGE_INSPECT_STEPS_FORM, encoding="utf-8")
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
    (tmp_path / "form.html").write_text(PAGE_INSPECT_STEPS_FORM, encoding="utf-8")
    (tmp_path / "result.html").write_text(RESULT, encoding="utf-8")
    out = tmp_path / "steps"
    out.mkdir()
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        trail = route_trail_page_inspect_steps.resolve("🏠 → 🗺", ROUTES, base=base, hash_routing=False)
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


# ═══ · page inspect url privacy ══════════════════════════════════════════════════════════════════════════
# URL evidence drops values without changing the URLs sent to the browser.

QUERY = "private-query-42"
FRAGMENT = "private-fragment-73"
SUFFIX = f"?q={QUERY}#{FRAGMENT}"


def assert_private_absent(text):
    assert QUERY not in text
    assert FRAGMENT not in text


@pytest.mark.parametrize("url,expected", [
    (f"http://user:password@127.0.0.1:8765/path{SUFFIX}", "http://127.0.0.1:8765/path"),
    (f"https://[::1]:8765/a%20b{SUFFIX}", "https://[::1]:8765/a%20b"),
    (f"http://127.0.0.1:8765/{SUFFIX}", "http://127.0.0.1:8765/"),
    (f"about:blank{SUFFIX}", "about:blank"),
    ("", ""),
    (f"http://[broken/path{SUFFIX}", "[invalid-url]"),
    (f"http://127.0.0.1:bad/path{SUFFIX}", "[invalid-url]"),
])
def test_evidence_url_omits_values_and_keeps_path(url, expected):
    assert btk.strip_userinfo(url) == expected


def test_navigation_validation_and_step_parsing_preserve_values():
    url = "http://127.0.0.1:8765/path" + SUFFIX
    assert btk.loopback_target(url) == url
    assert btk._parse_step("goto - " + url, 1)["text"] == url


@pytest.mark.parametrize("entrypoint", ["python", "node"])
def test_compare_legacy_urls_does_not_republish_values(tmp_path, entrypoint):
    left = {"schema": btk.SCHEMA_OBSERVE, "elements": [],
            "url": "http://127.0.0.1:8765/start" + SUFFIX}
    right = dict(left, url="http://127.0.0.1:8765/result" + SUFFIX)
    for name, body in (("left", left), ("right", right)):
        (tmp_path / f"{name}.json").write_text(json.dumps(body), encoding="utf-8")
    done = run_cli(entrypoint, ["compare", str(tmp_path / "left.json"), str(tmp_path / "right.json"), "--json"])
    assert done.returncode == 0, done.stdout + done.stderr
    assert_private_absent(done.stdout + done.stderr)
    payload = json.loads(done.stdout)
    assert payload["left_url"].endswith("/start")
    assert payload["right_url"].endswith("/result")


def test_navigation_error_call_logs_omit_arbitrary_url_values():
    exc = btk.InspectError('Page.goto: navigating to "http://127.0.0.1:8765/path' + SUFFIX + '"', "retry")
    payload = btk.error_envelope("observe", exc)
    assert_private_absent(btk.dumps(payload) + btk.error_text(payload))
    assert "http://127.0.0.1:8765/path" in payload["error"]


def run_cli(entrypoint, args):
    if entrypoint == "node":
        node = shutil.which("node")
        assert node, "Node 26+ is required for the URL privacy parity checks"
        command = [node, str(ROOT / "vendor" / "py_launch.cjs"), "--env-var", "BTK_PYTHON", "--site",
                   str(ROOT / "tools" / "btk.py")]
    else:
        command = [sys.executable, str(ROOT / "tools" / "btk.py")]
    return subprocess.run(command + args, capture_output=True, text=True, timeout=120,
                          env={**os.environ, "PYTHON": sys.executable, "BTK_PYTHON": sys.executable})


@pytest.fixture
def private_site(tmp_path):
    # The script reports only a boolean result. The server also records the received
    # query in memory; neither probe prints the original location into page content.
    html = f'''<!doctype html><title>pending</title><button id="ok">Ready</button>
    <script src="/asset.js{SUFFIX}"></script>
    <img src="http://localhost:9/fail.svg{SUFFIX}">
    <iframe src="/frame.html{SUFFIX}"></iframe>'''
    for name in ("start.html", "result.html"):
        (tmp_path / name).write_text(html, encoding="utf-8")
    (tmp_path / "asset.js").write_text(
        f'document.title = location.search === "?q={QUERY}" && location.hash === "#{FRAGMENT}" '
        '? "navigation-preserved" : "navigation-broken";', encoding="utf-8")
    (tmp_path / "frame.html").write_text("<!doctype html><title>frame</title>", encoding="utf-8")
    received = []

    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            received.append(self.path)
            super().do_GET()

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(tmp_path)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", received
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.browser
@pytest.mark.parametrize("entrypoint", ["python", "node"])
def test_initial_goto_network_steps_and_compare_hide_values(tmp_path, private_site, entrypoint):
    base, received = private_site
    out = tmp_path / "snapshots"
    out.mkdir()
    done = run_cli(entrypoint, ["observe", base + "/start.html" + SUFFIX,
                              "--step", "goto - " + base + "/result.html" + SUFFIX,
                              "--out-dir", str(out), "--json"])
    assert done.returncode == 0, done.stdout + done.stderr
    assert_private_absent(done.stdout + done.stderr)
    summary = json.loads(done.stdout)
    snapshots = [json.loads(Path(path).read_text(encoding="utf-8")) for path in summary["files"]]
    initial, final = snapshots
    for path in summary["files"]:
        assert_private_absent(Path(path).read_text(encoding="utf-8"))
    for snapshot, path in ((initial, "/start.html"), (final, "/result.html")):
        assert snapshot["url"] == base + path
        assert snapshot["title"] == "navigation-preserved"  # query AND fragment reached page script
        assert snapshot["sections"]["dom"]["document_url"] == base + path
        assert snapshot["sections"]["resources"]["document_url"] == base + path
        assert any(row["url"] == base + "/asset.js" for row in snapshot["sections"]["scripts"]["items"])
        assert any(row["url"] == base + "/frame.html" for row in snapshot["sections"]["frames"]["items"])
    network = initial["sections"]["network"]["items"]
    assert any(row["url"] == base + "/start.html" and row["status"] == 200 for row in network)
    assert any(row["url"] == "http://localhost:9/fail.svg" and row.get("failure") for row in network)
    assert final["step"]["url_before"] == base + "/start.html"
    assert final["step"]["url_after"] == base + "/result.html"
    assert "/start.html?q=" + QUERY in received
    assert "/result.html?q=" + QUERY in received
    compared = run_cli(entrypoint, ["compare", *summary["files"], "--json"])
    assert compared.returncode == 0, compared.stdout + compared.stderr
    assert_private_absent(compared.stdout + compared.stderr)
    payload = json.loads(compared.stdout)
    assert payload["left_url"] == base + "/start.html"
    assert payload["right_url"] == base + "/result.html"


@pytest.mark.browser
@pytest.mark.parametrize("entrypoint", ["python", "node"])
def test_act_goto_url_section_hides_values(tmp_path, private_site, entrypoint):
    base, received = private_site
    artifact = tmp_path / "act.json"
    done = run_cli(entrypoint, ["act", base + "/start.html" + SUFFIX, "--action", "goto",
                              "--text", base + "/result.html" + SUFFIX, "--json", "--out", str(artifact)])
    assert done.returncode == 0, done.stdout + done.stderr
    assert_private_absent(done.stdout + done.stderr + artifact.read_text(encoding="utf-8"))
    payload = json.loads(done.stdout)
    assert payload["url"] == base + "/result.html"
    assert payload["sections"]["url"]["url"] == base + "/result.html"
    assert "/result.html?q=" + QUERY in received


def test_colon_step_publication_hides_urls_without_changing_navigation():
    url = "http://127.0.0.1:8765/path" + SUFFIX
    step = btk.parse_step("goto:" + url)
    assert step["url"] == url
    public = btk.publish_step(step, {"status": "done"}, url_before=url, url_after=url, index=1)
    assert_private_absent(json.dumps(public))
    assert public["url_before"] == public["url_after"] == "http://127.0.0.1:8765/path"
