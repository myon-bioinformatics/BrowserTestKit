# Project documentation HTML/DOM survey — 2026-10-09

Scope: requested project technologies, excluding image/audio converters.
Open WebUI and OpenAI were both attempted to cover the spoken ambiguity.
No logins, API/model invocations or site mutations were performed.

The companion JSON retains bounded raw source headings, browser DOM fragments,
browser innerText, final URLs, acquisition status and full HTTP-body hashes.
Full HTTP bodies are in the separately delivered documentation-html-dom-2026-10-09.zip.
This is sampled DOM evidence, **not full-page serialized live DOM**, and not a
claim that every code block, table or page has been tested.

| Targets | Result |
| --- | --- |
| Stagehand, Flutter, Dart, FastMCP, Gradio, Docker, Open WebUI, LibreChat | HTTP body and actual documentation DOM observed |
| Polars, HTTPX, selectolax, OR-Tools, Streamlit | HTTP body and actual documentation DOM observed |
| llama.cpp server README, TypeSafe introduction, TypeScript 7 announcement | HTTP body and actual documentation DOM observed |
| Playwright | HTTP 200 but Site Unavailable body in both HTTP and browser; not a successful docs capture |
| OpenAI | Redirected to platform root; no h1 or document body observed, not a successful docs capture |

Gradio's first HTTP attempt hit the 3 MB limit (curl exit 63). One explicit retry
with a 12 MB cap completed at 3,081,237 bytes; the ZIP retains both with a warning.
HTTP 200 alone never certifies a documentation response.

## Structural findings

- Stagehand, FastMCP and TypeSafe expose main#content-container and h1#page-title.
  This observed similarity is not a general automatic selector contract.
- Flutter/Dart use main#page-content with an article. Gradio's docs index had no
  main/article in this observation; do not silently assume either exists.
- Docker has main plus article; its raw heading includes formatting that differs
  from the browser serialization. Compare text separately from HTML bytes.
- Polars and HTTPX use md-main/md-content__inner. Whitespace in source headings
  is trimmed in innerText. selectolax's permalink paragraph symbol appears in
  textContent but not the observed innerText.
- OR-Tools redirected the browser to ?hl=ja while the HTTP request used the
  unsuffixed URL. Hidden devsite controls appear inside the h1. Do not count
  localization differences as parser failures. A small real pre fragment is saved.
- Streamlit has ten article elements; selecting article must detect ambiguity.
  Its h1 includes a large SVG. A small actual permalink fragment is retained
  instead of reserializing the whole heading.
- llama.cpp was read through the rendered server README, not an LLaMA model
  documentation substitute. Its first h1 is the directory title; article scopes
  the README and should be selected for subsequent content-specific samples.
- The TypeScript blog includes comment articles; the observed post article is
  #post-5246. Do not treat all article elements as the post.
- TypeSafe docs were accessible in this run. Earlier lab notes describing a
  different sandbox's block are historical; this does not verify its live API.

The JSON's exact_heading_match measures only equality of two recorded strings.
The Playwright error-page match must be ignored as content success.
For OR-Tools/Streamlit, dom_html is null because the large heading was not
persisted; fragment_html is the real smaller sample, not a reconstructed heading.

Use existing page_text/readers for later offline content checks. These acquisition
observations do not claim HTML/Markdown reversibility or fix known hidden-copy
and terminal-newline differences. Capture ownership remains browser-test-kit.

Shared compiler direction:
[Parent PR #64](https://github.com/myon-bioinformatics/myon-bioinformatics/pull/64).

## Extraction follow-up (2026-10-09)

The observations above describe acquisition at the time, not current defect
status. PR #54 commit `2d4da483c30907bd7a392b009b73366421ee0c8d` subsequently fixed
explicit hidden-copy exclusion and preservation of preformatted whitespace.
`tests/python/test_page_text.py` now checks the saved Alpine
hidden copy and Deno terminal newline against their recorded live text. The
FFmpeg/Pillow and corrected Tone.js/Remotion checks are described in the
[media follow-up](media-doc-dom-survey.md#revalidation-after-extraction-fixes-2026-10-09).
This does not retroactively make an error page, ambiguous heading, unavailable
whole-page DOM, or HTML/Markdown roundtrip successful.
