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
    with net.store.tx() as db:db.execute("INSERT INTO context_versions(project_id,version,unit,header) VALUES(1,1,'','')")
    monkeypatch.setattr(net,'on_match',lambda *args:None)
    monkeypatch.setattr(net.progress,'sync',lambda *args,**kw:None)
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
    assert {p['function'] for p in progress[1:]} == {'f0', 'f1'}
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
    assert applied == []  # Batch publication does not repeat the single-function path.
    assert net._function('f3','game')['status']=='matched'
    assert net.store.one('SELECT COUNT(*) n FROM attempts')['n']==1
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
    assert result['passed']==1 and calls==[]
    assert not net.store.one('SELECT 1 FROM claims WHERE function_id=?',fn['id'])
    assert net._function('f3','game')['status']=='matched'


def test_apply_never_changes_untested_neighbours(net, monkeypatch):
    from decompnet.app import compiler_match_batch as batch
    before=net._project('game')['config']
    monkeypatch.setattr(net,'_judge_with',lambda *args:(True,NS(score=0),''))
    def unsafe(cfg,lo,hi,target):
        cfg['compiler_ranges']=[{'start':0x80001000,'end':0x80001040,'toolchain':target[0],'flags':list(target[1])}]
    monkeypatch.setattr(batch,'replace_range',unsafe)
    with pytest.raises(NetworkError,match='untested neighbour'):
        trial.apply_matches(net,'game',PROFILE,['f3'])
    assert net._project('game')['config']==before
    assert not net.store.all('SELECT * FROM attempts')


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
    assert applied==[]
    assert net._function('f3','game')['status']=='matched'
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


def test_apply_reuses_server_owned_trial_only_for_unchanged_function(net,monkeypatch):
    seen=[]
    monkeypatch.setattr(net,'_judge_with',lambda proj,fn,*args:(seen.append(fn['name']) or True,NS(score=0),''))
    out=trial.run(net,'game',PROFILE,nonmatches=True)
    assert seen==['f3']
    result=trial.apply_matches(net,'game',PROFILE,out['matches'],receipt=out['receipt'])
    assert result['reused']==result['passed']==1
    assert seen==['f3']
    assert net.store.one('SELECT COUNT(*) n FROM attempts')['n']==1
    assert net.store.one('SELECT COUNT(*) n FROM verified_matches')['n']==1


def test_only_changed_candidate_is_recompiled_and_unrelated_activity_is_ignored(net,monkeypatch):
    seen=[]
    with net.store.tx() as db:db.execute("UPDATE functions SET status='open' WHERE name='f2'")
    monkeypatch.setattr(net,'_judge_with',lambda proj,fn,*args:(seen.append(fn['name']) or True,NS(score=0),''))
    out=trial.run(net,'game',PROFILE,nonmatches=True)
    seen.clear()
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source='changed' WHERE name='f2'")
        db.execute("UPDATE functions SET matched_source='unrelated change' WHERE name='f0'")
    result=trial.apply_matches(net,'game',PROFILE,out['matches'],receipt=out['receipt'])
    assert result['passed']==2 and result['reused']==1
    assert seen==['f2']


def test_nonmatch_staleness_is_scoped_to_passing_function(net,monkeypatch):
    def judge(*args):
        with net.store.tx() as db:db.execute("UPDATE functions SET matched_source='unrelated' WHERE name='f0'")
        return True,NS(score=0),''
    monkeypatch.setattr(net,'_judge_with',judge)
    out=trial.run(net,'game',PROFILE,nonmatches=True)
    assert not out['stale'] and not out['changed_matches']


def test_changed_header_and_unknown_receipts_require_fresh_verification(net,monkeypatch):
    seen=[]
    monkeypatch.setattr(net,'_judge_with',lambda *args:(seen.append(1) or True,NS(score=0),''))
    out=trial.run(net,'game',PROFILE,nonmatches=True)
    with net.store.tx() as db:db.execute("INSERT INTO context_versions(project_id,version,unit,header) VALUES(1,2,'','typedef int changed;')")
    assert trial.apply_matches(net,'game',PROFILE,['f3'],receipt=out['receipt'])['reused']==0
    assert len(seen)==2


