# Context economy: keeping agent conversations small

An agent session re-reads its whole conversation on every turn, and every tool result stays in it. A 90k-character dump costs tokens once when it arrives and again on every later turn. These are the techniques that kept long PR/CI sessions in this organization workable. Tools marked (#N) arrive with that PR; until it merges, they live on its branch.

## 1. Digest, don't dump
- Write the complete data to a file and keep only its path in the conversation.
- Report counts and the exceptions instead of the whole list: `check_evidence.py` prints one `ok` line per project and names only what is missing (#12).

## 2. Bounded by default, visibly truncated
- Every tool gets a cap (`--max-lines`, `--max-files`, `--max-chars`, `--limit`), and truncation is announced (`[... truncated: showing 120 of 431 lines]`), never silent (`UNBOUNDED_TOOL_OUTPUT`).
- Look at code with `grep -n` first, then read only that range (offset/limit).
- Never re-read a file you just edited; the edit either failed loudly or is what you wrote. Check `git diff --stat` before a full diff.

## 3. Oversized tool results
When an MCP result exceeds the limit ("result (92,458 characters) exceeds maximum allowed tokens. Output has been saved to …"), do not read the saved file whole:
- Comments or issues JSON: slice with `python3 -c` / `jq`, or reopen the saved file with offset/limit.
- JSONL: `jsonl_digest.py SAVED` (below).
- One fact: `grep -o` for it, or slice by character range with `python3 -c 'print(open(p).read()[a:b])'`.

## 4. Logs, tests, CI
- Keep the summary line: `pytest -q -p no:cacheprovider | tail -1`.
- CI: read check runs as name + conclusion. Fetch job logs with a tail (`tail_lines`) and only for the failing job. Search for the failing test's name instead of scrolling.
- The same check name appears once per workflow run. Judge each name by its newest run, and treat zero checks as not green (`ZERO_CHECKS_AS_GREEN`).


- Before touching `playwright install`: `btk.py plan .` prints per workflow job whether `--only-shell` (headless only), `--no-shell` (config `channel: 'chromium'` or a headed-only run), or neither flag (the job launches bundled Chromium both headless and headed) fits, instead of reading the Playwright driver source.

## 5. JSONL events and LLM/MCP test transcripts
- `jsonl_digest.py run.jsonl` prints the record count, counts per `event`/`type`/`kind`/`method`, the time range, error-like records (including a JSON-RPC `error` object or an MCP `result.isError`), and the last N records as one compact line each. `--show N` prints one record in full. It turned a 16 MB, 3,853-record session transcript into 7 lines.
- For raw MCP/JSON-RPC traffic (including inspector/proxy logs that nest the message under `message`/`payload`/`data`/`msg`), `jsonl_digest.py mcp.jsonl --pairs` matches each request to its response by `id` and prints one line per call (name, args, ok/ERROR, elapsed) plus a `calls: N (errors, unanswered), notifications: ...` summary.
- For structured test/judgment traces (for example mcp-toolcall-lab's `MCP_TOOLCALL_LOG`), filter and summarize fields instead of scanning rows by eye: `jsonl_digest.py trace.jsonl --where event=jev/backend_answer --count prob_source --count kind --num duration_ms --num answer.noul`.
- Make runs classifiable from one line: emit a machine-readable terminal event (for example `terminal_browser_exit` with `returncode`/`interrupted`) rather than relying on prose in the transcript.
- To summarize an LLM/MCP test run, give pass/fail counts, the first failure with its stage, and the exact IDs (run, job, test). Do not paste the transcript.

## 6. Decide with exit codes, not prose
Prefer tools that return 0/1/2 for yes/no/error so a shell `if` or `&&` can decide without reading prose.

## 7. Waiting
Subscribe to events (PR activity, webhooks) instead of polling. Cancel scheduled check-ins when nothing is pending, because each wake-up re-reads the whole conversation.

## 8. Compaction and handoff
Before a conversation is compacted, or when a new session takes over, keep one short state block and resume from it instead of replaying history:

```text
Repos/branches: owner/repo @ branch (PR #N, head <sha>, CI <run id>: green|red)
Done and verified: <what>, <how verified>
Done, not verified: <what>, <why not>
Pending decisions (owner's call): <question>
Next step: <one concrete action>
Constraints: <e.g. never force-push; minimize tokens>
```

Exact identifiers (SHA, PR, run ID, file path) survive summarization. Adjectives do not.

## 9. Small traps that cost whole turns
- Inspect invisible or private-use characters with `repr()`/`ascii()`. The rendered glyph depends on the platform: iOS shows U+E001/U+E002 as emoji. See `FORGEABLE_INBAND_PLACEHOLDER` in the markdown repository.
- Subagents start with no context. Use them only for broad fan-out, and hand them the exact identifiers from section 8.
