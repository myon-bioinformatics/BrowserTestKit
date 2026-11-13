"""Route trails: shared navigation, execution, URL contracts and real-browser runs."""

import asyncio
import copy
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


# ═══ · route trail ═══════════════════════════════════════════════════════════════════════════════════════

spec = importlib.util.spec_from_file_location("route_trail", ROOT / "tools" / "route_trail.py")
route_trail = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(route_trail)

REGISTRY = route_trail.load_registry((ROOT / "fixtures" / "route_trail_registry.json").read_text(encoding="utf-8"))
DART = """const pageEmojis = <String, String>{
  '/': '🏠',
  '/hub': '🗺️',
  '/screen1': '🌍',
};
const pageRouteAliases = <String, String>{
  '/old-hub': '/hub',
};"""


@pytest.mark.parametrize(("trail", "routes"), [
    ("🏠 → 🗺", ["/", "/hub"]),
    ("🏠 ↔ 🗺", ["/", "/hub", "/"]),
    ("🏠→🗺→🌍↔🕒", ["/", "/hub", "/screen1", "/tools/time", "/screen1"]),
    ("🏠 -> 🗺 <-> 🌍", ["/", "/hub", "/screen1", "/hub"]),
    ("🗺", ["/hub"]),
])
def test_trail_grammar(trail, routes):
    assert [r["route"] for r in route_trail.resolve(trail, REGISTRY)] == routes


def test_variation_selector_is_ignored_both_ways():
    """EMOJI_VARIATION_SELECTOR: a typed 🗺 (no U+FE0F) must find the registered 🗺️, and vice versa."""
    assert "️" in "🗺️" and "️" not in "🗺"
    assert route_trail.resolve("🗺", REGISTRY)[0]["route"] == route_trail.resolve("🗺️", REGISTRY)[0]["route"]


def test_urls_for_hash_and_path_routing():
    assert route_trail.resolve("🏠→🗺", REGISTRY, base="https://x.github.io/app/")[1]["url"] == "https://x.github.io/app/#/hub"
    assert route_trail.resolve("🗺", REGISTRY, base="https://x/app/", hash_routing=False)[0]["url"] == "https://x/app/hub"


def test_dart_map_literal_and_alias_values_are_skipped():
    registry = route_trail.load_registry(DART)
    assert registry == {"🏠": "/", "🗺": "/hub", "🌍": "/screen1"}
    assert route_trail.load_registry(DART, map_name="pageEmojis") == registry
    with pytest.raises(ValueError, match="map not found"):
        route_trail.load_registry(DART, map_name="missing")


@pytest.mark.parametrize(("text", "message"), [
    ('{"/a": "🗺", "/b": "🗺️"}', "two routes"),   # collide once the selector is removed
    ('{"a": "b"}', "/route"),
    ("no pairs here", "no /route"),
])
def test_bad_registries(text, message):
    with pytest.raises(ValueError, match=message):
        route_trail.load_registry(text)


@pytest.mark.parametrize(("trail", "message"), [
    ("🏠 → 🦄", "unknown page emoji: 🦄"),
    ("→ 🏠", "alternate"),
    ("🏠 → ", "alternate"),
    ("🏠 →→ 🗺", "alternate"),
])
def test_bad_trails(trail, message):
    with pytest.raises(ValueError, match=message):
        route_trail.resolve(trail, REGISTRY)