def test_source_change_during_verification_skips_publication(net,monkeypatch):
    before=net._project('game')['config']
    def judge(*args):
        with net.store.tx() as db:db.execute("UPDATE functions SET best_source='changed mid-check' WHERE name='f3'")
        return True,NS(score=0),''
    monkeypatch.setattr(net,'_judge_with',judge)
    out=trial.apply_matches(net,'game',PROFILE,['f3'])
    assert out['failed']==1 and out['passed']==0
    assert net._project('game')['config']==before
    assert not net.store.all('SELECT * FROM attempts')


def test_batch_verifies_in_parallel_and_updates_project_config_once(net,monkeypatch):
    import threading
    with net.store.tx() as db:db.execute("UPDATE functions SET status='open' WHERE name='f2'")
    barrier=threading.Barrier(2)
    monkeypatch.setattr(net,'_judge_with',lambda *args:(barrier.wait(timeout=3) is not None,NS(score=0),''))
    statements=[]
    net.store.conn.set_trace_callback(statements.append)
    try:out=trial.apply_matches(net,'game',PROFILE,['f2','f3'])
    finally:net.store.conn.set_trace_callback(None)
    assert out['passed']==2
    # Trace callbacks repeat a statement while its SQLite triggers execute. The compiler event
    # is a stable assertion that exactly one settings publication occurred.
    assert net.store.one("SELECT COUNT(*) n FROM events WHERE kind='compiler'")['n']==1
    assert net.store.one('SELECT COUNT(*) n FROM attempts')['n']==2


def test_failed_batch_record_rolls_back_settings_matches_and_notifications(net,monkeypatch):
    with net.store.tx() as db:db.execute("UPDATE functions SET status='open' WHERE name='f2'")
    before=net._project('game')['config'];notifications=[]
    monkeypatch.setattr(net,'_judge_with',lambda *args:(True,NS(score=0),''))
    monkeypatch.setattr(net,'on_match',lambda *args:notifications.append(args))
    record=net._record;count=[]
    def fail_second(*args,**kwargs):
        count.append(1)
        if len(count)==2:raise NetworkError('injected recording failure')
        return record(*args,**kwargs)
    monkeypatch.setattr(net,'_record',fail_second)
    with pytest.raises(NetworkError,match='injected'):
        trial.apply_matches(net,'game',PROFILE,['f2','f3'])
    assert net._project('game')['config']==before
    assert not net.store.all('SELECT * FROM attempts')
    assert not notifications


def test_claim_acquired_during_check_is_redirected_after_exact_commit(net,monkeypatch):
    import time
    worker,_=net.local_agent('other-worker')
    def judge(proj,fn,*args):
        with net.store.tx() as db:db.execute('INSERT INTO claims VALUES(?,?,?,?)',(fn['id'],worker.id,time.time(),time.time()+3600))
        return True,NS(score=0),''
    monkeypatch.setattr(net,'_judge_with',judge)
    notifications=[]
    monkeypatch.setattr(net,'on_external_match',lambda *args:notifications.append((args,net._function('f3','game')['status'])),raising=False)
    result=trial.apply_matches(net,'game',PROFILE,['f3'])
    assert result['failed']==0 and result['passed']==1
    assert not net.store.one('SELECT agent_id FROM claims')
    assert notifications[0][0][0]==worker.id and notifications[0][1]=='matched'


@pytest.mark.parametrize('receipt',[None,'forged',{'untrusted':'proof'}])
def test_client_cannot_supply_an_exact_verdict(net,monkeypatch,receipt):
    seen=[]
    monkeypatch.setattr(net,'_judge_with',lambda *args:(seen.append(1) or False,NS(score=1),''))
    before=net._project('game')['config']
    result=trial.apply_matches(net,'game',PROFILE,['f3'],receipt=receipt)
    assert seen==[1] and result['failed']==1 and result['passed']==0
    assert net._project('game')['config']==before
    assert not net.store.all('SELECT * FROM attempts')


