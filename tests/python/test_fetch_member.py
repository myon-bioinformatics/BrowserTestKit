"""fetch_member: integrity first, then only the named members; no traversal; fixed host list."""
import base64
import hashlib
import io
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import fetch_member  # noqa: E402


def make_archive(path: Path, members: dict) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, (body, mode) in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            info.mode = mode
            archive.addfile(info, io.BytesIO(body))
    data = buffer.getvalue()
    path.write_bytes(data)
    return data


def sri(data: bytes) -> str:
    return "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()


def test_extracts_only_the_named_member_and_keeps_it_executable(tmp_path):
    archive = tmp_path / "pkg.tgz"
    data = make_archive(archive, {
        "package/bin/node": (b"#!/bin/sh\necho v24\n", 0o755),
        "package/README": (b"not needed", 0o644),
    })
    result = fetch_member.fetch_members(str(archive), sri(data), ["package/bin/node"], str(tmp_path / "out"))
    assert result["ok"] is True
    extracted = tmp_path / "out" / "package" / "bin" / "node"
    assert extracted.read_bytes() == b"#!/bin/sh\necho v24\n"
    assert extracted.stat().st_mode & 0o111
    assert not (tmp_path / "out" / "package" / "README").exists()


def test_integrity_mismatch_extracts_nothing(tmp_path):
    archive = tmp_path / "pkg.tgz"
    make_archive(archive, {"package/bin/node": (b"x", 0o755)})
    wrong = "sha256-" + base64.b64encode(b"0" * 32).decode()
    with pytest.raises(fetch_member.FetchError, match="integrity"):
        fetch_member.fetch_members(str(archive), wrong, ["package/bin/node"], str(tmp_path / "out"))
    assert not (tmp_path / "out").exists()


def test_missing_member_and_traversal_are_refused(tmp_path):
    archive = tmp_path / "pkg.tgz"
    data = make_archive(archive, {"package/bin/node": (b"x", 0o755)})
    with pytest.raises(fetch_member.FetchError, match="not found"):
        fetch_member.fetch_members(str(archive), sri(data), ["package/bin/npm"], str(tmp_path / "out"))
    with pytest.raises(fetch_member.FetchError, match="plain relative"):
        fetch_member.fetch_members(str(archive), sri(data), ["../escape"], str(tmp_path / "out"))
    with pytest.raises(fetch_member.FetchError, match="plain relative"):
        fetch_member.fetch_members(str(archive), sri(data), ["/etc/passwd"], str(tmp_path / "out"))


def test_hosts_outside_the_allowlist_are_refused():
    with pytest.raises(fetch_member.FetchError, match="not allowed"):
        fetch_member.read_archive("https://example.com/pkg.tgz")
    with pytest.raises(fetch_member.FetchError):
        fetch_member.read_archive("http://registry.npmjs.org/pkg.tgz")


def test_integrity_formats_are_parsed_or_refused():
    assert fetch_member.parse_integrity("sha256-" + base64.b64encode(b"a" * 32).decode())[0] == "sha256"
    assert fetch_member.parse_integrity("ab" * 32)[0] == "sha256"
    with pytest.raises(fetch_member.FetchError):
        fetch_member.parse_integrity("md5-abc")


def test_a_named_part_from_the_lock_file_resolves_source_integrity_and_member(tmp_path):
    archive = tmp_path / "node.tgz"
    data = make_archive(archive, {"package/bin/node": (b"#!/bin/sh\necho ok\n", 0o755)})
    lock = tmp_path / "parts.lock.json"
    lock.write_text(__import__("json").dumps({"schema": "btk-parts-lock/1", "parts": {
        "node": {"source": str(archive), "integrity": sri(data), "member": "package/bin/node"},
        "chromium": {"source": "environment", "integrity": None, "member": None},
    }}), encoding="utf-8")
    args = fetch_member.build_parser().parse_args([
        "--lock", str(lock), "--part", "node", "--dest", str(tmp_path / "out")])
    source, integrity, members = fetch_member.resolve(args)
    result = fetch_member.fetch_members(source, integrity, members, str(tmp_path / "out"))
    assert result["ok"] and (tmp_path / "out" / "package" / "bin" / "node").exists()
    with pytest.raises(fetch_member.FetchError, match="cannot be fetched"):
        fetch_member.load_part(str(lock), "chromium")
    with pytest.raises(fetch_member.FetchError, match="no part named"):
        fetch_member.load_part(str(lock), "firefox")


def test_checked_in_node_part_meets_workspace_and_launcher_floor():
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    floor = int((root / ".nvmrc").read_text().strip())
    node = json.loads((root / "parts.lock.json").read_text())["parts"]["node"]
    assert int(node["version"].split(".")[0]) >= floor
    assert int(node["minimum"].split(".")[0]) == floor
    assert node["maximum"] is None
    assert node["source"].endswith(f"node-linux-x64-{node['version']}.tgz")
    assert node["integrity"].startswith("sha512-")
