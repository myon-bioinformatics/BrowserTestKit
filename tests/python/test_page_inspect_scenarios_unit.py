"""Offline boundary tests for the platform and fault scenario command."""
import json, subprocess, sys
from pathlib import Path; from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import btk as api
import page_inspect_scenarios as scenario


def options(**updates):
    values = dict(target="http://127.0.0.1:8765/test?token=hidden", case="display",
                  browser="chromium", profile="desktop", channel=None, headed=False,
                  viewport="390x844", locale="ja-JP", timezone="Asia/Tokyo",
                  color_scheme="dark", reduced_motion="reduce",
                  device_scale_factor=2.0, latency_ms=100,
                  permission="notifications", permission_state="grant")
    values.update(updates)
    return SimpleNamespace(**values)


def test_validation_and_network_privacy():
    assert scenario.validate(options(), api) == {"width": 390, "height": 844}
    for changes in ({"viewport": "9999x9999"}, {"viewport": "bad"},
                    {"channel": "arbitrary"}, {"browser": "webkit", "channel": "chrome"},
                    {"target": "https://example.com/"}, {"locale": "en_US"},
                    {"timezone": "../../secret"}, {"device_scale_factor": float("nan")},
                    {"latency_ms": 2001}):
        with pytest.raises(api.InspectError):
            scenario.validate(options(**changes), api)
    assert scenario.safe_path("http://127.0.0.1:9/api/probe?token=SECRET", api) == "/api/probe"
    assert scenario.safe_path("http://127.0.0.1:9/private/secret", api) == "[other_loopback]"
    assert scenario.safe_path("http://example.com/secret", api) == "[blocked]"
    assert scenario.EVENT_LIMIT == 30


def test_coverage_relations_and_cli_bad_flags():
    a = api._section("test", "complete_within_window", {"case": "offline"})
    assert api.evidence_relation(a, api._section("test", "complete_within_window", {"case": "offline"})) == "same"
    assert api.evidence_relation(a, api._section("test", "complete", {"case": "latency"})) == "different"
    assert api.evidence_relation(a, api._section("test", "truncated", {"case": "offline"})) == "unknown"
    cmd = [sys.executable, "-S", str(ROOT / "scripts/btk.py"), "scenario",
           "http://127.0.0.1:9/page", "--case", "display", "--viewport", "9999x9999", "--json"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 2
    assert json.loads(proc.stdout)["stage"] == "input"


@pytest.mark.parametrize("profile", ["desktop", "iphone", "pixel"])
@pytest.mark.parametrize("headed", [False, True])
@pytest.mark.parametrize("viewport", [None, {"width": 800, "height": 600}])
@pytest.mark.parametrize("browser", ["chromium", "firefox", "webkit"])
def test_context_configuration(profile, headed, viewport, browser):
    devices = {name: {"viewport": {"width": 390, "height": 844},
                      "has_touch": True, "is_mobile": True, "device_scale_factor": 3,
                      "default_browser_type": "webkit"} for name in api.PROFILES.values() if name}
    args = options(profile=profile, headed=headed, browser=browser)
    config = scenario.context_configuration(args, api, devices, viewport)
    native = profile == "desktop" and headed and viewport is None
    assert bool(config.get("no_viewport")) == native
    assert ("device_scale_factor" not in config) if native else config["device_scale_factor"] == 2
    assert bool(config.get("has_touch")) == (profile != "desktop")
    assert "default_browser_type" not in config
    if browser == "firefox":
        assert "is_mobile" not in config
    if viewport is not None:
        assert config["viewport"] == viewport
