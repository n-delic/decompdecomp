"""Preflight budgets and cached evidence never replace the normal profile application judge."""
import hashlib
from types import SimpleNamespace

import pytest

from decompnet.app import matching_preflight as preflight
from decompnet.store import IN_MEMORY, Store


@pytest.fixture(autouse=True)
def installed_current_toolchain(monkeypatch):
    monkeypatch.setattr(preflight.toolchains, 'installed', lambda tid:{'fingerprint':'pinned'})


def setup():
    state = {'header':'typedef int word;', 'auto':False, 'calls':[], 'applied':[], 'suggested':[]}
    fn = {'id':1, 'name':'f', 'status':'open', 'best_source':'int f(void){return value;}',
          'best_score':50, 'vram':4096, 'size':16, 'unit':''}
    cfg = {'compiler':{'toolchain':'example', 'flags':['-O2']}}
    proj = {'id':1, 'slug':'game'}
    def judge(proj, fn, toolchain, flags, source):
        state['calls'].append(flags)
        score = 0 if flags == ['-O3'] else 50
        return score == 0, SimpleNamespace(score=score), ''
    net = SimpleNamespace(store=Store(IN_MEMORY), fork_slug=lambda s:None, is_rom=lambda p:True,
                          cfg=lambda p:cfg, project_row=lambda s:proj, _function=lambda *a:fn,
                          flag_variants=lambda *a:[['-O0'],['-O1'],['-O2'],['-O3'],['-O2','-g']],
                          _comparison_digest=lambda p,f,s,c:hashlib.sha256(repr((s,c,state['header'])).encode()).hexdigest(),
                          _judge_with=judge, auto_adjusts=lambda p:state['auto'],
                          apply_function_compiler=lambda *a:state['applied'].append(a),
                          local_agent=lambda n:(object(),None), suggest=lambda *a,**k:state['suggested'].append(k))
    net.project_store = lambda _: net.store
    return net, proj, fn, state


def test_probe_order_is_bounded_cached_and_rechecks_changed_source_or_context():
    net, proj, fn, state = setup()
    result = preflight.run(net, proj, fn)
    assert result['tested'] == 4 and result['best']['exact']
    assert state['calls'] == [['-O2'],['-O0'],['-O1'],['-O3']]
    assert not state['applied'] and len(state['suggested']) == 1
    assert preflight.run(net,proj,fn)['cached']
    assert len(state['calls']) == 4 and len(state['suggested']) == 1
    fn['best_source'] += '\n'
    preflight.run(net,proj,fn)
    state['header'] += '\nextern int value;'
    preflight.run(net,proj,fn)
    assert len(state['calls']) == 12


def test_application_uses_existing_revalidation_gate_and_respects_the_budget():
    net, proj, fn, state = setup();state['auto'] = True
    preflight.run(net,proj,fn,max_profiles=1)
    assert len(state['calls']) == 1 and not state['applied']
    preflight.run(net,proj,fn)
    assert state['applied'] == [('game','f','example',['-O3'])]


def test_cancellation_is_not_cached_and_unresolved_symbols_skip_other_profiles():
    net, proj, fn, state = setup()
    def cancel():raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError,match='cancelled'):
        preflight.run(net,proj,fn,check=cancel)
    assert not net.store.one('SELECT 1 FROM reuse_trials')
    net._judge_with = lambda *a:(False,SimpleNamespace(score=0),'`missing` is not a known symbol.')
    result = preflight.run(net,proj,fn)
    assert result['tested'] == 1 and result['link']['symbols'] == ['missing']
    assert not state['applied'] and not state['suggested']


def test_imported_projects_own_their_flags_and_stopped_searches_are_retryable():
    net, proj, fn, state = setup()
    assert preflight.run(net,proj,fn,check=lambda:False)['tested'] == 0
    assert not net.store.one('SELECT 1 FROM reuse_trials')
    net.fork_slug = lambda s:'imported'
    assert preflight.run(net,proj,fn)['tested'] == 0 and not state['calls']


def test_missing_compilers_are_not_installed_and_current_exact_profiles_are_preserved(monkeypatch):
    net, proj, fn, state = setup()
    monkeypatch.setattr(preflight.toolchains, 'installed', lambda tid:None)
    assert preflight.run(net,proj,fn)['tested'] == 0 and not state['calls']
    monkeypatch.setattr(preflight.toolchains, 'installed', lambda tid:{'fingerprint':'pinned'})
    net._judge_with = lambda *a:(True,SimpleNamespace(score=0),'')
    assert preflight.run(net,proj,fn)['tested'] == 1
    assert not state['applied'] and not state['suggested']


