"""Durable per-function comparisons resume bulk work without changing game settings."""
import json
from types import SimpleNamespace

import pytest

from decompnet import toolchains
from decompnet.app.engine import RomNetwork


@pytest.fixture
def comparison(tmp_path, monkeypatch):
    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    cfg = {'kind': 'rom', 'platform': 'n64', 'compiler': {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O2']}}
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)',
                         ('demo', 'Demo', str(tmp_path), json.dumps(cfg))).lastrowid
        for i, state in enumerate(['open', 'open', 'matched', 'asm']):
            db.execute('INSERT INTO functions(project_id,name,vram,size,n_insns,status,best_score,best_source) VALUES(?,?,?,?,?,?,?,?)',
                       (pid, f'f{i}', 0x80001000+i*32, 16, 4, state, 200, f'int f{i}(void){{return 1;}}'))
    profiles = [('gcc-2.7.2-kmc', ('-O2',)), ('sn-n64-281', ('-O1',))]
    monkeypatch.setattr(net, '_comparison_candidates', lambda *a: profiles)
    monkeypatch.setattr(net, 'compiler_map', lambda *a: SimpleNamespace(contradicted=set()))
    monkeypatch.setattr(toolchains, 'installed', lambda _: {'parts': ['pinned']})
    calls = []
    def judge(proj, fn, tc, flags, source):
        calls.append(fn['name'])
        return False, SimpleNamespace(percent=80.0, score=80), ''
    monkeypatch.setattr(net, '_judge_with', judge)
    return net, profiles, calls, tmp_path


def test_bulk_persists_short_functions_and_resumes_after_restart(comparison, monkeypatch):
    net, profiles, calls, path = comparison
    first = net.survey_compilers('demo', all_installed=True, untested_only=True, limit=1)
    assert first['tried'] == 1
    assert net.store.one('SELECT COUNT(*) AS n FROM compiler_tests')['n'] == 1
    reopened = RomNetwork(path / 'app.db', path)
    monkeypatch.setattr(reopened, '_comparison_candidates', net._comparison_candidates)
    monkeypatch.setattr(reopened, 'compiler_map', net.compiler_map)
    monkeypatch.setattr(reopened, '_judge_with', net._judge_with)
    second = reopened.survey_compilers('demo', all_installed=True, untested_only=True)
    assert second['tried'] == 1 and second['skipped'] == 1
    assert set(calls) == {'f0', 'f1'}
    third = reopened.survey_compilers('demo', all_installed=True, untested_only=True)
    assert third['tried'] == 0 and third['skipped'] == 2
    fn = reopened._function('f0', 'demo')
    history = reopened.compiler_test_result(reopened.project_row('demo'), fn)
    assert history['completed_at'] and not history['stale']
    assert len(history['comparison']['ranked']) == 2
    assert reopened.survey_compilers('demo', all_installed=True, untested_only=False)['tried'] == 2


def test_changed_source_context_and_inventory_invalidate_history(comparison):
    net, profiles, _, _ = comparison
    net.survey_compilers('demo', all_installed=True, function='f0')
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source='int f0(void){return 2;}' WHERE name='f0'")
    fn = net._function('f0', 'demo')
    assert net.compiler_test_result(net.project_row('demo'), fn)['stale']
    assert net.survey_compilers('demo', all_installed=True, untested_only=True)['tried'] == 2
    with net.store.tx() as db:
        db.execute('INSERT INTO context_versions(project_id,unit,version,header) VALUES(?,?,?,?)',
                   (fn['project_id'], '', 1, 'typedef int s32;'))
    assert net.compiler_test_result(net.project_row('demo'), fn)['stale']
    net.survey_compilers('demo', all_installed=True, function='f0')
    profiles.append(('gcc-2.7.2-kmc', ('-O3',)))
    assert net.compiler_test_result(net.project_row('demo'), fn)['stale']


def test_interrupted_function_is_not_marked_completed(comparison, monkeypatch):
    net, _, _, _ = comparison
    def fail(*a):
        raise RuntimeError('interrupted')
    monkeypatch.setattr(net, '_judge_with', fail)
    with pytest.raises(RuntimeError, match='interrupted'):
        net.survey_compilers('demo', all_installed=True, function='f0')
    assert net.store.one('SELECT COUNT(*) AS n FROM compiler_tests')['n'] == 0


