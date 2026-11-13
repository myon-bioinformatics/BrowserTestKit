"""Playwright Python smoke matrix, mobile context arguments and browser install/evidence parity."""

import ast
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


# ═══ · smoke ═════════════════════════════════════════════════════════════════════════════════════════════

FIXTURE = (ROOT / "fixtures" / "index.html").resolve().as_uri()
RESULTS = ROOT / "test-results" / "python"
BROWSERS = ("chromium", "firefox", "webkit")
PROFILES = (("desktop", None), ("mobile", "iPhone 13"))

_spec = importlib.util.spec_from_file_location("btk", ROOT / "tools" / "btk.py")
_btk = importlib.util.module_from_spec(_spec)
assert _spec.loader
_spec.loader.exec_module(_btk)

def build_context_args(pw, browser_name: str, device_name: str | None) -> dict:
    if not device_name:
        return {}
    args = dict(pw.devices[device_name])
    if browser_name == "firefox":
        # See DEVICE_DESCRIPTOR_CROSS_ENGINE in docs/antipatterns.md.
        args.pop("is_mobile", None)
    return args

def close_browser(browser, *, after_failure: bool) -> None:
    """Close the browser; after a test failure, report a close error instead of masking the failure."""
    try:
        browser.close()
    except Exception as close_exc:
        if not after_failure:
            raise
        # See EVIDENCE_MASKS_ROOT_FAILURE in docs/antipatterns.md.
        print(f"warning: browser.close() failed after the test failed: {type(close_exc).__name__}: {close_exc}", file=sys.stderr)

class _BrowserThatFailsToClose:
    def close(self) -> None:
        raise RuntimeError("close failed")

def test_close_browser_does_not_mask_an_earlier_failure(capsys) -> None:
    close_browser(_BrowserThatFailsToClose(), after_failure=True)
    assert "close failed" in capsys.readouterr().err
    with pytest.raises(RuntimeError, match="close failed"):
        close_browser(_BrowserThatFailsToClose(), after_failure=False)

@pytest.mark.browser
@pytest.mark.parametrize("browser_name", BROWSERS)
@pytest.mark.parametrize("profile,device_name", PROFILES)
def test_shared_fixture(browser_name: str, profile: str, device_name: str | None) -> None:
    from playwright.sync_api import sync_playwright
    RESULTS.mkdir(parents=True, exist_ok=True)
    png = RESULTS / f"{browser_name}-{profile}.png"
    meta = RESULTS / f"{browser_name}-{profile}.json"
    stage = "launch"
    page = None
    with sync_playwright() as pw:
        browser = None
        failed = False
        try:
            browser = getattr(pw, browser_name).launch()
            ctx_args = build_context_args(pw, browser_name, device_name)
            context = browser.new_context(**ctx_args)
            page = context.new_page()
            stage = "navigate"
            page.goto(FIXTURE)
            stage = "assert"
            assert page.locator("#unicode").inner_text() == "日本語 / Unicode ✓ / 🧪"
            stage = "interact"
            page.locator("#name").fill("Python")
            page.locator("#submit").click()
            stage = "assert"
            assert page.locator("#result").inner_text() == "Hello, Python!"
            stage = "screenshot"
            page.screenshot(path=png, full_page=True)
            stage = "artifact"
            width, height = _btk.png_dimensions(png.read_bytes())
            # "artifact" is relative to the metadata file, as in the Node lane; see btk.py check-evidence.
            meta.write_text(json.dumps({"runtime":"python","project":f"{browser_name}-{profile}","browser":browser_name,"profile":profile,"emulation":"partial" if browser_name=="firefox" and profile=="mobile" else "full","stage":"complete","artifact":png.name,"width":width,"height":height})+"\n", encoding="utf-8")
        except Exception as exc:
            failed = True
            emulation = "partial" if browser_name == "firefox" and profile == "mobile" else "full"
            meta.write_text(json.dumps({"runtime":"python","project":f"{browser_name}-{profile}","browser":browser_name,"profile":profile,"emulation":emulation,"stage":stage,"error":type(exc).__name__,"message":str(exc)[:500]})+"\n", encoding="utf-8")
            if page is not None:
                try:
                    page.screenshot(path=RESULTS / f"{browser_name}-{profile}-failure.png", full_page=True)
                except Exception as evidence_exc:
                    print(f"warning: failure screenshot unavailable: {type(evidence_exc).__name__}: {evidence_exc}", file=sys.stderr)
            raise
        finally:
            if browser is not None:
                close_browser(browser, after_failure=failed)


# ═══ · mobile profile ════════════════════════════════════════════════════════════════════════════════════

class FakePW:
    devices = {"iPhone 13": {"is_mobile": True, "has_touch": True, "viewport": {"width": 390, "height": 844}}}

def test_firefox_drops_unsupported_is_mobile_only() -> None:
    args = build_context_args(FakePW(), "firefox", "iPhone 13")
    assert "is_mobile" not in args
    assert args["has_touch"] is True
    assert args["viewport"] == {"width": 390, "height": 844}

def test_webkit_keeps_is_mobile() -> None:
    assert build_context_args(FakePW(), "webkit", "iPhone 13")["is_mobile"] is True


# ═══ · browser parity ════════════════════════════════════════════════════════════════════════════════════

