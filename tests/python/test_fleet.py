"""Fleet contracts through the parent's shared checks (vendor/fleet_check.py): vendor lock bytes and parent pins."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("browsertestkit_fleet_check", ROOT / "vendor/fleet_check.py")
fleet = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fleet)

PARENT_WORKFLOW_PIN = "9e3da7ae1846e9604ebc24bb5178f7de094a4e77"
ACCEPTED = {
    "vendor/py_launch.cjs": "384f5dd532076062f7833038764e9a62322c387c",
    "vendor/fleet_check.py": "6c29503cf17eda856766ddfdf4d66a5b0965a8b1",
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
