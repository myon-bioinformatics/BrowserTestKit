"""Guard source-preserving branding and relative fixture icon references."""
import importlib.util, json, subprocess, sys, unittest
from hashlib import sha256; from html.parser import HTMLParser; from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BRAND = ROOT / "docs/assets/brand"
MANIFEST = json.loads((BRAND / "manifest.json").read_text(encoding="utf-8"))
PILLOW_MIN = ROOT / "vendor/pillow_min/pillow_min.py"
spec = importlib.util.spec_from_file_location("brand_pillow_min", PILLOW_MIN)
assert spec is not None and spec.loader is not None
pillow_min = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pillow_min)
Image = pillow_min.Image

# Frozen decoded RGB baselines for the reviewed artwork in PR #111. These are
# not generated during tests. pillow_min decodes/crops but does not implement
# resize/LANCZOS, so validate the published derivatives without recreating them.
DERIVATIVE_BASELINES = {
    "fixtures/icons/favicon-16.png": (
        "docs/assets/brand/validation-buddy-favicon-source.png", (16, 16),
        "ce19a349a78ca6303767efbfac0b4d7a16e3ac7ca7a3689c1ab1b10ca9a2b40b",
    ),
    "fixtures/icons/favicon-32.png": (
        "docs/assets/brand/validation-buddy-favicon-source.png", (32, 32),
        "5247878e923089871c24f8f25b40635623dafaeee63bf7baa0b7054f048df622",
    ),
    "fixtures/icons/favicon-64.png": (
        "docs/assets/brand/validation-buddy-favicon-source.png", (64, 64),
        "1fe4f856ab1d91984015cb88466afb335c21c68f2e5e26bb2bf939f1b6695630",
    ),
    "fixtures/icons/apple-touch-icon.png": (
        "docs/assets/brand/camera-validation.png", (180, 180),
        "540b4a378b1a9a44aa1ceac2a8a9714388b8ab2ff2141d33337151a17b37ed00",
    ),
}


def test_brand_source_and_lossless_crops():
    source_path = ROOT / MANIFEST["source"]
    assert sha256(source_path.read_bytes()).hexdigest() == MANIFEST["source_sha256"]
    with Image.open(source_path) as source:
        assert list(source.size) == MANIFEST["source_size"] == [1080, 720]
        assert source.mode == MANIFEST["source_mode"] == "RGB"
        for record in MANIFEST["crops"]:
            path = ROOT / record["path"]
            assert sha256(path.read_bytes()).hexdigest() == record["sha256"]
            with Image.open(path) as actual:
                expected = source.crop(record["crop_xyxy"])
                assert actual.mode == expected.mode
                assert list(actual.size) == record["size"]
                assert actual.tobytes() == expected.tobytes()


def assert_derivative_pixels(actual, size, pixel_sha256):
    assert actual.mode == "RGB"
    assert actual.size == size
    assert sha256(actual.tobytes()).hexdigest() == pixel_sha256, (
        "decoded pixels differ from reviewed artwork"
    )


def test_brand_derivatives_preserve_reviewed_pixels():
    records = MANIFEST["derivatives"]
    assert {record["path"] for record in records} == set(DERIVATIVE_BASELINES)
    assert len(records) == len(DERIVATIVE_BASELINES)
    for record in MANIFEST["derivatives"]:
        source, size, pixel_sha256 = DERIVATIVE_BASELINES[record["path"]]
        assert record["source"] == source
        assert tuple(record["size"]) == size
        assert source in {crop["path"] for crop in MANIFEST["crops"]}
        path = ROOT / record["path"]
        assert sha256(path.read_bytes()).hexdigest() == record["sha256"]
        with Image.open(path) as actual:
            assert_derivative_pixels(actual, size, pixel_sha256)


def test_derivative_baseline_rejects_one_changed_channel():
    path = "fixtures/icons/favicon-16.png"
    _, size, pixel_sha256 = DERIVATIVE_BASELINES[path]
    with Image.open(ROOT / path) as actual:
        changed = actual.copy()
    red, green, blue = changed.getpixel((0, 0))
    changed.putpixel((0, 0), (red ^ 1, green, blue))
    with unittest.TestCase().assertRaisesRegex(AssertionError, "decoded pixels"):
        assert_derivative_pixels(changed, size, pixel_sha256)


class IconLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.in_head = False

    def handle_starttag(self, tag, attrs):
        if tag == "head":
            self.in_head = True
        if tag == "link":
            attributes = dict(attrs)
            if attributes.get("rel") in ("icon", "apple-touch-icon"):
                assert self.in_head
                self.links.append(attributes)

    def handle_endtag(self, tag):
        if tag == "head":
            self.in_head = False


def test_fixture_icons_are_local_and_match_declared_dimensions():
    fixture = ROOT / "fixtures/index.html"
    parser = IconLinks()
    parser.feed(fixture.read_text(encoding="utf-8"))
    expected = {
        ("icon", "16x16", "icons/favicon-16.png"),
        ("icon", "32x32", "icons/favicon-32.png"),
        ("icon", "64x64", "icons/favicon-64.png"),
        ("apple-touch-icon", "180x180", "icons/apple-touch-icon.png"),
    }
    assert {(a["rel"], a["sizes"], a["href"]) for a in parser.links} == expected
    assert len(parser.links) == len(expected)
    for attrs in parser.links:
        path = fixture.parent / attrs["href"]
        assert path.read_bytes().startswith(pillow_min.PNG_SIGNATURE)
        with Image.open(path) as icon:
            assert attrs["sizes"] == f"{icon.width}x{icon.height}"
        if attrs["rel"] == "icon":
            assert attrs["type"] == "image/png"


def test_each_large_mark_has_its_documented_home():
    assert 'src="docs/assets/brand/validation-buddy.png"' in (ROOT / "README.md").read_text(encoding="utf-8")
    assert 'src="assets/brand/camera-validation.png"' in (ROOT / "docs/screenshot-evidence.md").read_text(encoding="utf-8")
    assert 'src="assets/brand/pipeline-hub.png"' in (ROOT / "docs/fast-init.md").read_text(encoding="utf-8")


def test_brand_validation_without_site_packages():
    result = subprocess.run(
        [sys.executable, "-S", str(Path(__file__).resolve())],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "4 asset checks passed without site packages"


if __name__ == "__main__":
    test_brand_source_and_lossless_crops()
    test_brand_derivatives_preserve_reviewed_pixels()
    test_fixture_icons_are_local_and_match_declared_dimensions()
    test_each_large_mark_has_its_documented_home()
    assert not any(name == "PIL" or name.startswith("PIL.") for name in sys.modules)
    print("4 asset checks passed without site packages")
