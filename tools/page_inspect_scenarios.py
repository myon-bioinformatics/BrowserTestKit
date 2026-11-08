"""Bounded active platform probes for the existing page_inspect evidence contract.

This adapter does not implement general actions or compare. Only the declared
fixture is driven; values, arbitrary paths and raw browser errors stay private.
"""
from __future__ import annotations

import math
import re
import time
import urllib.parse

CASES = ("display", "shortcut", "offline", "http-error", "latency", "permission", "upload", "download")
CHANNELS = ("chrome", "msedge")
TIMEZONES = ("UTC", "Asia/Tokyo", "America/New_York", "Europe/London")
LOCALES = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
VIEWPORT = re.compile(r"^([1-9][0-9]{1,3})x([1-9][0-9]{1,3})$")
EVENT_LIMIT = 30
PERMISSION_STATES = {"granted", "denied", "prompt", "unsupported"}
FIXTURE_CONTRACT = "btk-platform-fixture/2"


def validate(args, api):
    """Reject unsafe configuration before launch without echoing its contents."""
    if args.case not in CASES:
        raise api.InspectError("unsupported scenario", "choose a declared fixture case")
    if args.channel and (args.browser != "chromium" or args.channel not in CHANNELS):
        raise api.InspectError("unsupported browser channel", "use chrome or msedge with chromium")
    match = VIEWPORT.fullmatch(args.viewport) if args.viewport else None
    if args.viewport and not match:
        raise api.InspectError("invalid viewport", "use WIDTHxHEIGHT, each dimension 200..4096")
    viewport = None
    if match:
        width, height = (int(part) for part in match.groups())
        if not (200 <= width <= 4096 and 200 <= height <= 4096):
            raise api.InspectError("viewport out of range", "each dimension must be 200..4096")
        viewport = {"width": width, "height": height}
    if not args.locale or len(args.locale) > 40 or not LOCALES.fullmatch(args.locale):
        raise api.InspectError("invalid locale", "use e.g. ja-JP or en-US")
    if args.timezone not in TIMEZONES:
        raise api.InspectError("unsupported timezone", "use a declared timezone")
    if not math.isfinite(args.device_scale_factor) or not 0.5 <= args.device_scale_factor <= 4:
        raise api.InspectError("device scale factor out of range", "use a finite value from 0.5 to 4")
    if args.latency_ms < 0 or args.latency_ms > 2000:
        raise api.InspectError("latency out of range", "use 0..2000 milliseconds")
    if args.permission not in ("notifications", "geolocation") or args.permission_state not in ("grant", "clear"):
        raise api.InspectError("unsupported permission operation", "use notifications/geolocation and grant/clear")
    api.loopback_target(args.target)
    return viewport


def safe_path(url, api):
    """Only fixed fixture paths may be emitted; never raw URLs or query strings."""
    try:
        api.loopback_target(url)
        path = urllib.parse.urlsplit(url).path or "/"
        return path if path in ("/api/probe", "/scenario.txt", "/") else "[other_loopback]"
    except (api.InspectError, ValueError):
        return "[blocked]"


def same_origin(url, target, api):
    """A different loopback port is not the declared fixture server."""
    try:
        api.loopback_target(url)
        api.loopback_target(target)
        left, right = urllib.parse.urlsplit(url), urllib.parse.urlsplit(target)
        return (left.scheme, left.hostname, left.port) == (right.scheme, right.hostname, right.port)
    except (api.InspectError, ValueError):
        return False


class EventWindow:
    """Bounded, explicitly filtered evidence; freezing detaches mutable listeners."""
    def __init__(self):
        self.items = []
        self.seen = 0
        self.phase = "navigation"
        self.closed = False

    def record(self, kind, path, status=None):
        if self.closed:
            return
        self.seen += 1
        if len(self.items) < EVENT_LIMIT:
            self.items.append({"kind": kind, "path": path, "status": status, "phase": self.phase})

    def freeze(self, api):
        self.closed = True
        truncated = self.seen > EVENT_LIMIT
        return api._section("playwright:response/requestfailed", "truncated" if truncated else "complete_within_window", {
            "items": [dict(item) for item in self.items], "event_limit": EVENT_LIMIT,
            "truncated": truncated, "omitted": max(0, self.seen - EVENT_LIMIT),
            "filter": "HTTP >=400 and requestfailed only", "window": "before navigation through final probe",
        })


