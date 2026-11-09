"""Offline compare of two observe snapshots: section relations and counts, never browser-driven."""
import json, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import btk  # noqa: E402

SCRIPT = ROOT / "scripts" / "btk.py"
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
