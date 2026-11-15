"""Synthetic bounds loops: joint candidates survive bad isolated scores, never bypass the judge."""
from types import SimpleNamespace

import pytest

from decompnet.app import matching_preflight
from decomprepair import guarded_loop_repairs as loops
from tests.test_matching_preflight import setup

SOURCE = '''char *f(char *p, char *end) {
    while (({ int t = p < end; t; })) {
        top:
        while (*p == ' ') {
            if (!(p < end)) goto top;
            p++;
        }
        return p;
    }
    return end;
}
'''


def test_corrected_backedge_and_return_guard_are_independent_source_hypotheses():
    guarded,correct=loops.sources(SOURCE,'f')
    assert 'if (!(p < end)) break;' in correct and 'goto top' not in correct
    assert 'while (({ int t=p<end; if (!t) return end; t; }))' in guarded
    assert '\n        top:\n' in guarded  # Preserve layout outside the changed condition.
    assert len(loops.sources(correct,'f'))==1
    assert any('bypass' in h for h in loops.hints(SOURCE,'f'))


@pytest.mark.parametrize('source',[
    SOURCE.replace('p < end','read() < end'),
    SOURCE.replace('int t','volatile int t'),
    SOURCE.replace('return end;','return p;'),
    SOURCE.replace('p++;','end++;'),
    SOURCE.replace('while (*p', 'switch (*p'),
    SOURCE.replace('while (*p', 'for (*p'),
    SOURCE.replace('goto top;', 'goto elsewhere;'),
])
def test_unsupported_or_opaque_control_is_not_repaired(source):
    # An unrelated goto must not receive a guard suggesting its control flow was corrected.
    assert not loops.sources(source,'f')


def test_joint_lane_is_bounded_and_compiler_scoped():
    fn={'name':'f','best_source':SOURCE,'best_score':210,'n_insns':87}
    own={'toolchain':'gcc-2.7.2-kmc','flags':['-O2']}
    pairs=loops.probes({'arch':'mips'},fn,own)
    assert len(pairs)==3 and pairs[0][0]==('-O2','-fno-thread-jumps')
    assert not loops.probes({'arch':'ppc'},fn,own)
    assert not loops.probes({'arch':'mips'},fn,{**own,'toolchain':'ido-5.3'})
    assert not loops.probes({'arch':'mips'},fn,own,'#define p elsewhere')


def test_branch_likely_diagnostic_does_not_claim_equivalence():
    rows=[('i','beqz $v0, 0x154','beql $v0, $zero, 0x154')]
    assert any('annulled' in h for h in loops.hints(SOURCE,'f',rows))
    assert not any('annulled' in h for h in loops.hints(SOURCE,'f',[('i','beqz $v0, 4','bnez $v0, 4')]))


def test_shared_preflight_checks_joint_candidate_before_greedy_source_selection(monkeypatch):
    net,proj,fn,state=setup();proj['arch']='mips'
    fn.update(best_source=SOURCE,n_insns=87,best_score=210)
    net.cfg(proj)['compiler']={'toolchain':'gcc-2.7.2-kmc','flags':['-O2']}
    monkeypatch.setattr(matching_preflight.toolchains,'installed',lambda *a:True)
    calls=[]
    def judge(p,f,tc,flags,source):
        calls.append((source,flags))
        exact=source==loops.sources(SOURCE,'f')[0] and '-fno-thread-jumps' in flags
        # Both isolated source repairs are worse than the saved best.
        return exact,SimpleNamespace(score=0 if exact else 710 if source!=SOURCE else 210),''
    net._judge_with=judge
    net._context=lambda *a:net.store.one("SELECT '' AS header")
    result=matching_preflight.run(net,proj,fn,max_profiles=4)
    assert result['best']['exact'] and result['tested']==2 and len(calls)==2
    assert state['suggested'][0]['source']==loops.sources(SOURCE,'f')[0]
    assert fn['best_source']==SOURCE  # Diagnostic-only until normal guarded adoption.


def test_backedge_destination_and_delay_slot_diagnostic_reaches_agent_brief():
    from decompnet.app.source_diagnostics import hints
    rows=[('i','beqz $v0, 0x14','beqz $v0, 0x1c'),('i','sltu $v0, $a0, $a1','nop')]
    assert any('delay-slot comparison' in h for h in hints(SOURCE,'f',arch='mips',rows=rows))
    assert not any('delay-slot comparison' in h for h in loops.hints(SOURCE,'f',rows[:1]))


@pytest.mark.parametrize('failure',['returned','raised'])
def test_exact_pair_failed_adoption_is_logged_and_retryable(monkeypatch,failure):
    from decompnet.network import NetworkError
    net,proj,fn,state=setup();proj['arch']='mips';state['auto']=True
    fn.update(best_source=SOURCE,n_insns=87,best_score=210)
    net.cfg(proj)['compiler']={'toolchain':'gcc-2.7.2-kmc','flags':['-O2']}
    monkeypatch.setattr(matching_preflight.toolchains,'installed',lambda *a:True)
    net._judge_with=lambda p,f,t,flags,s:(s!=SOURCE,SimpleNamespace(score=0 if s!=SOURCE else 210),'')
    calls=[];logs=[]
    def suggest(*a,**k):
        calls.append(k)
        if len(calls)==1:
            if failure=='raised':raise NetworkError('not suggested: stale check')
            return 'Exact bundle not applied: inputs changed.'
        fn['status']='matched'
        return 'Exact bundle applied.'
    net.suggest=suggest
    first=matching_preflight.run(net,proj,fn,log=logs.append)
    assert not first['applied'] and any('stale check' in s or 'inputs changed' in s for s in logs)
    second=matching_preflight.run(net,proj,fn,log=logs.append)
    assert second['applied'] and len(calls)==2 and second['tested']==0


def test_autopilot_deterministic_round_reaches_joint_lane_with_its_remaining_budget(monkeypatch):
    import json
    import threading

    from decompnet.app.permute import Permuter, State
    from tests.test_permute import AGENT, FakeNet
    net=FakeNet();net._comparison_digest=lambda *a:'scope'
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?',(json.dumps({'compiler':{'toolchain':'gcc-2.7.2-kmc','flags':['-O2']}}),))
        db.execute('UPDATE functions SET best_source=?,best_score=210,n_insns=87',(SOURCE,))
    calls=[]
    def preflight(net,proj,fn,log,check,max_profiles):
        assert check() and max_profiles==2
        assert not net.store.one('SELECT 1 FROM claims')
        calls.append(fn['name'])
        with net.store.tx() as db:db.execute("UPDATE functions SET status='matched',best_score=0")
        return {'tested':2,'applied':True}
    monkeypatch.setattr(matching_preflight,'run',preflight)
    state=State(stop=threading.Event())
    Permuter(net)._repair_round(AGENT,net.project_row('game'),state,budget=2)
    assert calls==['f'] and state.matched==1 and state.variants==2
