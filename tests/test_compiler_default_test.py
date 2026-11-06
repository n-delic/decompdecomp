"""A default-profile compatibility test is diagnostic and checks the exact requested population."""
import copy
import json
from types import SimpleNamespace as NS

import pytest

from decompnet.app import compiler_default_test as trial
from decompnet.app.engine import RomNetwork
from decompnet.network import NetworkError


@pytest.fixture
def net(tmp_path, monkeypatch):
    net = RomNetwork(tmp_path/'app.db', tmp_path)
    cfg = {'kind':'rom','platform':'n64','compiler':{'toolchain':'ido-5.3','flags':['-O2']},
           'compiler_ranges':[{'start':0x80001020,'end':0x80001040,'toolchain':'ido-5.3','flags':['-O1']}]}
    with net.store.tx() as db:
        db.execute('INSERT INTO projects(id,slug,name,root,config) VALUES(1,?,?,?,?)',('game','Game',str(tmp_path),json.dumps(cfg)))
        for i,status in enumerate(['matched','matched','matched','open']):
            db.execute('INSERT INTO functions(project_id,name,vram,size,n_insns,status,matched_source,best_source,best_score) VALUES(1,?,?,?,?,?,?,?,?)',
                       (f'f{i}',0x80001000+16*i,16,4,status,f'int f{i}(void){{return 1;}}' if status=='matched' else None,'draft',0))
    monkeypatch.setattr(trial.toolchains,'installed',lambda tc:object())
    monkeypatch.setattr(net,'_project',lambda slug:net.store.one('SELECT * FROM projects WHERE slug=?',slug))
    return net


PROFILE={'toolchain':'ido-5.3','flags':['-O1']}


def test_only_default_matches_are_checked_and_nothing_is_changed(net, monkeypatch):
    before_config=net._project('game')['config']
    before_rows=[dict(r) for r in net.store.all('SELECT * FROM functions ORDER BY id')]
    seen=[]; progress=[]
    def judge(proj, fn, tc, flags, source):
        seen.append(fn['name'])
        assert flags==['-O1'] and source==fn['matched_source']
        return fn['name']=='f0',NS(score=12),''
    monkeypatch.setattr(net,'_judge_with',judge)
    def log(line):pass
    log.progress=lambda p:progress.append(copy.deepcopy(p))
    out=trial.run(net,'game',PROFILE,log)
    assert set(seen)=={'f0','f1'}
    assert out['total']==out['checked']==2 and out['passed']==out['failed']==1
    assert not out['compatible'] and not out['stale']
    assert out['failures'][0]['function']=='f1'
    assert [p['checked'] for p in progress]==[0,1,2]
    assert all(p['passed']+p['failed']==p['checked'] for p in progress)
    assert net._project('game')['config']==before_config
    assert [dict(r) for r in net.store.all('SELECT * FROM functions ORDER BY id')]==before_rows
    assert not net.store.all('SELECT * FROM attempts') and not net.store.all('SELECT * FROM events')


def test_all_passing_and_empty_population(net, monkeypatch):
    monkeypatch.setattr(net,'_judge_with',lambda *a:(True,None,''))
    assert trial.run(net,'game',PROFILE)['compatible']
    with net.store.tx() as db:db.execute("UPDATE functions SET status='open' WHERE name IN ('f0','f1')")
    out=trial.run(net,'game',PROFILE)
    assert out['total']==0 and not out['compatible']


def test_changed_source_marks_report_stale(net, monkeypatch):
    def judge(*args):
        with net.store.tx() as db:db.execute("UPDATE functions SET matched_source='changed' WHERE name='f0'")
        return True,None,''
    monkeypatch.setattr(net,'_judge_with',judge)
    out=trial.run(net,'game',PROFILE)
    assert out['passed']==2 and out['stale'] and not out['compatible']


def test_compile_error_counts_as_failure(net, monkeypatch):
    monkeypatch.setattr(net,'_judge_with',lambda *a:(False,None,'compile error'))
    out=trial.run(net,'game',PROFILE)
    assert out['failed']==2 and not out['compatible']


@pytest.mark.parametrize('profile',[None,{}, {'toolchain':'ido-5.3','flags':'-O1'}, {'toolchain':'ido-5.3','flags':['-O2']}, {'toolchain':'missing','flags':[]}])
def test_rejects_invalid_default_or_unused_profile(net, profile):
    with pytest.raises(NetworkError):trial.run(net,'game',profile)


