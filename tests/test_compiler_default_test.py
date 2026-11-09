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


def test_compiler_answer_summary_omits_function_lists_but_preserves_counts(net, monkeypatch):
    monkeypatch.setattr(net, 'compiler_map', lambda *args, **kwargs: None)
    monkeypatch.setattr('decompnet.app.engine.compilers.consoles.compiler_name', lambda tc: tc)
    full = net.compiler_answer('game')
    summary = net.compiler_answer('game', include_functions=False)
    assert full['default_functions'] == summary['default_functions'] == 2
    assert len(full['default_function_list']) == 2
    assert 'default_function_list' not in summary
    assert full['parts'][0]['functions'] == summary['parts'][0]['functions'] == 2
    assert len(full['parts'][0]['function_list']) == 2
    assert 'function_list' not in summary['parts'][0]


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



def test_browser_counts_and_function_links_include_default_and_overrides(net, monkeypatch):
    from decompnet import consoles
    monkeypatch.setattr(consoles, 'compiler_name', lambda tid: tid)
    monkeypatch.setattr(net, 'compiler_map', lambda proj: None)
    result = net.compiler_answer('game')
    assert result['default_functions'] == 2
    assert {f['name'] for f in result['default_function_list']} == {'f0', 'f1'}
    part = result['parts'][0]
    assert part['functions'] == 2
    assert {f['name'] for f in part['function_list']} == {'f2', 'f3'}
    assert part['flag_list'] == ['-O1'] and part['toolchain'] == 'ido-5.3'
    assert part['family'] == 'ido' and not part['default']


def test_commit_rejects_a_broken_match_without_publishing(net, monkeypatch):
    from decompnet.app.compiler_cleanup import commit_config

    proj = net._project('game')
    proposed = copy.deepcopy(net.cfg(proj))
    proposed['compiler'] = dict(PROFILE)
    monkeypatch.setattr(net, '_judge_with', lambda *args: (False, NS(score=10), ''))
    with pytest.raises(NetworkError, match='would no longer match'):
        commit_config(net, proj, proposed)
    assert net._project('game')['config'] == proj['config']


def test_commit_rejects_concurrent_changes(net, monkeypatch):
    from decompnet.app.compiler_cleanup import commit_config

    proj = net._project('game')
    proposed = copy.deepcopy(net.cfg(proj))
    proposed['compiler'] = dict(PROFILE)
    def judge(*args):
        with net.store.tx() as db:
            db.execute("UPDATE functions SET matched_source='edited' WHERE name='f0'")
        return True, NS(score=0), ''
    monkeypatch.setattr(net, '_judge_with', judge)
    with pytest.raises(NetworkError, match='changed during verification'):
        commit_config(net, proj, proposed)
    assert net._project('game')['config'] == proj['config']


def test_replace_default_preserves_failed_matches_and_other_profiles(net, monkeypatch):
    from decompnet.project import compiler_for

    monkeypatch.setattr(net, '_judge_with', lambda proj, fn, tc, flags, source:
                        (fn['name'] != 'f1' or flags == ['-O2'], NS(score=20), ''))
    result = trial.replace_default(net, 'game', PROFILE)
    cfg = net.cfg(net._project('game'))
    assert cfg['compiler']['flags'] == ['-O1']
    assert compiler_for(cfg, 0x80001010)['flags'] == ['-O2']
    assert compiler_for(cfg, 0x80001020)['flags'] == ['-O1']
    assert result == {'replaced': True, 'preserved': 1}
    assert net.store.one("SELECT status FROM functions WHERE name='f1'")['status'] == 'matched'


def test_replace_default_refuses_empty_comparison(net, monkeypatch):
    before = net._project('game')['config']
    monkeypatch.setattr(trial, 'run', lambda *args: {'stale': False, 'checked': 0})
    with pytest.raises(NetworkError, match='Nothing was checked'):
        trial.replace_default(net, 'game', PROFILE)
    assert net._project('game')['config'] == before


def test_cleanup_moves_only_exact_matches_and_keeps_neighbour_settings(net, monkeypatch):
    from decompnet.app.compiler_cleanup import cleanup
    from decompnet.project import compiler_for

    monkeypatch.setattr(net, '_judge_with', lambda *args: (True, NS(score=0), ''))
    result = cleanup(net, 'game', source_profile=PROFILE)
    assert [r['function'] for r in result['changed']] == ['f2']
    cfg = net.cfg(net._project('game'))
    assert compiler_for(cfg, 0x80001020)['flags'] == ['-O2']
    assert compiler_for(cfg, 0x80001030)['flags'] == ['-O1']