def test_explicit_non_improving_choice_is_revalidated_and_rebases_score(comparison, monkeypatch):
    net, _, _, _ = comparison
    monkeypatch.setattr(net, '_judge_with', lambda *a: (False, SimpleNamespace(score=300), ''))
    applied = []
    monkeypatch.setattr(net, 'set_compiler_ranges', lambda *a: applied.append(a))
    monkeypatch.setattr(net, 'try_quietly', lambda *a, **kw: None)
    net.apply_function_compiler('demo', 'f0', 'sn-n64-281', ['-O1'], allow_non_improving=True)
    assert applied[0][2:] == ('sn-n64-281', ['-O1'])
    fn = net._function('f0', 'demo')
    assert fn['best_score'] == 300 and 'return 1' in fn['best_source']
    monkeypatch.setattr(net, '_judge_with', lambda *a: (False, None, 'compile failure'))
    with pytest.raises(Exception, match='could not compile'):
        net.apply_function_compiler('demo', 'f0', 'sn-n64-281', ['-O3'], allow_non_improving=True)
    assert len(applied) == 1


def test_bulk_logs_percentage_improvements_without_exact_matches(comparison):
    net, _, _, _ = comparison
    logs = []
    result = net.survey_compilers('demo', all_installed=True, log=logs.append)
    assert result['confirmed'] == 0
    assert any('better settings found: 50.00% → 80.00%' in line for line in logs)
    assert any('2 have better tested settings; 0 have exact matches' in line for line in logs)
    assert net._function('f0', 'demo')['best_score'] == 200


def test_bulk_progress_includes_best_percentage(comparison, monkeypatch):
    net, profiles, _, _ = comparison
    profiles.extend([('gcc-2.7.2-kmc', (f'-DTEST={i}',)) for i in range(8)])
    logs = []
    net.survey_compilers('demo', all_installed=True, log=logs.append)
    assert any('10/10 combinations tested; best 80.00% (saved 50.00%); 0 exact matches.' in line for line in logs)


def test_matched_comparison_uses_matching_source_and_prefers_default(comparison, monkeypatch):
    net, profiles, _, _ = comparison
    with net.store.tx() as db:
        db.execute("UPDATE functions SET matched_source='matching source',best_source='different draft' WHERE name='f2'")
    def judge(proj, fn, tc, flags, source):
        assert source == 'matching source'
        return True, SimpleNamespace(percent=100.0, score=0), ''
    monkeypatch.setattr(net, '_judge_with', judge)
    result = net.run_compiler_comparison('demo', function='f2')
    rows = result['comparison']['ranked']
    assert rows[0]['default'] and rows[0]['exact']
    assert result['comparison']['exact_matches'] == len(profiles)
    fn = net._function('f2', 'demo')
    assert fn['status'] == 'matched' and fn['matched_source'] == 'matching source'
    assert not net.compiler_test_result(net.project_row('demo'), fn)['stale']


def test_flags_only_uses_each_functions_current_compiler_and_resumes(comparison, monkeypatch):
    net, profiles, _, _ = comparison
    profiles.append(('gcc-2.7.2-kmc', ('-O3',)))
    proj = net.project_row('demo')
    cfg = json.loads(proj['config'])
    cfg['compiler_ranges'] = [{'start': 0x80001020, 'end': 0x80001040,
                               'toolchain': 'sn-n64-281', 'flags': ['-O1']}]
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=? WHERE id=?', (json.dumps(cfg), proj['id']))
    tested = []

    def judge(proj, fn, tc, flags, source):
        tested.append((fn['name'], tc, tuple(flags)))
        return False, SimpleNamespace(percent=80.0, score=80), ''

    monkeypatch.setattr(net, '_judge_with', judge)
    result = net.run_compiler_comparison('demo', flags_only=True)
    assert result['tried'] == 2
    assert sorted(tested) == [('f0', 'gcc-2.7.2-kmc', ('-O2',)), ('f0', 'gcc-2.7.2-kmc', ('-O3',)),
                              ('f1', 'sn-n64-281', ('-O1',))]
    history = net.compiler_test_result(net.project_row('demo'), net._function('f0', 'demo'))
    assert not history['stale'] and history['comparison']['flags_only']
    assert net.run_compiler_comparison('demo', flags_only=True)['skipped'] == 2
    # A limited comparison must never make a later full compiler search look completed.
    assert net.run_compiler_comparison('demo')['tried'] == 2
    assert ('f0', 'sn-n64-281', ('-O1',)) in tested


