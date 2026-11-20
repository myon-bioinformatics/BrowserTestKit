import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))
from btk import js_search


class JSSearchTests(unittest.TestCase):
    def test_html_entry_points_and_literal_module_references(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "index.html").write_text(
                '<script type="application/json">"import(\'ignored\')"</script>\n'
                '<link rel="modulepreload" href="./base.js">\n'
                '<script type="module" src="./view.mjs"></script>\n'
                '<script type="module">\nimport "./init.js";</script>', encoding="utf-8")
            (root / "view.mjs").write_text(
                '// import("fake.js")\nconst note = "import(\'fake\')";\n'
                'export {render} from "./base.js";\n'
                'const show = () => import( /* renderer */ "./Mermaid.mjs");\n'
                'const other = import("./" + name);\nrequire("./lib.cjs");', encoding="utf-8")
            report = js_search(root)
            refs = [(x["line"], x["kind"], x["target"]) for x in report["references"]]
            self.assertEqual(refs, [(2, "modulepreload", "./base.js"), (3, "script-src", "./view.mjs"),
                                    (5, "static-import", "./init.js"), (3, "static-import", "./base.js"),
                                    (4, "dynamic-import", "./Mermaid.mjs"), (6, "require", "./lib.cjs")])
            self.assertEqual(js_search(root, "mermaid")["counts"], {"files": 0, "references": 1, "matches": 1})

    def test_cli_limits_results_and_scans_build_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for folder in ("dist", "node_modules"):
                (root / folder).mkdir()
                for name in ("a.js", "b.cjs"):
                    (root / folder / name).write_text("hello", encoding="utf-8")
            done = subprocess.run([sys.executable, "-S", str(TOOLS / "btk.py"), "js-search", directory,
                                   "--limit", "1"], check=True, capture_output=True, text=True)
            report = json.loads(done.stdout)
            self.assertEqual(report["counts"]["files"], 2)
            self.assertEqual(report["files"], [{"path": "dist/a.js", "bytes": 5}])

    def test_shared_external_urls_across_projects(self):
        from btk import shared_js

        with tempfile.TemporaryDirectory() as directory:
            a, b, c = (Path(directory, name) for name in "abc")
            for project in (a, b, c):
                project.mkdir()
            url = "https://cdn.example/lib@1.js?v=1"
            alt = "//cdn.example/other.js"
            (a / "index.html").write_text(
                '<script src="./local.js"></script>\n' * 2
                + '<script src="https://cdn.example/only-a.js"></script>\n' * 2
                + f'<link rel="modulepreload" href="{url}">\n' * 2
                + f'<script src="{alt}"></script>', encoding="utf-8")
            (b / "view.mjs").write_text(
                f'import "./local.js";\nimport("{url}");\nimport "{alt}";\n'
                'import "https://cdn.example/lib@2.js?v=1";', encoding="utf-8")
            (c / "view.mjs").write_text(f'import "{url}";', encoding="utf-8")
            report = shared_js([a, b, c], limit=1)
            self.assertEqual(report["shared_count"], 2)
            self.assertEqual(len(report["shared"]), 1)
            item = report["shared"][0]
            self.assertEqual(item["url"], url)
            self.assertEqual(item["projects"][str(a)], {"count": 2, "locations": [
                {"path": "index.html", "line": 5, "kind": "modulepreload"}]})
            self.assertEqual(item["projects"][str(b)]["locations"][0]["kind"], "dynamic-import")
            command = [sys.executable, "-S", str(TOOLS / "btk.py"), "js-search", str(a),
                       "lib@", "--shared-with", str(b), "--shared-with", str(c)]
            self.assertEqual(json.loads(subprocess.check_output(command))["shared_count"], 1)
            for other in (a, a.parent):
                with self.assertRaisesRegex(ValueError, "overlap"):
                    shared_js([a, other])
