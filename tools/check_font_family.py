#!/usr/bin/env python3
"""Check that generated pages and Plotly figures ask for Meiryo first.

    python -S tools/check_font_family.py FILE_OR_DIR [...]    # .html .css .js .json (a directory is searched)

It reads the text, not a browser: every CSS `font-family` / `font:` shorthand and every Plotly
`family` must start with Meiryo, `inherit`, `var(...)` or be a monospace stack (code, pre, kbd).
`system-ui`, `-apple-system`, `Segoe UI` and a bare `sans-serif` fail, and so does a Plotly figure that sets
no family (Plotly would use "Open Sans"). A bundled plotly.js is skipped. What a browser actually renders on a given
OS is recorded by platform_probe (`fonts.meiryo`, `fonts.body_family`); this checks what was asked for.
Output is bounded. Exit codes: 0 every declaration passes; 1 a problem found, or no font
declaration at all; 2 input error.
"""
from __future__ import annotations

import json, re, sys
from pathlib import Path

SUFFIXES = {".html", ".htm", ".css", ".js", ".json"}
MAX_BYTES = 32 * 1024 * 1024
MAX_SHOWN = 20
MEIRYO = {"meiryo", "メイリオ"}

# A value runs to `;` `}` or the end of the line. A quoted family name may contain spaces but never `<` `>` `;`,
# so the closing quote of style="..." is not mistaken for the start of one.
VALUE = r"""((?:"[^"<>;\n]*"|'[^'<>;\n]*'|[^;}"'\n])+)"""
CSS_FAMILY = re.compile(r"(?<![\w-])font-family\s*:\s*" + VALUE, re.I)
# font:16px/1.65 Meiryo,... — the families follow the size.
CSS_SHORTHAND = re.compile(r"(?<![\w-])font\s*:\s*[^;}\n]*?\d(?:px|pt|em|rem|%)(?:/[\w.]+)?\s+" + VALUE, re.I)
# JSON `"family": "..."` and JS `family:'...'` (Plotly font objects); not `font-family`.
PLOTLY_FAMILY = re.compile(r"""(?<![\w-])["']?family["']?\s*:\s*(["'])((?:\\.|(?!\1).)*)\1""")
PLOTLY_CALL = re.compile(r"\bPlotly\s*\.\s*(?:newPlot|react|plot)\s*\(")
SCRIPT = re.compile(r"<script\b[^>]*>(.*?)</script>", re.S | re.I)


def reason(stack: str) -> str | None:
    """None when the stack is acceptable, otherwise why not."""
    value = re.sub(r"!important", "", stack, flags=re.I).strip().strip("\"'\\;").strip()
    if value.lower() == "inherit" or value.lower().startswith("var("):
        return None
    names = [n.strip().strip("\"'\\").strip().lower() for n in value.split(",")]
    if names[0] in MEIRYO or "monospace" in names:  # Meiryo is proportional, so code stacks stay monospace
        return None
    return f"starts with {names[0] or '(empty)'!r}, not Meiryo"


def strip_bundles(text: str) -> str:
    """Blank an inlined plotly.js (it carries its own Open Sans defaults), keeping line numbers."""
    def blank(m: re.Match) -> str:
        body = m.group(1)
        if "plotly.js" in body[:600].lower():
            return m.group(0)[:m.start(1) - m.start()] + "\n" * body.count("\n") + "</script>"
        return m.group(0)
    return SCRIPT.sub(blank, text) if "plotly.js" in text.lower() else text


def line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def scan(text: str, suffix: str) -> tuple[int, list[tuple[int, str]]]:
    """(declarations seen, [(line, problem)])."""
    if suffix == ".json":
        try:
            spec = json.loads(text)
        except ValueError:
            pass  # Preserve the text scanner for non-JSON script fragments.
        else:
            seen, problems = 0, []

            def visit(value):
                nonlocal seen
                if isinstance(value, dict):
                    for key, child in value.items():
                        if key == "family" and isinstance(child, str):
                            seen += 1
                            why = reason(child)
                            if why:
                                problems.append((1, f"plotly family: {child.strip()[:80]} ({why})"))
                        visit(child)
                elif isinstance(value, list):
                    for child in value:
                        visit(child)

            visit(spec)
            if isinstance(spec, dict) and isinstance(spec.get("data"), list):
                layout = spec.get("layout")
                font = layout.get("font") if isinstance(layout, dict) else None
                family = font.get("family") if isinstance(font, dict) else None
                if not isinstance(family, str) or not family.strip():
                    problems.append((1, "Plotly spec has no layout.font.family (Plotly's default is Open Sans)"))
            return seen, problems
    text = strip_bundles(text)
    seen, problems = 0, []
    for pattern, kind in ((CSS_FAMILY, "font-family"), (CSS_SHORTHAND, "font"), (PLOTLY_FAMILY, "plotly family")):
        for m in pattern.finditer(text):
            seen += 1
            value = m.group(2) if pattern is PLOTLY_FAMILY else m.group(1)
            why = reason(value)
            if why:
                problems.append((line_of(text, m.start()), f"{kind}: {value.strip()[:80]} ({why})"))
    plotly_families = len(PLOTLY_FAMILY.findall(text))
    if suffix in {".html", ".htm", ".js"} and PLOTLY_CALL.search(text) and not plotly_families:
        problems.append((1, "Plotly is used but no figure font family is set (Plotly's default is Open Sans)"))
    return seen, problems


def files(args: list[str]) -> list[Path]:
    out = []
    for raw in args:
        path = Path(raw)
        if path.is_dir():
            out += sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in SUFFIXES)
        elif path.is_file():
            out.append(path)
        else:
            raise ValueError(f"{raw}: no such file or directory")
    if not out:
        raise ValueError("no files to check")
    return out


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: check_font_family.py FILE_OR_DIR [...]", file=sys.stderr)
        return 2
    if argv[0] in {"-h", "--help"}:
        print(__doc__)
        return 0
    try:
        paths = files(argv)
        total, found = 0, []
        for path in paths:
            if path.stat().st_size > MAX_BYTES:
                raise ValueError(f"{path}: larger than {MAX_BYTES // 1024 // 1024} MB")
            seen, problems = scan(path.read_text(encoding="utf-8", errors="replace"), path.suffix.lower())
            total += seen
            found += [(path, line, why) for line, why in problems]
    except (OSError, ValueError) as error:
        print(f"check_font_family: {error}", file=sys.stderr)
        return 2
    for path, line, why in found[:MAX_SHOWN]:
        print(f"{path}:{line}: {why}")
    if len(found) > MAX_SHOWN:
        print(f"... {len(found) - MAX_SHOWN} more")
    if not total and not found:
        print(f"no font declarations in {len(paths)} files: nothing asks for Meiryo")
        return 1
    print(f"{'fail' if found else 'pass'} {len(paths)} files, {total} font declarations, {len(found)} problems")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
