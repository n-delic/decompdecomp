import json
from types import SimpleNamespace

import pytest

from decompnet.app import compiler_context
from decompnet.app import function_profile_suggestions as profiles
from decompnet.app.engine.compilers import CompilerMixin
from decompnet.app.permute import Permuter
from decompnet.network import Agent, NetworkError
from decompnet.project import compiler_for
from tests.test_prepared_evidence import prepared_net


def fixture(monkeypatch, auto=True):
    net,proj,fn=prepared_net()
    with net.store.tx() as db:
        cfg={'compiler':{'toolchain':'gcc','flags':['-O2']},'auto_compiler':auto,
             'compiler_ranges':[{'start':4092,'end':4104,'toolchain':'gcc','flags':['-O1']}]}
        db.execute('UPDATE projects SET config=?',(json.dumps(cfg),))
        db.execute("INSERT INTO functions(project_id,name,vram,size,status,matched_source,best_source,best_score) VALUES(1,'neighbor',4100,4,'matched','neighbor C','neighbor C',0)")
    net.cfg=lambda p:json.loads(p['config'])
    net.project_row=lambda slug:net._project(slug)
    def change(slug,update):
        with net.store.tx() as db:
            row=db.execute('SELECT config FROM projects WHERE slug=?',(slug,)).fetchone()
            cfg=json.loads(row['config']);update(cfg)
            db.execute('UPDATE projects SET config=? WHERE slug=?',(json.dumps(cfg),slug))
    net._update_cfg=change
    net.is_rom=lambda p:True;net.fork_slug=lambda s:None
    net.filecompilers=SimpleNamespace(applies=lambda s:False)
    net._toolchain_named=lambda cfg,c:c
    net._check_compiler=lambda *a:'checked: matches the game'
    net.auto_adjusts=CompilerMixin.auto_adjusts.__get__(net)
    net.auto_compiler=True
    net.suggest=CompilerMixin.suggest.__get__(net)
    net.resolve_suggestion=CompilerMixin.resolve_suggestion.__get__(net)
    net.local_agent=lambda name:(Agent(1,name,None),None)
    net.permuter=Permuter(net)
    net.rangechecks=SimpleNamespace(propose=lambda *a:pytest.fail('single function must not wait for neighbours'))
    net._compile=lambda *a:SimpleNamespace(ok=True,obj=b'object',effective_settings={'compiler':{'toolchain':'gcc','flags':['-O2','-ffast-math']}})
    net._evaluate=lambda *a:(object(),True,[])
    monkeypatch.setattr(compiler_context,'acceptable',lambda *a:True)
    return net,Agent(1,'autopilot',None)


@pytest.mark.parametrize('ranges',['','0x01000-0x01004'])
def test_explicit_suggestion_saves_its_source_and_profile_only_for_origin(monkeypatch,ranges):
    net,agent=fixture(monkeypatch)
    before=net.store.one("SELECT matched_source FROM functions WHERE name='neighbor'")['matched_source']
    source='\nint f(void) { return 2; }\n'
    said=net.suggest(agent,'game','exact flags',compiler='gcc',flags='-O2 -ffast-math',function='f',ranges=ranges,source=source)
    fn=net._function('f','game');cfg=net.cfg(net.project_row('game'))
    assert fn['status']=='matched' and fn['matched_source']==source
    assert compiler_for(cfg,4096)['flags']==['-O2','-ffast-math']
    assert compiler_for(cfg,4092)['flags']==compiler_for(cfg,4100)['flags']==['-O1']
    assert cfg['compiler']=={'toolchain':'gcc','flags':['-O2']}
    assert net.store.one("SELECT matched_source FROM functions WHERE name='neighbor'")['matched_source']==before
    assert cfg['suggestions'][0]['status']=='applied' and 'function only' in said
    assert not net.store.one('SELECT 1 FROM claims WHERE function_id=1')


