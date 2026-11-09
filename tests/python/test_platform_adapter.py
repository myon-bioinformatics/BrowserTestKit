"""Offline adapter contracts, dataset identity and app verdict gating."""
import importlib.util, json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('platform_probe', ROOT / 'tools/platform_probe.py')
pp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pp)
pa = pp.adapters


def config():
    return {'schema': 'btk-platform-adapter/1', 'id': 'demo', 'url': 'https://example.test/',
            'elements': ['#title', '#action'],
            'click': {'target': '#action', 'observe': '#action', 'attribute': 'data-clicks', 'before': '0', 'after': '1'}}


@pytest.mark.parametrize('url', ['file:///tmp/a', 'javascript:alert(1)', 'https://user:pass@example.test/', ''])
def test_reject_non_http_and_credentials(url):
    with pytest.raises(ValueError):
        pa.validate_url(url)


def test_adapters_are_valid_and_target_identity_is_sensitive_to_url_and_rules():
    for path in (ROOT / 'adapters').glob('*.json'):
        pa.load_adapter(path)
    a = config()
    first = pa.target_identity(a, a['url'])
    assert first == pa.target_identity(a, a['url'])
    assert first['key'] != pa.target_identity(a, a['url'] + '#counter')['key']
    assert first['key'] != pa.target_identity({**a, 'elements': ['#action']}, a['url'])['key']


@pytest.mark.parametrize('change', [{'schema': 'bad'}, {'id': '../bad'}, {'elements': []}, {'click': {}},
                                    {'elements': ['#other']}])
def test_invalid_adapter_fails_before_browser_launch(tmp_path, change):
    path = tmp_path / 'adapter.json'
    path.write_text(json.dumps({**config(), **change}))
    with pytest.raises(ValueError):
        pa.load_adapter(path)


def test_same_environment_different_targets_do_not_overwrite(tmp_path):
    base = {'schema': pp.FACTS_SCHEMA, 'env': {'id': 'Linux-chromium-desktop-headless'}}
    records = [base, {**base, 'target': {'key': 'flutter-home-a'}},
               {**base, 'target': {'key': 'flutter-counter-b'}}]
    for n, record in enumerate(records):
        (tmp_path / f'{n}.json').write_text(json.dumps(record))
    assert len(pp.load_records([str(tmp_path)])) == 3
    page = pp.render(records)
    assert "['Target'" in page and '実機ではありません' in page


@pytest.mark.parametrize('layout,click', [(False, True), (True, False)])
def test_narrow_width_failure_gates_verdict_even_if_initial_passes(layout, click):
    record = {'layout': {'ok': True}, 'click': {'ok': True},
              'samples': [{'layout': {'ok': layout}, 'click': {'ok': click}}]}
    assert pp.verdict(record) == 'fail'


def test_fixture_workflow_remains_42_and_published_lane_is_separate():
    fixture = (ROOT / '.github/workflows/platform-matrix.yml').read_text()
    apps = (ROOT / '.github/workflows/platform-apps.yml').read_text()
    assert 'os: [ubuntu-24.04, windows-latest, macos-latest]' in fixture
    assert 'for profile in desktop iphone pixel' in fixture
    assert '--adapter' not in fixture
    assert '--adapter' in apps and 'needs: [plan, published-flutter]' in apps