def context_configuration(args, api, devices, viewport):
    config = api.context_options(devices, args.browser, args.profile, args.headed, viewport=viewport)
    config.update(color_scheme=args.color_scheme, reduced_motion=args.reduced_motion,
                  locale=args.locale, timezone_id=args.timezone)
    # Playwright forbids a scale override when using the native window viewport.
    if not config.get("no_viewport"):
        config["device_scale_factor"] = args.device_scale_factor
    return config


def _state(page, selector, attribute, allowed, api):
    """Do not trust arbitrary fixture attributes as safe log data."""
    value = page.locator(selector).get_attribute(attribute)
    if value not in allowed:
        raise api.InspectError("invalid fixture evidence", "the fixture returned a missing or unexpected state", stage="inspect")
    return value


def _probe(page, button, output, allowed, api):
    """Wait for this invocation, not a stale result left by the preceding phase."""
    raw = page.locator(output).get_attribute("data-turn")
    if raw is None or not re.fullmatch(r"[0-9]{1,3}", raw) or int(raw) >= 999:
        raise api.InspectError("invalid fixture generation", "use the versioned scenario fixture", stage="inspect")
    turn = int(raw) + 1
    page.locator(button).click()
    page.locator(output + '[data-completed="%d"]' % turn).wait_for(state="attached")
    return _state(page, output, "data-state", allowed, api)


def permission_result(permission, requested, baseline, configured, reset):
    """Missing support is unknown, never a successful override or reset."""
    available = all(state in PERMISSION_STATES - {"unsupported"} for state in (baseline, configured, reset))
    matches = (configured == "granted" if requested == "grant" else configured == baseline) if available else None
    restored = reset == baseline if available else None
    return {
        "case": "permission", "permission": permission, "configured_state": requested,
        "reported_state": configured, "states": {"baseline": baseline, "configured": configured, "reset": reset},
        "matches_requested": matches, "reset_matches_baseline": restored,
        "verdict": ("pass" if matches and restored else "mismatch") if available else "unavailable",
    }, "complete" if available else "unavailable"


