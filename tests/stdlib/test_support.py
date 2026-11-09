"""Dependency-free support checks, run with python -S on Python 3.11+."""
import base64, importlib, pathlib, subprocess, sys, unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tools"))


class StdlibSupport(unittest.TestCase):
    def test_imports_without_site_packages(self):
        for source in sorted((ROOT / "scripts").glob("*.py")) + sorted((ROOT / "tools").glob("*.py")):
            if source.name == "pytest_btk_events.py":
                continue  # Explicit pytest plugin belongs to the 3.11+ dev lane.
            with self.subTest(module=source.stem):
                importlib.import_module(source.stem)

    def test_png_validation_without_image_dependency(self):
        from check_png import dimensions
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
        )
        self.assertEqual(dimensions(png), (1, 1))
        with self.assertRaises(ValueError):
            dimensions(b"not a png")

    def test_html_rendering_and_escaping(self):
        from html_builder import h
        self.assertEqual(
            h("p", "<unsafe>&", title='a"b'),
            '<p title="a&quot;b">&lt;unsafe&gt;&amp;</p>',
        )
        self.assertEqual(h("ul", h("li", "item")), "<ul><li>item</li></ul>")

    def test_representative_cli_help(self):
        for name in ("check_png", "page_text", "image_diff"):
            with self.subTest(module=name):
                result = subprocess.run(
                    [sys.executable, "-S", str(next(d / (name + ".py") for d in (ROOT / "scripts", ROOT / "tools") if (d / (name + ".py")).exists())), "--help"],
                    capture_output=True, text=True, timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stderr)



if __name__ == "__main__":
    unittest.main()