def test_fixture_linux_system_setup_has_its_own_bounded_allowance():
    workflow = yaml.safe_load((ROOT / '.github/workflows/platform-matrix.yml').read_text())
    collect = workflow['jobs']['collect']
    steps = collect['steps']
    setup = next(step for step in steps if 'playwright install-deps' in step.get('run', ''))
    browsers = next(step for step in steps if step.get('name') == 'Install browsers')
    measure = next(step for step in steps if step.get('name') == 'Collect every engine, profile and mode')
    upload = next(step for step in steps if step.get('uses', '').startswith('actions/upload-artifact@'))

    assert setup['if'] == "runner.os == 'Linux'"
    assert setup['timeout-minutes'] == 20
    assert setup['run'].splitlines() == [
        'sudo DEBIAN_FRONTEND=noninteractive dpkg --configure -a',
        'uv run --locked --group browser python -m playwright install-deps chromium firefox webkit',
    ]
    assert browsers['timeout-minutes'] == 10
    assert collect['timeout-minutes'] == 90
    assert steps.index(setup) < steps.index(browsers) < steps.index(measure) < steps.index(upload)
    assert all(step.get('continue-on-error', False) is False for step in [collect, setup, browsers, measure, upload])
    assert 'exit "$status"' in measure['run']
    assert upload['if'] == 'always()'
    assert upload['with']['name'] == 'platform-${{ matrix.os }}'
    assert upload['with']['path'] == 'platform-results'
    assert upload['with']['if-no-files-found'] == 'error'


def test_published_lane_runs_three_os_on_dispatch_and_schedule_but_linux_on_pull_requests():
    apps = (ROOT / '.github/workflows/platform-apps.yml').read_text()
    assert 'workflow_dispatch:' in apps and "cron: '17 3 * * 1'" in apps
    assert """echo 'os=["ubuntu-24.04"]'""" in apps
    assert """echo 'os=["ubuntu-24.04","macos-latest","windows-latest"]'""" in apps
    assert 'runs-on: ${{ matrix.os }}' in apps and 'shell: bash' in apps
    assert '$RUNNER_OS-$BROWSER-$PROFILE' in apps and 'platform-app-${{ runner.os }}-' in apps


def test_representative_flutter_pages_share_header_contract_and_provenance():
    adapters = {p.stem: json.loads(p.read_text(encoding='utf-8')) for p in (ROOT / 'adapters').glob('flutter-*.json')}
    routes = {name: a['url'].split('#', 1)[1] for name, a in adapters.items()}
    assert routes == {'flutter-home': '/', 'flutter-hub': '/hub', 'flutter-clipboard-shelf': '/core/clipboard-shelf',
                      'flutter-now-timeline': '/tools/time/now-timeline', 'flutter-photo-studio': '/tools/media/photo-studio',
                      'flutter-http-request-draft': '/tools/http/request-draft', 'flutter-ui-showcase': '/examples/ui-showcase',
                      'flutter-screen1': '/screen1', 'flutter-counter': '/examples/counter-playground'}
    home = adapters['flutter-home']
    for name, adapter in adapters.items():
        assert adapter['id'] == name and adapter['flutter_semantics'] is True
        assert adapter['provenance'] == {'path': 'assets/assets/diagnostics/build_metadata.json',
                                         'field': ['repository', 'revision', 'sha']}
        assert adapter['elements'][:4] == home['elements']
    assert len({pa.target_identity(a, a['url'])['key'] for a in adapters.values()}) == len(adapters)


@pytest.mark.parametrize('provenance', [{'path': '/abs.json', 'field': ['a']}, {'path': '../up.json', 'field': ['a']},
                                        {'path': 'https://evil.test/x.json', 'field': ['a']}, {'path': 'a.json', 'field': []},
                                        {'path': 'a.json', 'field': 'sha'}, 'a.json'])
def test_provenance_must_be_a_relative_same_site_path_with_keys(tmp_path, provenance):
    path = tmp_path / 'adapter.json'
    path.write_text(json.dumps({**config(), 'provenance': provenance}))
    with pytest.raises(ValueError, match='provenance'):
        pa.load_adapter(path)


class _Response:
    def __init__(self, status, body):
        self.status, self.ok, self._body = status, 200 <= status < 300, body

    def body(self):
        return self._body


class _Page:
    def __init__(self, response=None, error=None):
        self.requested, self._response, self._error = [], response, error
        self.request = self

    def get(self, url, timeout):
        self.requested.append(url)
        if self._error:
            raise self._error
        return self._response


