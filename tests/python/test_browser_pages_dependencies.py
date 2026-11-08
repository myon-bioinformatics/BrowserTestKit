"""Keep render dependencies in the locked, no-dev browser lanes."""
from copy import deepcopy
from pathlib import Path
import shlex

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def _assert_browser_groups(workflow):
    for name in ("python-browser", "python-browser-optional"):
        job = workflow["jobs"][name]
        runs = [s["run"] for s in job["steps"] if "run" in s]
        commands = [line.strip() for run in runs for line in run.splitlines()
                    if "uv run " in line]
        assert commands, name
        for command in commands:
            assert "--locked --no-dev --group browser-test --group pages python" in command
        tests = [s for s in job["steps"] if "python -m pytest tests/python" in s.get("run", "")]
        assert len(tests) == 1
        step = tests[0]
        assert not job.get("continue-on-error") and not step.get("continue-on-error")
        assert not step.get("if")
        suffix = ('-m browser --junitxml=test-results/junit/python-browser-3.15.xml'
                  if name == "python-browser" else
                  '-m browser -k "firefox or webkit" --junitxml=test-results/junit/python-browser-optional-3.15.xml')
        assert shlex.split(step["run"]) == shlex.split(
            "uv run --locked --no-dev --group browser-test --group pages python -m pytest tests/python -q " + suffix)


def test_browser_lanes_include_pinned_page_renderer():
    _assert_browser_groups(yaml.safe_load((ROOT / ".github/workflows/playwright.yml").read_text()))


def test_browser_contract_rejects_missing_pages_or_masked_tests():
    workflow = yaml.safe_load((ROOT / ".github/workflows/playwright.yml").read_text())
    _assert_browser_groups(workflow)
    for name in ("python-browser", "python-browser-optional"):
        for mutation in ("missing-pages", "masked-failure", "skip"):
            broken = deepcopy(workflow)
            step = next(s for s in broken["jobs"][name]["steps"]
                        if "python -m pytest tests/python" in s.get("run", ""))
            if mutation == "missing-pages":
                step["run"] = step["run"].replace(" --group pages", "")
            elif mutation == "masked-failure":
                step["run"] += " || true"
            else:
                step["if"] = "false"
            with pytest.raises(AssertionError):
                _assert_browser_groups(broken)
