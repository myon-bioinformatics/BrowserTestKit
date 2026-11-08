from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import textwrap
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "image_diff"
GOLDEN = json.loads((FIXTURES / "golden.json").read_text(encoding="utf-8"))
SCRIPT = ROOT / "tools" / "image_diff.py"
PILLOW_MIN = ROOT / "vendor" / "pillow_min" / "pillow_min.py"
spec = importlib.util.spec_from_file_location("image_diff", SCRIPT)
image_diff = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(image_diff)

pm_spec = importlib.util.spec_from_file_location("pillow_min", PILLOW_MIN)
pillow_min = importlib.util.module_from_spec(pm_spec)
assert pm_spec.loader
pm_spec.loader.exec_module(pillow_min)


def run_image_diff(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *argv],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def test_identical_images_exit_zero() -> None:
    left, right = FIXTURES / "identical_a.png", FIXTURES / "identical_b.png"
    proc = run_image_diff(str(left), str(right))
    expected = GOLDEN["identical"]
    assert proc.returncode == expected["returncode"]
    assert proc.stdout.strip() == expected["stdout"]
    assert "changed=0" in proc.stdout
    assert "differs=False" in proc.stdout


def test_red_rect_diff_metrics() -> None:
    left, right = FIXTURES / "red_rect_a.png", FIXTURES / "red_rect_b.png"
    proc = run_image_diff(str(left), str(right))
    expected = GOLDEN["red_rect"]
    assert proc.returncode == expected["returncode"]
    assert proc.stdout.strip() == expected["stdout"]
    assert "changed=50" in proc.stdout
    assert "bbox=(20, 10, 30, 15)" in proc.stdout


def test_clip_excluding_rect_exit_zero() -> None:
    left, right = FIXTURES / "red_rect_a.png", FIXTURES / "red_rect_b.png"
    proc = run_image_diff(str(left), str(right), "--clip", "0,0,20,50")
    expected = GOLDEN["clip_exclude"]
    assert proc.returncode == expected["returncode"]
    assert proc.stdout.strip() == expected["stdout"]
    assert "changed=0" in proc.stdout


def test_viewport_width_scales_clip() -> None:
    left, right = FIXTURES / "viewport_a.png", FIXTURES / "viewport_b.png"
    proc = run_image_diff(
        str(left),
        str(right),
        "--clip",
        "0,0,390,60",
        "--viewport-width",
        "390",
    )
    expected = GOLDEN["viewport"]
    assert proc.returncode == expected["returncode"]
    assert proc.stdout.strip() == expected["stdout"]
    assert "changed=0" in proc.stdout


def test_tolerance_boundary() -> None:
    left, right = FIXTURES / "tolerance_a.png", FIXTURES / "tolerance_b.png"
    assert run_image_diff(str(left), str(right), "--tolerance", "5").returncode == GOLDEN["tol5"]["returncode"]
    assert run_image_diff(str(left), str(right), "--tolerance", "4").returncode == GOLDEN["tol4"]["returncode"]


def test_different_sizes_exit_one(tmp_path: Path) -> None:
    left, right = FIXTURES / "small_a.png", FIXTURES / "small_b.png"
    proc = run_image_diff(str(left), str(right))
    expected = GOLDEN["diff_size"]
    assert proc.returncode == expected["returncode"]
    assert proc.stdout.strip() == expected["stdout"]
    assert "same_size=False" in proc.stdout
    assert "size_a=(10, 10)" in proc.stdout
    assert "size_b=(11, 10)" in proc.stdout


def test_zip_member_success_and_missing(tmp_path: Path) -> None:
    archive = tmp_path / "evidence.zip"
    header_bytes = (FIXTURES / "zip_header.png").read_bytes()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("t/header.png", header_bytes)
    proc = run_image_diff(f"{archive}::t/header.png", f"{archive}::t/header.png")
    assert proc.returncode == 0
    missing = run_image_diff(f"{archive}::t/nope.png", f"{archive}::t/header.png")
    assert missing.returncode == 2
    assert "image_diff:" in missing.stderr


def test_nonexistent_file_exit_two(tmp_path: Path) -> None:
    proc = run_image_diff(str(tmp_path / "missing.png"), str(tmp_path / "also-missing.png"))
    assert proc.returncode == 2


def test_bad_zip_exit_two(tmp_path: Path) -> None:
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    proc = run_image_diff(f"{bad}::member.png", f"{bad}::member.png")
    assert proc.returncode == 2


@pytest.mark.parametrize(
    "clip",
    [
        "1,2,3",
        "a,b,c,d",
        "0,0,0,10",
        "0,0,10,-1",
    ],
)
def test_invalid_clip_exit_two(clip: str) -> None:
    left = FIXTURES / "square_20.png"
    proc = run_image_diff(str(left), str(left), "--clip", clip)
    assert proc.returncode == 2


def test_clip_out_of_bounds_exit_two() -> None:
    left = FIXTURES / "square_10.png"
    proc = run_image_diff(str(left), str(left), "--clip", "0,0,20,20")
    assert proc.returncode == 2
    assert "outside image bounds" in proc.stderr


def test_viewport_width_non_positive_exit_two() -> None:
    left = FIXTURES / "square_10.png"
    proc = run_image_diff(str(left), str(left), "--viewport-width", "0")
    assert proc.returncode == 2