def test_similarity_alone_does_not_automatically_change_a_compiler_profile():
    net, proj, fn, state = setup();state['auto'] = True
    net._judge_with = lambda p,f,t,flags,s:(False,SimpleNamespace(score=20 if flags==['-O3'] else 50),'')
    result=preflight.run(net,proj,fn)
    assert result['best']['score']==20 and not result['best']['exact']
    assert not state['applied'] and len(state['suggested'])==1


def test_rejected_partial_suggestion_keeps_working_profile_and_returns_to_search():
    from decompnet.network import NetworkError
    net,proj,fn,state=setup()
    net._judge_with=lambda p,f,t,flags,s:(False,SimpleNamespace(score=20 if flags==['-O3'] else 50),'')
    def reject(*args,**kwargs):raise NetworkError('not suggested: the C does not match')
    net.suggest=reject;logs=[]
    result=preflight.run(net,proj,fn,log=logs.append)
    assert 'not suggested' in result['suggestion_rejected']
    assert net.cfg(proj)['compiler']['flags']==['-O2'] and not state['applied']
    assert any('continue source search' in line for line in logs)
    assert preflight.run(net,proj,fn)['tested']==1  # Continue the remaining debug profile.
    assert preflight.run(net,proj,fn)['cached']


def test_unexpected_suggestion_failure_is_not_swallowed():
    from decompnet.network import NetworkError
    net,proj,fn,state=setup()
    def fail(*args,**kwargs):raise NetworkError('project was removed')
    net.suggest=fail
    with pytest.raises(NetworkError,match='project was removed'):preflight.run(net,proj,fn)


def test_one_profile_rounds_resume_durably_instead_of_retesting_first_choices():
    net,proj,fn,state=setup()
    assert preflight.run(net,proj,fn,max_profiles=1)['pending']==4
    assert preflight.run(net,proj,fn,max_profiles=1)['pending']==3
    assert preflight.run(net,proj,fn,max_profiles=1)['pending']==2
    last=preflight.run(net,proj,fn,max_profiles=1)
    assert last['best']['exact'] and last['pending']==0
    assert state['calls']==[['-O2'],['-O0'],['-O1'],['-O3']]
    assert len(state['suggested'])==1
    assert preflight.run(net,proj,fn,max_profiles=1)['cached']
    fn['best_source']+=' /* improved source */'
    assert preflight.run(net,proj,fn,max_profiles=1)['tested']==1
    assert state['calls'][-1]==['-O2']


def test_completed_profiles_survive_a_stopped_later_probe():
    net,proj,fn,state=setup();checks=[]
    def check():
        checks.append(True)
        return len(checks)<=2
    assert preflight.run(net,proj,fn,check=check)['tested']==1
    result=preflight.run(net,proj,fn)
    assert result['best']['exact'] and result['tested']==3
    assert state['calls']==[['-O2'],['-O0'],['-O1'],['-O3']]


def test_promising_candidates_test_debug_profiles_before_optimization():
    net,proj,fn,state=setup()
    cfg=net.cfg(proj);cfg['compiler']={'toolchain':'gcc-2.7.2-kmc','flags':['-O2','-g3']}
    fn['n_insns']=100;fn['best_score']=500
    net.debug_flag_variants=lambda tid,flags:[flags,['-O2'],['-O2','-g']]
    net._judge_with=lambda p,f,t,flags,s:(flags==['-O2'],SimpleNamespace(score=0 if flags==['-O2']else 500),'')
    state['auto']=True;seen=[]
    judge=net._judge_with
    def record(*args):seen.append(args[3]);return judge(*args)
    net._judge_with=record
    result=preflight.run(net,proj,fn,max_profiles=2)
    assert result['best']['exact'] and result['applied']
    assert seen==[['-O2','-g3'],['-O2']]


def test_captured_profiles_are_ranked_but_rejudged_with_current_inputs():
    import json

    from decompnet.app.profile_hypotheses import captured
    net,proj,fn,state=setup();net.repair_feedback=object()
    with net.store.tx() as db:
        db.execute('CREATE TABLE repair_profile_evidence(example_id INTEGER PRIMARY KEY, project_id INTEGER, profile TEXT, before_hash TEXT, after_hash TEXT)')
        for flags in (['-O1'],['-O3'],['-O3']):
            profile={'toolchain':'example','flags':flags}
            db.execute('INSERT INTO repair_profile_evidence(project_id,profile,before_hash,after_hash) VALUES(?,?,?,?)',
                       (proj['id'],json.dumps(profile),'same','same'))
    assert captured(net,proj,net.cfg(proj)['compiler'])==[['-O3'],['-O1']]
    assert preflight.run(net,proj,fn,max_profiles=2)['best']['exact']
    assert state['calls']==[['-O2'],['-O3']]
    assert not state['applied']