def test_disabled_auto_retains_bundle_and_manual_apply_rejudges_it(monkeypatch):
    net,agent=fixture(monkeypatch,auto=False)
    said=net.suggest(agent,'game','exact flags',compiler='gcc',flags='-O2 -ffast-math',function='f',source='chosen source')
    assert 'off' in said and net._function('f','game')['status']=='open'
    with net.store.tx() as db:db.execute('DELETE FROM claims')
    net.resolve_suggestion('game',1,True)
    assert net._function('f','game')['matched_source']=='chosen source'
    assert not net.store.one('SELECT 1 FROM claims WHERE function_id=1')


@pytest.mark.parametrize('mode',['miss','compile','stale','overlap','busy'])
def test_failed_recheck_keeps_profile_source_and_neighbors(monkeypatch,mode):
    net,agent=fixture(monkeypatch);before=compiler_for(net.cfg(net.project_row('game')),4096)
    if mode=='miss':net._evaluate=lambda *a:(object(),False,['data differs'])
    if mode=='compile':net._compile=lambda *a:SimpleNamespace(ok=False)
    if mode=='stale':
        def evaluate(*args):
            with net.store.tx() as db:db.execute("UPDATE functions SET best_source='newer draft' WHERE id=1")
            return object(),True,[]
        net._evaluate=evaluate
    if mode=='overlap':
        with net.store.tx() as db:db.execute("INSERT INTO functions(project_id,name,vram,size) VALUES(1,'overlap',4098,4)")
    if mode=='busy':
        with net.store.tx() as db:
            db.execute("INSERT INTO agents(id,name,token_hash) VALUES(2,'other','y')")
            db.execute('UPDATE claims SET agent_id=2')
    said=net.suggest(agent,'game','exact flags',compiler='gcc',flags='-O2 -ffast-math',function='f',source='chosen source')
    assert 'not applied' in said
    assert compiler_for(net.cfg(net.project_row('game')),4096)==before
    assert net._function('f','game')['status']=='open'
    assert net.store.one('SELECT COUNT(*) n FROM attempts')['n']==0


def test_broader_suggestion_still_requires_range_evidence(monkeypatch):
    net,agent=fixture(monkeypatch);calls=[]
    net.rangechecks.propose=lambda *a:calls.append(a) or 'checking neighbours'
    said=net.suggest(agent,'game','broader evidence',compiler='gcc',flags='-O2 -ffast-math',function='f',ranges='0x01000-0x01008',source='chosen source')
    assert calls and 'checking neighbours' in said
    assert net._function('f','game')['status']=='open'


def test_legacy_waiting_single_function_range_can_be_applied(monkeypatch):
    net,agent=fixture(monkeypatch)
    def update(cfg):
        cfg['suggestions']=[{'id':96,'toolchain':'gcc','flags':['-O2','-ffast-math'],'ranges':[[4096,4100]],'status':'open'}]
        cfg['range_checks']=[{'id':303,'suggestion':96,'origin':4096,'origin_source':'saved winning C','status':'waiting','spans':[[4096,4100]],'toolchain':'gcc','flags':['-O2','-ffast-math']}]
    net._update_cfg('game',update)
    with net.store.tx() as db:db.execute('DELETE FROM claims')
    net.resolve_suggestion('game',96,True)
    assert net._function('f','game')['matched_source']=='saved winning C'
    cfg=net.cfg(net.project_row('game'))
    assert cfg['suggestions'][0]['status']=='applied'
    assert cfg['range_checks'][0]['status']=='function_applied'


def test_matched_or_changed_boundary_is_never_overwritten(monkeypatch):
    net,agent=fixture(monkeypatch);bundle=profiles.capture(net,net.project_row('game'),'f',[],'winning source')
    with net.store.tx() as db:db.execute('UPDATE functions SET size=8 WHERE id=1')
    with pytest.raises(NetworkError,match='boundary'):profiles.apply(net,agent,net.project_row('game'),bundle,'gcc',['-O2'])


def test_card_metadata_omits_private_source_and_header():
    item={'id':1,'function_match':{'function':'f','vram':4096,'size':4,'source':'private C','header':'private types'}}
    exposed=profiles.public(item)
    assert exposed=={'id':1,'function_scope':{'function':'f','vram':4096,'size':4}}
    assert 'source' in item['function_match']