def test_tolerance_out_of_range_exit_two() -> None:
    left = FIXTURES / "square_4.png"
    assert run_image_diff(str(left), str(left), "--tolerance", "256").returncode == 2
    assert run_image_diff(str(left), str(left), "--tolerance", "-1").returncode == 2


def test_missing_pillow_min_exit_two(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    left = FIXTURES / "square_4.png"
    missing = tmp_path / "missing_pillow_min.py"
    monkeypatch.setattr(image_diff, "_PILLOW_MIN", missing)
    monkeypatch.setattr(image_diff, "_IMAGE_BACKEND", None)
    with pytest.raises(SystemExit) as error:
        image_diff.load(str(left))
    assert error.value.code == 2


def test_missing_pillow_min_cli_exit_two() -> None:
    left = FIXTURES / "square_4.png"
    code = textwrap.dedent(
        f"""
        import importlib.util
        import sys
        from pathlib import Path
        spec = importlib.util.spec_from_file_location("image_diff", {repr(str(SCRIPT))})
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader
        spec.loader.exec_module(mod)
        mod._PILLOW_MIN = Path({repr(str(ROOT / "missing-pillow-min.py"))})
        mod._IMAGE_BACKEND = None
        raise SystemExit(mod.main([{repr(str(left))}, {repr(str(left))}]))
        """
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT)
    assert proc.returncode == 2
    assert "pillow_min" in proc.stderr


@pytest.mark.parametrize("flag,value", [
    ("--viewport-width", "nan"), ("--viewport-width", "inf"),
    ("--viewport-width", "-inf"), ("--clip", "nan,0,1,1"),
    ("--clip", "0,inf,1,1"), ("--clip", "0,0,inf,1"),
    ("--clip", "0,0,1,nan"), ("--clip", "1e308,0,1e308,1"),
])
def test_nonfinite_and_overflow_input_exit_two(flag, value):
    path = FIXTURES / "square_10.png"
    proc = run_image_diff(str(path), str(path), flag, value)
    assert proc.returncode == 2
    assert "Traceback" not in proc.stderr


def test_regular_file_over_limit_exit_two(monkeypatch):
    path = FIXTURES / "square_10.png"
    monkeypatch.setattr(image_diff, "MAX_INPUT_BYTES", 16)
    with pytest.raises(ValueError, match="size limit"):
        image_diff.load(str(path))


def test_compressed_zip_member_over_limit_exit_two(tmp_path, monkeypatch):
    archive = tmp_path / "compressed.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("huge.png", b"0" * 8192)
    monkeypatch.setattr(image_diff, "MAX_INPUT_BYTES", 1024)
    with pytest.raises(ValueError, match="size limit"):
        image_diff.load(f"{archive}::huge.png")


def test_normal_zip_and_file_within_limit(tmp_path):
    path = FIXTURES / "tiny.png"
    archive = tmp_path / "ok.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(path, "ok.png")
    assert run_image_diff(str(path), f"{archive}::ok.png").returncode == 0


def test_negative_clip_coordinate_exit_two():
    path = FIXTURES / "square_10.png"
    proc = run_image_diff(str(path), str(path), "--clip", "-1,0,5,5")
    assert proc.returncode == 2
    assert "Traceback" not in proc.stderr


@pytest.mark.parametrize("channel", range(4), ids=["red", "green", "blue", "alpha"])
@pytest.mark.parametrize("tolerance,changed", [(4, 1), (5, 0), (6, 0)])
def test_each_rgba_channel_tolerance_metrics(channel, tolerance, changed):
    base = pillow_min.Image.new("RGBA", (2, 1), (100, 100, 100, 100))
    altered = base.copy()
    pixel = [100, 100, 100, 100]
    pixel[channel] += 5
    altered.putpixel((1, 0), tuple(pixel))
    result = image_diff.compare(base, altered, tolerance=tolerance)
    assert result["changed"] == changed
    assert result["ratio"] == changed / 2
    assert result["bbox"] == ((1, 0, 2, 1) if changed else None)
    assert result["max_delta"] == 5
    assert result["differs"] is bool(changed)


@pytest.mark.parametrize("in_zip", [False, True], ids=["file", "zip"])
def test_invalid_image_bytes_exit_two(tmp_path, in_zip):
    path = FIXTURES / "invalid.png"
    source = str(path)
    if in_zip:
        archive = tmp_path / "invalid.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(path, "invalid.png")
        source = f"{archive}::invalid.png"
    proc = run_image_diff(source, source)
    assert proc.returncode == 2
    assert "image_diff:" in proc.stderr
    assert "Traceback" not in proc.stderr


@pytest.mark.parametrize("version", [(3, 9), (3, 10)])
def test_unsupported_python_fails_before_backend(monkeypatch, capsys, version):
    monkeypatch.setattr(image_diff.sys, "version_info", version)
    with pytest.raises(SystemExit) as error:
        image_diff.load("does-not-exist.png")
    assert error.value.code == 2
    assert "Python >=3.11 is required" in capsys.readouterr().err


def test_vendored_pillow_min_matches_lock() -> None:
    lock = json.loads((ROOT / "vendor.lock.json").read_text(encoding="utf-8"))
    entry = next(
        e for e in lock["files"]
        if e["destination"] == "vendor/pillow_min/pillow_min.py"
    )
    data = PILLOW_MIN.read_bytes()
    import hashlib
    assert entry["sha256"] == hashlib.sha256(data).hexdigest()
    assert entry["commit"] == "b45ccd3e529aefe6519aa14e14bb63fa5873639d"
