#!/usr/bin/env python3
"""Mirror tests without a browser: several independent implementations, one answer.

A mirror test asks two or more implementations the same question about one artifact
and checks that they agree, e.g. ExcelJS, SheetJS and a stdlib OOXML reader reading
the cells of one workbook, or your own Markdown helper vs markdown-it vs marked. No
side is the oracle; agreement is evidence, disagreement is a lead. Facts come from
``tools/mirror_facts.mjs`` (JS readers), ``facts xlsx`` below (stdlib reader) or
your own code, as ``mirror-facts/1`` JSON::

    python -S tools/mirror_check.py compare ours.json markdown-it.json marked.json
    python -S tools/mirror_check.py facts xlsx book.xlsx > stdlib.json

``compare`` reduces values to what every side can state (heading/alt text loses
inline Markdown and tags; ``<url>`` loses brackets; numbers are rounded to 9 digits,
integral floats become ints), compares each key as a multiset, and takes the most
common answer as the consensus. One ``diff`` line per (key, implementation) that
deviates, with what only it has and what it lacks; when every reader answers
differently, one ``split`` line per reader with the items not shared by all.
Inputs must share the schema, kind and complete list keys; an empty comparison
is an input error, never evidence of agreement.

Stdlib only. Exit codes: 0 = all agree; 1 = some implementation deviates; 2 = input error.
"""
from __future__ import annotations

import argparse, collections, json, re, sys, xml.etree.ElementTree as ET, zipfile

_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_TAG = re.compile(r"<[^>]+>")
_REQUIRED = {
    "markdown": ("headings", "links", "images", "code"),
    "html": ("headings", "links", "images", "code"),
    "xlsx": ("sheets", "cells", "formulas", "merges"),
}
_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
       "p": "http://schemas.openxmlformats.org/package/2006/relationships"}


def plain(text: str) -> str:
    text = _TAG.sub("", _LINK.sub(r"\1", text))
    return " ".join(re.sub(r"[`*_]", "", text).split())


def _value(v):
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float)):
        v = round(float(v), 9)
        return int(v) if v.is_integer() else v
    return v


def normalize(key: str, item):
    if key == "headings":
        return (int(item[0]), plain(item[1]))
    if key == "links":
        url = str(item).strip()
        return url[1:-1] if url.startswith("<") and url.endswith(">") else url
    if key == "images":
        return (str(item[0]), plain(item[1] or ""))
    if key == "code":
        return (item[0] or "", item[1].rstrip("\n"))
    if isinstance(item, list):
        return tuple(_value(x) for x in item)
    return _value(item)


def compare(documents: list[dict]) -> list[dict]:
    """Return deviations; raise ValueError for incomplete/incomparable inputs."""
    if len(documents) < 2:
        raise ValueError("compare needs at least two fact documents")
    kind = None
    keys = []
    for index, doc in enumerate(documents):
        if not isinstance(doc, dict):
            raise ValueError(f"document {index}: expected a fact object")
        if doc.get("schema") != "mirror-facts/1":
            raise ValueError(f"document {index}: schema must be mirror-facts/1")
        current = doc.get("kind")
        if not isinstance(current, str) or current not in _REQUIRED:
            raise ValueError(f"document {index}: unsupported kind {current!r}")
        if kind is None:
            kind = current
            keys.extend(_REQUIRED[kind])
        elif current != kind:
            raise ValueError(f"document {index}: kind {current!r} differs from {kind!r}")
        keys.extend(k for k, v in doc.items() if isinstance(v, list) and k not in keys)
    # Additional list keys remain supported, but cannot silently compare a subset
    # of the readers. Empty individual lists are valid (e.g. no images).
    for index, doc in enumerate(documents):
        for key in keys:
            if not isinstance(doc.get(key), list):
                raise ValueError(f"document {index}: {key} must be present as a list")
    if not any(doc[key] for doc in documents for key in _REQUIRED[kind]):
        raise ValueError("no facts to compare in the required lists")
    rows = []
    for key in keys:
        present = []
        for index, doc in enumerate(documents):
            try:
                counter = collections.Counter(normalize(key, x) for x in doc[key])
            except (ValueError, TypeError, IndexError, AttributeError, OverflowError) as exc:
                raise ValueError(f"document {index}: invalid {key} item: {exc}") from exc
            present.append((index, counter))
        groups: list[tuple[collections.Counter, list[int]]] = []
        for index, counter in present:
            for group in groups:
                if group[0] == counter:
                    group[1].append(index)
                    break
            else:
                groups.append((counter, [index]))
        if len(groups) == 1:
            continue
        consensus, members = max(groups, key=lambda g: (len(g[1]), -g[1][0]))
        if len(members) == 1:
            # Every reader answers differently: no majority to measure against.
            common = present[0][1].copy()
            for _, counter in present[1:]:
                common &= counter
            for index, counter in present:
                rows.append({"key": key, "index": index, "consensus": [], "only": sorted(
                    (counter - common).elements(), key=repr), "missing": [], "count": sum(counter.values())})
            continue
        for counter, indexes in groups:
            if indexes is members:
                continue
            for index in indexes:
                rows.append({"key": key, "index": index, "consensus": members,
                             "only": sorted((counter - consensus).elements(), key=repr),
                             "missing": sorted((consensus - counter).elements(), key=repr)})
    return rows


