"""Guard the real workflow's failure/evidence contract, using test-only PyYAML."""
import json
from copy import deepcopy; from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/playwright.yml"
COLLECTOR = (
    "myon-bioinformatics/myon-bioinformatics/.github/workflows/"
    "reusable-junit-identity.yml@9e3da7ae1846e9604ebc24bb5178f7de094a4e77"
)
COMMAND = (
    'uv run --locked python -m pytest tests/python -q -m "not browser" '
    "--junitxml=test-results/junit/python-3.15.xml"
)


def _step(job, name):
    matches = [step for step in job["steps"] if step.get("name") == name]
    assert len(matches) == 1, name
    return matches[0]


def _upload(job, name):
    matches = [step for step in job["steps"]
               if step.get("uses", "").startswith("actions/upload-artifact@")
               and step.get("with", {}).get("name") == name]
    assert len(matches) == 1, name
    return matches[0]


def _assert_contract(workflow):
    producer = workflow["jobs"]["python"]
    run = _step(producer, "Run Python tests with JUnit")
    assert producer.get("continue-on-error", False) is False
    assert run.get("continue-on-error", False) is False
    assert run.get("if") is None
    # The exact normalized command rejects || true, set +e and trailing exit 0.
    assert " ".join(run["run"].split()) == COMMAND
    assert run["env"]["FAILURE_EVIDENCE_DIR"] == "test-results/controlled-failure"
    for name, path in (
        ("junit-python-3.15", "test-results/junit/python-3.15.xml"),
        ("junit-controlled-python-3.15", "test-results/controlled-failure/"),
    ):
        upload = _upload(producer, name)
        assert producer["steps"].index(run) < producer["steps"].index(upload)
        assert upload.get("if") == "always()"
        assert upload.get("continue-on-error", False) is False
        assert upload["with"]["path"].strip() == path
        assert upload["with"]["if-no-files-found"] == "error"
        assert upload["with"]["retention-days"] == 14
    collector = workflow["jobs"]["junit-identity"]
    assert collector["needs"] == ["changes", "python", "node-browserless", "python-browser", "python-browser-optional"]
    assert collector["if"] == (
        "always() && needs.changes.outputs.product_changed == 'true'"
    )
    assert collector.get("continue-on-error", False) is False
    assert collector["uses"] == COLLECTOR
    reports = json.loads(collector["with"]["expected-reports"])
    assert isinstance(reports, list) and len(reports) == 5
    assert set(reports) == {
        "junit-python-3.15/python-3.15.xml",
        "junit-controlled-python-3.15/junit.xml",
        "junit-node-browserless/browserless-node.xml",
        "junit-python-browser-3.15/python-browser-3.15.xml",
        "junit-python-browser-optional-3.15/python-browser-optional-3.15.xml",
    }
    browser = _upload(workflow["jobs"]["python-browser"], "python-browser-evidence")
    assert browser["with"]["path"].splitlines() == [
        "test-results", "!test-results/junit/**",
        "!test-results/controlled-failure/**",
    ]


def test_failure_evidence_workflow_contract():
    _assert_contract(yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")))


def test_failure_evidence_contract_rejects_unsafe_mutations():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    _assert_contract(workflow)  # A broken baseline must not make all mutants pass.
    producer = workflow["jobs"]["python"]
    run_index = producer["steps"].index(_step(producer, "Run Python tests with JUnit"))
    job_path = ("jobs", "python")
    run_path = job_path + ("steps", run_index)
    collector_path = ("jobs", "junit-identity")
    mutations = [
        ("job masks failure", job_path + ("continue-on-error",), True),
        ("step masks failure", run_path + ("continue-on-error",), True),
        ("shell masks failure", run_path + ("run",), COMMAND + " || true"),
        ("shell disables fail-fast", run_path + ("run",), "set +e\n" + COMMAND),
        ("collector skips failed producer", collector_path + ("if",), "success()"),
        ("collector loses dependency", collector_path + ("needs",), []),
        ("collector missing report", collector_path + ("with", "expected-reports"), "[]"),
        ("collector unexpected report", collector_path + ("with", "expected-reports"),
         json.dumps(["junit-python-3.15/python-3.15.xml",
                     "junit-controlled-python-3.15/junit.xml", "unexpected/report.xml"])),
    ]
    for name in ("junit-python-3.15", "junit-controlled-python-3.15"):
        index = producer["steps"].index(_upload(producer, name))
        path = job_path + ("steps", index)
        mutations.extend([
            (name + " loses always", path + ("if",), None),
            (name + " ignores missing XML", path + ("with", "if-no-files-found"), "ignore"),
            (name + " retains raw for 90 days", path + ("with", "retention-days"), 90),
        ])
    browser_job = workflow["jobs"]["python-browser"]
    browser_index = browser_job["steps"].index(_upload(browser_job, "python-browser-evidence"))
    mutations.extend([
        ("raw JUnit duplicated", ("jobs", "python-browser", "steps", browser_index, "with", "path"),
         "test-results\n!test-results/controlled-failure/**\n"),
        ("controlled evidence duplicated", ("jobs", "python-browser", "steps", browser_index, "with", "path"),
         "test-results\n!test-results/junit/**\n"),
    ])
    for label, path, value in mutations:
        mutated = deepcopy(workflow)
        target = mutated
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        try:
            _assert_contract(mutated)
        except AssertionError:
            continue
        pytest.fail(f"unsafe workflow mutation escaped: {label}")
