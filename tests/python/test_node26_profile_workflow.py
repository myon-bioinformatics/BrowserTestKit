"""The live Node browser lane deliberately selects the exact Node 26 profile."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_live_browser_workflow_keeps_pins_and_exact_node26_guards():
    workflow = yaml.safe_load((ROOT / '.github/workflows/playwright.yml').read_text())
    step = next(s for s in workflow['jobs']['node']['steps']
                if s.get('name') == 'Prepare and install the fixed 30-package Node browser environment')
    assert step['env'] == {
        'NANO_PARENT_REV': '2ed1cbc5cc9f7ef23d42537356b3aa6e59741f28',
        'NANO_HELPER_SHA256': '147ac0f0279c5ffaaf79732917774197fdfd29a565dcf315f9c99a0358956d02',
    }
    script = step['run']
    for suffix in ('FEATURE', 'SOURCE_BLOBS', 'SELECTED', 'EXPECTED_PATHS'):
        assert 'nano.BTK_NODE26_' + suffix in script
        assert 'nano.BTK_' + suffix not in script
    assert 'hashlib.sha256(data).hexdigest() != digest' in script
    assert 'dependencies_only=True' in script
    assert "receipt['candidate_locked_package_records'] != 30" in script
    assert 'installed != expected or len(installed) != 30' in script
    assert 'nano.validate_projection(' in script
    assert 'nano.read_snapshot(checkout' in script
    assert "'live_checkout_tests': True" in script
    assert '|| true' not in script
