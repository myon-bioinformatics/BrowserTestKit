"""Offline page-inspect operations: scores, addresses, pages, and the CLI."""
import importlib.util, json, subprocess, sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("btk", ROOT / "scripts" / "btk.py")
btk = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(btk)


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
    return btk.assemble_observe(
        raw, environment={"os": "Linux", "browser": "chromium", "version": "0", "channel": None,
                          "headless": True, "profile": "desktop", "viewport": {"width": 1280, "height": 800}},
        url="http://127.0.0.1:9/fixture", start=0, limit=50,
        sections={"dom": {"source": "test", "coverage": "not_requested"}},
        limits={"page_js_called": False},
    )


def test_similarity_is_the_shorter_string_ratio():
    assert btk.lcs_length("save drft", "save draft") == 7
    assert btk.lcs_length("", "abc") == 0
    assert btk.lcs_length("abc", "xyz") == 0
    assert btk.similarity("Save", "save draft") == 1
    assert btk.similarity("save drft", "save draft") == btk.round6(7 / 9)
    assert btk.similarity("", "save") == 0
    assert btk.lcs_length("a\U0001f600b", "a\U0001f600b") == 4
    assert btk.similarity("\U0001f600", "x\U0001f600y") == 1


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
    unique = btk.locate(elements, query="Save draft", unique=True)
    assert unique["ok"] is True and unique["items"][0]["address"] == "id:save-draft"
    shared = btk.locate(elements, query="Save", unique=True)
    assert shared["ok"] is False and shared["error"].startswith("more than one")
    assert {item["name"] for item in shared["suggestions"]} == {"Save draft", "Saved items"}
    missed = btk.locate(elements, query="Save drft")
    assert missed["ok"] is False and missed["suggestions"][0]["name"] == "Save draft"
    page = btk.locate(elements, query="", start=1, limit=1)
    assert page["pagination"] == {"start": 1, "limit": 1, "returned": 1, "total": 3, "next_start": 2}
    counted = btk.count_elements(elements, role="button")
    assert counted["total"] == 1 and counted["by_role"] == {"button": 1}


def test_describe_accepts_common_target_forms_and_rejects_memory_addresses():
    elements = snapshot_of(
        element("Save draft", id="save-draft", testid="save-draft", id_unique=True, testid_unique=True, css="#save-draft", css_unique=True),
        element("Cancel"),
        element("Cancel"),
    )["elements"]
    for target in ("id:save-draft", "#save-draft", "save-draft", 'role=button[name="Save draft"]',
                   'getByRole("button", { name: "Save draft" })', 'button "Save draft"', "h:0"):
        found = btk.describe_element(elements, target)
        assert found["ok"] is True and found["item"]["id"] == "save-draft", target
    second = btk.describe_element(elements, "role:button/Cancel#1")
    assert second["ok"] is True and second["item"]["handle"] == "h:2"
    ambiguous = btk.describe_element(elements, 'button "Cancel"')
    assert ambiguous["ok"] is False and len(ambiguous["suggestions"]) == 2
    with pytest.raises(btk.InspectError) as raised:
        btk.describe_element(elements, "0x401000")
    assert "memory addresses" in raised.value.error
    missing = btk.describe_element(elements, "zzzz")
    assert missing["ok"] is False and missing["suggestions"] == []
    spaced = snapshot_of(element("Save draft"), element("Go \U0001f600", role="link", tag="a"))["elements"]
    assert spaced[0]["address"] == "role:button/Save%20draft#0"
    emoji = btk.describe_element(spaced, spaced[1]["address"])
    assert emoji["ok"] is True and emoji["item"]["handle"] == "h:1"
    paged = btk.normalize_elements(
        {"elements": [element("One"), element("Two"), element("Three")], "total": 3}, start=1, limit=1,
    )
    assert paged["truncated"] is True and paged["elements"][0]["handle"] == "h:1"


