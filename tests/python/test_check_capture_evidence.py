import hashlib, importlib.util, json, struct, subprocess, sys, zlib
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "check_capture_evidence.py"
spec = importlib.util.spec_from_file_location("check_capture_evidence", SCRIPT)
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)
SHA = "a" * 40
CANONICAL = {"head": {"sha": SHA, "timestamp": "2026-09-30T01:00:00Z"},
             "generated_at": "2026-09-30T02:00:00Z"}
FILES = ["home.png", "build-diagnostics.png"]


def png(width=1, height=1):
    def chunk(name, data):
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">II5B", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\0\xff\0\0\xff")) + chunk(b"IEND", b""))


def receipt(root, project="chromium", attempt="", **changes):
    directory = root / (project + attempt)
    directory.mkdir(parents=True)
    records = []
    for filename in FILES:
        data = png()
        (directory / filename).write_bytes(data)
        records.append({"file": filename, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    value = {"project": project, "sha": SHA, "committed_at": CANONICAL["head"]["timestamp"],
             "generated_at": CANONICAL["generated_at"], "captures": records, **changes}
    save(directory, value)
    return directory, value


def save(directory, value):
    (directory / "evidence.json").write_text(json.dumps(value), encoding="utf-8")


def check(root, **kwargs):
    return validator.check(root, "**/evidence.json", ["chromium", "webkit"], FILES, SHA,
                           canonical=CANONICAL, **kwargs)


def test_matrix_retries_and_flutter_legacy_shape(tmp_path):
    for project in ["chromium", "webkit"]:
        receipt(tmp_path, project)
        receipt(tmp_path, project, "-retry1")
    ok, notes, problems = check(tmp_path)
    assert ok and not problems
    assert "run identity" in notes[0]


@pytest.mark.parametrize("field,value", [("sha", "b" * 40), ("committed_at", "stale"),
                                        ("generated_at", "stale"), ("captures", None),
                                        ("project", None)])
def test_bad_receipt_is_not_covered(tmp_path, field, value):
    receipt(tmp_path, **{field: value}) if field != "project" else receipt(tmp_path, project="chromium")
    if field == "project":
        directory = tmp_path / "chromium"
        data = json.loads((directory / "evidence.json").read_text())
        data["project"] = value
        save(directory, data)
    assert not check(tmp_path)[0]


@pytest.mark.parametrize("change", ["missing", "duplicate", "hash", "size", "bool-size", "width", "corrupt", "zero", "escape", "absolute", "symlink"])
def test_invalid_capture_cannot_hide_behind_valid_retry(tmp_path, change):
    for project in ["chromium", "webkit"]:
        receipt(tmp_path, project)
    directory, value = receipt(tmp_path, "chromium", "-retry1")
    capture = value["captures"][0]
    if change == "missing":
        value["captures"].pop()
    elif change == "duplicate":
        value["captures"].append(dict(capture))
    elif change == "hash":
        capture["sha256"] = "0" * 64
    elif change == "size":
        capture["bytes"] += 1
    elif change == "bool-size":
        capture["bytes"] = True
    elif change == "width":
        capture["width"] = 2
    elif change in {"corrupt", "zero"}:
        data = b"broken" if change == "corrupt" else png(width=0)
        (directory / FILES[0]).write_bytes(data)
        capture.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    else:
        outside = tmp_path / "outside.png"
        outside.write_bytes(png())
        if change == "escape":
            capture["file"] = "../outside.png"
        elif change == "absolute":
            capture["file"] = str(outside)
        else:
            (directory / FILES[0]).unlink()
            (directory / FILES[0]).symlink_to(outside)
    save(directory, value)
    ok, _, problems = check(tmp_path)
    assert not ok and any("retry1" in problem for problem in problems)


def test_partial_receipts_do_not_combine_into_complete_scenario_set(tmp_path):
    for attempt, index in [("", 0), ("-retry", 1)]:
        directory, value = receipt(tmp_path, attempt=attempt)
        value["captures"] = [value["captures"][index]]
        save(directory, value)
    assert not check(tmp_path)[0]


def test_failure_receipt_ignored_and_missing_project_fails(tmp_path):
    receipt(tmp_path)
    receipt(tmp_path, "webkit", stage="failed")
    ok, notes, problems = check(tmp_path)
    assert not ok and any("incomplete" in note for note in notes)
    assert any("webkit" in problem for problem in problems)


def test_run_identity(tmp_path):
    for project in ["chromium", "webkit"]:
        receipt(tmp_path, project, run_id="123", run_attempt="2")
    assert check(tmp_path, run_id="123", run_attempt="2")[0]
    assert not check(tmp_path, run_id="123", run_attempt="1")[0]
    assert not check(tmp_path, run_id="124", run_attempt="2")[0]


@pytest.mark.parametrize("canonical", [None, {}, [], {**CANONICAL, "head": {"sha": "b" * 40}}])
def test_invalid_canonical_shape(tmp_path, canonical):
    receipt(tmp_path)
    # None deliberately disables canonical comparison.
    assert validator.check(tmp_path, "**/evidence.json", ["chromium"], FILES, SHA, canonical)[0] is (canonical is None)


def test_cli_without_site_packages_and_exit_codes(tmp_path):
    for project in ["chromium", "webkit"]:
        receipt(tmp_path, project)
    args = [sys.executable, "-S", str(SCRIPT), str(tmp_path), "--expect", "chromium,webkit",
            "--captures", ",".join(FILES), "--sha", SHA]
    result = subprocess.run(args, capture_output=True, text=True)
    assert result.returncode == 0 and "PASS" in result.stdout
    (tmp_path / "webkit" / FILES[0]).unlink()
    assert subprocess.run(args, capture_output=True).returncode == 1
    assert subprocess.run(args[:-1] + ["short"], capture_output=True).returncode == 2
    canonical = tmp_path / "canonical.json"
    canonical.write_text("null")
    result = subprocess.run(args + ["--canonical", str(canonical)], capture_output=True, text=True)
    assert result.returncode == 1 and "must be an object" in result.stderr


@pytest.mark.parametrize("value", [[], "text", {"captures": []}])
def test_malformed_receipt(tmp_path, value):
    tmp_path.joinpath("evidence.json").write_text(json.dumps(value))
    assert not check(tmp_path)[0]
