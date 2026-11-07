"""settle: a quiet window without an LLM (Stagehand quietWait rule); redaction ported from Stagehand."""
import functools, json, subprocess, sys, threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer; from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import btk  # noqa: E402

SCRIPT = ROOT / "scripts" / "btk.py"

SETTLES = """<!doctype html><meta charset="utf-8"><title>settles</title><p id="p">0</p>
<script>
  let n = 0;
  const t = setInterval(() => { document.getElementById('p').textContent = String(++n); if (n === 5) clearInterval(t); }, 80);
</script>
"""
NEVER = """<!doctype html><meta charset="utf-8"><title>never</title><p id="p">0</p>
<script>
  let n = 0;
  setInterval(() => { document.getElementById('p').textContent = String(++n); }, 50);
</script>
"""


def test_redaction_removes_keys_tokens_and_bearer_values():
    text = ("nav failed https://h/x?token=abcd1234efgh&a=1 Bearer abcdefghijkl "
            "sk-abcdefghij1234 AIza" + "x" * 32)
    cleaned = btk.redact_text(text)
    assert "abcd1234efgh" not in cleaned and "abcdefghijkl" not in cleaned
    assert "sk-abcdef[redacted]" in cleaned and "AIza[redacted]" in cleaned
    assert "a=1" not in cleaned  # query values are omitted regardless of key name
    envelope = btk.error_envelope("act", btk.InspectError(
        "bad https://h/?apiKey=zzzzzzzz", "retry with Bearer abcdefghijk"))
    assert "zzzzzzzz" not in json.dumps(envelope) and "abcdefghijk" not in json.dumps(envelope)


def test_settle_needs_no_locator_and_is_declared_as_an_action(tmp_path):
    assert "settle" in btk.ACT_ACTIONS
    assert btk._parse_step("settle - -", 1)["action"] == "settle"


def serve(directory: Path):
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(directory))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def act_settle(url, cwd, timeout_ms="5000"):
    return subprocess.run([sys.executable, str(SCRIPT), "act", url, "--action", "settle",
                           "--timeout-ms", timeout_ms, "--json"], cwd=cwd, capture_output=True, text=True, timeout=180)


@pytest.mark.browser
def test_settle_reports_quiet_when_mutations_stop_and_not_quiet_on_timeout(tmp_path):
    (tmp_path / "settles.html").write_text(SETTLES, encoding="utf-8")
    (tmp_path / "never.html").write_text(NEVER, encoding="utf-8")
    server = serve(tmp_path)
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        quiet = act_settle(f"{base}/settles.html", tmp_path)
        busy = act_settle(f"{base}/never.html", tmp_path, timeout_ms="1500")
    finally:
        server.shutdown()
    assert quiet.returncode == 0, quiet.stderr
    assert json.loads(quiet.stdout)["limits"]["quiet"] is True
    assert busy.returncode == 0, busy.stderr
    assert json.loads(busy.stdout)["limits"]["quiet"] is False