def run_scenario(args, api):
    """Collect active evidence without changing passive observe or its schema."""
    viewport = validate(args, api)
    playwright = None
    browser = None
    failure = None
    result = None
    stage = "launch"
    try:
        playwright = api._playwright()().start()
        browser = getattr(playwright, args.browser).launch(
            headless=not args.headed, **({"channel": args.channel} if args.channel else {}))
        options = context_configuration(args, api, playwright.devices, viewport)
        context = browser.new_context(**options, service_workers="block", accept_downloads=True)
        events = EventWindow()
        state = {"fault": None, "delay_ms": None}

        def guard(route):
            if not same_origin(route.request.url, args.target, api):
                route.abort("blockedbyclient")
                return
            path = urllib.parse.urlsplit(route.request.url).path
            if state["fault"] == "offline":
                route.abort("internetdisconnected")
            elif path == "/api/probe" and state["fault"] == "http-error":
                route.fulfill(status=503, content_type="text/plain", body="unavailable")
            elif path == "/api/probe" and state["fault"] == "latency":
                began = time.monotonic()
                time.sleep(args.latency_ms / 1000)
                state["delay_ms"] = (time.monotonic() - began) * 1000
                route.fulfill(status=200, content_type="text/plain", body="ok")
            else:
                response = route.fetch(max_redirects=0, timeout=10000)
                if 300 <= response.status < 400:
                    route.abort("blockedbyclient")
                else:
                    route.fulfill(response=response)

        context.route("**/*", guard)
        # HTTP routing alone does not constrain WebSockets. No socket is opened by these probes.
        context.route_web_socket("**/*", lambda socket: socket.close())
        page = context.new_page()
        page.set_default_timeout(8000)
        page.set_default_navigation_timeout(15000)
        page.on("requestfailed", lambda request: events.record("request_failed", safe_path(request.url, api)))
        page.on("response", lambda response: events.record("http_error", safe_path(response.url, api), response.status)
                if response.status >= 400 else None)
        stage = "navigate"
        page.goto(args.target, wait_until="load", timeout=15000)
        stage = "inspect"
        _state(page, 'meta[name="btk-scenario"]', "content", {FIXTURE_CONTRACT}, api)
        outcome = {"case": args.case}
        scenario_coverage = "complete"
        measurements = {}
        events.phase = "probe"
        if args.case == "display":
            probe = page.locator("#scenario-display")
            locale = probe.get_attribute("data-locale")
            timezone = probe.get_attribute("data-timezone")
            raw_dpr = probe.get_attribute("data-dpr")
            if raw_dpr is None or not re.fullmatch(r"[0-9](?:\.[0-9]{1,6})?", raw_dpr):
                raise api.InspectError("invalid fixture pixel ratio", "use the versioned fixture", stage="inspect")
            outcome["computed"] = {
                "color_scheme": _state(page, "#scenario-display", "data-scheme", {"light", "dark"}, api),
                "reduced_motion": _state(page, "#scenario-display", "data-motion", {"reduce", "no-preference"}, api),
                "locale": args.locale if locale == args.locale else "[different]",
                "timezone": args.timezone if timezone == args.timezone else "[different]",
                "dpr": float(raw_dpr),
            }
        elif args.case == "shortcut":
            field = page.locator("#scenario-shortcut")
            field.click()
            page.keyboard.press("ControlOrMeta+A")
            page.keyboard.type("Z")
            outcome["select_all_replaced"] = field.input_value() == "Z"
            outcome["shortcut"] = "ControlOrMeta+A"
            page.keyboard.press("ControlOrMeta+Z")
            outcome["undo_restored"] = field.input_value() == "keyboard probe"
            redo_key = "ControlOrMeta+Shift+Z" if api.os_name() == "macOS" else "ControlOrMeta+Y"
            page.keyboard.press(redo_key)
            outcome["redo_applied"] = field.input_value() == "Z"
            outcome["redo_shortcut"] = redo_key
            page.keyboard.press("Tab")
            outcome["tab_focus_advanced"] = page.locator("#scenario-fetch:focus").count() == 1
        elif args.case in ("offline", "http-error", "latency"):
            phases = []
            for phase, fault in (("baseline", None), ("fault", args.case), ("recovery", None)):
                events.phase = phase
                state["fault"] = fault
                context.set_offline(fault == "offline")
                try:
                    observed = _probe(page, "#scenario-fetch", "#scenario-result", {"ok", "error"}, api)
                    phases.append({"phase": phase, "result": observed})
                finally:
                    state["fault"] = None
                    context.set_offline(False)
            outcome["fetch_result"] = phases[1]["result"]
            outcome["phases"] = phases
            outcome["recovered"] = phases[2]["result"] == "ok"
            expected = ["ok", "ok" if args.case == "latency" else "error", "ok"]
            outcome["verdict"] = "pass" if [row["result"] for row in phases] == expected else "mismatch"
            if args.case == "latency":
                elapsed = state["delay_ms"]
                outcome["delay_at_least_requested"] = elapsed is not None and elapsed >= args.latency_ms
                outcome["requested_delay_ms"] = args.latency_ms
                measurements["injected_delay_ms"] = round(elapsed, 3) if elapsed is not None else None
                if not outcome["delay_at_least_requested"]:
                    outcome["verdict"] = "mismatch"
        elif args.case == "permission":
            button = "#scenario-permission" if args.permission == "notifications" else "#scenario-geolocation"
            output = "#scenario-permission-result" if args.permission == "notifications" else "#scenario-geolocation-result"
            baseline = _probe(page, button, output, PERMISSION_STATES, api)
            configured = reset = "unsupported"
            if baseline != "unsupported":
                try:
                    if args.permission_state == "grant":
                        target = urllib.parse.urlsplit(args.target)
                        context.grant_permissions([args.permission], origin=target.scheme + "://" + target.netloc)
                    else:
                        context.clear_permissions()
                    configured = _probe(page, button, output, PERMISSION_STATES, api)
                finally:
                    context.clear_permissions()
                reset = _probe(page, button, output, PERMISSION_STATES, api)
            outcome, scenario_coverage = permission_result(args.permission, args.permission_state, baseline, configured, reset)
        elif args.case == "upload":
            field = page.locator("#scenario-upload")
            field.set_input_files({"name": "probe.txt", "mimeType": "text/plain", "buffer": b"probe"})
            outcome["upload_accepted"] = _state(page, "#scenario-upload-result", "data-state", {"accepted", "rejected", "cleared"}, api) == "accepted"
            field.set_input_files([])
            outcome["cleared"] = _state(page, "#scenario-upload-result", "data-state", {"accepted", "rejected", "cleared"}, api) == "cleared"
            outcome.update(bytes=5, source="in-memory-test-payload")
        elif args.case == "download":
            with page.expect_download(timeout=8000) as event:
                page.locator("#scenario-download").click()
            download = event.value
            outcome["name_matches_fixture"] = download.suggested_filename == "scenario.txt"
            outcome["completed"] = download.failure() is None
            download.delete()
            outcome.update(body="not_read", temporary_artifact="deleted")
        effective_viewport = page.viewport_size or {}
        environment = api._environment(browser, args.browser, args.channel, args.headed, args.profile, effective_viewport,
                                       has_touch=bool(options.get("has_touch")),
                                       viewport_source=api.viewport_source_for(args.profile, args.headed, viewport))
        environment.update(device_profile_emulated=args.profile != "desktop", actual_host_os=api.os_name(),
                           color_scheme=args.color_scheme, reduced_motion=args.reduced_motion,
                           locale=args.locale, timezone=args.timezone, device_scale_factor=options.get("device_scale_factor"),
                           environment_source="playwright:browser.new_context", device_claim="emulation_only")
        settings = {key: environment[key] for key in (
            "os", "browser", "version", "channel", "headless", "profile", "viewport", "color_scheme",
            "reduced_motion", "locale", "timezone", "device_scale_factor", "device_profile_emulated",
            "has_touch", "viewport_source")}
        sections = {
            "scenario": api._section("playwright:bounded-scenario", scenario_coverage, outcome),
            "environment_settings": api._section("playwright:browser.new_context", "complete", settings),
            "network": events.freeze(api),
        }
        sections.update({name: api._section(None, "not_requested") for name in ("dom", "accessibility", "console", "storage", "frames")})
        target = urllib.parse.urlsplit(args.target)
        result = api.assemble_observe(
            {"title": "", "elements": [], "child_frames": 0, "total": 0, "truncated": False},
            environment=environment, url=target.scheme + "://" + target.netloc + "/[fixture]",
            start=0, limit=1, sections=sections,
            limits={"page_js_called": True, "navigation_runs_page_scripts": True,
                    "scenario_active": True, "source": "playwright", "http_scope": "fixture_origin_only",
                    "websocket": "blocked", "service_workers": "blocked", "redirects": "blocked",
                    "secret_input_values": "not_recorded", "file_bodies": "not_recorded",
                    "os_scope": "host_and_emulated_browser; not a real mobile OS"})
        if measurements:
            # Timing is intentionally outside section digests used by evidence_relation.
            result["measurements"] = measurements
    except api.InspectError as exc:
        failure = exc
    except Exception as exc:
        failure = api.InspectError("scenario failed: " + type(exc).__name__,
                                   "verify the versioned fixture and installed browser; raw errors are not recorded", stage=stage)
    if playwright is None:
        raise failure
    return api._finish(playwright, browser, failure, result)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(prog="page_inspect_scenarios", description="Internal adapter; use page_inspect.py scenario.")
    parser.parse_args()
