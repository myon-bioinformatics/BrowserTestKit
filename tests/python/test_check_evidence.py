from __future__ import annotations

import importlib.util, json, struct, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "check_evidence.py"
spec = importlib.util.spec_from_file_location("check_evidence", SCRIPT)
check_evidence = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(check_evidence)

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
    return check_evidence.check(directory, pattern, expected)


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
    cli = [sys.executable, "-S", str(SCRIPT), str(tmp_path), "--expect"]
    passed = subprocess.run([*cli, "chromium"], capture_output=True, text=True)
    assert passed.returncode == 0 and passed.stdout.startswith("ok chromium: ")
    failed = subprocess.run([*cli, "chromium,firefox"], capture_output=True, text=True)
    assert failed.returncode == 1
    assert "error: missing complete evidence for project(s): firefox" in failed.stderr
    usage = subprocess.run([*cli, " , "], capture_output=True, text=True)
    assert usage.returncode == 2
