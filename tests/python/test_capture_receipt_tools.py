"""Exercise the adapters against each consumer's bundle shape, using real CLIs."""
import json, subprocess, sys
from pathlib import Path

import pytest
from test_check_capture_evidence import png, SHA

SCRIPTS = Path(__file__).resolve().parents[2] / "tools"


@pytest.mark.parametrize("names", [
    ["original.png", "roundtrip.png"],
    [f"ironmate-{i}.png" for i in range(6)],
    [f"web-ui-{i}.png" for i in range(6)],
    ["pixiv-pages-desktop.png", "repository-diagnostics-desktop.png", "pixiv-pages-mobile.png"],
    ["pixiv-live-desktop.png", "pixiv-live-mobile.png"],
])
def test_actual_bundle_can_be_sealed_and_all_four_corruptions_rejected(tmp_path, names):
    for name in names:
        (tmp_path / name).write_bytes(png())
    identity = ["--sha", SHA, "--run-id", "123", "--run-attempt", "2", "--captures", ",".join(names)]
    writer = subprocess.run([sys.executable, "-S", str(SCRIPTS / "write_capture_evidence.py"),
        str(tmp_path), "--project", "bundle", "--stage", "complete", *identity], capture_output=True, text=True)
    assert writer.returncode == 0, writer.stderr
    probe = subprocess.run([sys.executable, "-S", str(SCRIPTS / "probe_capture_rejections.py"),
        str(tmp_path), "--expect", "bundle", *identity], capture_output=True, text=True)
    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.count("correctly rejected:") == 4
    # Negative checks do not alter the source receipts/images.
    assert json.loads((tmp_path / "evidence.json").read_text())["stage"] == "complete"
    assert all((tmp_path / name).is_file() for name in names)


@pytest.mark.parametrize("stage", ["complete", "failed", "skipped"])
def test_absent_images_never_cover_required_success_and_old_receipt_is_replaced(tmp_path, stage):
    (tmp_path / "evidence.json").write_text('{"stage":"complete"}')
    writer = subprocess.run([sys.executable, "-S", str(SCRIPTS / "write_capture_evidence.py"), str(tmp_path),
        "--project", "bundle", "--captures", "required.png", "--sha", SHA,
        "--run-id", "123", "--run-attempt", "2", "--stage", stage], capture_output=True, text=True)
    assert (writer.returncode == 0) == (stage != "complete")
    assert json.loads((tmp_path / "evidence.json").read_text())["stage"] != "complete"
    checker = subprocess.run([sys.executable, "-S", str(SCRIPTS / "check_capture_evidence.py"), str(tmp_path),
        "--expect", "bundle", "--captures", "required.png", "--sha", SHA,
        "--run-id", "123", "--run-attempt", "2"], capture_output=True, text=True)
    assert checker.returncode == 1
