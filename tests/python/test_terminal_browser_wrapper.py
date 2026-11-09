import importlib.util, io, json, os, signal, stat, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / "tools" / "terminal_browser.py"

sys.path.insert(0, str(ROOT / "tools"))
import btk_events  # noqa: E402


def _fake_terminal_browser(tmp_path: Path, body: str) -> dict[str, str]:
    fake = tmp_path / "terminal-browser"
    fake.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}:{env.get('PATH', '')}"
    return env


def _events(stderr: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in stderr.splitlines() if line.startswith("{")]


def test_unavailable_is_distinct(tmp_path):
    env = os.environ.copy()
    env["PATH"] = str(tmp_path)
    result = subprocess.run([sys.executable, str(WRAPPER), "--check"], env=env, text=True, capture_output=True)
    assert result.returncode == 127
    assert result.stdout == ""
    assert '"event": "TERMINAL_BROWSER_UNAVAILABLE"' in result.stderr
    (event,) = _events(result.stderr)
    assert event["schema"] == "btk-event/1"
    assert btk_events.classify_terminal_browser(event) == "unavailable"


def test_passthrough_and_log_keep_events_separate(tmp_path):
    env = _fake_terminal_browser(
        tmp_path,
        "printf 'fake terminal-browser: %s\\n' \"$*\"\nprintf '{child-json-like-output}\\n'\n",
    )
    log = tmp_path / "tb.log"
    result = subprocess.run(
        [sys.executable, str(WRAPPER), "--log", str(log), "action", "--help"],
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0
    assert result.stdout == "fake terminal-browser: action --help\n{child-json-like-output}\n"
    assert log.read_text(encoding="utf-8") == result.stdout
    events = _events(result.stderr)
    assert [event["event"] for event in events] == [
        "terminal_browser_found",
        "terminal_browser_start",
        "terminal_browser_exit",
    ]
    assert all(event["source"] == "terminal-browser" for event in events)
    assert all(event["schema"] == "btk-event/1" for event in events)
    assert events[-1]["status"] == "passed"


def test_log_mode_tolerates_non_utf8_output(tmp_path):
    # \377 (octal) is byte 0xFF: not valid UTF-8 anywhere. Without
    # errors="replace" this crashes the wrapper with a UnicodeDecodeError
    # instead of producing a log.
    env = _fake_terminal_browser(tmp_path, "printf 'bad: \\377 end\\n'\n")
    log = tmp_path / "bad.log"
    result = subprocess.run(
        [sys.executable, str(WRAPPER), "--log", str(log), "action"],
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0
    assert "bad: � end\n" == result.stdout
    assert log.read_text(encoding="utf-8") == result.stdout


def test_double_dash_passes_top_level_child_flag_verbatim(tmp_path):
    env = _fake_terminal_browser(tmp_path, "printf '%s\\n' \"$*\"\n")
    result = subprocess.run(
        [sys.executable, str(WRAPPER), "--log", str(tmp_path / "version.log"), "--", "--version"],
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0
    assert result.stdout == "--version\n"
    start = next(event for event in _events(result.stderr) if event["event"] == "terminal_browser_start")
    assert start["argv"][-1] == "--version"
    assert "--" not in start["argv"][1:]


def test_default_mode_does_not_capture_child_stdio(tmp_path, monkeypatch):
    spec = __import__("importlib.util").util.spec_from_file_location("terminal_browser_wrapper", WRAPPER)
    module = __import__("importlib.util").util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    monkeypatch.setattr(module.shutil, "which", lambda _: "/fake/terminal-browser")
    seen = {}

    class Result:
        returncode = 0

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return Result()

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", [str(WRAPPER), "open", "http://127.0.0.1:8000"])

    assert module.main() == 0
    assert seen["argv"] == ["/fake/terminal-browser", "open", "http://127.0.0.1:8000"]
    assert "stdout" not in seen["kwargs"]
    assert "stderr" not in seen["kwargs"]


def test_sigint_is_handed_to_the_child_instead_of_raising(monkeypatch, capsys):
    """The default (inherited-stdio) mode must not let subprocess.run's
    KeyboardInterrupt handling SIGKILL the child. Instead, main() installs a
    SIGINT handler around the subprocess.run() call that only records the
    interrupt; the real SIGINT reaches the child too (same foreground process
    group), and the child decides how it exits. Here the fake subprocess.run
    simulates that delivery by invoking whatever handler main() installed, then
    returns as if the child had died from the SIGINT it received (-2)."""
    importlib_spec = importlib.util.spec_from_file_location("terminal_browser_interrupt", WRAPPER)
    assert importlib_spec is not None
    module = importlib.util.module_from_spec(importlib_spec)
    assert importlib_spec.loader is not None
    importlib_spec.loader.exec_module(module)

    monkeypatch.setattr(module.shutil, "which", lambda _: "/fake/terminal-browser")

    class Result:
        returncode = -2  # child terminated by the SIGINT it received (signal 2)

    def fake_run(argv, **kwargs):
        # Simulate the real SIGINT arriving while the child is still running:
        # it reaches the handler main() installed, not a KeyboardInterrupt.
        handler = module.signal.getsignal(module.signal.SIGINT)
        handler(module.signal.SIGINT, None)
        return Result()

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", [str(WRAPPER), "open", "http://127.0.0.1:8000"])

    assert module.main() == 130
    events = _events(capsys.readouterr().err)
    assert events[-1]["event"] == "terminal_browser_exit"
    assert events[-1]["returncode"] == 130
    assert events[-1]["signal"] == 2
    assert events[-1]["interrupted"] is True
    assert events[-1]["status"] == "interrupted"
    # The handler main() installed must not leak past the call.
    assert module.signal.getsignal(module.signal.SIGINT) is signal.default_int_handler


def test_default_mode_lets_child_finish_its_own_cleanup_after_sigint(tmp_path):
    """End-to-end: a real Ctrl-C (SIGINT to the whole process group) must not
    get the child SIGKILLed mid-cleanup. The fake child ignores the first
    SIGINT, sleeps (simulating cleanup work), and only then exits -- if the
    wrapper were still relying on subprocess.run's default KeyboardInterrupt
    handling, that sleep would be cut short by a kill()."""
    marker = tmp_path / "cleaned-up"
    env = _fake_terminal_browser(
        tmp_path,
        "trap 'true' INT\n"
        "sleep 0.3\n"
        f"touch {marker}\n"
        "exit 130\n",
    )
    wrapper = subprocess.Popen(
        [sys.executable, str(WRAPPER), "open", "http://127.0.0.1:8000"],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        time.sleep(0.1)
        os.killpg(wrapper.pid, signal.SIGINT)
        _, stderr = wrapper.communicate(timeout=5)
    finally:
        if wrapper.poll() is None:
            wrapper.kill()
            wrapper.wait()

    assert marker.exists(), "child was killed before it finished its own cleanup"
    assert wrapper.returncode == 130
    events = _events(stderr)
    assert events[-1]["event"] == "terminal_browser_exit"
    assert events[-1]["returncode"] == 130
    assert events[-1]["interrupted"] is True
    assert events[-1]["status"] == "interrupted"


def test_signal_exit_is_normalized(tmp_path):
    env = _fake_terminal_browser(tmp_path, "kill -TERM $$\n")
    result = subprocess.run(
        [sys.executable, str(WRAPPER), "action"],
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 143
    events = _events(result.stderr)
    assert events[-1]["event"] == "terminal_browser_exit"
    assert events[-1]["returncode"] == 143
    assert events[-1]["signal"] == 15
    assert events[-1]["status"] == "error"


def test_fallback_without_btk_events(monkeypatch):
    """tools/terminal_browser.py must stay usable copied alone, without
    tools/btk_events.py alongside it (its module docstring's "dependency-
    free" promise). Simulate that by making `import btk_events` fail inside
    a fresh load of the module, then check its inline classify/emit fallback
    still produces the same btk-event/1-shaped JSONL."""
    monkeypatch.setitem(sys.modules, "btk_events", None)  # forces ImportError on `import btk_events`
    spec = importlib.util.spec_from_file_location("terminal_browser_no_btk_events", WRAPPER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    assert module.btk_events is None
    assert module.classify_terminal_browser({"event": "terminal_browser_exit", "returncode": 0}) == "passed"
    assert module.classify_terminal_browser({"event": "terminal_browser_exit", "returncode": 1}) == "failed"
    assert module.classify_terminal_browser({"event": "TERMINAL_BROWSER_UNAVAILABLE"}) == "unavailable"
    assert (
        module.classify_terminal_browser(
            {"event": "terminal_browser_exit", "returncode": 130, "signal": 2, "interrupted": True}
        )
        == "interrupted"
    )

    stderr = io.StringIO()
    monkeypatch.setattr(module.sys, "stderr", stderr)
    module.emit("terminal_browser_found", executable="/x")
    payload = json.loads(stderr.getvalue().strip())
    assert payload["schema"] == "btk-event/1"
    assert payload["source"] == "terminal-browser"
    assert payload["event"] == "terminal_browser_found"
    assert payload["executable"] == "/x"
    assert "T" in payload["time"]


def test_check_rejects_child_arguments(tmp_path):
    env = _fake_terminal_browser(tmp_path, "exit 0\n")
    result = subprocess.run(
        [sys.executable, str(WRAPPER), "--check", "action"],
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 2
    assert "--check cannot be combined" in result.stderr