def test_cancellation_never_changes_config(net, monkeypatch):
    before=net._project('game')['config']
    monkeypatch.setattr(net,'_judge_with',lambda *a:(True,None,''))
    def log(line):pass
    def cancelled():raise RuntimeError('cancelled')
    log.check_cancel=cancelled
    with pytest.raises(RuntimeError,match='cancelled'):trial.run(net,'game',PROFILE,log)
    assert net._project('game')['config']==before


def test_nonmatches_checks_saved_attempts_from_other_profiles(net, monkeypatch):
    monkeypatch.setattr(net, '_judge_with', lambda *args:(True, NS(score=0), ''))
    out=trial.run(net, 'game', PROFILE, nonmatches=True)
    assert out['total']==1 and out['passed']==1 and out['matches']==['f3']
    assert net._project('game')['config'] == net.store.one('SELECT config FROM projects')['config']


def test_replacement_preserves_failed_default_matches_and_existing_profiles(net, monkeypatch):
    import contextlib
    monkeypatch.setattr(net.progress, 'judging', lambda slug:contextlib.nullcontext())
    monkeypatch.setattr(net, '_judge_with', lambda proj, fn, *args:(fn['name']=='f0', NS(score=20), ''))
    captured=[]
    monkeypatch.setattr(net, '_apply_compiler_config', lambda proj, cfg, **kw:captured.append(cfg))
    result=trial.replace_default(net, 'game', PROFILE)
    from decompnet.project import compiler_for
    cfg=captured[0]
    assert trial.profile(cfg['compiler'])==trial.profile(PROFILE)
    assert trial.profile(compiler_for(cfg,0x80001000))==trial.profile(PROFILE)
    assert compiler_for(cfg,0x80001010)['flags']==['-O2']
    assert compiler_for(cfg,0x80001020)['flags']==['-O1']
    assert result=={'replaced':True,'preserved':1}


def test_replacement_rejects_stale_trial(net, monkeypatch):
    import contextlib
    monkeypatch.setattr(net.progress, 'judging', lambda slug:contextlib.nullcontext())
    monkeypatch.setattr(trial, 'run', lambda *args:{'stale':True})
    with pytest.raises(NetworkError, match='changed'):
        trial.replace_default(net, 'game', PROFILE)


def test_replacement_commits_through_normal_guard_and_preserves_matches(net, monkeypatch):
    import contextlib
    monkeypatch.setattr(net.progress, 'judging', lambda slug:contextlib.nullcontext())
    monkeypatch.setattr(net, '_judge_with', lambda proj, fn, *args:(fn['name']=='f0', NS(score=20), ''))
    verified=[]
    def regression(proj, agent, only, **kwargs):
        verified.extend(f['name'] for f in only)
        return []
    monkeypatch.setattr(net, 'regression_test', regression)
    trial.replace_default(net, 'game', PROFILE)
    cfg=json.loads(net._project('game')['config'])
    assert cfg['compiler']['flags']==['-O1']
    assert verified==['f0']
    assert net.store.one("SELECT count(*) AS n FROM functions WHERE status='matched'")['n']==3
    assert net.store.one("SELECT matched_source FROM functions WHERE name='f1'")['matched_source']


def test_apply_rechecks_successes_and_uses_normal_compiler_match_path(net, monkeypatch):
    checked, applied = [], []
    monkeypatch.setattr(net, '_comparison_source', lambda fn:'saved C')
    monkeypatch.setattr(net, '_judge_with', lambda proj, fn, tc, flags, source:(checked.append((fn['name'], source)) or True, NS(score=0), ''))
    monkeypatch.setattr(net, 'apply_function_compiler', lambda *args:applied.append(args) or {'newly_matched':True})
    result = trial.apply_matches(net, 'game', PROFILE, ['f3', 'f3', 'f0'])
    assert checked == [('f3', 'saved C')]
    assert applied == [('game', 'f3', PROFILE['toolchain'], PROFILE['flags'])]
    assert result['passed'] == 1 and result['failed'] == 1
    assert 'No longer unmatched' in result['failures'][0]['reason']


