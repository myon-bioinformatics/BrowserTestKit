"""Fleet contracts through the parent's shared checks (vendor/fleet_check.py): vendor lock bytes, action and parent pins, Node floor, staging, launcher."""
import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("browsertestkit_fleet_check", ROOT / "vendor/fleet_check.py")
fleet = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fleet)

PARENT_WORKFLOW_PIN = "9e3da7ae1846e9604ebc24bb5178f7de094a4e77"
ACCEPTED = {
    "vendor/py_launch.cjs": "384f5dd532076062f7833038764e9a62322c387c",
    "vendor/fleet_check.py": "81f6aea6231d4490e581443fdeb43a09e6018ff0",
}


def test_vendor_lock_matches_checked_in_bytes() -> None:
    fleet.check_lock(ROOT)


def test_launcher_pin_uses_canonical_first_parent_source():
    rows = fleet.check_lock(ROOT, commits=ACCEPTED)
    for destination in ACCEPTED:
        assert (rows[destination]["repository"], rows[destination]["source"]) == (fleet.PARENT, Path(destination).name)


# Local rollout contract; adopted vendor locks are deliberately independent.
def test_parent_workflow_references_use_reviewed_rollout_commit():
    pins = fleet.parent_refs(ROOT)
    assert pins, "shared parent references must remain observable"
    assert set(pins) == {PARENT_WORKFLOW_PIN}, pins


# Every executable remote Actions reference must identify immutable code (full SHA and version comment).
@pytest.mark.parametrize("path", sorted((ROOT / ".github/workflows").glob("*.yml")), ids=lambda p: p.name)
def test_all_remote_workflow_actions_and_reusable_jobs_pin_full_sha(path):
    assert fleet.check_actions(ROOT, workflow=path.name), f"{path.name}: no remote references inspected"


# Every job that sets up Node carries the fleet's Node 26 floor guard (setup of 26 plus the executed bash guard).
@pytest.mark.parametrize("workflow, job", [
    ("ci.yml", "node-browserless"), ("ci.yml", "node"), ("ci.yml", "python"), ("ci.yml", "python-browser"),
    ("ci.yml", "vendor-locked"), ("runtime-compatibility.yml", "development-floor")])
def test_node_jobs_keep_the_node_26_floor(workflow, job):
    fleet.workflow_job(ROOT, job, workflow, node_floor=26)


# Both staging steps stage exactly the locked bytes with the pinned parent tool and fail closed.
def test_canonical_staging_preserves_locked_bytes():
    if not (ROOT / ".vendor-sync-tools/vendor_stage.py").is_file() and not os.environ.get("CI"):
        pytest.skip(".vendor-sync-tools not available locally (CI still requires it)")
    commands = fleet.vendor_staging(ROOT)
    assert len(commands) == 2 and all("--kind locked" in command for command in commands), commands


# Behavioral regressions for the source-identical shared parent launcher (discovery, sitecustomize, signals).
def test_vendored_launcher_contract():
    if not fleet.node(ci_jobs=None):
        pytest.skip("Node 26+ is not available (set NODE)")
    fleet.launcher_contract(ROOT, "BTK_PYTHON")