def test_compiler_check_preserves_leading_blank_lines(monkeypatch):
    net,agent=fixture(monkeypatch);seen=[]
    net._judge_with=lambda *args:seen.append(args[-1]) or (True,object(),'')
    source='\n\nint f(){return 0;}\n'
    CompilerMixin._check_compiler(net,net.project_row('game'),'gcc',['-O2'], 'f',source)
    assert seen==[source]


def test_atomic_bundle_settles_only_same_profile_single_function_checks(monkeypatch):
    net,agent=fixture(monkeypatch)
    def update(cfg):
        cfg['range_checks']=[{'id':i,'suggestion':100+i,'origin':4096,'toolchain':'gcc',
                             'flags':['-O2','-ffast-math'] if i!=3 else ['-O3'],
                             'spans':[[4096,4100 if i!=2 else 4104]],'status':'waiting'} for i in (1,2,3)]
        cfg['suggestions']=[{'id':101,'status':'open'}]
    net._update_cfg('game',update)
    bundle=profiles.capture(net,net.project_row('game'),'f',[],'exact')
    profiles.apply(net,agent,net.project_row('game'),bundle,'gcc',['-O2','-ffast-math'])
    checks=net.cfg(net.project_row('game'))['range_checks']
    assert [r['status'] for r in checks]==['function_applied','waiting','waiting']
    # An already running range checker must not revert or disparage the settled match.
    from decompnet.app.rangecheck import RangeChecks
    stale={**checks[0],'status':'quirk'}
    RangeChecks(net)._save(net.project_row('game'),stale,'stale result',resolve='dismissed')
    RangeChecks(net)._finish(net.project_row('game'),stale,[],'quirk',4096,4100,{})
    assert net.cfg(net.project_row('game'))['range_checks'][0]['status']=='function_applied'


def test_imported_file_compiler_workflow_is_delegated(monkeypatch):
    net,agent=fixture(monkeypatch)
    net.filecompilers=SimpleNamespace(applies=lambda s:True,suggest=lambda *a:'project-owned file settings')
    assert net.suggest(agent,'game','flags',compiler='gcc',function='f',source='C')=='project-owned file settings'


def test_invalid_function_extent_cannot_fall_back_to_project_default(monkeypatch):
    net,agent=fixture(monkeypatch)
    with net.store.tx() as db:db.execute('UPDATE functions SET size=0 WHERE id=1')
    with pytest.raises(NetworkError,match='boundary'):
        net.suggest(agent,'game','flags',compiler='gcc',flags='-O3',function='f',source='C')
    assert not net.cfg(net.project_row('game')).get('suggestions')


def test_extent_changed_while_claiming_is_rejected(monkeypatch):
    net,agent=fixture(monkeypatch)
    bundle=profiles.capture(net,net.project_row('game'),'f',[],'winning source')
    with net.store.tx() as db:db.execute('DELETE FROM claims')
    hold=net.permuter._hold
    def claim(*args,**kwargs):
        result=hold(*args,**kwargs)
        with net.store.tx() as db:db.execute('UPDATE functions SET size=8 WHERE id=1')
        return result
    net.permuter._hold=claim
    with pytest.raises(NetworkError,match='changed while claiming'):
        profiles.apply(net,agent,net.project_row('game'),bundle,'gcc',['-O2'])
    assert not net.store.one('SELECT 1 FROM claims WHERE function_id=1')
    assert net.store.one('SELECT COUNT(*) n FROM attempts')['n']==0


def test_function_can_return_to_default_profile_without_changing_neighbors(monkeypatch):
    net,agent=fixture(monkeypatch)
    net._compile=lambda *a:SimpleNamespace(ok=True,obj=b'object',effective_settings={'compiler':{'toolchain':'gcc','flags':['-O2']}})
    net.suggest(agent,'game','exact default profile',compiler='gcc',flags='-O2',function='f',source='new exact C')
    cfg=net.cfg(net.project_row('game'))
    assert compiler_for(cfg,4096)['flags']==['-O2']
    assert compiler_for(cfg,4100)['flags']==['-O1']
    assert net._function('f','game')['status']=='matched'


