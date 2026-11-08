"""The live Node browser lane deliberately selects the exact Node 26 profile."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_live_browser_workflow_keeps_pins_and_exact_node26_guards():
    workflow = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())
    step = next(s for s in workflow['jobs']['node']['steps']
                if s.get('name') == 'Prepare and install the fixed 30-package Node browser environment')
    assert step['env'] == {
        'NANO_PARENT_REV': '8ce289a78754cf8ca0ae2ca3889b52281929fdc4',
        'NANO_HELPER_SHA256': '349921219cfdcd8a5054df962c7132324ac7134784f73147d1e8867ab6aa01b0',
    }
    script = step['run']
    for suffix in ('FEATURE', 'SOURCE_BLOBS', 'SELECTED', 'EXPECTED_PATHS'):
        assert 'nano.BTK_NODE26_PARSERS_' + suffix in script
        assert 'nano.BTK_' + suffix not in script
    assert 'hashlib.sha256(data).hexdigest() != digest' in script
    assert 'dependencies_only=True' in script
    assert "receipt['candidate_locked_package_records'] != 30" in script
    assert 'installed != expected or len(installed) != 30' in script
    assert 'nano.validate_projection(' in script
    assert 'nano.read_snapshot(checkout' in script
    assert "'live_checkout_tests': True" in script
    assert '|| true' not in script
