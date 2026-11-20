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