def test_cancelled_parallel_checks_do_not_publish_settings_or_matches(net,monkeypatch):
    import threading
    stopped=threading.Event();before=net._project('game')['config']
    def judge(*args):stopped.set();return True,NS(score=0),''
    monkeypatch.setattr(net,'_judge_with',judge)
    def log(line):pass
    def cancel():
        if stopped.is_set():raise RuntimeError('cancelled')
    log.check_cancel=cancel
    with pytest.raises(RuntimeError,match='cancelled'):
        trial.apply_matches(net,'game',PROFILE,['f3'],log)
    assert net._project('game')['config']==before
    assert not net.store.all('SELECT * FROM attempts')


def test_missing_address_skips_only_that_candidate(net,monkeypatch):
    with net.store.tx() as db:db.execute("UPDATE functions SET status='open',vram=NULL WHERE name='f2'")
    seen=[]
    monkeypatch.setattr(net,'_judge_with',lambda proj,fn,*args:(seen.append(fn['name']) or True,NS(score=0),''))
    result=trial.apply_matches(net,'game',PROFILE,['f2','f3'])
    assert result['passed']==1 and result['failed']==1 and seen==['f3']
    assert result['matches']==['f3']


def test_custom_command_is_verified_with_actual_invocation_not_trial_receipt(net,monkeypatch):
    monkeypatch.setattr(net,'_judge_with',lambda *args:(True,NS(score=0),''))
    out=trial.run(net,'game',PROFILE,nonmatches=True)
    cfg=json.loads(net._project('game')['config']);cfg['compiler']['cmd']=['custom compiler']
    with net.store.tx() as db:db.execute('UPDATE projects SET config=?',(json.dumps(cfg),))
    def compile_custom(proj,header,source,fn):
        assert trial.profile(compiler_for(json.loads(proj['config']),fn['vram'],fn))==trial.profile(PROFILE)
        assert json.loads(proj['config'])['compiler']['cmd']==['custom compiler']
        return NS(ok=False)
    from decompnet.project import compiler_for
    monkeypatch.setattr(net,'_compile',compile_custom)
    result=trial.apply_matches(net,'game',PROFILE,['f3'],receipt=out['receipt'])
    assert result['passed']==0 and result['failed']==1 and result['reused']==0
    assert not net.store.all('SELECT * FROM attempts')


