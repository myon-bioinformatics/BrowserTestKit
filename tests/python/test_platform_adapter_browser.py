"""Local app doubles test adapters without depending on the published Flutter site."""
import functools, importlib.util, json, threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer; from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('platform_probe', ROOT / 'tools/platform_probe.py')
pp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pp)
pa = pp.adapters


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