def test_apply_rejects_stale_source_and_overlay_addresses(net, monkeypatch):
    monkeypatch.setattr(net, '_comparison_source', lambda fn:'changed C')
    monkeypatch.setattr(net, '_judge_with', lambda *args:(False, NS(score=1), ''))
    monkeypatch.setattr(net, 'apply_function_compiler', lambda *args:pytest.fail('must not apply a failed recheck'))
    result = trial.apply_matches(net, 'game', PROFILE, ['f3'])
    assert result['failed'] == 1 and 'no longer matches' in result['failures'][0]['reason']
    with net.store.tx() as db:
        db.execute("INSERT INTO functions(project_id,name,vram,size,status) VALUES(1,'overlay',?,16,'open')", (0x80001030,))
    result = trial.apply_matches(net, 'game', PROFILE, ['f3'])
    assert 'overlay' in result['failures'][0]['reason']


def test_apply_rechecks_active_claims(net, monkeypatch):
    agent, _ = net.local_agent('test-worker')
    fn = net._function('f3', 'game')
    import time
    with net.store.tx() as db:
        db.execute('INSERT INTO claims(function_id,agent_id,expires_at) VALUES(?,?,?)', (fn['id'],agent.id,time.time()+3600))
    monkeypatch.setattr(net, '_judge_with', lambda *args:(True,NS(score=0),''))
    calls=[]
    monkeypatch.setattr(net,'apply_function_compiler',lambda *args:calls.append(args) or {'newly_matched':True})
    result = trial.apply_matches(net, 'game', PROFILE, ['f3'])
    assert result['passed']==1 and calls[0][1]=='f3'


def test_apply_returns_structured_affected_matches(net, monkeypatch):
    monkeypatch.setattr(net, '_comparison_source', lambda fn:'saved C')
    monkeypatch.setattr(net, '_judge_with', lambda *args:(True, NS(score=0), ''))
    def blocked(*args):
        error = NetworkError('Existing match would fail')
        error.compiler_blockers = [{'function':'f0', 'reason':'different bytes'}]
        raise error
    monkeypatch.setattr(net, 'apply_function_compiler', blocked)
    result = trial.apply_matches(net, 'game', PROFILE, ['f3'])
    failure = result['failures'][0]
    assert failure['category'] == 'affected_matches'
    assert failure['blockers'][0]['function'] == 'f0'


def test_check_affected_uses_matched_source_and_changes_nothing(net, monkeypatch):
    before = [dict(row) for row in net.store.all('SELECT * FROM functions')]
    config = net._project('game')['config']
    seen = []
    def judge(proj, fn, tc, flags, source):
        seen.append((fn['name'], source, tc, flags))
        return fn['name'] == 'f0', NS(score=0), ''
    monkeypatch.setattr(net, '_judge_with', judge)
    out = trial.check_affected(net, 'game', PROFILE, ['f0','f1','f0'])
    assert out['passed'] == 1 and out['total'] == 2
    assert seen[0][1] == before[0]['matched_source']
    assert seen[0][2:] == (PROFILE['toolchain'], PROFILE['flags'])
    assert [dict(row) for row in net.store.all('SELECT * FROM functions')] == before
    assert net._project('game')['config'] == config


def test_safe_range_isolates_overlapping_extent_and_preserves_other_match(net, monkeypatch):
    from decompnet.project import compiler_for
    with net.store.tx() as db:
        db.execute("UPDATE functions SET size=64 WHERE name='f3'")
        db.execute("INSERT INTO functions(project_id,name,vram,size,status,matched_source) VALUES(1,'neighbor',?,16,'matched','saved neighbor')", (0x80001040,))
    before = net.cfg(net._project('game'))
    monkeypatch.setattr(net, '_comparison_source', lambda fn:'saved candidate')
    monkeypatch.setattr(net, '_judge_with', lambda *args:(True, NS(score=0), ''))
    verified = []
    def regression(proj, agent, only, **kwargs):
        verified.extend(fn['name'] for fn in only)
        return [(fn['name'], 'different bytes') for fn in only if fn['name'] == 'neighbor']
    monkeypatch.setattr(net, 'regression_test', regression)
    def save(agent, proj, fn, source, **options):
        with net.store.tx() as db:
            db.execute("UPDATE functions SET status='matched',matched_source=? WHERE id=?", (source, fn['id']))
    monkeypatch.setattr(net, 'try_quietly', save)
    selected = {'toolchain':'ido-5.3','flags':['-O3']}
    with pytest.raises(NetworkError, match="wouldn't match"):
        net.apply_function_compiler('game', 'f3', **selected)
    assert net.cfg(net._project('game')) == before
    result = trial.apply_matches(net, 'game', selected, ['f3'], safe_ranges=True)
    assert result['passed'] == 1
    after = net.cfg(net._project('game'))
    assert compiler_for(after, 0x80001030)['flags'] == ['-O3']
    assert compiler_for(after, 0x80001040) == compiler_for(before, 0x80001040)
    assert net._function('neighbor', 'game')['matched_source'] == 'saved neighbor'
    assert net._function('neighbor', 'game')['status'] == 'matched'
    assert next(r for r in after['compiler_ranges'] if r['start'] == 0x80001030)['end'] == 0x80001040