def test_matched_and_changed_function_suggestions_are_not_actionable(monkeypatch):
    net, _ = fixture(monkeypatch)
    proj=net.project_row('game');fn=net._function('f','game')
    item = {'id':1,'toolchain':'gcc','function_match':{'function':fn['name'],'vram':fn['vram'],'size':fn['size']}}
    assert profiles.actionable(net, proj, item)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched' WHERE id=?", (fn['id'],))
    assert not profiles.actionable(net, proj, item)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='open',size=size+4 WHERE id=?", (fn['id'],))
    assert not profiles.actionable(net, proj, item)


def test_legacy_matched_suggestion_is_not_actionable(monkeypatch):
    net, _ = fixture(monkeypatch)
    proj=net.project_row('game');fn=net._function('f','game')
    net._update_cfg(proj['slug'], lambda cfg:cfg.update(range_checks=[{'suggestion':1,'origin':fn['vram']}]))
    item={'id':1,'toolchain':'gcc','ranges':[[fn['vram'],fn['vram']+fn['size']]]}
    assert profiles.actionable(net, net.project_row(proj['slug']), item)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched' WHERE id=?", (fn['id'],))
    assert not profiles.actionable(net, net.project_row(proj['slug']), item)


def test_accept_all_rechecks_and_continues_after_failure(monkeypatch):
    net, _ = fixture(monkeypatch)
    proj=net.project_row('game')
    items=[{'id':i,'status':'open','toolchain':'gcc'} for i in range(1,4)]
    net._update_cfg(proj['slug'],lambda cfg:cfg.update(suggestions=items))
    net.overview=lambda slug:{'suggestions':items}
    applied=[]
    def resolve(slug, sid, accept, log):
        applied.append(sid)
        log.progress({'phase':'Updating saved scores', 'done':2, 'total':8, 'function':'current_function'})
        snapshot=log_sink.snapshots[-1]
        assert snapshot['suggestion']==sid and snapshot['compiler']=='gcc'
        assert snapshot['done']==sid-1 and snapshot['total']==3
        assert snapshot['current']['function']=='current_function'
        if sid==2:raise NetworkError('Busy function')
    net.resolve_suggestion=resolve
    net.progress=SimpleNamespace(sync=lambda *a,**kw:None)
    class Log:
        def __call__(self,line):pass
        def check_cancel(self):pass
        def progress(self,value):self.last=value; self.snapshots.append(value)
    log=Log();log.snapshots=[]
    log_sink=log
    result=profiles.accept_all(net,proj['slug'],log)
    assert applied==[1,2,3]
    assert (result['applied'],result['skipped'])==(2,1)
    assert result['results'][1]['reason']=='Busy function'
    assert log.last['done']==3


def test_dismiss_all_only_resolves_displayed_open_compiler_suggestions(monkeypatch):
    net, _ = fixture(monkeypatch)
    items = [
        {'id':1, 'status':'open', 'toolchain':'gcc'},
        {'id':2, 'status':'open', 'toolchain':None},
        {'id':3, 'status':'applied', 'toolchain':'gcc'},
        {'id':4, 'status':'open', 'toolchain':'gcc'},
    ]
    net._update_cfg('game', lambda cfg:cfg.update(suggestions=items))
    before = net.cfg(net.project_row('game'))['compiler']
    net.resolve_suggestion = lambda *a, **kw:pytest.fail('Dismissal must not apply or compile settings')
    assert profiles.dismiss_all(net, 'game', [1, 1, 2, 3, 999]) == 1
    cfg = net.cfg(net.project_row('game'))
    assert [s['status'] for s in cfg['suggestions']] == ['dismissed', 'open', 'applied', 'open']
    assert cfg['compiler'] == before
    assert profiles.dismiss_all(net, 'game', [1]) == 0


@pytest.mark.parametrize('ids', [None, 'all', [True], ['1']])
def test_dismiss_all_requires_explicit_snapshot(monkeypatch, ids):
    net, _ = fixture(monkeypatch)
    with pytest.raises(NetworkError, match='Refresh the compiler suggestions'):
        profiles.dismiss_all(net, 'game', ids)