def test_cleanup_requires_a_selected_profile(net):
    from decompnet.app.compiler_cleanup import cleanup

    before = net._project('game')['config']
    with pytest.raises(NetworkError, match='Choose a compiler profile'):
        cleanup(net, 'game')
    assert net._project('game')['config'] == before


def test_commit_ignores_unrelated_function_and_symbol_updates(net, monkeypatch):
    from decompnet.app.compiler_cleanup import commit_config

    proposed = copy.deepcopy(net.cfg(net._project('game')))
    proposed['compiler'] = dict(PROFILE)

    def judge(*args):
        with net.store.tx() as db:
            db.execute("UPDATE functions SET best_score=17 WHERE name='f2'")
            db.execute("INSERT OR REPLACE INTO symbols(project_id,name,address) VALUES(1,'unrelated',?)",
                       (0x80009000,))
        return True, NS(score=0), ''

    monkeypatch.setattr(net, '_judge_with', judge)
    commit_config(net, net._project('game'), proposed)
    assert net.cfg(net._project('game'))['compiler']['flags'] == ['-O1']


def test_failed_apply_restores_original_compiler_range(net, monkeypatch):
    from decompnet.project import compiler_for

    monkeypatch.setattr(net, '_judge_with', lambda *args: (True, NS(score=0), ''))
    def near_match(*args):
        with net.store.tx() as db:
            db.execute("UPDATE functions SET best_score=7 WHERE name='f3'")
        return {'matched': False}

    monkeypatch.setattr(net, 'try_quietly', near_match)
    before = net._project('game')['config']
    out = trial.apply_matches(net, 'game', PROFILE, ['f3'])
    assert out['passed'] == 0 and out['failed'] == 1
    assert net._project('game')['config'] == before
    assert compiler_for(net.cfg(net._project('game')), 0x80001030)['flags'] == ['-O1']


def test_failed_apply_restores_range_when_agent_claims_function(net, monkeypatch):
    agent, _ = net.local_agent('you')
    monkeypatch.setattr(net, '_judge_with', lambda *args: (True, NS(score=0), ''))

    def near_match(*args):
        with net.store.tx() as db:
            fid = db.execute("SELECT id FROM functions WHERE name='f3'").fetchone()['id']
            db.execute('INSERT INTO claims VALUES(?,?,?,?)', (fid, agent.id, 0, 9999999999))
        return {'matched': False}

    monkeypatch.setattr(net, 'try_quietly', near_match)
    before = net._project('game')['config']
    out = trial.apply_matches(net, 'game', PROFILE, ['f3'])
    assert out['passed'] == 0 and out['failed'] == 1
    assert net._project('game')['config'] == before


def test_apply_rechecks_and_skips_claimed_functions(net, monkeypatch):
    agent, _ = net.local_agent('you')
    with net.store.tx() as db:
        fid = db.execute("SELECT id FROM functions WHERE name='f3'").fetchone()['id']
        db.execute('INSERT INTO claims VALUES(?,?,?,?)', (fid, agent.id, 0, 9999999999))
    monkeypatch.setattr(net, '_judge_with', lambda *args: pytest.fail('claimed function must not compile'))
    out = trial.apply_matches(net, 'game', PROFILE, ['f3'])
    assert out['passed'] == 0 and out['failed'] == 1
    assert 'agent' in out['failures'][0]['reason']


def test_apply_saves_exact_match_and_preserves_other_ranges(net, monkeypatch):
    monkeypatch.setattr(net, '_judge_with', lambda *args: (True, NS(score=0), ''))
    def record(agent, proj, fn, source):
        with net.store.tx() as db:
            db.execute("UPDATE functions SET status='matched',matched_source=? WHERE id=?", (source, fn['id']))
        return {'matched': True}
    monkeypatch.setattr(net, 'try_quietly', record)
    out = trial.apply_matches(net, 'game', PROFILE, ['f3'])
    assert out['passed'] == 1 and out['matches'] == ['f3']
    assert net.store.one("SELECT status FROM functions WHERE name='f0'")['status'] == 'matched'