def test_bulk_safe_range_repairs_continue_after_a_skipped_function(net, monkeypatch):
    monkeypatch.setattr(net, '_comparison_source', lambda fn:'saved C')
    monkeypatch.setattr(net, '_judge_with', lambda *args:(True, NS(score=0), ''))
    applied=[]
    def apply(slug, name, tc, flags, **options):
        applied.append((name, options))
        return {'newly_matched':True}
    monkeypatch.setattr(net, 'apply_function_compiler', apply)
    progress=[]
    def log(line):pass
    log.progress=progress.append
    result=trial.apply_matches(net, 'game', PROFILE, ['f0','f3','f3'], log, safe_ranges=True)
    assert result['total']==2 and result['passed']==1 and result['failed']==1
    assert applied==[('f3', {'preserve_other_profiles':True})]
    assert result['matches']==['f3'] and result['failures'][0]['function']=='f0'
    assert progress[-1]['checked']==2


@pytest.mark.parametrize('safe_ranges', [False, True])
def test_bulk_apply_scopes_same_address_overlays_by_rom_location(net, monkeypatch, safe_ranges):
    from decompnet.project import compiler_for, function_compiler_key
    address=net._function('f3','game')['vram']
    with net.store.tx() as db:
        db.execute("UPDATE functions SET rom_offset=100 WHERE name='f3'")
        db.execute("INSERT INTO functions(project_id,name,vram,size,rom_offset,status,matched_source) VALUES(1,'other_overlay',?,16,500,'matched','saved overlay C')",(address,))
    before=net.cfg(net._project('game'))
    selected={'toolchain':'ido-5.3','flags':['-O3']}
    monkeypatch.setattr(net,'_comparison_source',lambda fn:'saved candidate')
    seen=[]
    def judge(proj, fn, *args):
        seen.append(fn['name'])
        return True,NS(score=0),''
    monkeypatch.setattr(net,'_judge_with',judge)
    # Real compiler application must preserve the other overlay rather than ask it to adopt O3.
    def regression(proj, agent, only, **kwargs):
        return [(fn['name'],'different bytes') for fn in only if fn['name']=='other_overlay']
    monkeypatch.setattr(net,'regression_test',regression)
    def save(agent, proj, fn, source, **options):
        with net.store.tx() as db:
            db.execute("UPDATE functions SET status='matched',matched_source=? WHERE id=?",(source,fn['id']))
    monkeypatch.setattr(net,'try_quietly',save)
    result=trial.apply_matches(net,'game',selected,['f3'],safe_ranges=safe_ranges)
    assert result['passed']==1 and result['failed']==0
    after=net.cfg(net._project('game'))
    own=net._function('f3','game');other=net._function('other_overlay','game')
    assert compiler_for(after,address,own)['flags']==['-O3']
    assert compiler_for(after,address,other)==compiler_for(before,address,other)
    assert after['compiler_ranges']==before['compiler_ranges']
    assert list(after['function_compilers'])==[function_compiler_key(own)]
    assert other['status']=='matched' and other['matched_source']=='saved overlay C'
    assert own['status']=='matched' and seen and set(seen)=={'f3'}


def test_bulk_apply_rejects_duplicate_overlay_identity(net, monkeypatch):
    with net.store.tx() as db:
        db.execute("UPDATE functions SET rom_offset=100 WHERE name='f3'")
        db.execute("INSERT INTO functions(project_id,name,vram,size,rom_offset,status) VALUES(1,'duplicate',?,16,100,'open')",(net._function('f3','game')['vram'],))
    monkeypatch.setattr(net,'_judge_with',lambda *args:pytest.fail('ambiguous entries cannot be applied'))
    result=trial.apply_matches(net,'game',PROFILE,['f3'])
    assert result['failed']==1
    assert result['failures'][0]['category']=='overlay'
