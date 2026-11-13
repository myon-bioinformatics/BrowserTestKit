"""📸 btk.py's evidence commands, which were separate tools: check-png (check_png.py), check-evidence (check_evidence.py),
check-capture-evidence / write-capture-evidence (check_capture_evidence.py, write_capture_evidence.py) and
probe-capture-rejections (probe_capture_rejections.py). The CLIs run under ``python -S``."""
from __future__ import annotations

import hashlib, importlib.util, json, struct, subprocess, sys, zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BTK = ROOT / "tools" / "btk.py"
spec = importlib.util.spec_from_file_location("btk", BTK)
btk = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(btk)


# ---- check-png ----------------------------------------------------------------------------------------------------

def test_png_header_dimensions() -> None:
    assert btk.png_dimensions(minimal_png()) == (390, 844)

def test_rejects_non_png() -> None:
    with pytest.raises(ValueError):
        btk.png_dimensions(b"not an image")

def test_rejects_wrong_ihdr_length() -> None:
    data = btk.PNG_SIGNATURE + struct.pack(">I", 12) + b"IHDR" + b"x" * 32 + b"IEND" + b"xxxx"
    with pytest.raises(ValueError, match="IHDR"):
        btk.png_dimensions(data)

def test_rejects_missing_iend() -> None:
    with pytest.raises(ValueError, match="IEND"):
        btk.png_dimensions(minimal_png()[:-8] + b"NOPE1234")


def test_check_png_cli_prints_dimensions(tmp_path):
    (tmp_path / "a.png").write_bytes(minimal_png())
    done = subprocess.run([sys.executable, "-S", str(BTK), "check-png", str(tmp_path / "a.png")], capture_output=True, text=True)
    assert done.returncode == 0 and done.stdout.strip().endswith(": png 390x844 45 bytes")
    assert subprocess.run([sys.executable, "-S", str(BTK), "check-png"], capture_output=True).returncode == 2


# ---- check-evidence: one PNG per project ---------------------------------------------------------------------------

NODE_PROJECTS = ["chromium", "firefox", "webkit", "mobile-chromium", "mobile-webkit"]


def minimal_png(width: int = 390, height: int = 844) -> bytes:
    return (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height)
            + b"\x08\x06\x00\x00\x00" + b"xxxx" + struct.pack(">I", 0) + b"IEND" + b"xxxx")


def node_evidence(root: Path, project: str, *, attempt: str = "", png: bytes | None = None, **extra) -> Path:
    directory = root / f"smoke-shared-fixture-{project}{attempt}"
    directory.mkdir(parents=True)
    (directory / "evidence.png").write_bytes(minimal_png() if png is None else png)
    meta = {"runtime": "node", "browser": project.split("-")[-1], "project": project, "stage": "complete",
            "artifact": "evidence.png", **extra}
    (directory / "evidence.json").write_text(json.dumps(meta) + "\n", encoding="utf-8")
    return directory


def run(directory: Path, pattern: str, expected: list) -> tuple:
    return btk.check_evidence(directory, pattern, expected)


def test_every_expected_node_project_passes(tmp_path: Path) -> None:
    for project in NODE_PROJECTS:
        node_evidence(tmp_path, project)
    (tmp_path / ".last-run.json").write_text('{"status": "passed"}', encoding="utf-8")
    ok, notes, problems = run(tmp_path, "**/evidence.json", NODE_PROJECTS)
    assert problems == [] and notes == []
    assert len(ok) == 5 and all("png 390x844" in line for line in ok)


def test_missing_desktop_evidence_fails_even_when_mobile_evidence_exists(tmp_path: Path) -> None:
    node_evidence(tmp_path, "mobile-chromium")
    node_evidence(tmp_path, "mobile-webkit")
    _, _, problems = run(tmp_path, "**/evidence.json", ["chromium", "mobile-chromium", "webkit", "mobile-webkit"])
    assert problems == ["missing complete evidence for project(s): chromium, webkit"]


def test_every_retry_artifact_is_validated(tmp_path: Path) -> None:
    node_evidence(tmp_path, "chromium")
    node_evidence(tmp_path, "chromium", attempt="-retry1")
    ok, _, problems = run(tmp_path, "**/evidence.json", ["chromium"])
    assert problems == [] and len(ok) == 2
    node_evidence(tmp_path, "chromium", attempt="-retry2", png=b"not a png")
    _, _, problems = run(tmp_path, "**/evidence.json", ["chromium"])
    assert len(problems) == 1 and "retry2" in problems[0] and "not a valid PNG header" in problems[0]


def test_complete_record_without_its_png_fails(tmp_path: Path) -> None:
    directory = node_evidence(tmp_path, "firefox")
    (directory / "evidence.png").unlink()
    _, _, problems = run(tmp_path, "**/evidence.json", ["firefox"])
    assert "evidence.png for firefox" in problems[0]
    assert problems[1] == "missing complete evidence for project(s): firefox"


