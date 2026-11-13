"""btk plan: per-job install recommendation from configs and later commands."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("btk", ROOT / "tools" / "btk.py")
pip = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pip)

WORKFLOW = """name: x
jobs:
  tests:
    steps:
      - run: npx playwright install --with-deps chromium firefox
      - run: npx playwright test
  shots:
    steps:
      - run: npx playwright install --with-deps --only-shell chromium
      - run: npx playwright screenshot --browser=chromium http://x a.png
  py:
    steps:
      - run: python -m playwright install --with-deps chromium
      - run: python -m pytest
"""


def repo(tmp_path, config):
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text(WORKFLOW)
    (tmp_path / "playwright.config.js").write_text(config)
    return tmp_path


def test_channel_chromium_needs_full_build_only_where_the_config_is_used(tmp_path):
    rows = pip.plan(str(repo(tmp_path, "use: { channel: 'chromium' }, projects: [{ use: devices['Desktop Chrome'] }]")))
    by_line = {where: (state, text) for state, where, text in rows if state != "warn"}
    assert by_line[".github/workflows/ci.yml:5"] == ("suggest", "add --no-shell (playwright.config.js uses channel 'chromium')")
    assert by_line[".github/workflows/ci.yml:9"][0] == "ok"
    assert by_line[".github/workflows/ci.yml:13"] == ("suggest", "add --only-shell (job runs headless only)")
    assert ("warn", ".github/workflows/ci.yml:5", "installs firefox but no config project uses it") in rows


MIXED = """name: x
jobs:
  collect:
    steps:
      - run: python -m playwright install --with-deps chromium firefox webkit
      - run: |
          for browser in chromium firefox webkit; do
            collect --browser "$browser" --profile desktop
          done
          xvfb-run -a python tools/platform_probe.py collect --browser "$browser" --headed
  headed_only:
    steps:
      - run: python -m playwright install --with-deps chromium firefox webkit
      - run: xvfb-run -a python tools/platform_probe.py collect --browser chromium --headed
  branded:
    steps:
      - run: python -m playwright install --with-deps --no-shell chromium
      - run: python tools/platform_probe.py collect --browser chromium --channel chrome --headed
"""


def test_headed_and_headless_chromium_keep_both_builds(tmp_path):
    root = repo(tmp_path, "projects: [{ name: 'chromium' }, { name: 'firefox' }, { name: 'webkit' }]")
    (root / ".github" / "workflows" / "ci.yml").write_text(MIXED)
    by_line = {where: (state, text) for state, where, text in pip.plan(str(root)) if state != "warn"}
    assert by_line[".github/workflows/ci.yml:5"] == ("ok", "full + shell (job uses both the full build and the shell)")
    assert by_line[".github/workflows/ci.yml:13"] == ("suggest", "add --no-shell (job runs --headed)")
    assert by_line[".github/workflows/ci.yml:17"] == ("ok", "--no-shell (job runs --headed)")


def test_platform_matrix_keeps_the_unflagged_install(capsys):
    workflow = (ROOT / ".github" / "workflows" / "platform-matrix.yml").read_text()
    install = "python -m playwright install chromium firefox webkit"
    assert install in workflow
    assert "--with-deps" not in workflow
    assert workflow.index("playwright install-deps chromium firefox webkit") < workflow.index(install)
    assert "--no-shell" not in workflow
    probe = (ROOT / "tools" / "platform_probe.py").read_text()
    assert "headed: bool = False" in probe
    assert "headless=not headed" in probe
    assert 'for browser in chromium firefox webkit; do' in workflow
    assert 'collect --browser "$browser" --profile "$profile"' in workflow
    assert pip.plan_cli([str(ROOT)]) == 0
    out = capsys.readouterr().out
    assert "platform-matrix.yml:51 full + shell (job uses both the full build and the shell)" in out
    assert "suggest" not in out


def test_headless_config_suggests_only_shell_and_cli(tmp_path, capsys):
    root = repo(tmp_path, "projects: [{ name: 'chromium' }, { name: 'firefox' }]")
    rows = pip.plan(str(root))
    assert rows[0] == ("suggest", ".github/workflows/ci.yml:5", "add --only-shell (job runs headless only)")
    assert pip.plan_cli([str(root)]) == 1
    assert pip.plan_cli([str(tmp_path / "nope")]) == 2
    empty = tmp_path / "empty"
    empty.mkdir()
    assert pip.plan_cli([str(empty)]) == 0 and "no playwright install lines" in capsys.readouterr().out


def test_install_deps_is_not_a_browser_install():
    """`playwright install-deps` installs system libraries; it must not be planned as a browser install."""
    assert pip.INSTALL.search("npx playwright install-deps --dry-run chromium firefox webkit") is None
    assert pip.INSTALL.search("npx playwright install-deps chromium firefox webkit") is None
    match = pip.INSTALL.search("npx playwright install --only-shell chromium")
    assert match is not None and match.group(1).split() == ["--only-shell", "chromium"]


def test_find_browser_uses_a_build_already_on_disk(tmp_path):
    def build(*parts):
        path = tmp_path.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        return str(path)
    expected = str(tmp_path / "chromium-1248" / "chrome-linux64" / "chrome")
    assert pip.find_browser("chromium", expected, roots=[], system=())["source"] == "missing"
    older = build("chromium-1200", "chrome-linux64", "chrome")
    assert pip.find_browser("chromium", expected, roots=[], system=()) == {"source": "revision", "path": older, "revision": 1200}
    shell = build("chromium_headless_shell-1243", "chrome-headless-shell-linux64", "chrome-headless-shell")
    assert pip.find_browser("chromium", expected, roots=[], system=())["path"] == shell                # newest; headless runs the shell
    assert pip.find_browser("chromium", expected, headless=False, roots=[], system=())["path"] == older
    build("chromium_headless_shell-1248", "chrome-headless-shell-linux64", "chrome-headless-shell")
    assert pip.find_browser("chromium", expected, roots=[], system=())["source"] == "playwright"       # --only-shell installs count
    assert pip.find_browser("chromium", older, roots=[], system=())["source"] == "playwright"
    elsewhere = tmp_path / "cache"
    other = build("cache", "firefox-1500", "firefox", "firefox")
    assert pip.find_browser("firefox", str(tmp_path / "firefox-1510" / "firefox" / "firefox"), roots=[elsewhere], system=())["path"] == other
    assert pip.find_browser("webkit", None, roots=[], system=(older,))["source"] == "missing"          # no system WebKit
    assert pip.find_browser("chromium", None, roots=[], system=(older,)) == {"source": "system", "path": older}


def test_find_browser_same_revision_in_other_cache_needs_explicit_path(tmp_path):
    for engine, folder, executable in (
        ("firefox", "firefox-1500", "firefox/firefox"),
        ("chromium", "chromium_headless_shell-1500", "chrome-headless-shell-linux64/chrome-headless-shell"),
    ):
        expected = tmp_path / "configured" / f"{engine}-1500" / ("firefox/firefox" if engine == "firefox" else "chrome-linux64/chrome")
        other = tmp_path / "other" / folder / executable
        other.parent.mkdir(parents=True, exist_ok=True)
        other.touch()
        assert pip.find_browser(engine, str(expected), roots=[tmp_path / "other"], system=()) == {
            "source": "revision", "path": str(other), "revision": 1500}
