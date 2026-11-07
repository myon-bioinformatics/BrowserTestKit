#!/usr/bin/env python3
"""Check a real capture bundle, then require four mutated copies to be rejected."""
from __future__ import annotations
import argparse, json, shutil, tempfile
from pathlib import Path

import check_capture_evidence as validator


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--pattern", default="**/evidence.json")
    parser.add_argument("--expect", required=True, type=validator._names)
    parser.add_argument("--captures", required=True, type=validator._names)
    parser.add_argument("--sha", required=True, type=validator._sha)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    args = parser.parse_args(argv)
    def check(root):
        return validator.check(root, args.pattern, args.expect, args.captures,
                               args.sha, run_id=args.run_id, run_attempt=args.run_attempt)
    ok, _, errors = check(args.directory)
    if not ok:
        raise SystemExit("baseline failed: " + "; ".join(errors))
    for mutation in ("missing-image", "wrong-hash", "stale-run", "failed-receipt"):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "bundle"
            shutil.copytree(args.directory, root)
            paths = list(root.glob(args.pattern))
            for path in paths:
                receipt = json.loads(path.read_text(encoding="utf-8"))
                if receipt.get("stage") != "complete":
                    continue
                if mutation == "missing-image":
                    (path.parent / receipt["captures"][0]["file"]).unlink(missing_ok=True)
                elif mutation == "wrong-hash":
                    receipt["captures"][0]["sha256"] = "0" * 64
                elif mutation == "stale-run":
                    receipt["run_id"] = args.run_id + "-old"
                else:
                    receipt["stage"] = "failed"
                path.write_text(json.dumps(receipt), encoding="utf-8")
            if check(root)[0]:
                raise SystemExit("mutation unexpectedly accepted: " + mutation)
            print("correctly rejected: " + mutation)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