def test_comparison_queue_counts_exclude_completed_and_respect_run_limit(comparison):
    net, _, _, _ = comparison
    snapshots = []

    class Log:
        def __call__(self, line):
            pass

        def progress(self, value):
            snapshots.append(dict(value))

    first = net.survey_compilers('demo', all_installed=True, untested_only=True, limit=1, log=Log())
    assert first['total'] == 1 and first['remaining'] == 0 and first['deferred'] == 1
    assert snapshots[0]['phase']=='preparation'
    scans=[s for s in snapshots if s.get('phase')=='eligibility']
    assert scans[0]['checked']==0
    compiling = [s for s in snapshots if s.get('phase') not in ('eligibility','preparation')]
    assert compiling[0]['completed'] == 0 and compiling[0]['remaining'] == 1
    assert snapshots[-1]['completed'] == 1 and snapshots[-1]['remaining'] == 0
    snapshots.clear()
    second = net.survey_compilers('demo', all_installed=True, untested_only=True, log=Log())
    assert second['total'] == 1 and second['skipped'] == 1 and second['deferred'] == 0
    assert next(s for s in snapshots if s.get('phase') not in ('eligibility','preparation'))['total'] == 1


def test_auto_apply_uses_best_completed_profile_and_records_outcomes(comparison, monkeypatch):
    net, _, _, _ = comparison
    applied=[]
    def judge(proj, fn, tc, flags, source):
        return False, SimpleNamespace(percent=90 if tc=='sn-n64-281' else 80,score=40 if tc=='sn-n64-281' else 80), ''
    monkeypatch.setattr(net,'_judge_with',judge)
    def apply(slug,function,tc,flags):
        applied.append((function,tc,flags))
        if function=='f0':
            from decompnet.network import NetworkError
            raise NetworkError('This function is being worked on.')
        with net.store.tx() as db:
            db.execute('UPDATE functions SET best_score=40 WHERE name=?',(function,))
        return {'said':'Applied and rechecked.'}
    monkeypatch.setattr(net,'apply_function_compiler',apply)
    result=net.run_compiler_comparison('demo',auto_apply=True)
    assert applied==[('f0','sn-n64-281',['-O1']),('f1','sn-n64-281',['-O1'])]
    assert result['auto_apply'] and result['applied_count']==1
    assert not result['results'][0]['applied'] and 'worked on' in result['results'][0]['apply_error']
    assert result['results'][1]['applied']
    for name in ('f0','f1'):
        saved=net.compiler_test_result(net.project_row('demo'),net._function(name,'demo'))['comparison']
        assert saved['applied']==(name=='f1')
    assert net.cfg(net.project_row('demo'))['compiler']=={'toolchain':'gcc-2.7.2-kmc','flags':['-O2']}


def test_auto_apply_rejects_changed_input_and_remains_opt_in(comparison, monkeypatch):
    net,_,_,_=comparison
    calls=[]
    monkeypatch.setattr(net,'apply_function_compiler',lambda *args:calls.append(args))
    net.run_compiler_comparison('demo')
    assert not calls
    original=net._judge_with
    def changed(proj,fn,*args):
        result=original(proj,fn,*args)
        if fn['name']=='f0':
            with net.store.tx() as db:
                db.execute("UPDATE functions SET best_source='int f0(void){return 9;}' WHERE name='f0'")
        return result
    monkeypatch.setattr(net,'_judge_with',changed)
    result=net.run_compiler_comparison('demo',function='f0',auto_apply=True)
    assert not calls and result['applied_count']==0
    assert 'changed' in result['results'][0]['apply_error']
    with pytest.raises(Exception,match='boolean'):net.run_compiler_comparison('demo',auto_apply='false')


