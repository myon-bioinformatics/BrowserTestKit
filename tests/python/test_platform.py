"""Platform adapters and probes, hit probes and measured interaction, offline and in real browsers."""

import asyncio
import functools
import importlib.util
import json
import math
import os
import subprocess
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


# ═══ · platform adapter ══════════════════════════════════════════════════════════════════════════════════
# Offline adapter contracts, dataset identity and app verdict gating.

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
    pin = re.search(r'repository: myon-bioinformatics/PySaiyanWeBoo\n\s+ref: ([0-9a-f]+)', apps)
    assert pin and pin.group(1) == 'bbe5c0b0087f40ff3fffd53bec5111c2ba2d747d'
    assert 'ref: e77d2a3f7b66f37c899832b261450b2bcd29f3dc' in apps
    assert apps.count('repository: myon-bioinformatics/Ironmate') == 1  # one comparison, not two
    assert '--report app-matrix/screenshots/report.json --dataset app-matrix/dataset.json' in apps and '--embed-css' not in apps
    # web-ui keeps its page table and CSS in pysaiyanweboo.py (no examples/ or css/) and its helpers in tools/.
    assert '.web-ui/pysaiyanweboo.py --example flutter-pages' in apps and '.web-ui/tools/capture_matrix.py' in apps
    assert '/pysaiyanweboo.py\n            /tools/\n' in apps and '/web_ui.py' not in apps  # tools/ import pysaiyanweboo, not the old forwarding name
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


# ═══ · platform adapter browser ══════════════════════════════════════════════════════════════════════════
# Local app doubles test adapters without depending on the published Flutter site.

@pytest.fixture
def application(tmp_path):
    # This app has no __platformFacts / __layoutChecks hooks and uses different DOM ids.
    # Nonmonotonic responsive rules: passes at 260..287, fails at 288..303, passes >=304.
    # On the default 8 px grid the first pass is 264; with --width-step 1 it is exactly 260.
    (tmp_path / 'index.html').write_text('''<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">
    <style>body{margin:0}h1{margin:0;width:180px;height:32px}button{display:block;width:260px;height:40px}
    @media(min-width:288px) and (max-width:303px){button{width:320px}}
    #cover{display:none;position:fixed;inset:32px 0 0;background:white;z-index:5}</style>
    <h1 id="title">App title</h1><button id="increment" data-value="0" onclick="this.dataset.value=String(+this.dataset.value+1);localStorage.setItem('counter',this.dataset.value)">Increment</button><div id="cover"></div><script>document.querySelector("#increment").dataset.value=localStorage.getItem("counter") || "0";</script>''')
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(SimpleHTTPRequestHandler, directory=str(tmp_path)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{server.server_port}/index.html'
    config = {'schema': 'btk-platform-adapter/1', 'id': 'local-app', 'url': url,
              'elements': ['#title', '#increment'],
              'click': {'target': '#increment', 'observe': '#increment', 'attribute': 'data-value', 'before': '0', 'after': '1'}}
    path = tmp_path / 'adapter.json'
    path.write_text(json.dumps(config))
    try:
        yield url, config, path
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.browser
@pytest.mark.parametrize('profile', ['desktop', 'pixel'])
def test_app_without_fixture_hooks_finds_true_first_pass_and_clicks_each_width(application, profile, tmp_path):
    url, config, path = application
    record = pp.collect('chromium', profile, adapter=str(path), widths=(320, 384), evidence_dir=str(tmp_path/'shots'))
    assert record['verdict'] == 'pass'
    assert record['min_ok_width'] == 264  # binary search would miss this passing island
    assert [s['width'] for s in record['samples']] == [264, 320, 384]
    assert record['width_search']['step'] == 8 and all(o['width'] % 8 == 0 for o in record['width_search']['observations'])
    assert all(s['click']['before'] == '0' and s['click']['after'] == '1' for s in record['samples'])
    assert record['facts']['fonts']['sans'] > 0
    assert record['target']['url'] == url and record['target']['config_sha256']
    assert (tmp_path/'shots/width-264.png').exists()
    assert record['env']['device_validation'] == ('emulation' if profile == 'pixel' else 'desktop-browser')


@pytest.mark.browser
def test_exact_grid_finds_the_true_edge_and_device_widths_are_allowed(application, tmp_path):
    url, config, path = application
    record = pp.collect('chromium', 'desktop', adapter=str(path), widths=(390,), width_step=1)
    assert record['verdict'] == 'pass' and record['min_ok_width'] == 260
    assert record['width_search']['step'] == 1 and record['width_search']['low'] == 250
    assert [s['width'] for s in record['samples']] == [260, 390]


@pytest.mark.browser
def test_overlap_occlusion_missing_nodes_and_real_click_failure(application):
    from playwright.sync_api import sync_playwright
    url, config, _ = application
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={'width':390,'height':844})
            page.goto(url)
            assert pa.layout(page, config)['ok']
            page.locator('#increment').evaluate("e => e.style.transform='translateY(-20px)'")
            assert any(s.startswith('overlap:') for s in pa.layout(page, config)['failed'])
            page.locator('#increment').evaluate("e => e.style.transform='' ")
            page.locator('#cover').evaluate("e => e.style.display='block'")
            assert any(s.startswith('occluded:') for s in pa.layout(page, config)['failed'])
            # Even for Flutter forwarding mode, a coordinate click must change application state.
            assert not pa.click_check(page, {**config, 'flutter_semantics': True}, False)['ok']
            page.locator('#title').evaluate('e => e.remove()')
            assert any(s.startswith('missing_or_ambiguous:') for s in pa.layout(page, config)['failed'])
        finally:
            browser.close()


