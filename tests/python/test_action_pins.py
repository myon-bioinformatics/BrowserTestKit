"""Every executable remote Actions reference must identify immutable code."""
from pathlib import Path
import re

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def remote_refs(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == 'uses' and isinstance(child, str) and not child.startswith('./'):
                yield child
            else:
                yield from remote_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from remote_refs(child)


@pytest.mark.parametrize('path', sorted((ROOT / '.github/workflows').glob('*.yml')), ids=lambda p: p.name)
def test_all_remote_workflow_actions_and_reusable_jobs_pin_full_sha(path):
    refs = list(remote_refs(yaml.safe_load(path.read_text())))
    assert refs, f'{path.name}: no remote references inspected'
    for ref in refs:
        assert re.fullmatch(r'[^\s@]+@[0-9a-f]{40}', ref), f'{path.name}: mutable reference {ref}'


def test_python_compatibility_lanes_keep_their_executable_support_checks():
    jobs = yaml.safe_load((ROOT / '.github/workflows/runtime-compatibility.yml').read_text())['jobs']
    assert jobs['stdlib']['strategy']['matrix']['python'] == ['3.9', '3.10']
    commands = '\n'.join(step.get('run', '') for step in jobs['stdlib']['steps'])
    assert 'python -S -m unittest discover -s tests/stdlib' in commands
    assert 'uv pip install --no-deps .' in commands
    assert 'python -S scripts/image_diff.py --help' in commands
    floor = jobs['development-floor']['steps']
    assert next(step for step in floor if step.get('uses', '').startswith('astral-sh/setup-uv@'))['with']['python-version'] == '3.11'
    assert any(step.get('run') == 'uv sync --locked --all-groups' for step in floor)
    assert any(step.get('run') == 'python -S scripts/image_diff.py --help' for step in floor)


def test_node_execution_lanes_keep_node_26():
    jobs = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())['jobs']
    for name in ('node', 'node-browserless'):
        setup = next(step for step in jobs[name]['steps'] if step.get('uses', '').startswith('actions/setup-node@'))
        assert setup['with']['node-version'] == '26'