def test_auto_apply_changes_only_function_ranges_and_rechecks_current_c(comparison, monkeypatch):
    from decompnet.project import compiler_for
    net,_,_,_=comparison
    original=net.cfg(net.project_row('demo'))['compiler'].copy()
    monkeypatch.setattr(net,'_judge_with',lambda proj,fn,tc,flags,source:
                        (False,SimpleNamespace(percent=95 if tc=='sn-n64-281' else 50,score=20 if tc=='sn-n64-281' else 200),''))
    rechecked=[]
    def recheck(agent,proj,fn,source,**kwargs):
        assert compiler_for(net.cfg(proj),fn['vram'])=={'toolchain':'sn-n64-281','flags':['-O1']}
        rechecked.append(fn['name'])
        with net.store.tx() as db:db.execute('UPDATE functions SET best_score=20 WHERE id=?',(fn['id'],))
    monkeypatch.setattr(net,'try_quietly',recheck)
    result=net.run_compiler_comparison('demo',auto_apply=True)
    assert result['applied_count']==2, result['results']
    assert rechecked==['f0','f1']
    cfg=net.cfg(net.project_row('demo'))
    assert cfg['compiler']==original
    assert compiler_for(cfg,0x80001040)==original
    assert net.run_compiler_comparison('demo',auto_apply=True)['tried']==0


def test_used_profiles_only_tests_exact_assignments_and_invalidates_history(comparison, monkeypatch):
    net, _, _, _ = comparison
    cfg = json.loads(net.project_row('demo')['config'])
    cfg['compiler_ranges'] = [
        {'start':0x80001020, 'end':0x80001040, 'toolchain':'sn-n64-281', 'flags':['-O1']},
        {'start':0x90000000, 'end':0x90000010, 'toolchain':'unused', 'flags':['-O3']}]
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?', (json.dumps(cfg),))
    monkeypatch.setattr(toolchains, 'get', lambda tc:SimpleNamespace(supported=True))
    monkeypatch.setattr(toolchains, 'runtime_error', lambda tc:None)
    tested = []
    def judge(proj, fn, tc, flags, source):
        tested.append((tc, tuple(flags)))
        return False, SimpleNamespace(percent=80.0, score=80), ''
    monkeypatch.setattr(net, '_judge_with', judge)
    result = net.run_compiler_comparison('demo', function='f0', used_profiles_only=True)
    assert result['comparison']['tested'] == 2
    assert sorted(tested) == [('gcc-2.7.2-kmc', ('-O2',)), ('sn-n64-281', ('-O1',))]
    fn = net._function('f0', 'demo')
    history = net.compiler_test_result(net.project_row('demo'), fn)
    assert not history['stale'] and history['comparison']['used_profiles_only']
    cfg['compiler_ranges'][0]['flags'] = ['-O3']
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?', (json.dumps(cfg),))
    assert net.compiler_test_result(net.project_row('demo'), fn)['stale']


def test_shared_eligibility_digest_preserves_existing_fingerprints(comparison):
    import hashlib

    from decompnet.project import compiler_for
    net, profiles, _, _ = comparison
    proj = net._project('demo')
    fn = net._function('f0', 'demo')
    source = net._comparison_source(fn)
    cfg = net.cfg(proj)
    contexts = [list(r) for r in net.store.all(
        "SELECT unit,version,header FROM context_versions WHERE project_id=? AND unit IN ('',?) ORDER BY unit",
        proj['id'], fn['unit'])]
    symbols = [list(r) for r in net.store.all('SELECT name,address,size,kind FROM symbols WHERE project_id=? ORDER BY name', proj['id'])]
    tools = [[tid, toolchains.installed(tid)] for tid in sorted({tc for tc, _ in profiles})]
    original = [source, profiles, contexts, symbols, tools, (fn['target'] or b'').hex(), fn['relocs'], fn['vram'],
                cfg.get('gp'), cfg.get('big_endian'), compiler_for(cfg, fn['vram']), cfg['compiler']]
    expected = hashlib.sha256(json.dumps(original, sort_keys=True).encode()).hexdigest()
    shared = net._comparison_inputs(proj, profiles)
    assert net._comparison_digest(proj, fn, source, profiles, shared) == expected
    assert net._comparison_digest(proj, fn, source, profiles) == expected


