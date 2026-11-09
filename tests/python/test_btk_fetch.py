"""btk fetch: integrity first, then only the named members; no traversal; fixed host list."""
import base64, hashlib, io, sys, tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import btk  # noqa: E402


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
    result = btk.fetch_members(str(archive), sri(data), ["package/bin/node"], str(tmp_path / "out"))
    assert result["ok"] is True
    extracted = tmp_path / "out" / "package" / "bin" / "node"
    assert extracted.read_bytes() == b"#!/bin/sh\necho v24\n"
    assert extracted.stat().st_mode & 0o111
    assert not (tmp_path / "out" / "package" / "README").exists()


def test_integrity_mismatch_extracts_nothing(tmp_path):
    archive = tmp_path / "pkg.tgz"
    make_archive(archive, {"package/bin/node": (b"x", 0o755)})
    wrong = "sha256-" + base64.b64encode(b"0" * 32).decode()
    with pytest.raises(btk.FetchError, match="integrity"):
        btk.fetch_members(str(archive), wrong, ["package/bin/node"], str(tmp_path / "out"))
    assert not (tmp_path / "out").exists()


def test_missing_member_and_traversal_are_refused(tmp_path):
    archive = tmp_path / "pkg.tgz"
    data = make_archive(archive, {"package/bin/node": (b"x", 0o755)})
    with pytest.raises(btk.FetchError, match="not found"):
        btk.fetch_members(str(archive), sri(data), ["package/bin/npm"], str(tmp_path / "out"))
    with pytest.raises(btk.FetchError, match="plain relative"):
        btk.fetch_members(str(archive), sri(data), ["../escape"], str(tmp_path / "out"))
    with pytest.raises(btk.FetchError, match="plain relative"):
        btk.fetch_members(str(archive), sri(data), ["/etc/passwd"], str(tmp_path / "out"))


def test_hosts_outside_the_allowlist_are_refused():
    with pytest.raises(btk.FetchError, match="not allowed"):
        btk.read_archive("https://example.com/pkg.tgz")
    with pytest.raises(btk.FetchError):
        btk.read_archive("http://registry.npmjs.org/pkg.tgz")


def test_integrity_formats_are_parsed_or_refused():
    assert btk.parse_integrity("sha256-" + base64.b64encode(b"a" * 32).decode())[0] == "sha256"
    assert btk.parse_integrity("ab" * 32)[0] == "sha256"
    with pytest.raises(btk.FetchError):
        btk.parse_integrity("md5-abc")


def test_a_named_part_from_the_lock_file_resolves_source_integrity_and_member(tmp_path):
    archive = tmp_path / "node.tgz"
    data = make_archive(archive, {"package/bin/node": (b"#!/bin/sh\necho ok\n", 0o755)})
    lock = tmp_path / "parts.lock.json"
    lock.write_text(__import__("json").dumps({"schema": "btk-parts-lock/1", "parts": {
        "node": {"source": str(archive), "integrity": sri(data), "member": "package/bin/node"},
        "chromium": {"source": "environment", "integrity": None, "member": None},
    }}), encoding="utf-8")
    args = btk.fetch_parser().parse_args([
        "--lock", str(lock), "--part", "node", "--dest", str(tmp_path / "out")])
    source, integrity, members = btk.resolve(args)
    result = btk.fetch_members(source, integrity, members, str(tmp_path / "out"))
    assert result["ok"] and (tmp_path / "out" / "package" / "bin" / "node").exists()
    with pytest.raises(btk.FetchError, match="cannot be fetched"):
        btk.load_part(str(lock), "chromium")
    with pytest.raises(btk.FetchError, match="no part named"):
        btk.load_part(str(lock), "firefox")