@pytest.mark.browser
def test_facts_probe_is_isolated_from_target_css_and_bindings_observe_null_transition(application):
    from playwright.sync_api import sync_playwright
    url, _, _ = application
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(url)
            page.add_script_tag(path=str(ROOT/'tools/platform_facts.js'))
            before = page.locator('#increment').evaluate('(e) => window.__btkPlatformFacts(e)')
            page.add_style_tag(content='div {border:10px solid red!important; padding:30px!important; box-sizing:border-box!important}')
            after = page.locator('#increment').evaluate('(e) => window.__btkPlatformFacts(e)')
            assert before['viewport']['scrollbar_classic'] == after['viewport']['scrollbar_classic']
            assert before['safe_area'] == after['safe_area']
            assert page.locator('btk-facts-probe').count() == 0
            page.set_content('''<button id="up" onclick="document.querySelector('#reset').removeAttribute('aria-disabled')">Increase</button>
                                <button id="reset" aria-disabled="true">Reset</button>''')
            cfg = {'elements':['[data-btk-probe="up"]'],
                   'bindings':{'up':{'selector':'button','text':'Increase'}, 'reset':{'selector':'button','text':'Reset'}},
                   'click':{'target':'[data-btk-probe="up"]','observe':'[data-btk-probe="reset"]',
                            'attribute':'aria-disabled','before':'true','after':None}}
            pa.prepare(page,cfg)
            assert pa.layout(page,cfg)['ok']
            assert pa.click_check(page,cfg,False)['ok']
        finally:
            browser.close()


@pytest.mark.browser
@pytest.mark.parametrize('mobile', [False, True])
def test_flutter_accessibility_activation_uses_one_appropriate_event(mobile):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={'width':393,'height':851},has_touch=mobile,is_mobile=mobile)
            page.set_content('''<meta name="viewport" content="width=device-width,initial-scale=1">
            <style>html,body{width:100%;height:100%;margin:0}</style><flt-glass-pane></flt-glass-pane>
            <button id="action">Ready</button><script>window.activationEvents=0;
            const p=document.createElement('flt-semantics-placeholder');p.setAttribute('aria-label','Enable accessibility');
            p.style.cssText='position:absolute;inset:0';document.body.append(p);
            p.addEventListener('click',e=>{window.activationEvents++;
              const r=p.getBoundingClientRect();
              if(window.mobile && (!e.isTrusted || Math.hypot(e.clientX-(r.x+r.width/2),e.clientY-(r.y+r.height/2))>=1)) return;
              document.body.append(document.createElement('flt-semantics'));p.remove();
            });</script>''')
            page.evaluate('(mobile)=>{window.mobile=mobile;if(!mobile) document.querySelector("flt-semantics-placeholder").style.cssText="position:absolute;left:-1px;top:-1px;width:1px;height:1px"}',mobile)
            cfg={'flutter_semantics':True,'elements':['#action']}
            pa.prepare(page,cfg)
            assert page.locator('flt-semantics').count()==1
            assert page.evaluate('window.activationEvents')==1
        finally:
            browser.close()


# ═══ · platform probe ════════════════════════════════════════════════════════════════════════════════════
# platform_probe offline: width search, verdict, dataset merge, summary gate, viewer HTML.

spec_platform_probe = importlib.util.spec_from_file_location("platform_probe", ROOT / "tools" / "platform_probe.py")
pp_platform_probe = importlib.util.module_from_spec(spec_platform_probe)
assert spec_platform_probe.loader
spec_platform_probe.loader.exec_module(pp_platform_probe)


def record(os_="Linux", browser="chromium", profile="desktop", headless=True, ok=True, click=True, channel=None):
    env = {"os": os_, "os_release": "x", "runner_image": None, "browser": browser, "channel": channel,
           "browser_version": "1.0", "profile": profile, "device": "Desktop", "headless": headless}
    env["id"] = pp_platform_probe.env_id(env)
    r = {"schema": pp_platform_probe.FACTS_SCHEMA, "env": env,
         "facts": {"window": {"frame_width": 0, "frame_height": 0}, "viewport": {"scrollbar_width": 0, "device_pixel_ratio": 1}},
         "layout": {"ok": ok, "failed": [] if ok else ["toolbar_clipped"]},
         "click": {"ok": click, "count": int(click), "input": "mouse", "point": [1, 2]}, "min_ok_width": 300}
    r["verdict"] = pp_platform_probe.verdict(r)
    return r


@pytest.mark.parametrize("threshold", [240, 241, 266, 407, 1279, 1280])
def test_narrowest_ok_width_matches_a_linear_scan_with_few_probes(threshold):
    calls = []

    def ok_at(width):
        calls.append(width)
        return width >= threshold
    assert pp_platform_probe.narrowest_ok_width(ok_at, 240, 1280) == threshold
    assert len(calls) <= 13  # log2(1040) + the two ends


def test_narrowest_ok_width_none_when_even_the_full_width_fails():
    assert pp_platform_probe.narrowest_ok_width(lambda w: False, 240, 1280) is None


def test_verdict_needs_layout_and_click():
    assert record()["verdict"] == "pass"
    assert record(ok=False)["verdict"] == "fail"
    assert record(click=False)["verdict"] == "fail"


def test_env_id_uses_channel_and_mode():
    assert record(channel="msedge", headless=False)["env"]["id"] == "Linux-msedge-desktop-headed"


def test_merge_dedupes_by_env_and_accepts_datasets(tmp_path):
    a, b = record(), record(os_="Windows")
    (tmp_path / "runs").mkdir()
    (tmp_path / "runs" / "a.json").write_text(json.dumps(a))
    (tmp_path / "runs" / "dup.json").write_text(json.dumps(a))
    (tmp_path / "other.json").write_text(json.dumps({"schema": "something-else"}))
    dataset = tmp_path / "d.json"
    dataset.write_text(json.dumps({"schema": pp_platform_probe.DATASET_SCHEMA, "records": [b]}))
    records = pp_platform_probe.load_records([str(tmp_path / "runs"), str(dataset), str(tmp_path / "other.json")])
    assert [r["env"]["id"] for r in records] == ["Linux-chromium-desktop-headless", "Windows-chromium-desktop-headless"]