def test_option_names_are_not_abbreviated_and_do_not_swallow_the_next_flag():
    parser = btk.build_parser()
    args = parser.parse_args(["observe", "http://127.0.0.1:9/page", "--browser", "firefox", "--json", "--limit", "2"])
    assert args.browser == "firefox" and args.json is True and args.limit == 2
    attached = parser.parse_args(["observe", "http://127.0.0.1:9/page", "--browser=webkit"])
    assert attached.browser == "webkit"
    with pytest.raises(SystemExit) as raised:
        parser.parse_args(["observe", "http://127.0.0.1:9/page", "--brow", "firefox"])
    assert raised.value.code == 2


def test_context_options_drop_is_mobile_only_for_firefox():
    devices = {"iPhone 13": {"is_mobile": True, "has_touch": True, "viewport": {"width": 390, "height": 844}, "default_browser_type": "webkit"}}
    firefox = btk.context_options(devices, "firefox", "iphone", False)
    webkit = btk.context_options(devices, "webkit", "iphone", False)
    assert "is_mobile" not in firefox and firefox["has_touch"] is True
    assert webkit["is_mobile"] is True and "default_browser_type" not in webkit
    desktop = btk.context_options(devices, "chromium", "desktop", False)
    assert desktop["viewport"] == {"width": 1280, "height": 800}


def test_cli_offline_commands_match_the_library(tmp_path: Path):
    survey = snapshot_of(
        element("Save draft", id="save-draft", id_unique=True, css="#save-draft", css_unique=True),
        element("Saved items", role="link", tag="a"),
    )
    path = tmp_path / "survey.json"
    path.write_text(btk.dumps(survey), encoding="utf-8")
    script = ROOT / "scripts" / "btk.py"

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
    assert btk.loopback_target("http://127.0.0.1:9/a") == "http://127.0.0.1:9/a"
    assert btk.loopback_target("http://[::1]:80/a") == "http://[::1]:80/a"
    assert btk.loopback_target("http://127.0.0.1:8081/") == "http://127.0.0.1:8081/"
    for url in (
        "http://localhost:9/",
        "http://127.0.0.1/a",
        "file:///tmp/page.html",
        "javascript:alert(1)",
        "https://example.test:9/",
        "http://user:secret" + chr(64) + "127.0.0.1:9/",
    ):
        with pytest.raises(btk.InspectError):
            btk.loopback_target(url)
    missing = {"coverage": "not_requested"}
    assert btk.evidence_relation(missing, missing) == "unknown"
    same = {"coverage": "complete", "digest": "abc"}
    assert btk.evidence_relation(same, dict(same)) == "same"
    assert btk.evidence_relation(same, {"coverage": "complete", "digest": "def"}) == "different"
    limited = {"coverage": "attach_limited", "digest": "abc"}
    assert btk.evidence_relation(limited, dict(limited)) == "unknown"
    finger = btk.storage_fingerprint("secret-value")
    assert finger["fingerprint_version"] == 2 and "secret-value" not in json.dumps(finger)
    # Passive observation and Chromium settle use CDP without page evaluation.
    source = (ROOT / "scripts" / "btk.py").read_text(encoding="utf-8")
    assert ".evaluate(" not in source


def test_act_rejects_ordinals_before_launch():
    with pytest.raises(btk.InspectError) as raised:
        btk.act_page(
            url="http://127.0.0.1:9/", browser_name="chromium", profile="desktop", headed=False,
            channel=None, action="click", locator="h:0", text=None, timeout_ms=500,
        )
    assert "ordinal" in raised.value.error
    with pytest.raises(btk.InspectError):
        btk.act_page(
            url="http://127.0.0.1:9/", browser_name="chromium", profile="desktop", headed=False,
            channel=None, action="dance", locator="id:save", text=None, timeout_ms=500,
        )


def test_request_boundary_rejects_redirects_and_external_resources():
    from unittest.mock import Mock
    rejected = "http://localhost:9/"
    route = Mock()
    route.request.url = rejected
    btk.restrict_request(route)
    route.abort.assert_called_once_with("blockedbyclient")
    route.fetch.assert_not_called()
    for status in (200, 302):
        route = Mock()
        route.request.url = "http://127.0.0.1:9/"
        route.fetch.return_value.status = status
        btk.restrict_request(route)
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
        session = btk.observe_page(
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
            steps=[btk.parse_step("scroll:0,800")],
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
