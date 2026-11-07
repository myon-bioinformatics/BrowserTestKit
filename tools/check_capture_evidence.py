#!/usr/bin/env python3
"""Validate multi-capture receipts and provenance using only the standard library."""
from __future__ import annotations

import argparse, hashlib, importlib.util, json, re, sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("_btk_check_png", Path(__file__).with_name("check_png.py"))
assert _spec and _spec.loader
_png = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_png)


def _names(raw: str) -> list[str]:
    names = raw.split(",")
    if any(not name or name != name.strip() for name in names) or len(set(names)) != len(names):
        raise argparse.ArgumentTypeError("use distinct, nonempty comma-separated names")
    return names


def _sha(raw: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", raw):
        raise argparse.ArgumentTypeError("expected a full lowercase Git SHA")
    return raw


def check(directory: Path, pattern: str, expected: list[str], captures: list[str],
          sha: str, canonical=None, run_id=None, run_attempt=None) -> tuple[bool, list[str], list[str]]:
    """Require a whole capture set per project; validate every matching receipt/retry."""
    problems = []
    notes = []
    covered = set()
    if not expected or not captures or len(set(expected)) != len(expected) or len(set(captures)) != len(captures):
        return False, notes, ["expected projects and captures must be nonempty and distinct"]
    metadata = {}
    if canonical is not None:
        try:
            metadata = {"sha": canonical["head"]["sha"],
                        "committed_at": canonical["head"]["timestamp"],
                        "generated_at": canonical["generated_at"]}
            if metadata["sha"] != sha or any(not isinstance(v, str) or not v for v in metadata.values()):
                raise ValueError("canonical SHA/timestamps missing or inconsistent with expected SHA")
        except (KeyError, TypeError, ValueError) as exc:
            return False, notes, [f"invalid canonical metadata: {exc}"]
    if run_id is None or run_attempt is None:
        notes.append("run identity not fully checked; use a clean output directory for this attempt")
    try:
        receipts = sorted(directory.glob(pattern))
    except (ValueError, NotImplementedError) as exc:
        return False, notes, [f"invalid receipt pattern: {exc}"]
    root = directory.resolve()
    for path in receipts:
        try:
            path.resolve().relative_to(root)
            receipt = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(receipt, dict):
                raise ValueError("receipt must be an object")
            project = receipt.get("project")
            if not isinstance(project, str) or not project:
                raise ValueError("missing project")
            if receipt.get("stage", "complete") != "complete":
                notes.append(f"{path}: incomplete receipt ignored")
                continue
            if receipt.get("sha") != sha:
                raise ValueError("SHA does not match expected checkout")
            for key, value in metadata.items():
                if receipt.get(key) != value:
                    raise ValueError(f"{key} does not match canonical metadata")
            for key, value in (("run_id", run_id), ("run_attempt", run_attempt)):
                if value is not None and receipt.get(key) != value:
                    raise ValueError(f"{key} does not match expected identity")
            records = receipt.get("captures")
            if not isinstance(records, list) or not records:
                raise ValueError("captures must be a nonempty list")
            seen = set()
            for record in records:
                if not isinstance(record, dict):
                    raise ValueError("capture must be an object")
                filename = record.get("file")
                if not isinstance(filename, str) or not filename or filename in seen:
                    raise ValueError("capture file must be nonempty and unique")
                seen.add(filename)
                relative = Path(filename)
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("capture path must stay inside the receipt directory")
                image = (path.parent / relative).resolve()
                image.relative_to(path.parent.resolve())
                image.relative_to(root)
                data = image.read_bytes()
                width, height = _png.dimensions(data)
                if width < 1 or height < 1:
                    raise ValueError(f"{filename}: invalid PNG dimensions")
                if type(record.get("bytes")) is not int or record["bytes"] != len(data):
                    raise ValueError(f"{filename}: byte count mismatch")
                if record.get("sha256") != hashlib.sha256(data).hexdigest():
                    raise ValueError(f"{filename}: SHA-256 mismatch")
                for key, value in (("width", width), ("height", height)):
                    if key in record and (type(record[key]) is not int or record[key] != value):
                        raise ValueError(f"{filename}: {key} mismatch")
            missing = set(captures) - seen
            if missing:
                raise ValueError("missing captures: " + ", ".join(sorted(missing)))
            covered.add(project)
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            problems.append(f"{path}: {exc}")
    for project in expected:
        if project not in covered:
            problems.append(f"missing complete capture set for project: {project}")
    extra = covered - set(expected)
    if extra:
        notes.append("additional validated projects: " + ", ".join(sorted(extra)))
    return not problems, notes, problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--pattern", default="**/evidence.json")
    parser.add_argument("--expect", required=True, type=_names, help="required projects")
    parser.add_argument("--captures", required=True, type=_names, help="required capture file names")
    parser.add_argument("--sha", required=True, type=_sha, help="tested checkout SHA")
    parser.add_argument("--canonical", type=Path, help="JSON with head.sha, head.timestamp, generated_at")
    parser.add_argument("--run-id", help="exact receipt run_id string")
    parser.add_argument("--run-attempt", help="exact receipt run_attempt string")
    args = parser.parse_args(argv)
    try:
        canonical = json.loads(args.canonical.read_text(encoding="utf-8")) if args.canonical else None
        if args.canonical and not isinstance(canonical, dict):
            raise ValueError("canonical metadata must be an object")
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"invalid canonical metadata: {exc}", file=sys.stderr)
        return 1
    ok, notes, problems = check(args.directory, args.pattern, args.expect, args.captures,
                                args.sha, canonical, args.run_id, args.run_attempt)
    for note in notes[:20]:
        print("note: " + note)
    for problem in problems[:20]:
        print("error: " + problem, file=sys.stderr)
    if len(notes) > 20 or len(problems) > 20:
        print(f"{len(notes)} notes, {len(problems)} errors; output limited to 20 each")
    print("capture evidence: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