def test_python_layout_failure_records_do_not_count(tmp_path: Path) -> None:
    (tmp_path / "webkit-desktop.png").write_bytes(minimal_png(1280, 720))
    (tmp_path / "webkit-desktop.json").write_text(json.dumps({
        "runtime": "python", "project": "webkit-desktop", "stage": "complete", "artifact": "webkit-desktop.png",
        "width": 1280, "height": 720}), encoding="utf-8")
    (tmp_path / "firefox-mobile.json").write_text(json.dumps({
        "runtime": "python", "project": "firefox-mobile", "stage": "navigate", "error": "TimeoutError"}),
        encoding="utf-8")
    (tmp_path / "firefox-mobile-failure.png").write_bytes(minimal_png())
    ok, notes, problems = run(tmp_path, "*.json", ["webkit-desktop", "firefox-mobile"])
    assert ok == [f"ok webkit-desktop: {tmp_path / 'webkit-desktop.png'} png 1280x720"]
    assert notes == [f"{tmp_path / 'firefox-mobile.json'}: firefox-mobile attempt ended at stage 'navigate'; "
                     "not counted"]
    assert problems == ["missing complete evidence for project(s): firefox-mobile"]


def test_recorded_dimensions_must_match_the_png(tmp_path: Path) -> None:
    node_evidence(tmp_path, "webkit", width=100, height=200)
    _, _, problems = run(tmp_path, "**/evidence.json", ["webkit"])
    assert "metadata says 100x200, PNG header says 390x844" in problems[0]


def test_metadata_without_project_or_unreadable_fails(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "evidence.json").write_text('{"stage": "complete"}', encoding="utf-8")
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "evidence.json").write_text("{not json", encoding="utf-8")
    _, _, problems = run(tmp_path, "**/evidence.json", ["chromium"])
    assert 'metadata has no "project"' in problems[0]
    assert "unreadable metadata" in problems[1]


def test_project_names_match_exactly_and_extras_are_noted(tmp_path: Path) -> None:
    node_evidence(tmp_path, "mobile-chromium")
    _, notes, problems = run(tmp_path, "**/evidence.json", ["chromium"])
    assert problems == ["missing complete evidence for project(s): chromium"]
    assert notes == ["evidence for project(s) not in --expect: mobile-chromium"]


def test_missing_directory_reports_every_expected_project(tmp_path: Path) -> None:
    _, _, problems = run(tmp_path / "absent", "*.json", ["chromium-desktop", "webkit-mobile"])
    assert problems == [f"{tmp_path / 'absent'}: no such directory",
                        "missing complete evidence for project(s): chromium-desktop, webkit-mobile"]


def test_cli_exit_codes_without_site_packages(tmp_path: Path) -> None:
    node_evidence(tmp_path, "chromium")
    cli = [sys.executable, "-S", str(BTK), "check-evidence", str(tmp_path), "--expect"]
    passed = subprocess.run([*cli, "chromium"], capture_output=True, text=True)
    assert passed.returncode == 0 and passed.stdout.startswith("ok chromium: ")
    failed = subprocess.run([*cli, "chromium,firefox"], capture_output=True, text=True)
    assert failed.returncode == 1
    assert "error: missing complete evidence for project(s): firefox" in failed.stderr
    usage = subprocess.run([*cli, " , "], capture_output=True, text=True)
    assert usage.returncode == 2


# ---- check-capture-evidence: multi-capture receipts ----------------------------------------------------------------

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
    return btk.check_capture_evidence(root, "**/evidence.json", ["chromium", "webkit"], FILES, SHA,
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
    assert btk.check_capture_evidence(tmp_path, "**/evidence.json", ["chromium"], FILES, SHA, canonical)[0] is (canonical is None)


def test_cli_without_site_packages_and_exit_codes(tmp_path):
    for project in ["chromium", "webkit"]:
        receipt(tmp_path, project)
    args = [sys.executable, "-S", str(BTK), "check-capture-evidence", str(tmp_path), "--expect", "chromium,webkit",
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


# ---- write-capture-evidence and probe-capture-rejections against each consumer's bundle shape -------------------------

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
    writer = subprocess.run([sys.executable, "-S", str(BTK), "write-capture-evidence",
        str(tmp_path), "--project", "bundle", "--stage", "complete", *identity], capture_output=True, text=True)
    assert writer.returncode == 0, writer.stderr
    probe = subprocess.run([sys.executable, "-S", str(BTK), "probe-capture-rejections",
        str(tmp_path), "--expect", "bundle", *identity], capture_output=True, text=True)
    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.count("correctly rejected:") == 4
    # Negative checks do not alter the source receipts/images.
    assert json.loads((tmp_path / "evidence.json").read_text())["stage"] == "complete"
    assert all((tmp_path / name).is_file() for name in names)


@pytest.mark.parametrize("stage", ["complete", "failed", "skipped"])
def test_absent_images_never_cover_required_success_and_old_receipt_is_replaced(tmp_path, stage):
    (tmp_path / "evidence.json").write_text('{"stage":"complete"}')
    writer = subprocess.run([sys.executable, "-S", str(BTK), "write-capture-evidence", str(tmp_path),
        "--project", "bundle", "--captures", "required.png", "--sha", SHA,
        "--run-id", "123", "--run-attempt", "2", "--stage", stage], capture_output=True, text=True)
    assert (writer.returncode == 0) == (stage != "complete")
    assert json.loads((tmp_path / "evidence.json").read_text())["stage"] != "complete"
    checker = subprocess.run([sys.executable, "-S", str(BTK), "check-capture-evidence", str(tmp_path),
        "--expect", "bundle", "--captures", "required.png", "--sha", SHA,
        "--run-id", "123", "--run-attempt", "2"], capture_output=True, text=True)
    assert checker.returncode == 1