def xlsx_facts(path: str) -> dict:
    """Stdlib OOXML reader: sheets, cells (cached values), formulas, merges."""
    facts = {"schema": "mirror-facts/1", "kind": "xlsx", "reader": "python-stdlib-ooxml",
             "sheets": [], "cells": [], "formulas": [], "merges": []}
    with zipfile.ZipFile(path) as book:
        def xml(name):
            data = book.read(name)
            if re.search(rb"<!(DOCTYPE|ENTITY)", data, re.I):
                raise ValueError("DOCTYPE/ENTITY refused in " + name)
            return ET.fromstring(data)
        strings = []
        if "xl/sharedStrings.xml" in book.namelist():
            strings = ["".join(t.text or "" for t in si.iter("{%s}t" % _NS["m"]))
                       for si in xml("xl/sharedStrings.xml").findall("m:si", _NS)]
        rels = {r.get("Id"): r.get("Target") for r in xml("xl/_rels/workbook.xml.rels").findall("p:Relationship", _NS)}
        for sheet in xml("xl/workbook.xml").find("m:sheets", _NS):
            name = sheet.get("name")
            target = rels[sheet.get("{%s}id" % _NS["r"])].lstrip("/")
            target = target if target.startswith("xl/") else "xl/" + target
            facts["sheets"].append(name)
            root = xml(target)
            for merge in root.iter("{%s}mergeCell" % _NS["m"]):
                facts["merges"].append([name, merge.get("ref")])
            for cell in root.iter("{%s}c" % _NS["m"]):
                ref, kind = cell.get("r"), cell.get("t", "n")
                formula, raw = cell.find("m:f", _NS), cell.find("m:v", _NS)
                if formula is not None and formula.text:
                    facts["formulas"].append([name, ref, formula.text])
                if kind == "inlineStr":
                    value = "".join(t.text or "" for t in cell.iter("{%s}t" % _NS["m"]))
                elif raw is None or raw.text is None:
                    continue
                elif kind == "s":
                    value = strings[int(raw.text)]
                elif kind == "b":
                    value = raw.text == "1"
                elif kind in ("str", "e"):
                    value = raw.text
                else:
                    value = float(raw.text)
                if value != "":
                    facts["cells"].append([name, ref, value])
    return facts


def _short(item, width=60) -> str:
    text = json.dumps(item, ensure_ascii=False)
    return text if len(text) <= width else text[: width - 1] + "…"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("compare", help="compare two or more mirror-facts/1 files")
    p.add_argument("files", nargs="+")
    p.add_argument("--examples", type=int, default=2)
    p = sub.add_parser("facts", help="facts from the stdlib reader")
    p.add_argument("kind", choices=["xlsx"])
    p.add_argument("file")
    args = parser.parse_args(argv)
    try:
        if args.command == "facts":
            print(json.dumps(xlsx_facts(args.file), ensure_ascii=False))
            return 0
        if len(args.files) < 2:
            raise ValueError("compare needs at least two fact files")
        documents = []
        for name in args.files:
            with open(name, encoding="utf-8") as handle:
                documents.append(json.load(handle))
        rows = compare(documents)
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, ET.ParseError) as exc:
        print(f"mirror_check: cannot compare: {exc}", file=sys.stderr)
        return 2
    names = [d.get("reader") or f for d, f in zip(documents, args.files)]
    for row in rows:
        if not row["consensus"]:
            print(f"split {row['key']} {names[row['index']]}: {row['count']} item(s), not shared by all: "
                  f"{len(row['only'])} {[_short(x) for x in row['only'][:args.examples]]}")
            continue
        others = "+".join(names[i] for i in row["consensus"])
        print(f"diff {row['key']} {names[row['index']]} vs {others}: "
              f"only={len(row['only'])} {[_short(x) for x in row['only'][:args.examples]]} "
              f"missing={len(row['missing'])} {[_short(x) for x in row['missing'][:args.examples]]}")
    print(f"mirror: {'all agree' if not rows else str(len(rows)) + ' deviation(s)'} across {len(documents)} readers")
    return 1 if rows else 0


if __name__ == "__main__":
    sys.exit(main())
