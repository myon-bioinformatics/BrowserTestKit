"""Fetch and verify a fresh locked snapshot before replacing checkout copies.

Download/verification failure leaves checked-in files available for CI evidence.
Placement is atomic per file, not a filesystem transaction across the batch.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def recreate(root, tool):
    root, tool = Path(root).resolve(), Path(tool).resolve()
    manifest = root / "vendor.lock.json"
    command = [sys.executable, "-S", str(tool)]
    subprocess.run(command + ["check", "--root", str(root), "--manifest", "vendor.lock.json"], check=True)
    with tempfile.TemporaryDirectory(prefix=".locked-vendor-", dir=root) as directory:
        staging = Path(directory)
        shutil.copyfile(manifest, staging / "vendor.lock.json")
        for mode in ("materialize", "check"):
            subprocess.run(command + [mode, "--root", str(staging), "--manifest", "vendor.lock.json"], check=True)
        # The canonical tool has validated all paths and both digests. Nothing
        # from the checkout can satisfy materialization in this empty staging tree.
        for entry in json.loads(manifest.read_text(encoding="utf-8"))["files"]:
            relative = entry["destination"]
            source, target = staging / relative, root / relative
            os.replace(source, target)
    subprocess.run(command + ["check", "--root", str(root), "--manifest", "vendor.lock.json"], check=True)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--tool", type=Path, default=Path(".vendor-sync-tools/vendor_sync.py"))
    args = parser.parse_args(argv)
    recreate(args.root, args.tool)


if __name__ == "__main__":
    raise SystemExit(main())
