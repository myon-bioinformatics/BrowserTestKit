"""Fetch one archive from an allowed host, verify its integrity, and extract only named members.

Stdlib only. The archive is checked before any member is read, and only the members named on the
command line are written. Nothing is built or installed. Allowed hosts are fixed in this file.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import io
import json
import os
import sys
import tarfile
import urllib.parse
import urllib.request
from pathlib import Path

ALLOWED_HOSTS = {"registry.npmjs.org", "nodejs.org", "codeload.github.com"}
MAX_BYTES = 200 * 1024 * 1024


class FetchError(Exception):
    pass


def parse_integrity(text: str) -> tuple[str, bytes]:
    """Accept SRI (sha512-BASE64, sha256-BASE64) or a bare 64-character sha256 hex digest."""
    if text.startswith(("sha512-", "sha256-")):
        algorithm, _, encoded = text.partition("-")
        try:
            return algorithm, base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise FetchError("integrity is not valid base64") from exc
    if len(text) == 64:
        try:
            return "sha256", bytes.fromhex(text)
        except ValueError as exc:
            raise FetchError("integrity is not a sha256 hex digest") from exc
    raise FetchError("integrity must be sha512-BASE64, sha256-BASE64, or a sha256 hex digest")


def check_member_name(name: str) -> str:
    path = Path(name)
    if not name or path.is_absolute() or ".." in path.parts:
        raise FetchError(f"member name is not a plain relative path: {name}")
    return name


def read_archive(source: str) -> bytes:
    parts = urllib.parse.urlsplit(source)
    if parts.scheme == "https":
        if parts.hostname not in ALLOWED_HOSTS:
            raise FetchError(f"host is not allowed: {parts.hostname}")
        with urllib.request.urlopen(source, timeout=60) as response:
            data = response.read(MAX_BYTES + 1)
    elif parts.scheme == "" and os.path.isfile(source):
        # A local file path is accepted for tests and for archives already on disk.
        data = Path(source).read_bytes()
    else:
        raise FetchError("source must be an https URL on an allowed host, or a local file")
    if len(data) > MAX_BYTES:
        raise FetchError("archive is larger than the 200 MiB limit")
    return data


def fetch_members(source: str, integrity: str, members: list[str], dest: str) -> dict:
    algorithm, expected = parse_integrity(integrity)
    if not members:
        raise FetchError("name at least one member to extract")
    names = [check_member_name(name) for name in members]
    blob = read_archive(source)
    digest = hashlib.new(algorithm, blob).digest()
    if digest != expected:
        raise FetchError("archive does not match the expected integrity; nothing was extracted")
    target = Path(dest)
    target.mkdir(parents=True, exist_ok=True)
    written = []
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:*") as archive:
        for name in names:
            try:
                member = archive.getmember(name)
            except KeyError as exc:
                raise FetchError(f"member not found in archive: {name}") from exc
            if not member.isfile():
                raise FetchError(f"member is not a regular file: {name}")
            out = target / name
            out.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source_file:
                out.write_bytes(source_file.read())
            if member.mode & 0o111:
                out.chmod(0o755)
            written.append(str(out))
    return {"ok": True, "algorithm": algorithm, "files": written}


def load_part(lock_path: str, name: str) -> dict:
    """One part from parts.lock.json: its source, integrity, and the archive member it needs."""
    try:
        lock = json.loads(Path(lock_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FetchError(f"cannot read lock file: {lock_path}") from exc
    if lock.get("schema") != "btk-parts-lock/1":
        raise FetchError("lock file schema is not btk-parts-lock/1")
    part = (lock.get("parts") or {}).get(name)
    if not isinstance(part, dict):
        raise FetchError(f"no part named {name} in the lock file")
    if not part.get("integrity") or not part.get("source") or not part.get("member"):
        raise FetchError(f"part {name} cannot be fetched: it has no source, integrity, or member")
    return part


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fetch_member.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("source", nargs="?", help="https URL on an allowed host, or a local archive path")
    parser.add_argument("--integrity", help="sha512-BASE64 or sha256-BASE64 from the registry")
    parser.add_argument("--member", action="append", default=[], help="path inside the archive (repeatable)")
    parser.add_argument("--lock", help="parts.lock.json; use with --part instead of source, integrity, and member")
    parser.add_argument("--part", help="part name in the lock file, for example node")
    parser.add_argument("--dest", required=True, help="directory to extract into")
    return parser


def resolve(args) -> tuple[str, str, list[str]]:
    if args.lock or args.part:
        if not (args.lock and args.part):
            raise FetchError("--lock and --part go together")
        part = load_part(args.lock, args.part)
        return part["source"], part["integrity"], [part["member"]]
    if not (args.source and args.integrity and args.member):
        raise FetchError("give source, --integrity, and --member, or use --lock with --part")
    return args.source, args.integrity, args.member


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        source, integrity, members = resolve(args)
        payload = fetch_members(source, integrity, members, args.dest)
    except (FetchError, OSError, tarfile.TarError) as exc:
        sys.stdout.write(json.dumps({"ok": False, "error": str(exc)}) + "\n")
        return 2
    sys.stdout.write(json.dumps(payload, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