def test_eligibility_batches_shared_reads_and_reports_scan(comparison):
    net, _, _, _ = comparison
    queries, snapshots = [], []
    net.store._reader().set_trace_callback(queries.append)
    class Log:
        def __call__(self, line):
            pass
        def progress(self, value):
            snapshots.append(dict(value))
    net.survey_compilers('demo', all_installed=True, untested_only=True, limit=0, log=Log())
    net.store._reader().set_trace_callback(None)
    assert sum('SELECT name,address,size,kind FROM symbols' in q for q in queries) == 1
    assert sum('SELECT unit,version,header FROM context_versions' in q for q in queries) == 1
    assert sum('SELECT t.function_id,t.input_digest' in q for q in queries) == 1
    assert not any('SELECT input_digest FROM compiler_tests WHERE function_id' in q for q in queries)
    assert not any('SELECT source FROM attempts' in q for q in queries)
    scans = [s for s in snapshots if s.get('phase') == 'eligibility']
    assert scans[0]['checked'] == 0 and scans[-1]['checked'] == scans[-1]['scan_total'] == 2
    assert scans[-1]['eligible'] == 2


def test_bulk_comparison_does_not_sync_saved_files_or_build_a_map(comparison,monkeypatch):
    net,*_=comparison
    monkeypatch.setattr(net.progress,'sync',lambda *args:(_ for _ in ()).throw(AssertionError('implicit saved-file sync')))
    monkeypatch.setattr(net,'compiler_map',lambda *args:(_ for _ in ()).throw(AssertionError('unneeded compiler map')))
    assert net.run_compiler_comparison('demo')['tried']==2


def test_first_compile_precedes_later_eligibility_batches(comparison,monkeypatch):
    net,profiles,_,_=comparison
    pid=net.store.one("SELECT id FROM projects WHERE slug='demo'")['id']
    with net.store.tx() as db:
        for n in range(60):
            db.execute('INSERT INTO functions(project_id,name,vram,n_insns,status,best_score,best_source) VALUES(?,?,?,?,?,?,?)',
                       (pid,f'extra{n}',0x80003000+n*16,4,'open',200,f'int extra{n}(void){{return 1;}}'))
    checked=[];timings=[]
    digest=net._comparison_digest
    def tracked(*args,**kwargs):
        checked.append(args[1]['id'])
        return digest(*args,**kwargs)
    def judge(*args):
        if not timings:timings.append(len(checked))
        return False,SimpleNamespace(percent=80.0,score=80),''
    monkeypatch.setattr(net,'_comparison_digest',tracked)
    monkeypatch.setattr(net,'_judge_with',judge)
    result=net.run_compiler_comparison('demo')
    assert timings[0]<=25 and len(checked)==62
    assert result['tried']==62 and result['total']==62


def test_used_profiles_preserve_overlay_assignments_and_probe_each_tool_once(comparison,monkeypatch):
    from decompnet.project import function_compiler_key
    net,*_=comparison
    cfg=json.loads(net.store.one("SELECT config FROM projects WHERE slug='demo'")['config'])
    with net.store.tx() as db:
        db.execute("UPDATE functions SET vram=2147487744,rom_offset=id*100 WHERE project_id=1")
        rows=[dict(r) for r in db.execute('SELECT name,vram,rom_offset FROM functions WHERE project_id=1')]
        cfg['function_compilers']={function_compiler_key(r):dict(function=r['name'],vram=r['vram'],toolchain='gcc-2.7.2-kmc',flags=[f'-O{n%3}']) for n,r in enumerate(rows)}
        db.execute('UPDATE projects SET config=? WHERE id=1',(json.dumps(cfg),))
    probes=[]
    monkeypatch.setattr(toolchains,'runtime_error',lambda tid:probes.append(tid))
    proj=net.store.one("SELECT * FROM projects WHERE slug='demo'")
    usage=net.compiler_profile_usage(proj)
    assert sum(p['functions'] for p in usage)==4
    assert {tuple(p['flags']) for p in usage}=={('-O0',),('-O1',),('-O2',)}
    assert len(net._used_comparison_candidates(proj))==3
    assert probes==['gcc-2.7.2-kmc']


def test_compiler_panel_refresh_does_not_reconcile_saved_files(comparison,monkeypatch):
    net,*_=comparison
    monkeypatch.setattr(net.progress,'sync',lambda *args:(_ for _ in ()).throw(AssertionError('implicit saved-file sync')))
    monkeypatch.setattr(net,'compiler_map',lambda *args:None)
    assert net.compiler_answer('demo',display_only=True)['compiler']


