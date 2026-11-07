#!/usr/bin/env python3
"""Tiny stdlib HTML builder for DRY Playwright/Stagehand test fixtures.

``h(tag, *children, **attrs)`` renders one element as a string:

- Children: strings are escaped with ``html.escape``; numbers become
  ``str(number)``; ``None``/``False`` are skipped; lists/tuples are
  flattened (recursively); ``Raw(text)`` is inserted unescaped.
- Attributes: a trailing underscore is stripped (``class_`` -> ``class``,
  ``for_`` -> ``for``); other underscores become hyphens (``data_id`` ->
  ``data-id``, ``aria_label`` -> ``aria-label``); ``True`` renders as a bare
  attribute; ``False``/``None`` omit the attribute entirely; other values
  are HTML-escaped and quoted. Attribute order matches the keyword order.
- Void elements (area base br col embed hr img input link meta source
  track wbr) never get a closing tag; giving one children raises
  ``ValueError``.

``page(title, *body, lang="en")`` wraps ``body`` in a full HTML5 document
with a UTF-8 meta charset.

Stdlib only; runs under ``python -S``::

    python -S scripts/html_builder.py h1:Title "p:Hello world" br li:a li:b
    python -S scripts/html_builder.py h1:Title p:Body --page "My Page"

Each CLI argument is ``TAG[:TEXT]``; consecutive ``li`` arguments are
wrapped in a single ``<ul>``. ``--page TITLE`` wraps the rendered elements
with ``page()``. The result is printed to stdout.

Exit codes: 0 = OK, 2 = usage error (argparse: no tags given, etc.).
"""
from __future__ import annotations

import argparse, html

VOID_ELEMENTS = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source",
                           "track", "wbr"})


class Raw(str):
    """A string :func:`h` inserts unescaped. ``h()`` itself returns ``Raw``,
    so nesting calls (``h("ul", h("li", "a"))``) composes without
    double-escaping; wrap any other pre-built markup the same way."""

    __slots__ = ()


def _attr_name(key: str) -> str:
    if key.endswith("_"):
        key = key[:-1]
    return key.replace("_", "-")


def _render_attrs(attrs: dict) -> str:
    parts = []
    for key, value in attrs.items():
        if value is False or value is None:
            continue
        name = _attr_name(key)
        if value is True:
            parts.append(f" {name}")
        else:
            parts.append(f' {name}="{html.escape(str(value), quote=True)}"')
    return "".join(parts)


def _flatten(children: tuple) -> list:
    flat = []
    for child in children:
        if child is None or child is False:
            continue
        if isinstance(child, (list, tuple)):
            flat.extend(_flatten(tuple(child)))
        else:
            flat.append(child)
    return flat


def _render_child(child: object) -> str:
    if isinstance(child, Raw):
        return child
    if isinstance(child, (int, float)):
        return str(child)
    return html.escape(str(child))


def h(tag: str, *children: object, **attrs: object) -> Raw:
    """Render one HTML element as a ``Raw`` string. See module docstring for rules."""
    is_void = tag in VOID_ELEMENTS
    flat = _flatten(children)
    if is_void and flat:
        raise ValueError(f"<{tag}> is a void element and cannot have children")
    open_tag = f"<{tag}{_render_attrs(attrs)}>"
    if is_void:
        return Raw(open_tag)
    return Raw(f"{open_tag}{''.join(_render_child(child) for child in flat)}</{tag}>")


def page(title: str, *body: object, lang: str = "en") -> str:
    """A full HTML5 document: ``<!doctype html>`` + ``<html>`` wrapping ``body``."""
    head = h("head", h("meta", charset="utf-8"), h("title", title))
    return "<!doctype html>\n" + h("html", head, h("body", *body), lang=lang)


def _parse_item(raw: str) -> tuple[str, str | None]:
    tag, sep, text = raw.partition(":")
    return tag, text if sep else None


def build(items: list) -> str:
    """Render ``TAG[:TEXT]`` items, folding consecutive ``li`` items into one ``<ul>``."""
    parts: list = []
    pending_li: list = []

    def flush_li() -> None:
        if pending_li:
            parts.append(h("ul", *(h("li") if text is None else h("li", text) for text in pending_li)))
            pending_li.clear()

    for raw in items:
        tag, text = _parse_item(raw)
        if tag == "li":
            pending_li.append(text)
            continue
        flush_li()
        parts.append(h(tag) if text is None else h(tag, text))
    flush_li()
    return "\n".join(parts)


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render HTML elements from TAG[:TEXT] arguments, for DRY test fixtures.")
    parser.add_argument("items", nargs="+", metavar="TAG[:TEXT]",
                        help="element tag, optionally ':text' (repeatable; consecutive 'li' share one <ul>)")
    parser.add_argument("--page", metavar="TITLE", help="wrap the output in a full document with this title")
    args = parser.parse_args(argv)
    body = build(args.items)
    print(page(args.page, Raw(body)) if args.page is not None else body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