def test_nonmatch_testing_starts_before_another_function_finishes_loading(net,monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='open' WHERE name='f2'")
    one=net.store.one
    blocked=threading.Event();first_result=threading.Event();release=threading.Event();seen=[]
    def slow_read(sql,*args):
        if sql.startswith('SELECT * FROM functions WHERE project_id=') and args[-1]==3 and not release.is_set():
            blocked.set()
            assert release.wait(5),'blocked preparation was not released'
        return one(sql,*args)
    monkeypatch.setattr(net.store,'one',slow_read)
    monkeypatch.setattr(net,'_judge_with',lambda proj,fn,*args:(seen.append(fn['name']) or True,None,''))
    def log(line):pass
    log.progress=lambda state:first_result.set() if state.get('checked',0)>0 else None
    with ThreadPoolExecutor(max_workers=1) as pool:
        task=pool.submit(trial.run,net,'game',PROFILE,log,True)
        try:
            assert blocked.wait(3)
            assert first_result.wait(3),'ready function waited for unrelated preparation'
            assert seen==['f3']
        finally:release.set()
        out=task.result(timeout=5)
    assert out['checked']==out['passed']==2


def test_nonmatch_metadata_does_not_load_matched_source_or_trigger_disk_sync(net,monkeypatch):
    # A project full of compressed matches should not be inflated before testing one open function.
    with net.store.tx() as db:
        db.execute("UPDATE functions SET asm=?,matched_source=? WHERE status='matched'",('assembly'*10000,'matched C'*10000))
    monkeypatch.setattr(net,'_project',RomNetwork._project.__get__(net))
    monkeypatch.setattr(net.progress,'sync',lambda *a,**k:pytest.fail('diagnostic must not reconcile/export saved files'))
    original=net.store.all
    def metadata_only(sql,*args):
        assert not (sql.startswith('SELECT * FROM functions') and 'id IN' not in sql),'full project payload scan'
        return original(sql,*args)
    monkeypatch.setattr(net.store,'all',metadata_only)
    monkeypatch.setattr(net,'_judge_with',lambda *a:(True,None,''))
    out=trial.run(net,'game',PROFILE,nonmatches=True)
    assert out['matches']==['f3'] and not out['stale']


def test_nonmatch_cancellation_only_admits_a_bounded_window(net,monkeypatch):
    import threading
    with net.store.tx() as db:
        for i in range(30):
            db.execute("INSERT INTO functions(project_id,name,vram,size,best_source,status) VALUES(1,?,?,16,'draft','open')",(f'queued{i}',0x80002000+16*i))
    stopped=threading.Event();judged=[]
    def judge(proj,fn,*args):
        judged.append(fn['name']);stopped.set()
        return False,None,''
    monkeypatch.setattr(net,'_judge_with',judge)
    before=net.store.one('SELECT config FROM projects')[0]
    def log(line):pass
    def cancel():
        if stopped.is_set():raise RuntimeError('cancelled')
    log.check_cancel=cancel
    with pytest.raises(RuntimeError,match='cancelled'):
        trial.run(net,'game',PROFILE,log,True)
    assert len(judged)<=8
    assert net.store.one('SELECT config FROM projects')[0]==before
    assert not net.store.all('SELECT * FROM attempts')


def test_nonmatch_symbol_changes_invalidate_receipts(net,monkeypatch):
    def judge(*args):
        with net.store.tx() as db:
            db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(1,'changed',4096,4,'data')")
        return True,None,''
    monkeypatch.setattr(net,'_judge_with',judge)
    out=trial.run(net,'game',PROFILE,nonmatches=True)
    assert out['stale'] and out['changed_matches']==['f3']


def test_common_environment_is_a_compact_digest_with_all_inputs(net,monkeypatch):
    from decompnet.app import compiler_match_batch as batch
    proj=net._project('game')
    monkeypatch.setattr(batch.match_bundle,'compiler_identity',lambda selected:{'compiler':'first'})
    first=batch.environment(net,proj,PROFILE)
    assert len(first)==64 and isinstance(first,str)
    assert batch.environment(net,proj,PROFILE)==first
    monkeypatch.setattr(batch.match_bundle,'compiler_identity',lambda selected:{'compiler':'second'})
    assert batch.environment(net,proj,PROFILE)!=first


def test_nonmatch_changed_or_missing_function_is_reported_without_testing(net,monkeypatch):
    original=net.store.one
    def removed(sql,*args):
        if sql.startswith('SELECT * FROM functions WHERE project_id='):return None
        return original(sql,*args)
    monkeypatch.setattr(net.store,'one',removed)
    monkeypatch.setattr(net,'_judge_with',lambda *a:pytest.fail('removed function was compiled'))
    out=trial.run(net,'game',PROFILE,nonmatches=True)
    assert out['failed']==out['checked']==1 and 'No longer unmatched' in out['failures'][0]['reason']


def test_nonmatch_uses_current_name_when_renamed_before_preparation(net,monkeypatch):
    original=net.store.one;renamed=False
    def rename_before_load(sql,*args):
        nonlocal renamed
        if sql.startswith('SELECT * FROM functions WHERE project_id=') and not renamed:
            renamed=True
            with net.store.tx() as db:db.execute("UPDATE functions SET name='renamed' WHERE name='f3'")
        return original(sql,*args)
    monkeypatch.setattr(net.store,'one',rename_before_load)
    monkeypatch.setattr(net,'_judge_with',lambda *args:(True,None,''))
    out=trial.run(net,'game',PROFILE,nonmatches=True)
    assert out['matches']==['renamed'] and not out['stale']