def test_discovery_does_not_load_assembly_or_seeds(comparison):
    net, *_ = comparison
    queries = []
    net.store._reader().set_trace_callback(queries.append)
    net.survey_compilers('demo', all_installed=True, untested_only=True, limit=0)
    net.store._reader().set_trace_callback(None)
    assert not any('SELECT * FROM' in q and 'functions' in q for q in queries)


def test_auto_apply_reports_its_actual_stage(comparison, monkeypatch):
    net, *_ = comparison
    snapshots = []
    class Log:
        def __call__(self, line):
            pass
        def progress(self, value):
            snapshots.append(dict(value))
    monkeypatch.setattr(net, '_judge_with', lambda *a: (True, SimpleNamespace(percent=100, score=0), ''))
    def apply(*args):
        assert snapshots[-1]['phase'] == 'applying'
    monkeypatch.setattr(net, 'apply_function_compiler', apply)
    net.run_compiler_comparison('demo', log=Log(), auto_apply=True)
    assert sum(s.get('phase') == 'applying' for s in snapshots) == 2


def test_default_task_compares_every_function_past_old_batch_limit(comparison):
    net, _, calls, _ = comparison
    pid = net.project_row('demo')['id']
    with net.store.tx() as db:
        for i in range(4, 405):
            db.execute('INSERT INTO functions(project_id,name,vram,size,n_insns,status,best_score,best_source) VALUES(?,?,?,?,?,?,?,?)',
                       (pid, f'f{i}', 0x80001000+i*32, 16, 4, 'open', 200, f'int f{i}(void){{return 1;}}'))
    result = net.run_compiler_comparison('demo')
    assert result['tried'] == 403
    assert result['deferred'] == 0
    assert 'f404' in calls
    assert net.store.one('SELECT COUNT(*) n FROM compiler_tests')['n'] == 403
    assert net.run_compiler_comparison('demo')['tried'] == 0


def test_continuous_task_can_stop_and_resume_saved_functions(comparison):
    net, _, _, _ = comparison
    class Stopped(Exception):
        pass
    class Log:
        def __call__(self, line):
            pass
        def check_cancel(self):
            if net.store.one('SELECT COUNT(*) n FROM compiler_tests')['n']:
                raise Stopped()
    with pytest.raises(Stopped):
        net.run_compiler_comparison('demo', log=Log())
    assert net.store.one('SELECT COUNT(*) n FROM compiler_tests')['n'] == 1
    resumed = net.run_compiler_comparison('demo')
    assert resumed['tried'] == 1 and resumed['skipped'] == 1


def test_single_apply_uses_current_database_without_project_restore(comparison, monkeypatch):
    net, _, _, _ = comparison
    monkeypatch.setattr(net.progress, 'sync', lambda *a, **kw: pytest.fail('single apply must not import saved files'))
    monkeypatch.setattr(net, '_judge_with', lambda *a: (True, SimpleNamespace(score=0), ''))
    monkeypatch.setattr(net, '_apply_compiler_config', lambda *a, **kw: pytest.fail('profile is already assigned'))
    monkeypatch.setattr(net, 'set_compiler_ranges', lambda *a, **kw: pytest.fail('profile is already assigned'))
    checked = []
    def save(agent, proj, fn, source, **kwargs):
        assert kwargs == {'exact_only': True, 'synchronize': False}
        with net.progress.judging(proj['slug'], synchronize=kwargs['synchronize']):
            net.progress.check_result()
            checked.append(fn['name'])
    monkeypatch.setattr(net, 'try_quietly', save)
    net.apply_function_compiler('demo', 'f0', 'gcc-2.7.2-kmc', ['-O2'])
    assert checked == ['f0']


