import ast, re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]

def test_browser_install_and_config_parity() -> None:
    config = (ROOT / "playwright.config.ts").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "playwright.yml").read_text(encoding="utf-8")
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
    matches = re.findall(rf"check_evidence\.py {re.escape(directory)} .*--expect ([\w,-]+)", workflow)
    assert matches, f"no check_evidence.py step for {directory}"
    return set(",".join(matches).split(","))

def test_evidence_expectations_match_the_test_matrices() -> None:
    config = (ROOT / "playwright.config.ts").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "playwright.yml").read_text(encoding="utf-8")
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
        assert set(re.findall(r"check_evidence\.py test-results/python .*--expect ([\w,-]+)", steps)[0].split(",")) == wanted, name


def test_browserless_lanes_do_not_install_engines_and_gate_browser_work():
    jobs = yaml.safe_load((ROOT / '.github/workflows/playwright.yml').read_text())['jobs']
    lightweight = {'python', 'test-locked', 'node-browserless'}
    for name in lightweight:
        commands = '\n'.join(step.get('run', '') for step in jobs[name]['steps'])
        assert 'playwright install' not in commands, name
    for name in ('python', 'test-locked'):
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
    jobs = yaml.safe_load((ROOT / '.github/workflows/playwright.yml').read_text())['jobs']
    for name in ('python', 'test-locked'):
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
    jobs = yaml.safe_load((ROOT / '.github/workflows/playwright.yml').read_text())['jobs']
    for name in ('python-browser', 'python-browser-optional'):
        commands = '\n'.join(step.get('run', '') for step in jobs[name]['steps'])
        uv_commands = re.findall(r'uv run[^\n]*', commands)
        assert uv_commands
        assert all(command.startswith('uv run --locked --no-dev --group browser-test ')
                   for command in uv_commands), name