def test_browser_install_and_config_parity() -> None:
    config = (ROOT / "playwright.config.ts").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    engines = {"chromium", "firefox", "webkit"}
    assert engines <= set(re.findall(r"name: '(chromium|firefox|webkit)'", config))
    jobs = yaml.load(workflow, Loader=yaml.BaseLoader)["jobs"]
    installs = {
        name: re.findall(r"playwright install(?: --with-deps)? ([^\n]+)",
                         "\n".join(step.get("run", "") for step in job.get("steps", [])))
        for name, job in jobs.items()
    }
    assert {name for name, lines in installs.items() if lines} == {"node", "python-browser", "python-browser-optional"}
    # Each lane installs exactly the engines its tests use; one identical retry line is allowed.
    expected = {
        "node": engines,
        "python-browser": {"chromium"},
        "python-browser-optional": {"firefox", "webkit"},
    }
    for name, wanted in expected.items():
        assert 1 <= len(installs[name]) <= 2 and len(set(installs[name])) == 1, name
        words = installs[name][0].strip().split()
        assert wanted == {w for w in words if not w.startswith("-")}, name
    # Headless Chromium lanes launch only the headless shell; the full build is never used in CI.
    for name in ("node", "python-browser"):
        assert "--only-shell" in installs[name][0].split(), name

def _evidence_expectations(workflow: str, directory: str) -> set[str]:
    # A directory can be checked by more than one lane; the expectations of all of them are collected.
    matches = re.findall(rf"btk\.py check-evidence {re.escape(directory)} .*--expect ([\w,-]+)", workflow)
    assert matches, f"no btk.py check-evidence step for {directory}"
    return set(",".join(matches).split(","))

def test_evidence_expectations_match_the_test_matrices() -> None:
    config = (ROOT / "playwright.config.ts").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert _evidence_expectations(workflow, "test-results") == set(re.findall(r"name: '([^']+)'", config))
    smoke = ast.parse((ROOT / "tests" / "python" / "test_smoke.py").read_text(encoding="utf-8"))
    matrix = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in smoke.body
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in {"BROWSERS", "PROFILES"}
    }
    projects = {f"{browser}-{profile}" for browser in matrix["BROWSERS"] for profile, _ in matrix["PROFILES"]}
    assert _evidence_expectations(workflow, "test-results/python") == projects
    lanes = {
        "python-browser": {f"chromium-{profile}" for profile, _ in matrix["PROFILES"]},
        "python-browser-optional": {f"{browser}-{profile}" for browser in ("firefox", "webkit") for profile, _ in matrix["PROFILES"]},
    }
    jobs = yaml.safe_load(workflow)["jobs"]
    for name, wanted in lanes.items():
        steps = "\n".join(step.get("run", "") for step in jobs[name]["steps"])
        assert set(re.findall(r"btk\.py check-evidence test-results/python .*--expect ([\w,-]+)", steps)[0].split(",")) == wanted, name


def test_browserless_lanes_do_not_install_engines_and_gate_browser_work():
    jobs = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())['jobs']
    lightweight = {'python', 'vendor-locked', 'node-browserless'}
    for name in lightweight:
        commands = '\n'.join(step.get('run', '') for step in jobs[name]['steps'])
        assert 'playwright install' not in commands, name
    for name in ('python', 'vendor-locked'):
        commands = '\n'.join(step.get('run', '') for step in jobs[name]['steps'])
        assert 'uv run --locked' in commands
        assert '--group browser' not in commands
        assert '-m "not browser"' in commands
        assert 'pip install' not in commands
    assert 'playwright' not in (ROOT / 'requirements-test-core.txt').read_text().lower()
    dev_group = re.search(r'(?ms)^dev = \[(.*?)^\]', (ROOT / 'pyproject.toml').read_text())
    assert dev_group and 'playwright' not in dev_group.group(1).lower()
    for name in ('node', 'python-browser'):
        assert lightweight <= set(jobs[name]['needs'])
        assert 'always()' not in jobs[name]['if']  # Failed fast checks must not start browser downloads.
    assert '-m browser' in '\n'.join(step.get('run', '') for step in jobs['python-browser']['steps'])


def test_direct_browser_test_imports_are_lazy_and_marked():
    for path in (ROOT / 'tests/python').glob('test_*.py'):
        tree = ast.parse(path.read_text())
        for node in tree.body:
            assert not (isinstance(node, ast.ImportFrom) and (node.module or '').startswith('playwright.')), path
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or not node.name.startswith('test_'):
                continue
            if any(isinstance(part, ast.ImportFrom) and (part.module or '').startswith('playwright.')
                   for part in ast.walk(node)):
                assert any(ast.unparse(d) == 'pytest.mark.browser' for d in node.decorator_list), (path, node.name)


def test_module_smoke_still_checks_noneditable_install_outside_checkout():
    jobs = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())['jobs']
    for name in ('python', 'vendor-locked'):
        step = next(s for s in jobs[name]['steps'] if s.get('name') == 'Scripts install and run as modules (python -m)')
        assert 'cd "$RUNNER_TEMP"' in step['run']
        assert 'uv run --locked --no-dev --no-editable --project "$GITHUB_WORKSPACE" python -m btk --help' in step['run']


def test_browser_jobs_use_test_dependencies_without_dev_pip():
    import tomllib

    project = tomllib.loads((ROOT / 'pyproject.toml').read_text())
    assert project['dependency-groups']['browser-test'] == [
        {'include-group': 'browser'}, 'pytest>=9.0.3', 'PyYAML>=6.0.3',
    ]
    assert project['tool']['uv']['dependency-groups']['browser-test'] == {
        'requires-python': '>=3.11',
    }
    jobs = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())['jobs']
    for name in ('python-browser', 'python-browser-optional'):
        commands = '\n'.join(step.get('run', '') for step in jobs[name]['steps'])
        uv_commands = re.findall(r'uv run[^\n]*', commands)
        assert uv_commands
        assert all(command.startswith('uv run --locked --no-dev --group browser-test ')
                   for command in uv_commands), name
