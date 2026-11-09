#!/usr/bin/env python3
"""Seal a current-run capture bundle; failed/skipped bundles cannot cover success.

Call only after the owning application's capture/assertion step. Clear old output
before capture; this tool cannot tell whether an unchanged PNG was freshly taken.
"""
from __future__ import annotations
import argparse, hashlib, json, sys
from pathlib import Path

import check_capture_evidence as validator


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--receipt", default="evidence.json")
    parser.add_argument("--project", required=True)
    parser.add_argument("--captures", required=True, type=validator._names)
    parser.add_argument("--sha", required=True, type=validator._sha)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--stage", choices=("complete", "failed", "skipped"), required=True)
    parser.add_argument("--canonical", type=Path)
    args = parser.parse_args(argv)
    if Path(args.receipt).name != args.receipt or not args.run_id or not args.run_attempt or not args.project:
        parser.error("receipt must be a filename; project and run identity must be nonempty")
    args.directory.mkdir(parents=True, exist_ok=True)
    receipt = {"stage": args.stage, "project": args.project, "sha": args.sha,
               "run_id": args.run_id, "run_attempt": args.run_attempt, "captures": []}
    target = args.directory / args.receipt
    # Remove any previous complete receipt before reading this attempt's inputs.
    target.unlink(missing_ok=True)
    canonical = None
    try:
        if args.canonical:
            canonical = json.loads(args.canonical.read_text(encoding="utf-8"))
            receipt.update(committed_at=canonical["head"]["timestamp"], generated_at=canonical["generated_at"])
        for filename in args.captures:
            relative = Path(filename)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("capture must stay inside the receipt directory")
            image = (args.directory / relative).resolve()
            image.relative_to(args.directory.resolve())
            if args.stage != "complete" and not image.is_file():
                continue
            data = image.read_bytes()
            width, height = validator._png.dimensions(data)
            receipt["captures"].append({"file": filename, "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(), "width": width, "height": height})
        target.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        if args.stage == "complete":
            ok, _, errors = validator.check(args.directory, args.receipt, [args.project], args.captures,
                args.sha, canonical, args.run_id, args.run_attempt)
            if not ok:
                raise ValueError("; ".join(errors))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        receipt.update(stage="failed", error=str(exc))
        target.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        print(str(exc), file=sys.stderr)
        return 1
    print(f"capture receipt: {args.stage} ({args.project})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
