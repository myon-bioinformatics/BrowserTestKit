"""mirror_check: N-way consensus, normalization, split answers, stdlib xlsx reader."""
import importlib.util, json, subprocess, sys, zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "mirror_check.py"
spec = importlib.util.spec_from_file_location("mirror_check", SCRIPT)
mc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mc)


def doc(reader, kind="markdown", **keys):
    required = ("sheets", "cells", "formulas", "merges") if kind == "xlsx" else ("headings", "links", "images", "code")
    return {"schema": "mirror-facts/1", "kind": kind, "reader": reader,
            **{key: [] for key in required}, **keys}


def test_normalization_lets_different_renderings_agree():
    ours = doc("ours", headings=[[2, "Section *two*"], [3, "Third `level`"]], links=["<https://x>"],
               images=[["a.png", "A [b](c)"]])
    theirs = doc("theirs", headings=[[2, "Section two"], [3, "Third level"]], links=["https://x"],
                 images=[["a.png", "A b"]])
    assert mc.compare([ours, theirs]) == []


def test_majority_names_the_deviating_reader():
    a = doc("a", headings=[[1, "T"], [1, "not a heading"]])
    b = doc("b", headings=[[1, "T"]])
    c = doc("c", headings=[[1, "T"]])
    rows = mc.compare([a, b, c])
    assert rows == [{"key": "headings", "index": 0, "consensus": [1, 2],
                     "only": [(1, "not a heading")], "missing": []}]


def test_split_when_every_reader_differs():
    rows = mc.compare([doc("a", links=["x"]), doc("b", links=["x", "y"]), doc("c", links=["x", "z"])])
    assert [(r["index"], r["only"]) for r in rows] == [(0, []), (1, ["y"]), (2, ["z"])]
    assert all(r["consensus"] == [] for r in rows)


def write_xlsx(path):
    m = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    r = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook {m} {r}><sheets><sheet name="売上" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="x"/></Relationships>')
        z.writestr("xl/sharedStrings.xml", f'<sst {m}><si><t>商品</t></si><si><r><t>合</t></r><r><t>計</t></r></si></sst>')
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet {m}><sheetData><row r="1">'
                   '<c r="A1" t="s"><v>0</v></c><c r="B1"><v>1000</v></c><c r="C1" t="b"><v>1</v></c>'
                   '<c r="D1"><f>SUM(B1:B1)</f><v>1000</v></c><c r="E1" t="inlineStr"><is><t>in</t></is></c>'
                   '<c r="F1" t="s"><v>1</v></c><c r="G1"/></row></sheetData>'
                   '<mergeCells><mergeCell ref="A3:C3"/></mergeCells></worksheet>')


def test_stdlib_xlsx_reader(tmp_path):
    book = tmp_path / "b.xlsx"
    write_xlsx(book)
    facts = mc.xlsx_facts(str(book))
    assert facts["sheets"] == ["売上"] and facts["merges"] == [["売上", "A3:C3"]]
    assert facts["formulas"] == [["売上", "D1", "SUM(B1:B1)"]]
    assert facts["cells"] == [["売上", "A1", "商品"], ["売上", "B1", 1000.0], ["売上", "C1", True],
                              ["売上", "D1", 1000.0], ["売上", "E1", "in"], ["売上", "F1", "合計"]]


def test_cli(tmp_path):
    run = lambda *a: subprocess.run([sys.executable, "-S", str(SCRIPT), *a], capture_output=True, text=True)
    (tmp_path / "a.json").write_text(json.dumps(doc("a", links=["x"])))
    (tmp_path / "b.json").write_text(json.dumps(doc("b", links=["x"])))
    (tmp_path / "c.json").write_text(json.dumps(doc("c", links=["y"])))
    assert run("compare", str(tmp_path / "a.json"), str(tmp_path / "b.json")).returncode == 0
    out = run("compare", *(str(tmp_path / n) for n in ("a.json", "b.json", "c.json")))
    assert out.returncode == 1 and out.stdout.startswith("diff links c vs a+b: only=1")
    assert run("compare", str(tmp_path / "a.json")).returncode == 2
    write_xlsx(tmp_path / "b.xlsx")
    facts = run("facts", "xlsx", str(tmp_path / "b.xlsx"))
    assert facts.returncode == 0 and json.loads(facts.stdout)["reader"] == "python-stdlib-ooxml"


@pytest.mark.parametrize("kind", ["markdown", "html", "xlsx"])
def test_complete_multiple_readers_agree(kind):
    keys = {"cells": [["S", "A1", 2.0], ["S", "B1", 0.1 + 0.2]]} if kind == "xlsx" else {"links": ["x"]}
    a, b, c = [doc(reader, kind, **keys) for reader in ("a", "b", "c")]
    if kind == "xlsx":
        c["cells"] = [["S", "B1", 0.3], ["S", "A1", 2]]
    assert mc.compare([a, b, c]) == []


def invalid_inputs():
    valid = doc("a", links=["x"])
    yield "zero-documents", []
    yield "one-document", [valid]
    yield "empty-object", [valid, {}]
    yield "non-object", [valid, []]
    yield "null-object", [valid, None]
    for schema in (None, "mirror-facts/2"):
        yield f"schema-{schema}", [valid, {**valid, "schema": schema}]
    missing = dict(valid)
    del missing["schema"]
    yield "missing-schema", [valid, missing]
    missing = dict(valid)
    del missing["kind"]
    yield "missing-kind", [valid, missing]
    for kind in (None, [], "unknown", "html", "xlsx"):
        yield f"kind-{kind}", [valid, {**valid, "kind": kind}]
    yield "disjoint-partial-keys", [
        {"schema": "mirror-facts/1", "kind": "markdown", "headings": [[1, "T"]]},
        {"schema": "mirror-facts/1", "kind": "xlsx", "cells": [["S", "A1", 1]]}]
    for kind, keys in (("markdown", ("headings", "links", "images", "code")),
                       ("html", ("headings", "links", "images", "code")),
                       ("xlsx", ("sheets", "cells", "formulas", "merges"))):
        a = doc("a", kind, **({"sheets": ["S"]} if kind == "xlsx" else {"links": ["x"]}))
        for key in keys:
            b = dict(a)
            del b[key]
            yield f"missing-{kind}-{key}", [a, b]
            yield f"nonlist-{kind}-{key}", [a, {**a, key: None}]
        yield f"empty-{kind}", [doc("a", kind), doc("b", kind)]
    yield "partial-extension", [valid, {**valid, "extra": ["x"]}]
    yield "malformed-item", [valid, {**valid, "headings": [[]]}]


@pytest.mark.parametrize("case,documents", list(invalid_inputs()))
def test_incomparable_inputs_raise_and_cli_returns_2(case, documents, tmp_path):
    with pytest.raises(ValueError):
        mc.compare(documents)
    files = []
    for i, document in enumerate(documents):
        path = tmp_path / f"{i}.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        files.append(str(path))
    result = subprocess.run([sys.executable, "-S", str(SCRIPT), "compare", *files],
                            capture_output=True, text=True)
    assert result.returncode == 2, (case, result.stdout, result.stderr)
    assert "all agree" not in result.stdout
    assert "Traceback" not in result.stderr


def test_empty_list_in_one_reader_is_a_deviation():
    rows = mc.compare([doc("a", links=["x"]), doc("b")])
    assert rows and all(row["key"] == "links" for row in rows)


def test_shared_extension_lists_are_still_compared():
    a = doc("a", links=["x"], extra=["left"])
    b = doc("b", links=["x"], extra=["right"])
    assert {row["key"] for row in mc.compare([a, b])} == {"extra"}
