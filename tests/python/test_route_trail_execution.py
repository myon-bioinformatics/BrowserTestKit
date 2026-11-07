import asyncio, copy, importlib.util, json, subprocess
from pathlib import Path; from types import SimpleNamespace
import pytest
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('trail_execution',ROOT/'scripts/route_trail.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
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