def test_cli(tmp_path, capsys):
    registry = str(ROOT / "fixtures" / "route_trail_registry.json")
    assert route_trail.main(["🏠 ↔ 🗺", "--registry", registry, "--base", "https://x/"]) == 0
    assert capsys.readouterr().out.splitlines() == ["https://x/#/", "https://x/#/hub", "https://x/#/"]
    assert route_trail.main(["🗺", "--registry", registry, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["route"] == "/hub"
    assert route_trail.main(["🦄", "--registry", registry]) == 2
    assert "unknown page emoji" in capsys.readouterr().err


# ═══ · route trail browser ═══════════════════════════════════════════════════════════════════════════════
# Real browsers exercise shared navigation, readiness and resumable execution.

BASE = (ROOT / "fixtures" / "route_trail_delayed.html").as_uri()


def registry():
    return route_trail.load_registry((ROOT / "fixtures" / "route_trail_execution.json").read_text())


@pytest.mark.browser
@pytest.mark.parametrize("browser_name", ("chromium", "firefox", "webkit"))
def test_walk_resume_and_failure(browser_name, tmp_path):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = getattr(pw, browser_name).launch()
        try:
            page = browser.new_page()
            iterator = route_trail.walk_steps(page, "🏠 → @hub → 🌍 → 🏠", registry(), base=BASE,
                check=lambda page, step: page.screenshot(path=str(tmp_path / f"{step['index']:03}.png"), full_page=True))
            assert next(iterator)["index"] == 0
            page.evaluate("window.retained = 42")
            assert len(list(iterator)) == 3
            assert page.evaluate("window.retained") == 42
            assert page.locator("#visits").inner_text() == "4"
            shots = sorted(tmp_path.glob("*.png"))
            assert len(shots) == 4
            assert all(p.read_bytes().startswith(b"\x89PNG\r\n\x1a\n") for p in shots)
            identities = [page.locator("#page").inner_text().split(maxsplit=1)[0],
                          page.title().rsplit(maxsplit=1)[-1]]
            for displayed_emoji in identities:  # Leading and trailing title conventions.
                assert route_trail.resolve(displayed_emoji, registry(), base=BASE)[0]["url"] == page.url
            bad = registry()
            bad["@hub"]["ready"]["text"] = "Never rendered"
            with pytest.raises(route_trail.TrailExecutionError) as err:
                route_trail.walk(page, "@home → @hub → @world", bad, base=BASE, timeout_ms=250)
            assert err.value.receipt["completed"] == 1
            assert err.value.receipt["error"]["phase"] == "ready"
            assert page.url.endswith("#/hub")
        finally:
            browser.close()


@pytest.mark.browser
@pytest.mark.parametrize("browser_name", ("chromium", "firefox", "webkit"))
def test_async_walk(browser_name, tmp_path):
    from playwright.async_api import async_playwright
    async def run():
        async with async_playwright() as pw:
            browser = await getattr(pw, browser_name).launch()
            try:
                page = await browser.new_page()
                async def check(page, step):
                    await asyncio.sleep(0)
                    assert await page.locator("#visits").inner_text() == str(step["index"] + 1)
                    await page.screenshot(path=str(tmp_path / f"{step['index']:03}.png"), full_page=True)
                result = await route_trail.awalk(page, "🏠 ↔ 🗺️", registry(), base=BASE, check=check)
                assert result["ok"] and result["completed"] == 3
                assert len(list(tmp_path.glob("*.png"))) == 3
            finally:
                await browser.close()
    asyncio.run(run())


@pytest.mark.browser
@pytest.mark.parametrize("backend", ("python", "python-async"))
def test_cli_run_from_stdlib_parent(backend):
    args = [sys.executable, "-S", str(ROOT / "tools/route_trail.py"), "🏠 ↔ 🌍",
            "--registry", str(ROOT / "fixtures/route_trail_execution.json"), "--base", BASE,
            "--run", "--backend", backend, "--json"]
    child = subprocess.run(args, capture_output=True, text=True, timeout=30)
    assert child.returncode == 0, child.stdout + child.stderr
    result = json.loads(child.stdout)
    assert result["ok"] and result["completed"] == 3
    assert all(s["verification"] == "url+marker" for s in result["steps"])


# ═══ · route trail execution ═════════════════════════════════════════════════════════════════════════════

spec_route_trail_execution=importlib.util.spec_from_file_location('trail_execution',ROOT/'tools/route_trail.py')
m=importlib.util.module_from_spec(spec_route_trail_execution);spec_route_trail_execution.loader.exec_module(m)
m=importlib.util.module_from_spec(spec_route_trail_execution);spec_route_trail_execution.loader.exec_module(m)
CATALOG=json.loads((ROOT/'fixtures/route_trail_execution.json').read_text())
REG=m.load_registry(json.dumps(CATALOG))
LEGACY={'🏠':'/','🗺':'/hub'}
class Page:
    def __init__(self):self.url='about:blank';self.visits=[]
    def goto(self,url,**kwargs):self.url=url;self.visits.append(url)
    def wait_for_url(self,predicate,**kwargs):assert predicate(self.url)
class AsyncPage(Page):
    async def goto(self,url,**kwargs):return super().goto(url,**kwargs)
    async def wait_for_url(self,predicate,**kwargs):return super().wait_for_url(predicate,**kwargs)

def test_ids_and_emojis_produce_identical_plans_and_keep_metadata():
    a=m.resolve('@home ↔ @hub',REG);b=m.resolve('🏠 ↔ 🗺',REG)
    assert a==b and [r['id'] for r in a]==['home','hub','home']
    assert a[1]['title']=='Hub' and a[1]['ready']['text']=='🗺️ Hub'
    a[0]['ready']['text']='mutated'
    assert a[2]['ready']['text']=='🏠 Home' and REG['@home']['ready']['text']=='🏠 Home'

@pytest.mark.parametrize('mutation',[
 lambda d:d['pages'].append(copy.deepcopy(d['pages'][0])),
 lambda d:d['pages'][1].update(emoji='🏠️'),
 lambda d:d['pages'][1].update(id='home'),
 lambda d:d['pages'][1].update(ready={'selector':'#page','script':'bad'}),
 lambda d:d['pages'][1].update(ready={'selector':''}),
 lambda d:d.update(unknown=True),
 lambda d:d['pages'][0].update(id='not an id'),
])
def test_invalid_catalogs_rejected(mutation):
    d=copy.deepcopy(CATALOG);mutation(d)
    with pytest.raises(ValueError):m.load_registry(json.dumps(d))

def test_full_plan_validation_precedes_navigation():
    p=Page()
    with pytest.raises(ValueError):m.walk(p,'🏠 → 🦄',LEGACY)
    with pytest.raises(ValueError):m.walk(p,'🏠',LEGACY,base='javascript:bad')
    assert p.visits==[]
    with pytest.raises(ValueError):m.validate_steps(m.resolve('🏠',LEGACY),0)
    with pytest.raises(ValueError):m.resolve('→'.join(['🏠']*1001),LEGACY)


def test_hooks_are_ordered_and_caller_page_remains_owned():
    p=Page();seen=[]
    result=m.walk(p,'🏠 ↔ 🗺',LEGACY,ready=lambda p,s:seen.append(('ready',s['index'])),check=lambda p,s:seen.append(('check',s['index'])))
    assert result['ok'] and result['completed']==3
    assert seen==[('ready',0),('check',0),('ready',1),('check',1),('ready',2),('check',2)]
    assert m.walk(p,'🏠',LEGACY)['ok'] and len(p.visits)==4
    assert all(s['verification']=='url' for s in result['steps'])


def test_pause_and_custom_navigation_keep_same_page():
    p=Page();it=m.walk_steps(p,'🏠 → 🗺',LEGACY,navigate=lambda p,s:p.goto(s['url']))
    assert next(it)['index']==0 and len(p.visits)==1
    p.retained='keep';assert next(it)['index']==1 and p.retained=='keep'
    with pytest.raises(StopIteration):next(it)


def test_failed_check_preserves_only_verified_prefix():
    p=Page()
    def check(p,s):return s['index']!=1
    with pytest.raises(m.TrailExecutionError) as caught:m.walk(p,'🏠 → 🗺 → 🏠',LEGACY,check=check)
    r=caught.value.receipt
    assert r['completed']==1 and r['total']==3 and not r['ok']
    assert r['error']['index']==1 and r['error']['phase']=='verify'
    assert len(p.visits)==2


def test_sync_rejects_async_hook_without_silently_ignoring_it():
    async def hook(p,s):return True
    with pytest.raises(m.TrailExecutionError,match='use awalk'):m.walk(Page(),'🏠',LEGACY,ready=hook)


def test_async_iterator_awaits_hooks_and_preserves_page_state():
    async def run():
        p=AsyncPage();order=[]
        async def ready(p,s):await asyncio.sleep(0);order.append(s['index'])
        it=m.awalk_steps(p,'🏠 → 🗺',LEGACY,ready=ready)
        assert (await anext(it))['index']==0 and p.visits==['http://localhost:8080/#/']
        p.retained='keep';assert (await anext(it))['index']==1 and p.retained=='keep'
        assert order==[0,1]
        with pytest.raises(StopAsyncIteration):await anext(it)
        assert (await m.awalk(p,'🏠',LEGACY))['ok']
    asyncio.run(run())


def test_cleanup_failure_does_not_hide_primary_failure():
    r=m._receipt([],2,{'phase':'ready','message':'primary'})
    def close():raise RuntimeError('cleanup')
    assert m._close_result(r,close)['error']['message']=='primary'
    assert r['cleanup_error']=='cleanup'


def test_bridge_is_one_process_and_checks_receipt(monkeypatch):
    steps=m.resolve('🏠 ↔ 🗺',LEGACY);calls=[]
    def run(cmd,**kw):
        calls.append((cmd,kw));request=json.loads(kw['input']);rows=[{**s,'index':i,'status':'passed','observed_url':s['url']} for i,s in enumerate(request['steps'])]
        return SimpleNamespace(stdout=json.dumps(m._receipt(rows,len(rows))),stderr='',returncode=0)
    monkeypatch.setattr(subprocess,'run',run)
    result=m.run_plan(steps,backend='node',node_cwd='/consumer')
    assert result['ok'] and len(calls)==1 and calls[0][1]['cwd']=='/consumer'
    assert calls[0][1]['timeout']==120 and 'shell' not in calls[0][1]

@pytest.mark.parametrize('raw',[
 '{"schema":"route-trail-run/1","ok":true,"total":3,"completed":0,"steps":[]}',
 'not json',
 json.dumps({'schema':'route-trail-run/1','ok':False,'total':3,'completed':0,'steps':[]}),
])
def test_invalid_or_stale_runtime_success_is_not_accepted(monkeypatch,raw):
    monkeypatch.setattr(subprocess,'run',lambda *a,**kw:SimpleNamespace(stdout=raw,stderr='failed runtime',returncode=0))
    r=m.run_plan(m.resolve('🏠 ↔ 🗺',LEGACY));assert not r['ok'] and r['error']['phase']=='protocol'


def test_overall_deadline_and_cli_failure_are_reported(monkeypatch,capsys):
    def timeout(*a,**kw):raise subprocess.TimeoutExpired(a[0],kw['timeout'])
    monkeypatch.setattr(subprocess,'run',timeout)
    assert m.main(['@home','--registry',str(ROOT/'fixtures/route_trail_execution.json'),'--run','--json'])==1
    r=json.loads(capsys.readouterr().out);assert not r['ok'] and r['error']['phase']=='process'


@pytest.mark.parametrize('trail', ['🏠 ↔ 🌍', '🏠 ↔ @world', '@home ↔ 🌍', '🏠→@world→🏠'])
def test_emoji_and_id_arguments_are_interchangeable(trail, capsys):
    assert m.main([trail, '--registry', str(ROOT/'fixtures/route_trail_execution.json'), '--json']) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows == m.resolve('@home ↔ @world', REG)


def test_multicodepoint_emoji_is_a_whole_identity():
    catalog = {'schema':'route-trail/1','pages':[
        {'id':'coder','emoji':'👩🏽‍💻','route':'/coder'},
        {'id':'flag','emoji':'🇯🇵','route':'/flag'},
        {'id':'key','emoji':'1️⃣','route':'/key'},
    ]}
    registry = m.load_registry(json.dumps(catalog))
    assert [s['route'] for s in m.resolve('👩🏽‍💻 → 🇯🇵 → 1️⃣', registry)] == ['/coder','/flag','/key']
    with pytest.raises(ValueError, match='unknown'):
        m.resolve('👩‍💻', registry)  # Skin tone and ZWJ are not discarded.


def test_node_helper_found_in_user_install(monkeypatch, tmp_path):
    import site
    helper = tmp_path / 'user/share/browser-test-kit/route_trail_node.mjs'
    helper.parent.mkdir(parents=True)
    helper.write_text('// installed asset')
    monkeypatch.setattr(m, '__file__', str(tmp_path/'site-packages/route_trail.py'))
    monkeypatch.setattr(m.sys, 'prefix', str(tmp_path/'environment'))
    monkeypatch.setattr(site, 'getuserbase', lambda: str(tmp_path/'user'))
    steps = m.resolve('🏠', LEGACY)
    def run(cmd, **kwargs):
        assert cmd == ['node', str(helper.resolve())]
        rows = [{**steps[0], 'index':0, 'status':'passed', 'observed_url':steps[0]['url']}]
        return SimpleNamespace(stdout=json.dumps(m._receipt(rows,1)), stderr='', returncode=0)
    monkeypatch.setattr(subprocess, 'run', run)
    assert m.run_plan(steps, backend='node')['ok']


# ═══ · execution url contract ════════════════════════════════════════════════════════════════════════════
# Expected URL decisions and preflight side effects for the Python and Node route trails.

def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tools' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


trail = load('route_trail')

NODE_PROGRAM = r"""
import {pathToFileURL} from 'node:url';
const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const {module, name, cases} = JSON.parse(Buffer.concat(chunks).toString('utf8'));
const fn = (await import(pathToFileURL(module).href))[name];
const out = [];
for (const args of cases) {
  try { out.push({ok: true, value: await fn(...args)}); }
  catch (error) { out.push({ok: false, error: String(error?.message ?? error)}); }
}
process.stdout.write(JSON.stringify(out));
"""


def node_calls(module, name, cases):
    done = subprocess.run(['node', '--input-type=module', '-e', NODE_PROGRAM],
                          input=json.dumps({'module': str(module), 'name': name, 'cases': cases}),
                          capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def python_calls(fn, cases):
    out = []
    for args in cases:
        try:
            out.append({'ok': True, 'value': fn(*args)})
        except Exception as error:
            out.append({'ok': False, 'error': str(error)})
    return out

VALID = [
    'http://a:1/', 'http://a:65535/', 'http://a:00001/',
    'https://localhost:443/x?q=1#f', 'HTTP://A/', 'http://example.com./',
    'http://127.0.0.1:65535/', 'http://0.0.0.0/', 'http://255.255.255.255/',
    'http://[::1]:65535/', 'https://[2001:db8::1]/',
    'http://[::ffff:192.0.2.1]:80/', 'http://[1:2:3:4:5:6:7:8]/',
    'file:///tmp/a', 'file://localhost/tmp/a', 'FILE:///C:/temp/a',
    'http://a/日本語?q=絵文字😀#内容', 'http://a/?next=http://b:99999/',
    'http://a/' + '😀' * (4096 - len('http://a/')),
]
INVALID = [
    'http://a:0/', 'http://a:00000/', 'http://a:65536/', 'http://a:99999/',
    'http://[::1]:0/', 'http://[::1]:65536/', 'http://a:-1/', 'http://a:+80/',
    'http://a:/', 'http://a:123456/', 'http://a:８０/', 'http://a:80:90/',
    'http://[::::]/', 'http://[1.2.3.4]/', 'http://[1:2:3]/',
    'http://[1:2:3:4:5:6:7:8:9]/', 'http://[::ffff:999.0.0.1]/',
    'http://[fe80::1%25eth0]/', 'http://::1/', 'http://[::1/', 'http://[::1]]/',
    'http://a b/', 'http://a\t/', 'http://a\n/', 'http://a/\n', 'http://a/\r',
    'http://a/\u00a0', 'http://a/\u2003', 'http://a/\ufeff', 'http://a/\x00',
    'http://a/\u0085', 'http://a/\\b', 'http://a\\@b/',
    'http://user:pass@a/', 'http://a%2fb/', 'http://a%00/', 'http://a<b/',
    'http://a>b/', 'http://a^b/', 'http://a|b/', 'http://a_b/', 'http://a"b/',
    'http:///a/', 'http:///', 'http://?a/', 'http://#a/', 'http:/a/', '//a/',
    'http://.a/', 'http://a..b/', 'http://-a/', 'http://a-/', 'http://a../',
    'http://256.0.0.1/', 'http://1.2.3.999/', 'http://127.1/', 'http://2130706433/',
    'http://0177.0.0.1/', 'http://0x7f.0.0.1/', 'http://example.123/', 'http://0x/',
    'http://xn--a/', 'https://例え.jp/', 'http://xn--r8jz45g.jp/',
    'http://' + 'a'*64 + '/', 'http://' + '.'.join(['a'*63]*4) + '/',
    'file:/tmp/a', 'file://', 'file://remote/tmp/a', 'file://localhost:80/tmp/a',
    'ftp://a/', 'javascript:alert(1)', ' http://a/',
    'http://a/' + '😀' * (4097 - len('http://a/')),
]


def step(url):
    return {'emoji': '🏠', 'route': '/', 'url': url}


@pytest.mark.parametrize('url,expected', [(u, True) for u in VALID] + [(u, False) for u in INVALID])
def test_python_expected_url_decisions(url, expected):
    if expected:
        assert trail.validate_steps([step(url)]) == [step(url)]
    else:
        with pytest.raises(ValueError, match='execution URLs'):
            trail.validate_steps([step(url)])


@pytest.mark.skipif(shutil.which('node') is None, reason='node unavailable')
def test_node_expected_decisions_and_native_parser_agree():
    cases = [[[step(url)]] for url in VALID + INVALID]
    actual = node_calls(ROOT / 'tools/route_trail_node.mjs', 'validateSteps', cases)
    assert [r['ok'] for r in actual] == [True]*len(VALID) + [False]*len(INVALID)
    # An independent implementation checks the positive oracle is browser-parseable.
    parsed = subprocess.run(['node', '--input-type=module', '-e',
        'const urls=JSON.parse(process.argv[1]);for(const u of urls)new URL(u);', json.dumps(VALID)],
        capture_output=True, text=True, timeout=30)
    assert parsed.returncode == 0, parsed.stderr


@pytest.mark.skipif(shutil.which('node') is None, reason='node unavailable')
def test_port_boundaries_have_expected_decisions_in_python_and_node():
    urls = [f'http://{host}:{port}/' for host in ('a', '127.0.0.1', '[::1]')
            for port in (0, 1, 65534, 65535, 65536, 99999)]
    expected = [port in (1, 65534, 65535) for _ in range(3)
                for port in (0, 1, 65534, 65535, 65536, 99999)]
    cases = [[[step(u)]] for u in urls]
    for results in (python_calls(trail.validate_steps, cases),
                    node_calls(ROOT / 'tools/route_trail_node.mjs', 'validateSteps', cases)):
        assert [r['ok'] for r in results] == expected


@pytest.mark.parametrize('bad_url', INVALID)
def test_invalid_later_step_prevents_python_navigation_and_process(monkeypatch, bad_url):
    plan = [step('http://localhost/'), step(bad_url)]
    monkeypatch.setattr(trail, 'resolve', lambda *a, **kw: plan)
    class Page:
        def goto(self, *a, **kw):
            pytest.fail('navigation began before whole-plan validation')
    def run(*a, **kw):
        pytest.fail('runtime subprocess began before whole-plan validation')
    monkeypatch.setattr(subprocess, 'run', run)
    with pytest.raises(ValueError):
        trail.walk(Page(), 'unused', {})
    with pytest.raises(ValueError):
        asyncio.run(trail.awalk(Page(), 'unused', {}))
    for backend in ('python', 'python-async', 'node'):
        with pytest.raises(ValueError):
            trail.run_plan(plan, backend=backend)


@pytest.mark.skipif(shutil.which('node') is None, reason='node unavailable')
def test_invalid_later_step_prevents_node_navigation():
    script = """
import {walk,walkSteps} from './tools/route_trail_node.mjs';
let calls=0;
const page={goto(){calls++;throw Error('navigation called');}};
for(const url of JSON.parse(process.argv[1])){
  const plan=[{emoji:'a',route:'/',url:'http://localhost/'},{emoji:'b',route:'/b',url}];
  for(const iterate of [false,true]){
    let rejected=false;
    try{if(iterate){for await(const row of walkSteps(page,plan)){};}else await walk(page,plan);}
    catch(e){if(!e.message.includes('execution URLs'))throw e;rejected=true;}
    if(!rejected)throw Error('invalid plan accepted');
  }
}
if(calls!==0)throw Error('navigation began');
"""
    result = subprocess.run(['node', '--input-type=module', '-e', script, json.dumps(INVALID)],
                            cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
