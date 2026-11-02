"""Failure evidence survives fresh-download and verification errors."""
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("recreate_locked_vendor", ROOT / "tools/recreate_locked_vendor.py")
recreate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recreate)


@pytest.mark.parametrize("failure", [None, "materialize", "staging-check"])
def test_fresh_materialization_is_verified_before_any_checkout_replacement(tmp_path, monkeypatch, failure):
    root = tmp_path / "checkout"
    root.mkdir()
    files = {"scripts/helper.py": b"locked helper\n", "scripts/helper-LICENSE": b"locked license\n"}
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    lock = {"files": [{"destination": name} for name in files]}
    manifest = root / "vendor.lock.json"
    manifest.write_text(json.dumps(lock))
    original_inodes = {name: (root / name).stat().st_ino for name in files}
    calls = []

    def run(command, *, check):
        assert check is True
        mode = command[3]
        target = Path(command[command.index("--root") + 1])
        calls.append((mode, target == root))
        if target != root:
            assert {name: (root / name).read_bytes() for name in files} == files
            assert {name: (root / name).stat().st_ino for name in files} == original_inodes
        if mode == "materialize":
            assert target != root
            assert list(target.iterdir()) == [target / "vendor.lock.json"]
            if failure == mode:
                raise subprocess.CalledProcessError(2, command, stderr="HTTP Error 404")
            for name, data in files.items():
                path = target / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        elif target != root and failure == "staging-check":
            raise subprocess.CalledProcessError(2, command, stderr="digest mismatch")
        else:
            assert {name: (target / name).read_bytes() for name in files} == files

    monkeypatch.setattr(recreate.subprocess, "run", run)
    if failure:
        with pytest.raises(subprocess.CalledProcessError):
            recreate.recreate(root, tmp_path / "pinned-vendor_sync.py")
        assert {name: (root / name).stat().st_ino for name in files} == original_inodes
    else:
        recreate.recreate(root, tmp_path / "pinned-vendor_sync.py")
        assert calls == [("check", True), ("materialize", False), ("check", False), ("check", True)]
        assert all((root / name).stat().st_ino != original_inodes[name] for name in files)
    assert not list(root.glob(".locked-vendor-*"))
    assert {name: (root / name).read_bytes() for name in files} == files


def test_launcher_pin_uses_canonical_first_parent_source():
    lock = json.loads((ROOT / "vendor.lock.json").read_text())
    entry = next(e for e in lock["files"] if e["destination"] == "vendor/py_launch.cjs")
    assert entry["repository"] == "myon-bioinformatics/myon-bioinformatics"
    assert entry["source"] == "py_launch.cjs"
    assert entry["commit"] == entry["ref"] == "384f5dd532076062f7833038764e9a62322c387c"