def test_already_matched_current_profile_is_reverified_without_config_write(comparison, monkeypatch):
    net, _, _, _ = comparison
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched',matched_source=best_source WHERE name='f0'")
    calls = []
    def judge(*args):
        calls.append(args[1]['name'])
        return True, SimpleNamespace(score=0), ''
    monkeypatch.setattr(net, '_judge_with', judge)
    monkeypatch.setattr(net, '_apply_compiler_config', lambda *a, **kw: pytest.fail('redundant config write'))
    monkeypatch.setattr(net, 'set_compiler_ranges', lambda *a, **kw: pytest.fail('redundant config write'))
    monkeypatch.setattr(net, 'try_quietly', lambda *a, **kw: pytest.fail('already matched'))
    out = net.apply_function_compiler('demo', 'f0', 'gcc-2.7.2-kmc', ['-O2'])
    assert out['matched'] and not out['newly_matched']
    assert calls == ['f0']


def test_bulk_comparison_loads_latest_attempt_without_best_source(comparison):
    net, _, calls, _ = comparison
    fn = net._function('f1', 'demo')
    with net.project_store(fn['project_id']).tx() as db:
        db.execute('UPDATE functions SET best_source=NULL WHERE id=?', (fn['id'],))
        db.execute('INSERT INTO attempts(function_id,source,created_at) VALUES(?,?,?)',
                   (fn['id'], 'int f1(void){return 2;}', 1))
    result = net.survey_compilers('demo', all_installed=True, untested_only=True)
    assert result['tried'] == 2
    assert set(calls) == {'f0', 'f1'}
    assert net.project_store(fn['project_id']).one('SELECT COUNT(*) AS n FROM compiler_tests')['n'] == 2
    assert net.survey_compilers('demo', all_installed=True, untested_only=True)['tried'] == 0


def test_bulk_comparison_skips_function_without_any_source(comparison):
    net, _, calls, _ = comparison
    fn = net._function('f1', 'demo')
    with net.project_store(fn['project_id']).tx() as db:
        db.execute('UPDATE functions SET best_source=NULL WHERE id=?', (fn['id'],))
    result = net.survey_compilers('demo', all_installed=True)
    assert result['tried'] == 1 and set(calls) == {'f0'}


def test_original_entry_apply_does_not_scan_unrelated_profiles(comparison, monkeypatch):
    net, _, _, _ = comparison
    from decompnet.app.prepared_evidence import scoped_config
    with net.store.tx() as db:
        db.execute("UPDATE functions SET rom_offset=256 WHERE name='f0'")
    proj = net._project('demo')
    fn = net._function('f0', 'demo')
    cfg = scoped_config(proj, fn, {'toolchain':'sn-n64-281', 'flags':['-O1']})
    from decompnet.app.engine import compilers
    original = compilers.compiler_for
    seen = []
    def profile(config, address, function=None):
        seen.append(function['name'])
        return original(config, address, function)
    monkeypatch.setattr(compilers, 'compiler_for', profile)
    with net.progress.view():
        net._apply_compiler_config(proj, cfg)
    assert seen and set(seen) == {'f0'}
    assert original(net.cfg(net._project('demo')), fn['vram'], fn)['toolchain'] == 'sn-n64-281'


def test_single_apply_defers_optional_match_effects(comparison, monkeypatch):
    net, _, _, _ = comparison
    from decompnet.app import restore_effects
    queued = []
    monkeypatch.setattr(restore_effects, '_queue', lambda net, pid, pending: queued.append(pending))
    monkeypatch.setattr(net, '_judge_with', lambda *a: (True, SimpleNamespace(score=0), ''))
    def save(agent, proj, fn, source, **kwargs):
        net.on_match(proj, fn['id'], agent)
        assert not queued
    monkeypatch.setattr(net, 'try_quietly', save)
    net.apply_function_compiler('demo', 'f0', 'gcc-2.7.2-kmc', ['-O2'])
    assert len(queued) == 1 and queued[0]['matched']


def test_apply_reuses_one_private_compile_session(comparison, monkeypatch):
    net, _, _, _ = comparison
    from decompnet.compile_sessions import current
    sessions = []
    def judge(*args):
        sessions.append(current())
        return True, SimpleNamespace(score=0), ''
    monkeypatch.setattr(net, '_judge_with', judge)
    def save(*args, **kwargs):
        sessions.append(current())
    monkeypatch.setattr(net, 'try_quietly', save)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET rom_offset=256 WHERE name='f0'")
    net.apply_function_compiler('demo', 'f0', 'sn-n64-281', ['-O1'])
    assert len(sessions) == 3
    assert sessions[0] is not None and all(s is sessions[0] for s in sessions)
    assert current() is None