def test_summary_gate_and_cli(tmp_path, capsys):
    (tmp_path / "ok.json").write_text(json.dumps(record()))
    assert pp_platform_probe.main(["summary", str(tmp_path)]) == 0
    (tmp_path / "ng.json").write_text(json.dumps(record(os_="macOS", ok=False)))
    assert pp_platform_probe.main(["summary", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "fail macOS-chromium-desktop-headless" in out and "failed=toolbar_clipped" in out
    assert pp_platform_probe.main(["summary", str(tmp_path / "missing")]) == 2
    assert pp_platform_probe.main(["collect", "--browser", "firefox", "--channel", "chrome", "--out", str(tmp_path)]) == 2


def test_render_embeds_data_safely(tmp_path):
    hostile = record()
    hostile["facts"]["platform"] = {"ua": "</script><script>alert(1)</script>"}
    page = pp_platform_probe.render([hostile, record(browser="webkit")])
    assert "</script><script>alert(1)" not in page and "\\u003c/script>" in page
    data = page.split('<script type="application/json" id="data">')[1].split("</script>")[0]
    assert len(json.loads(data)["records"]) == 2


def test_merge_and_render_create_the_output_directory(tmp_path):
    (tmp_path / "r.json").write_text(json.dumps(record()))
    assert pp_platform_probe.main(["merge", str(tmp_path / "r.json"), "--out", str(tmp_path / "new" / "dataset.json")]) == 0
    assert pp_platform_probe.main(["render", str(tmp_path / "new" / "dataset.json"), "--out", str(tmp_path / "x" / "index.html")]) == 0
    assert (tmp_path / "x" / "index.html").read_text(encoding="utf-8").startswith("<!doctype html>")


def test_errors_become_actions_annotations(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert pp_platform_probe.main(["summary", str(tmp_path)]) == 2
    assert "::error title=platform_probe summary::ValueError" in capsys.readouterr().out


def test_collect_failure_is_reported_with_the_environment(tmp_path, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("Browser closed unexpectedly\n  stack line")
    monkeypatch.setattr(pp_platform_probe, "collect", boom)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert pp_platform_probe.main(["collect", "--browser", "webkit", "--profile", "iphone", "--headed", "--out", str(tmp_path)]) == 2
    out = capsys.readouterr().out
    assert "::error title=platform_probe collect::webkit iphone headed: RuntimeError: Browser closed unexpectedly" in out
    assert "stack line" not in out


GOOD_LIBS = {"purify": "3.4.17", "sanitized": '<img src="x"><b>ok</b>', "seed": 0.18705921546491625, "sri_refused": True}


@pytest.mark.parametrize(("change", "failed"), [
    ({}, []),
    ({"purify": None}, ["purify_missing"]),
    ({"sanitized": '<img src="x" onerror="alert(1)"><b>ok</b>'}, ["sanitize_unsafe"]),
    ({"seed": 0.5}, ["seed_mismatch"]),
    ({"sri_refused": False}, ["sri_not_enforced"]),
    ({"sri_refused": None}, ["sri_not_enforced"]),
])
def test_adopted_libraries_must_load_behave_and_enforce_sri(change, failed):
    result = pp_platform_probe.libs_check({**GOOD_LIBS, **change})
    assert result == {"ok": not failed, "failed": failed}
    rec = record()
    rec["libs"] = result
    assert pp_platform_probe.verdict(rec) == ("pass" if not failed else "fail")


def test_records_without_libs_still_judge_on_layout_and_click():
    assert pp_platform_probe.libs_check(None) == {"ok": True, "failed": []}
    assert "libs" not in record() and record()["verdict"] == "pass"


def test_fixture_integrity_matches_js_lock():
    """Re-adopting a library must update the fixture's <script integrity> too."""
    import re
    lock = json.loads((ROOT / "js.lock.json").read_text(encoding="utf-8"))
    page = (ROOT / "fixtures" / "platform_probe.html").read_text(encoding="utf-8")
    for entry in lock["files"]:
        tags = re.findall(r'<script src="\.\./' + re.escape(entry["destination"]) + r'" integrity="([^"]+)"', page)
        assert tags and tags[0] == entry["sri"], entry["name"]
    assert len(set(re.findall(r'integrity="([^"]+)"', page))) == len(lock["files"]) + 1  # + the refused one


def test_adopted_files_match_js_lock():
    import base64
    import hashlib
    lock = json.loads((ROOT / "js.lock.json").read_text(encoding="utf-8"))
    for entry in lock["files"]:
        data = (ROOT / entry["destination"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["sha256"], entry["name"]
        assert "sha384-" + base64.b64encode(hashlib.sha384(data).digest()).decode() == entry["sri"]


def test_summary_reports_whether_meiryo_renders_but_never_fails_on_it():
    seen = {}
    for value in (True, False, None, "absent"):
        r = record()
        if value != "absent":
            r["facts"]["fonts"] = {"sans": 1, "meiryo": value}
        r["verdict"] = pp_platform_probe.verdict(r)
        seen[value] = pp_platform_probe.summary_lines([r])[0]
        assert r["verdict"] == "pass"
    assert "meiryo=yes" in seen[True] and "meiryo=no" in seen[False]
    assert "meiryo=unknown" in seen[None] and "meiryo=-" in seen["absent"]


# ═══ · platform probe browser ════════════════════════════════════════════════════════════════════════════
# platform-matrix in real browsers: every engine/profile passes, then the viewer's tabs show the differences.

RESULTS = ROOT / "test-results" / "platform-matrix"
CASES = [("chromium", "desktop"), ("firefox", "desktop"), ("webkit", "desktop"),
         ("chromium", "pixel"), ("webkit", "iphone"), ("firefox", "iphone")]


@pytest.mark.browser
@pytest.mark.parametrize(("browser_name", "profile"), CASES)
def test_environment_passes_and_records_facts(browser_name, profile):
    record = pp_platform_probe.collect(browser_name, profile)
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"{record['env']['id']}.json").write_text(pp_platform_probe.json.dumps(record, ensure_ascii=False), encoding="utf-8")
    assert record["verdict"] == "pass", (record["layout"], record["click"], record["libs"], record["facts"]["libs"])
    facts = record["facts"]
    assert facts["window"]["inner_width"] == facts["viewport"]["client_width"] + facts["viewport"]["scrollbar_width"]
    assert record["min_ok_width"] is not None and record["min_ok_width"] <= facts["window"]["inner_width"]
    # Reported on every engine: whether Meiryo renders (None when the generic fallbacks measure the same), and the declared stack.
    assert facts["fonts"]["meiryo"] in (True, False, None) and facts["fonts"]["body_family"]
    if profile != "desktop":
        assert record["click"]["input"] == "touch" and facts["window"]["inner_width"] < 500


@pytest.mark.browser
def test_viewer_tabs_highlight_what_changed(tmp_path):
    from playwright.sync_api import sync_playwright
    records = pp_platform_probe.load_records([str(RESULTS)]) if RESULTS.exists() else []
    if len(records) < 2:
        pytest.skip("run the environment tests first")
    page_path = tmp_path / "matrix.html"
    page_path.write_text(pp_platform_probe.render(records), encoding="utf-8")
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(error))
            page.goto(page_path.as_uri())
            first = page.locator("#head").inner_text()
            other = next(r for r in records if r["env"]["device"] != records[0]["env"]["device"]
                         and r["env"]["browser"] == records[0]["env"]["browser"])
            page.get_by_role("button", name=other["env"]["device"], exact=True).click()
            second = page.locator("#head").inner_text()
            changed = page.locator("td.ui-changed").count()
            rects = page.locator("#diagram rect").count()
        finally:
            browser.close()
    assert not errors and first != second and other["env"]["id"] in second
    assert changed > 0 and rects >= 3


# ═══ · hit probe ═════════════════════════════════════════════════════════════════════════════════════════

spec_hit_probe = importlib.util.spec_from_file_location("hit_probe", ROOT / "tools" / "hit_probe.py")
hit_probe = importlib.util.module_from_spec(spec_hit_probe)
assert spec_hit_probe.loader
spec_hit_probe.loader.exec_module(hit_probe)

W, H = 64, 48


def shape_probe(inside, calls=None):
    def probe(points):
        if calls is not None:
            calls.append(len(points))
        return [1.0 if inside(x, y) else 0.0 for x, y in points]
    return probe


SHAPES = {
    "box": lambda x, y: 10 <= x <= 40 and 8 <= y <= 30,
    "disc": lambda x, y: (x - 32) ** 2 + (y - 24) ** 2 <= 15 ** 2,
    "box_with_overlay": lambda x, y: 10 <= x <= 50 and 8 <= y <= 40 and not (30 <= x <= 50 and 8 <= y <= 25),
    "rotated": lambda x, y: abs((x - 32) * math.cos(0.5) + (y - 24) * math.sin(0.5)) <= 14
    and abs(-(x - 32) * math.sin(0.5) + (y - 24) * math.cos(0.5)) <= 9,
    "empty": lambda x, y: False,
}


@pytest.mark.parametrize("name", sorted(SHAPES))
def test_refine_matches_exhaustive_with_fewer_probes(name):
    inside = SHAPES[name]
    calls = []
    result = hit_probe.refine(W, H, shape_probe(inside, calls), stride=8)
    assert result["rows"] == hit_probe.exhaustive(W, H, shape_probe(inside))
    assert result["probes"] == sum(calls) < result["cells"] * 0.5
    assert len(calls) == result["rounds"]  # one batch per round, so a browser needs few round trips


def test_random_rotated_boxes_match_exhaustive():
    import random
    rng = random.Random(7)
    for _ in range(60):
        a, cx, cy = rng.uniform(0, 3.14), rng.uniform(20, 44), rng.uniform(15, 33)
        hw, hh = rng.uniform(8, 20), rng.uniform(5, 12)

        def inside(x, y):
            u = (x - cx) * math.cos(a) + (y - cy) * math.sin(a)
            v = -(x - cx) * math.sin(a) + (y - cy) * math.cos(a)
            return abs(u) <= hw and abs(v) <= hh
        result = hit_probe.refine(W, H, shape_probe(inside))
        assert result["rows"] == hit_probe.exhaustive(W, H, shape_probe(inside)), (a, cx, cy, hw, hh)
        assert result["probes"] < result["cells"] * 0.4


def test_old_corner_rule_misses_a_poking_corner():
    """CORNER_POKES_BETWEEN_CORNERS in docs/antipatterns.md.

    With this rotated box, settling a block from its four agreeing corners alone missed 3 cells
    (a corner of the box pokes in between them). The neighbour check must find them.
    """
    a, cx, cy, hw, hh = 2.27, 25.5, 32.0, 18.8, 5.2

    def inside(x, y):
        u = (x - cx) * math.cos(a) + (y - cy) * math.sin(a)
        v = -(x - cx) * math.sin(a) + (y - cy) * math.cos(a)
        return abs(u) <= hw and abs(v) <= hh
    assert hit_probe.refine(W, H, shape_probe(inside))["rows"] == hit_probe.exhaustive(W, H, shape_probe(inside))


def test_plain_box_is_rank_one_and_others_leave_residual():
    box = hit_probe.refine(W, H, shape_probe(SHAPES["box"]))
    fact = hit_probe.factorize(box["rows"], rank=1, scores=box["scores"])
    assert fact["residual"] == 0
    assert hit_probe.ranges(fact["factors"][0]["cols"]) == "10-40"
    assert hit_probe.ranges(fact["factors"][0]["rows"]) == "8-30"
    assert fact["factors"][0]["scale"] == 1.0
    for name in ("disc", "rotated"):
        result = hit_probe.refine(W, H, shape_probe(SHAPES[name]))
        assert hit_probe.factorize(result["rows"], rank=1)["residual"] > 0


def test_overlay_cut_is_exact_with_two_factors():
    result = hit_probe.refine(W, H, shape_probe(SHAPES["box_with_overlay"]))
    assert hit_probe.factorize(result["rows"], rank=1)["residual"] > 0
    assert hit_probe.factorize(result["rows"], rank=2)["residual"] == 0


def test_factors_never_cover_a_miss():
    result = hit_probe.refine(W, H, shape_probe(SHAPES["disc"]))
    for factor in hit_probe.factorize(result["rows"], rank=5)["factors"]:
        for y in range(H):
            if factor["rows"] >> y & 1:
                assert result["rows"][y] & factor["cols"] == factor["cols"]


def test_low_confidence_scores_are_refined_and_reported():
    # Fractional scores near the edge behave like low-confidence decisions.
    def probe(points):
        return [min(1.0, max(0.0, (20.5 - x) / 2 + 0.5)) for x, _ in points]
    result = hit_probe.refine(W, 8, probe, stride=8, margin=0.25)
    assert result["rows"] == hit_probe.exhaustive(W, 8, probe)
    assert result["uncertain"] and all(19 <= x <= 22 for x, _ in result["uncertain"])


def test_boundary_pairs_cross_the_edge():
    result = hit_probe.refine(W, H, shape_probe(SHAPES["box"]))
    pairs = hit_probe.boundary_pairs(result["rows"], W, limit=6)
    assert len(pairs) == 6
    for (ix, iy), (ox, oy) in pairs:
        assert SHAPES["box"](ix, iy) and not SHAPES["box"](ox, oy)
        assert abs(ix - ox) + abs(iy - oy) == 1


def test_input_errors():
    with pytest.raises(ValueError, match="width"):
        hit_probe.refine(0, 4, shape_probe(SHAPES["box"]))
    with pytest.raises(ValueError, match="0..1"):
        hit_probe.refine(4, 4, lambda pts: [2.0] * len(pts))
    with pytest.raises(ValueError, match="number of scores"):
        hit_probe.refine(4, 4, lambda pts: [])
    with pytest.raises(ValueError, match="budget"):
        hit_probe.refine(W, H, shape_probe(SHAPES["disc"]), max_probes=10)


def test_tiny_grids():
    assert hit_probe.refine(1, 1, lambda pts: [1.0])["rows"] == [1]
    assert hit_probe.refine(3, 1, shape_probe(lambda x, y: x == 1), stride=1)["rows"] == [2]


def test_to_json_and_cli(tmp_path, capsys):
    result = hit_probe.refine(W, H, shape_probe(SHAPES["box_with_overlay"]))
    data = hit_probe.to_json(result, rank=2)
    assert data["schema"] == "btk-hit-probe/1" and data["residual"] == 0
    assert len(data["map"]) == H and all(len(row) == W for row in data["map"])
    path = tmp_path / "map.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert hit_probe.main(["factorize", str(path), "--rank", "2"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].endswith("factors=2 residual=0") and len(out) == 3
    path.write_text(json.dumps({"map": ["01", "1"]}), encoding="utf-8")
    assert hit_probe.main(["factorize", str(path)]) == 2
    assert "equal length" in capsys.readouterr().err


# ═══ · hit probe browser ═════════════════════════════════════════════════════════════════════════════════
# Map each fixture target's real hit area with few probes and check it against clicks.
#
# The probe is the browser's own hit test (document.elementFromPoint); each cell's score
# is the fraction of its 2x2 integer sub-points that land on the target, a 0..1 value like a Jev
# confidence. Observations are collected first and asserted at the end (assert-late).

FIXTURE = (ROOT / "fixtures" / "hit_probe.html").resolve().as_uri()
HIT_PROBE_BROWSER_RESULTS = ROOT / "test-results" / "hit-probe"
BROWSERS = ("chromium", "firefox", "webkit")
CELL, PAD = 2, 8

SCORE_JS = """([id, pts, sub]) => { const t = document.getElementById(id);
  return pts.map(([x, y]) => { let hit = 0;
    for (const [dx, dy] of sub) { const e = document.elementFromPoint(x + dx, y + dy);
      if (e && (e === t || t.contains(e))) hit++; }
    return hit / sub.length; }); }"""
# Integer sub-points: engines round fractional hit-test coordinates differently.
SUB = [[0, 0], [1, 0], [0, 1], [1, 1]]


def observe(page, target):
    box = page.locator("#" + target).bounding_box()
    # Integer origin: engines round fractional hit-test and click coordinates differently.
    x0, y0 = int(box["x"] - PAD), int(box["y"] - PAD)
    width, height = int((box["width"] + 2 * PAD) // CELL), int((box["height"] + 2 * PAD) // CELL)
    calls = []

    def probe(points):
        calls.append(len(points))
        return page.evaluate(SCORE_JS, [target, [[x0 + x * CELL, y0 + y * CELL] for x, y in points], SUB])

    result = hit_probe.refine(width, height, probe, stride=8, margin=0.25)
    reference = hit_probe.exhaustive(width, height, probe)
    centre = lambda cell: (x0 + cell[0] * CELL + 1, y0 + cell[1] * CELL + 1)
    clicks = []
    for inside, outside in hit_probe.boundary_pairs(result["rows"], width, limit=6):
        pts = [centre(inside), centre(outside)]
        predicted = page.evaluate(SCORE_JS, [target, [list(p) for p in pts], [[0, 0]]])
        if predicted != [1.0, 0.0]:
            continue  # the cell centre itself sits on the other side; not a clean edge pair
        for point, expect in zip(pts, (1, 0)):
            before = int(page.locator("#" + target).get_attribute("data-clicks"))
            hit_before = page.evaluate("([x,y]) => { const e=document.elementFromPoint(x,y); return e && (e.id || e.tagName); }", list(point))
            page.mouse.click(*point)
            hit_after = page.evaluate("([x,y]) => { const e=document.elementFromPoint(x,y); return e && (e.id || e.tagName); }", list(point))
            after = int(page.locator("#" + target).get_attribute("data-clicks"))
            clicks.append({"point": point, "predicted": expect, "clicked": after - before,
                           "hit_before": hit_before, "hit_after": hit_after})
    return {"target": target, "evidence": hit_probe.to_json(result, rank=3), "matches_exhaustive":
            result["rows"] == reference, "batches": len(calls) - 1, "clicks": clicks}


def annotate(capsys, browser_name, text):
    """One GitHub Actions notice per browser, so CI shows the numbers without downloading artifacts."""
    if os.environ.get("GITHUB_ACTIONS"):
        with capsys.disabled():
            print(f"\n::notice title=hit-probe {browser_name}::{text[:900]}", flush=True)


def summary(observed):
    rows = []
    for target, obs in observed.items():
        ev = obs["evidence"]
        good = sum(c["clicked"] == c["predicted"] for c in obs["clicks"])
        rows.append(f"{target}: probes={ev['probes']}/{ev['cells']} exact={obs['matches_exhaustive']} "
                    f"clicks={good}/{len(obs['clicks'])} factors={len(ev['factors'])} residual={ev['residual']}")
    return "; ".join(rows)


@pytest.mark.browser
@pytest.mark.parametrize("browser_name", BROWSERS)
def test_hit_maps_need_few_probes_and_agree_with_clicks(browser_name, capsys):
    from playwright.sync_api import sync_playwright
    HIT_PROBE_BROWSER_RESULTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = getattr(pw, browser_name).launch()
        try:
            page = browser.new_page(viewport={"width": 480, "height": 320})
            page.goto(FIXTURE)
            observed = {t: observe(page, t) for t in ("plain", "rounded", "covered", "rotated")}
        except Exception as exc:
            annotate(capsys, browser_name, f"observe failed: {type(exc).__name__}: {exc}")
            raise
        finally:
            browser.close()
    (HIT_PROBE_BROWSER_RESULTS / f"{browser_name}.json").write_text(json.dumps(observed, indent=1) + "\n", encoding="utf-8")
    annotate(capsys, browser_name, summary(observed))

    for target, obs in observed.items():
        ev = obs["evidence"]
        assert obs["matches_exhaustive"], target
        assert ev["probes"] < ev["cells"] * 0.5, (target, ev["probes"], ev["cells"])
        assert obs["clicks"], f"{target}: no clean boundary pair to click"
        assert all(c["clicked"] == c["predicted"] for c in obs["clicks"]), (target, obs["clicks"])
    rank1 = {t: hit_probe.factorize(hit_probe._rows_from_map(o["evidence"]["map"])[0], rank=1)["residual"]
             for t, o in observed.items()}
    assert rank1["plain"] == 0  # a plain box is one rectangle
    assert rank1["rounded"] > 0 and rank1["rotated"] > 0  # corners / rotation leave a residual
    covered = observed["covered"]["evidence"]
    # The blocker takes the right part of the button away; the pointer-events:none ghost does not.
    assert covered["ones"] < observed["plain"]["evidence"]["ones"]
    assert covered["map"][PAD // CELL + 2][PAD // CELL + 2] == "1"


EDGE_JS = """([x, y]) => { const e = document.elementFromPoint(x, y); return e ? (e.id || e.tagName) : null; }"""


@pytest.mark.browser
@pytest.mark.parametrize("browser_name", BROWSERS)
def test_integer_edges_agree_and_fractional_edges_are_recorded(browser_name, capsys):
    """FRACTIONAL_HIT_COORDINATES in docs/antipatterns.md.

    At integer pixels around the plain box's left edge, elementFromPoint and a real click agree on
    every engine (asserted). At fractional pixels engines may round differently, so those results
    are only recorded, per engine, as evidence.
    """
    from playwright.sync_api import sync_playwright
    rows = []
    with sync_playwright() as pw:
        browser = getattr(pw, browser_name).launch()
        try:
            page = browser.new_page(viewport={"width": 480, "height": 320})
            page.goto(FIXTURE)
            box = page.locator("#plain").bounding_box()
            edge, y = int(box["x"]), int(box["y"] + box["height"] / 2)
            for x in (edge - 1, edge - 0.75, edge - 0.5, edge - 0.25, edge):
                hit = page.evaluate(EDGE_JS, [x, y]) == "plain"
                before = int(page.locator("#plain").get_attribute("data-clicks"))
                page.mouse.click(x, y)
                clicked = int(page.locator("#plain").get_attribute("data-clicks")) - before
                rows.append({"x": x, "hit": hit, "clicked": clicked})
        finally:
            browser.close()
    HIT_PROBE_BROWSER_RESULTS.mkdir(parents=True, exist_ok=True)
    (HIT_PROBE_BROWSER_RESULTS / f"{browser_name}-fractional-edge.json").write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")
    annotate(capsys, browser_name + " fractional edge",
             " ".join(f"x={r['x']}:hit={int(r['hit'])},click={r['clicked']}" for r in rows))
    integer = [r for r in rows if float(r["x"]).is_integer()]
    assert [(r["hit"], r["clicked"]) for r in integer] == [(False, 0), (True, 1)], integer


# ═══ · interaction ═══════════════════════════════════════════════════════════════════════════════════════
# Measured press and side-effect records: pure rules, CLI, and real browsers.

spec_interaction = importlib.util.spec_from_file_location("interaction", ROOT / "tools" / "interaction.py")
interaction = importlib.util.module_from_spec(spec_interaction)
spec_interaction.loader.exec_module(interaction)
URL = (ROOT / "fixtures" / "interaction.html").as_uri()
VIEW = {"width": 800, "height": 600}


def test_press_point_uses_integer_centre_and_viewport_ratio():
    assert interaction.press_point({"x": 40, "y": 10.25, "width": 161, "height": 47}, VIEW) == \
        {"x": 120, "y": 33, "visible_ratio": 1.0}
    half = {"x": 700, "y": 0, "width": 200, "height": 10}
    assert interaction.press_point(half, VIEW, ratio=0.5)["visible_ratio"] == 0.5
    with pytest.raises(ValueError, match="0.50 in the viewport"):
        interaction.press_point(half, VIEW)
    for box in (None, {"x": 0, "y": 0, "width": 0, "height": 5}):
        with pytest.raises(ValueError, match="no layout box"):
            interaction.press_point(box, VIEW)
    with pytest.raises(ValueError, match="ratio"):
        interaction.press_point(half, VIEW, ratio=0)


def _side(quiet, **extra):
    record = {"schema": "side-effects/1", "quiet": quiet, "requests": [], "dialogs": [], "page_errors": [],
              "popups": [], "storage": {"before": {"local": 0, "session": 0}, "after": {"local": 0, "session": 0}}}
    record.update(extra)
    return record


def test_cli_check_is_assert_late(tmp_path):
    press = {"schema": "pointer-press/1", "input": "mouse", "input_source": "explicit", "x": 1, "y": 2,
             "visible_ratio": 1.0, "box": {}, "hit": {"tag": "div", "self": False}}
    good = tmp_path / "good.jsonl"
    good.write_text("\n".join(json.dumps(r) for r in (press, _side(True))), encoding="utf-8")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([_side(False, requests=["http://x.invalid/"])]), encoding="utf-8")
    junk = tmp_path / "junk.json"
    junk.write_text(json.dumps({"schema": "other"}), encoding="utf-8")
    run = lambda *a: subprocess.run([sys.executable, "-S", str(ROOT / "tools" / "interaction.py"), "check", *a],
                                    capture_output=True, text=True)
    done = run(str(good))
    assert done.returncode == 0 and "self=False" in done.stdout and "PASS 2 records" in done.stdout
    done = run(str(bad), "--json")
    assert done.returncode == 1 and json.loads(done.stdout)["lines"] == ["0 side-effects NOT QUIET: requests"]
    assert run(str(junk)).returncode == 2


@pytest.mark.browser
@pytest.mark.parametrize("browser_name", ("chromium", "firefox", "webkit"))
def test_press_reaches_coordinate_hit_target_and_watch_records(browser_name):
    from playwright.sync_api import sync_playwright, Error as PlaywrightError
    with sync_playwright() as pw:
        browser = getattr(pw, browser_name).launch()
        try:
            page = browser.new_page(viewport=VIEW)
            page.goto(URL)
            target, log = page.locator("#target"), page.locator("#log")
            # Actionability sees the pane on top: the DOM click path cannot work.
            with pytest.raises(PlaywrightError):
                target.click(timeout=500)
            record = interaction.press(page, target, touch=False)
            # Annotation for CI when this fails (job logs may be unreadable from agents).
            print(f"::notice title=interaction {browser_name}::press {json.dumps(record)} log={log.inner_text()!r}")
            assert log.inner_text() == "pressed 1 mouse"
            assert record["hit"] == {"tag": "div", "self": False}
            assert record["visible_ratio"] == 1.0 and isinstance(record["x"], int) and record["input_source"] == "explicit"
            # A synthetic DOM click "passes" through a path no real input can take.
            target.evaluate("el => el.click()")
            assert log.inner_text() == "synthetic"

            page.goto(URL)
            w = interaction.watch(page)
            page.locator("#quiet").click()
            assert w.stop()["quiet"] is True
            w = interaction.watch(page)
            page.locator("#noisy").click()
            page.wait_for_timeout(200)
            report = w.stop()
            print(f"::notice title=interaction {browser_name}::watch {json.dumps(report)}")
            assert report["quiet"] is False
            assert report["dialogs"] == [{"type": "alert", "message": "noisy"}]
            assert "http://side-effect.invalid/beacon" in report["requests"]
            assert len(report["page_errors"]) == 1 and "noisy error" in report["page_errors"][0]

            # Same key, same value length: only the content fingerprint can change.
            page.goto(URL)
            w = interaction.watch(page)
            page.locator("#overwrite").click()
            report = w.stop()
            before, after = report["storage"]["before"], report["storage"]["after"]
            assert before["local"] is not None and after["local"]["keys"] == before["local"]["keys"]
            assert after["local"]["fingerprint"] != before["local"]["fingerprint"]
            assert after["session"] == before["session"]
            assert report["quiet"] is False and "bbbb" not in json.dumps(report)
        finally:
            browser.close()


@pytest.mark.browser
def test_async_press_and_watch_chromium():
    from playwright.async_api import async_playwright

    async def run():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page(viewport=VIEW, has_touch=True)
                await page.goto(URL)
                w = await interaction.awatch(page)
                record = await interaction.apress(page, page.locator("#target"))
                assert record["input"] == "touch" and record["input_source"] == "navigator.maxTouchPoints"
                assert await page.locator("#log").inner_text() == "pressed 1 touch"
                await page.locator("#noisy").evaluate("el => el.click()")
                await page.wait_for_timeout(200)
                report = await w.stop()
                assert report["dialogs"] == [{"type": "alert", "message": "noisy"}]
                assert report["quiet"] is False
                w = await interaction.awatch(page)
                await page.locator("#overwrite").evaluate("el => el.click()")
                report = await w.stop()
                assert report["quiet"] is False
                assert report["storage"]["after"]["local"]["fingerprint"] != report["storage"]["before"]["local"]["fingerprint"]
            finally:
                await browser.close()

    asyncio.run(run())


def test_node_and_python_share_the_page_functions():
    mjs = (ROOT / "tools" / "interaction.mjs").read_text(encoding="utf-8")
    assert interaction._STORAGE in mjs and interaction._INVENTORY in mjs
    assert f"INVENTORY_SELECTOR = '{interaction.INVENTORY_SELECTOR}'" in mjs


@pytest.mark.browser
@pytest.mark.parametrize("browser_name", ("chromium", "firefox", "webkit"))
def test_inventory_shows_merged_card_and_field_press(browser_name):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = getattr(pw, browser_name).launch()
        try:
            page = browser.new_page(viewport=VIEW)
            page.goto(URL)
            listed = interaction.inventory(page, selector='[role="button"], [role="combobox"]', limit=1)
            assert listed["truncated"] is True and len(listed["elements"]) == 1
            listed = interaction.inventory(page, selector='[role="button"], [role="combobox"]')
            card = next(e for e in listed["elements"] if e["aria_label"] == "Door card")
            field = next(e for e in listed["elements"] if e["id"] == "door-field")
            assert card["box"]["height"] > 3 * field["box"]["height"] and card["in_viewport"]
            # Raw attribute, not the accessible name: Playwright still names this button "quiet".
            quiet = interaction.inventory(page, selector="#quiet")["elements"][0]
            assert (quiet["role"], quiet["aria_label"], quiet["text"]) == (None, None, "quiet")
            assert page.get_by_role("button", name="quiet").count() == 1
            interaction.press(page, page.get_by_role("button", name="Door card"), touch=False)
            assert page.locator("#log").inner_text() == "card body"
            interaction.press(page, page.get_by_test_id("door-field"), touch=False)
            assert page.locator("#log").inner_text() == "field opened"
        finally:
            browser.close()


NAME_CASES = json.loads((ROOT / "fixtures" / "inventory-names.json").read_text(encoding="utf-8"))
NAMES_URL = (ROOT / "fixtures" / "inventory-names.html").as_uri()


@pytest.mark.browser
@pytest.mark.parametrize("browser_name", ("chromium", "firefox", "webkit"))
@pytest.mark.parametrize("touch", (False, True))
def test_inventory_raw_labels_and_accessible_names(browser_name, touch):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = getattr(pw, browser_name).launch()
        try:
            page = browser.new_page(viewport=VIEW, has_touch=touch)
            page.goto(NAMES_URL)
            for case in NAME_CASES:
                record = interaction.inventory(page, selector=case["selector"])
                assert record["total"] == 1
                element = record["elements"][0]
                assert element["aria_label"] == case["aria_label"]
                assert element["text"] == case["text"]
                assert "name" not in element
                assert page.get_by_role(case["role"], name=case["accessible_name"], exact=True).count() == 1
                if case["aria_label"] and case["aria_label"] != case["accessible_name"]:
                    assert page.get_by_role(case["role"], name=case["aria_label"], exact=True).count() == 0
            merged = interaction.inventory(page, selector="#merged-card")["elements"][0]
            assert merged["id"] == "door-card" and merged["box"]["height"] == 130
            # Python's Playwright distribution already bundles a Node driver and
            # JS API. Use those for a real second Page: no npm install is required
            # by the Python CI lanes, and both APIs run the same browser revision.
            import playwright
            driver = Path(playwright.__file__).parent / "driver"
            node = driver / ("node.exe" if sys.platform == "win32" else "node")
            selector = ", ".join(case["selector"] for case in NAME_CASES)
            payload = {"package": str(driver / "package"),
                       "module": (ROOT / "tools" / "interaction.mjs").as_uri(),
                       "browser": browser_name, "touch": touch, "viewport": VIEW,
                       "url": NAMES_URL, "selector": selector}
            script = r"""
const fs = require('node:fs');
const args = JSON.parse(fs.readFileSync(0, 'utf8'));
(async () => {
  const pw = require(args.package);
  const {inventory} = await import(args.module);
  const browser = await pw[args.browser].launch();
  try {
    const page = await browser.newPage({viewport: args.viewport, hasTouch: args.touch});
    await page.goto(args.url);
    console.log(JSON.stringify(await inventory(page, {selector: args.selector})));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
            result = subprocess.run([str(node), "-e", script], input=json.dumps(payload),
                                    capture_output=True, text=True, timeout=60)
            assert result.returncode == 0, result.stderr
            assert json.loads(result.stdout) == interaction.inventory(page, selector=selector)
        finally:
            browser.close()


@pytest.mark.browser
def test_async_inventory_names():
    from playwright.async_api import async_playwright

    async def run():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page(viewport=VIEW)
                await page.goto(NAMES_URL)
                for case in NAME_CASES:
                    record = await interaction.ainventory(page, selector=case["selector"])
                    element = record["elements"][0]
                    assert element["aria_label"] == case["aria_label"]
                    assert element["text"] == case["text"] and "name" not in element
                    assert await page.get_by_role(case["role"], name=case["accessible_name"], exact=True).count() == 1
            finally:
                await browser.close()

    asyncio.run(run())


# ═══ · engine selection ══════════════════════════════════════════════════════════════════════════════════
# BTK_BROWSER_ENGINES chooses which optional engines a browser lane collects (offline collect-only).

def collect(engines: str | None) -> str:
    env = dict(os.environ)
    env.pop("BTK_BROWSER_ENGINES", None)
    if engines is not None:
        env["BTK_BROWSER_ENGINES"] = engines
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/python/test_platform.py", "--collect-only", "-q", "-m", "browser"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=120,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout


def test_default_collects_every_engine_and_chromium_only_drops_the_optional_ones():
    everything = collect(None)
    assert "firefox" in everything and "webkit" in everything
    chromium = collect("chromium")
    assert "chromium" in chromium
    assert "firefox" not in chromium and "webkit" not in chromium