def test_read_provenance_reports_value_or_reason_without_raising():
    cfg = {**config(), 'url': 'https://example.test/app/#/hub',
           'provenance': {'path': 'assets/meta.json', 'field': ['repository', 'revision', 'sha']}}
    page = _Page(_Response(200, json.dumps({'repository': {'revision': {'sha': 'a' * 40}}}).encode()))
    out = pa.read_provenance(page, cfg, cfg['url'])
    assert page.requested == ['https://example.test/app/assets/meta.json']
    assert out == {'url': 'https://example.test/app/assets/meta.json', 'field': ['repository', 'revision', 'sha'],
                   'status': 200, 'value': 'a' * 40, 'error': None}
    assert pa.read_provenance(_Page(_Response(200, b'{"repository": {"revision": {"sha": null}}}')), cfg, cfg['url'])['error'] == 'field_missing'
    assert pa.read_provenance(_Page(_Response(404, b'')), cfg, cfg['url'])['error'] == 'http_status'
    assert pa.read_provenance(_Page(_Response(200, b'not json')), cfg, cfg['url'])['error'] == 'JSONDecodeError'
    assert pa.read_provenance(_Page(error=TimeoutError()), cfg, cfg['url'])['error'] == 'TimeoutError'
    assert pa.read_provenance(_Page(), config(), config()['url']) is None


def test_summary_shows_the_build_the_page_came_from():
    record = {'env': {'id': 'Linux-chromium-desktop-headless', 'browser_version': '1'}, 'verdict': 'pass',
              'min_ok_width': 256, 'facts': {'window': {'frame_width': 0, 'frame_height': 0},
              'viewport': {'scrollbar_width': 0, 'device_pixel_ratio': 1}}, 'layout': {'failed': []},
              'click': {'ok': True}, 'target': {'adapter': 'flutter-hub', 'provenance': {'value': 'b' * 40}}}
    assert pp.summary_lines([record])[0].endswith('target=flutter-hub build=' + 'b' * 12)
    record['target']['provenance'] = {'value': None, 'error': 'http_status'}
    assert pp.summary_lines([record])[0].endswith('build=unknown(http_status)')


def test_summary_reports_narrow_width_click_and_layout_failure():
    record = {'env':{'id':'Linux-chromium-desktop-headless','browser_version':'1'},
              'verdict':'fail', 'min_ok_width':300, 'facts':{'window':{'frame_width':0,'frame_height':0},
              'viewport':{'scrollbar_width':0,'device_pixel_ratio':1}}, 'layout':{'failed':[]}, 'click':{'ok':True},
              'samples':[{'width':320,'layout':{'failed':['overlap:#a:#b']},'click':{'ok':False}}]}
    line = pp.summary_lines([record])[0]
    assert '320px:overlap:#a:#b' in line and '320px:click' in line


def test_report_adds_the_web_ui_viewer_on_top_of_the_ironmate_comparison():
    import re
    apps = (ROOT / '.github/workflows/platform-apps.yml').read_text()
    pin = re.search(r'repository: myon-bioinformatics/web-ui\n\s+ref: ([0-9a-f]+)', apps)
    assert pin and pin.group(1) == 'b35ed9b77d4222b946d9813298243489a69e624a'
    assert 'ref: e77d2a3f7b66f37c899832b261450b2bcd29f3dc' in apps
    assert apps.count('repository: myon-bioinformatics/Ironmate') == 1  # one comparison, not two
    assert '--report app-matrix/screenshots/report.json --dataset app-matrix/dataset.json --embed-css' in apps
    assert "hashFiles('app-matrix/screenshots/report.json')" in apps and 'screenshots/viewer.html' in apps
    # The viewer joins on adapter ids: web-ui's page table must name exactly these adapters.
    assert {p.stem for p in (ROOT / 'adapters').glob('flutter-*.json')} == {
        'flutter-home', 'flutter-hub', 'flutter-clipboard-shelf', 'flutter-now-timeline', 'flutter-photo-studio',
        'flutter-http-request-draft', 'flutter-ui-showcase', 'flutter-counter', 'flutter-screen1'}


