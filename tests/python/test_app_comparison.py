"""Offline coverage and evidence interpretation; real codecs tested via explicit checkout."""
import importlib.util, sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
spec = importlib.util.spec_from_file_location('app_comparison', ROOT / 'tools/app_comparison.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


def record(env='Linux', build='a', verdict='pass'):
    return {'env': {'os': env, 'browser': 'chromium', 'profile': 'desktop', 'browser_version': '1'},
            'target': {'adapter': 'p', 'url': 'https://example.test/', 'provenance': {'value': build}},
            'facts': {'viewport': {'device_pixel_ratio': 3 if env == 'macOS' else 1}},
            'layout': {'ok': True}, 'click': {'ok': True}, 'verdict': verdict,
            'samples': [{'width': 384, 'layout': {'ok': verdict == 'pass'}, 'click': {'ok': True}}]}


def report():
    return {'pages': ['p'], 'envs': ['Linux-chromium-desktop', 'macOS-chromium-desktop'],
            'reference': 'Linux-chromium-desktop', 'missing': [],
            'cells': [{'page': 'p', 'env': e, 'width': 384, 'height': 800, 'fit': {},
                       'difference': {'rgb_mae': 0}} for e in ['Linux-chromium-desktop', 'macOS-chromium-desktop']]}


def test_os_plan_keeps_missing_lanes_instead_of_inventing_observations():
    assert len(app.planned_envs(list(app.OS_NAMES))) == 15
    with pytest.raises(ValueError):
        app.planned_envs(['unknown'])
    r = app.annotate(report(), [record()], 'width-384')
    assert r['cells'][1]['observation'] is None
    assert r['cells'][1]['build_comparison'] == 'unknown'


def test_zero_pixel_difference_does_not_override_failed_click_or_layout_observation():
    r = app.annotate(report(), [record(), record('macOS', 'b', 'fail')], 'width-384')
    assert r['cells'][1]['difference']['rgb_mae'] == 0
    assert r['cells'][1]['observation']['verdict'] == 'fail'
    assert r['cells'][1]['observation']['shot_layout']['ok'] is False
    assert r['cells'][1]['build_comparison'] == 'different'
    assert r['observed_builds'] == ['a', 'b']
    assert 'not UI defects' in app.evidence_html(r)


def test_unknown_build_unmeasured_shot_and_duplicate_records_are_not_assumed_successful():
    r = app.annotate(report(), [record(build=None)], 'width-320')
    assert r['cells'][0]['build_comparison'] == 'unknown'
    assert r['cells'][0]['observation']['shot_click'] is None
    with pytest.raises(ValueError, match='ambiguous'):
        app.annotate(report(), [record(), record()], 'width-384')


def test_evidence_html_escapes_observed_values():
    r = app.annotate(report(), [record(build='<script>alert(1)</script>')], 'initial')
    rendered = app.evidence_html(r)
    assert '<script>' not in rendered and '&lt;script&gt;' in rendered
