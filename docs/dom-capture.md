# Optional browser DOM capture

This example belongs to the browser-testing lane. It uses the repository's existing
`browser` dependency group (Playwright) and Chromium installation, rather
than adding a browser dependency to the stdlib scripts.

```sh
python examples/capture_browser_dom.py https://chatgpt.com/ --selector h1 --output chatgpt-dom.json
python examples/capture_browser_dom.py http://127.0.0.1:7860 --selector '#dom-lab-body' --output local-dom.json
```

The sample opens a fresh isolated context, navigates once, requires a unique
selector, and stores HTML, textContent, innerText, source URL/time and hash. It
does not log in, submit, accept consent or retry. Normal page subresources run.
Output is historical evidence, not content validation: `content_verified` is false.
Limits apply per operation and to selected output after collection, not all
network traffic/memory. Existing evidence files are not overwritten. Exit 0 means
captured, 1 ambiguous selection, 2 error. `--headed` shows the temporary browser.

`--aria-snapshot` also stores the selected element's Playwright aria snapshot (roles
and the browser's accessible names, including CSS generated content such as
`::before`), so static readers can be compared with the browser. BlueProbe's
`accessible_names.compare_aria_snapshot()` uses it on its `tests/cases/pages/` corpus.
`--same-origin-only` aborts requests to other origins (badges, analytics, fonts) and
lists them in `blocked_requests`; the captured page then depends only on the served
commit. `browser_version` is always recorded.

```sh
python examples/capture_browser_dom.py http://127.0.0.1:8000/index.html --aria-snapshot --same-origin-only --output page.json
```

Use existing `tools/page_text.py` for static extraction without a browser.
`python tools/page_text.py <URL> --compare-rendered page.json` compares that static
text with this capture's `visible` (`innerText`); capture the same element
(`--selector main` when the page has a `<main>`) so the scopes match.
GHI's selected-body HTML reader remains a separate downstream consumer. No GHI
import or vendor update is required here. Local fixture coverage runs in the
existing Python Playwright CI lane; external sites are not core test fixtures.
CLI preflight passed locally; real Chromium execution awaits that CI lane because
the authoring environment has no installed Playwright/browser binaries.

## Shared documentation evidence, separate extraction contracts

`fixtures/page_text/python-method.html` is a small live DOM fragment from Python's
HTMLParser.feed documentation, captured 2026-10-09 at
https://docs.python.org/3/library/html.parser.html . Python documentation is under
PSF License Version 2 (https://docs.python.org/3/license.html). The capture has a
terminal newline added for storage. It covers definition lists, highlighted
signature spans, nested inline code and relative documentation links.

The recorded browser innerText did not show the `¶` permalink. Static page_text
and GHI selected-body extraction both retain it because CSS hides it in the live
page. Their token sequences matched in an optional local cross-check against GHI
#24; this is not an assertion of browser-visible equivalence. No parser was copied
between repositories. `test_documentation_dom.py` uses existing page_text and
find_elements, and verifies link resolution. Consumers may adopt this fixture at
an explicit revision for their own contract, without adopting a second parser.

The first real-browser CI exposed an omitted charset in the local test fixture:
UTF-8 bytes were displayed as mojibake. The fixture now declares UTF-8 explicitly;
it does not force the browser's encoding or hide the problem in comparison logic.

## Static extraction regressions from saved DOM

`tests/python/test_page_text_live_regressions.py` reuses saved Alpine and Deno
DOM fragments. Explicit `hidden` and inline `display:none` subtrees are excluded
without text deduplication; `aria-hidden` alone is not visual hiding. Hidden void
and nested elements must neither leak text nor swallow following siblings.
Stylesheet rules, CSS escapes/comments/variables and the full cascade are outside
the stdlib reader's visibility model. `hidden=until-found` is not discarded.

Preformatted spaces, tabs and literal linefeeds now survive normalization,
including terminal and repeated linefeeds. Tests compare Python strings, not
`print()` output, so a CLI output separator cannot conceal a content difference.
This is separate from the Markdown converter's round-trip extra-LF issue.

web-ui PR #43 adds an optional real Gradio/Chromium host regression which imports
this parser at an exact commit; acquisition and extraction remain owned here.

Known-unusable output paths are rejected before Playwright import or browser
launch: existing files/directories, dangling symlinks, and missing/non-directory
parents. The final write still uses exclusive creation to reject a path created
by another process during capture. Preflight does not certify later write access
or prevent races; it avoids network work for a destination already known invalid.
A local HTTP counter and import sentinel verify zero requests/browser imports
and unchanged evidence for these rejected cases.