@pytest.mark.parametrize('step,floor', [(1, 250), (2, 250), (8, 256), (10, 250), (64, 256)])
def test_search_grid_is_a_default_that_can_be_switched(step, floor):
    assert pp.search_floor(step) == floor and pp.WIDTH_STEP == 8 and pp.MIN_SEARCH_WIDTH == 256


@pytest.mark.parametrize('step', [0, 65, 8.0, True])
def test_invalid_width_step_fails_before_browser_launch(step):
    with pytest.raises(ValueError, match='width_step'):
        pp.collect('chromium', 'desktop', width_step=step)


def test_cli_accepts_device_and_edge_widths_and_rejects_below_250(tmp_path, monkeypatch, capsys):
    seen = {}
    def fake_collect(*args, **kwargs):
        seen.update(kwargs)
        raise RuntimeError('stop before a browser')
    monkeypatch.setattr(pp, 'collect', fake_collect)
    pp.main(['collect', '--browser', 'chromium', '--out', str(tmp_path), '--widths', '390', '419', '420',
             '--width-step', '1'])
    assert seen['widths'] == (390, 419, 420) and seen['width_step'] == 1
    seen.clear()
    pp.main(['collect', '--browser', 'chromium', '--out', str(tmp_path), '--widths', '249'])
    assert not seen


def test_manual_runs_can_switch_grid_and_widths_while_schedule_keeps_defaults():
    apps = (ROOT / '.github/workflows/platform-apps.yml').read_text()
    assert "WIDTH_STEP: ${{ inputs.width_step || '8' }}" in apps and "WIDTHS: ${{ inputs.widths || '320 384' }}" in apps
    assert '--widths "${widths[@]}" --width-step "$WIDTH_STEP"' in apps and '^[0-9]+( [0-9]+)*$' in apps


def test_report_publishes_a_small_digest_and_job_summary():
    apps = (ROOT / '.github/workflows/platform-apps.yml').read_text()
    assert 'tools/app_digest.py app-results --ironmate .ironmate' in apps and 'name: platform-app-digest' in apps
    assert 'cat app-digest/summary.md >> "$GITHUB_STEP_SUMMARY"' in apps


def test_workflow_checks_inputs_before_fanout_and_matches_capture_width():
    apps = (ROOT / '.github/workflows/platform-apps.yml').read_text()
    plan = apps.split('  published-flutter:', 1)[0]
    assert '^([1-9]|[1-5][0-9]|6[0-4])$' in plan
    assert '10#$width >= 250 && 10#$width <= 3840' in plan
    assert 'shot=width-$((10#$width))' in plan
    assert apps.count('--shot "$SHOT"') == 2
    assert "- 'tools/app_digest.py'" in apps
    import re
    assert all(re.fullmatch(r'[0-9a-f]{40} # v[0-9]+(?:\.[0-9]+){0,2}', pin) for pin in re.findall(r'uses: actions/[^@]+@([^\n]+)', apps))


@pytest.mark.parametrize('step,widths,expected', [('8', '320 384', 'width-384'), ('1', '390 412', 'width-412'),
                                                  ('8', '0320 0384', 'width-384'), ('0', '320 384', None),
                                                  ('65', '320 384', None), ('8', '249', None),
                                                  ('8', '3841', None), ('8', '99999999999999999999', None)])
def test_workflow_plan_validates_actual_inputs(tmp_path, step, widths, expected):
    import os
    import subprocess
    import yaml
    workflow = yaml.safe_load((ROOT / '.github/workflows/platform-apps.yml').read_text())
    script = workflow['jobs']['plan']['steps'][0]['run']
    output = tmp_path / 'outputs'
    run = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', script], capture_output=True, text=True,
                         env={**os.environ, 'WIDTH_STEP': step, 'WIDTHS': widths, 'EVENT': 'workflow_dispatch',
                              'GITHUB_OUTPUT': str(output)})
    if expected is None:
        assert run.returncode == 2 and not output.exists()
    else:
        assert run.returncode == 0, run.stderr
        assert f'shot={expected}\n' in output.read_text()
