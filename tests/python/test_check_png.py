import importlib.util, struct
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("check_png", ROOT / "tools" / "check_png.py")
check_png = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(check_png)

def minimal_png(width=390, height=844):
    return check_png.PNG + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00" + b"xxxx" + struct.pack(">I", 0) + b"IEND" + b"xxxx"

def test_png_header_dimensions() -> None:
    assert check_png.dimensions(minimal_png()) == (390, 844)

def test_rejects_non_png() -> None:
    with pytest.raises(ValueError):
        check_png.dimensions(b"not an image")

def test_rejects_wrong_ihdr_length() -> None:
    data = check_png.PNG + struct.pack(">I", 12) + b"IHDR" + b"x" * 32 + b"IEND" + b"xxxx"
    with pytest.raises(ValueError, match="IHDR"):
        check_png.dimensions(data)

def test_rejects_missing_iend() -> None:
    with pytest.raises(ValueError, match="IEND"):
        check_png.dimensions(minimal_png()[:-8] + b"NOPE1234")
