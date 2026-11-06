"""Repeated claims and captured fixes, using only homemade C and fake judges."""
import hashlib

import pytest

from decompnet import candidate_quality, claim_stalls
from decompnet.app import evidence_repairs, register_reuse_repairs, release_repairs
from decompnet.app.permute import Permuter
from tests.test_claims import _game


@pytest.mark.parametrize('control', ['for(i=0;i<n;i++){ total += stride << 2; }',
                                     'while(n--){ total += stride * 4; }',
                                     'do { total += stride << 2; } while(n--);',
                                     'again: total += stride << 2; if(n--) goto again;'])
def test_last_textual_use_is_not_reused_across_a_back_edge(control):
    source = 'int f(int n){ int stride=2; int i; int total=0; '+control+' return total; }'
    assert not any(f=='last_shift_reuse' for f,_ in register_reuse_repairs.variants(source,'f'))


def test_straight_line_last_shift_is_still_available():
    assert any(f=='last_shift_reuse' for f,_ in register_reuse_repairs.variants(
        'int f(){int index=2; return index << 2;}', 'f'))


def test_returning_to_an_exhausted_source_does_not_reset_its_history(tmp_path):
    net,_=_game(tmp_path,[('f',10,'open')])
    with net.store.tx() as db: db.execute('UPDATE functions SET best_source=?',('int f(){return 1;}',))
    fn=net._function('f','game');claim_stalls.released(net,fn,exhausted=True)
    with net.store.tx() as db: db.execute('UPDATE functions SET best_source=?',('int f(){return 2;}',))
    claim_stalls.released(net,net._function('f','game'))
    assert not claim_stalls.blocked(net,net._project('game'))
    with net.store.tx() as db: db.execute('UPDATE functions SET best_source=?',('int f(){return 1;}',))
    assert claim_stalls.blocked(net,net._project('game'))==[fn['id']]


def test_quality_report_keeps_work_available_and_preserves_evidence(tmp_path, monkeypatch):
    net,_=_game(tmp_path,[('f',10,'open')]);agent,_=net.local_agent('worker')
    net.claim_row(agent,'game','f');fn=net._function('f','game')
    original='int f(){int x;return x;}'
    net._record(fn,agent,10,original,1);net._record(fn,agent,20,'int f(){return 1;}',1)
    net.release(agent,'f','Historical candidate uses an uninitialized value.','game')
    fn=net._function('f','game')
    assert fn['best_score']==10 and fn['best_source']==original
    assert candidate_quality.blocked(net,net._project('game')) == []
    assert not claim_stalls.blocked(net,net._project('game'))
    assert candidate_quality.pending(net,fn)
    assert 'Work continues' in ' '.join(candidate_quality.brief(net,fn))
    assert Permuter(net)._hold(agent,fn)
    with net.store.tx() as db: db.execute('DELETE FROM claims')
    assert net.forks._hold_for_try('game','f',agent) == fn['id']
    with net.store.tx() as db: db.execute('DELETE FROM claims')
    from decompnet.app import deep_permuter
    assert deep_permuter.validate(net,'game','f',1,10)[1]['id'] == fn['id']
    from types import SimpleNamespace
    monkeypatch.setattr(net,'_context',lambda *args: {'header':''})
    monkeypatch.setattr(net,'_compile',lambda *args: SimpleNamespace(ok=False))
    assert net.try_quietly(agent,net._project('game'),fn,original)['score'] is None
    calls=[]
    monkeypatch.setattr(Permuter,'_repair_round',lambda *args,**kwargs: calls.append(kwargs['function_id']))
    release_repairs.run(net,'game',fn)
    assert calls == [fn['id']]
    # A generic claim must see the function, without an explicit name override.
    next_agent,_=net.local_agent('next-worker')
    assert net.claim_row(next_agent,'game',None)['id']==fn['id']
    digest=hashlib.sha256(original.encode()).hexdigest()
    with pytest.raises(Exception,match='changed|reviewed'):
        candidate_quality.review(net,'game','f','old hash')
    assert candidate_quality.listing(net,'game')[0]['source_hash']==digest
    claim_stalls.released(net,fn,exhausted=True)
    candidate_quality.review(net,'game','f',digest)
    assert not candidate_quality.pending(net,fn)
    assert not claim_stalls.blocked(net,net._project('game'))


def test_changed_source_does_not_inherit_another_candidates_quality_report(tmp_path):
    net,_=_game(tmp_path,[('f',10,'open')]);agent,_=net.local_agent('worker')
    net.claim_row(agent,'game','f');fn=net._function('f','game')
    net._record(fn,agent,10,'int f(){int x;return x;}',1)
    net.release(agent,'f','Historical candidate uses an uninitialized value.','game')
    with net.store.tx() as db: db.execute('UPDATE functions SET best_source=?',('int f(){return 1;}',))
    assert not candidate_quality.blocked(net,net._project('game'))


def test_table_slot_is_staged_early_only_for_a_known_array():
    source='void f(){u8 *row; u16 index; int b; index=load(); b=next(); row=table[index]+b*4;}'
    variants=evidence_repairs.variants(source,'f','extern u8 *table[];')
    early=[s for f,s in variants if f=='table_slot_lifetime' and s.index('&table[index]')<s.index('b=next()')]
    assert early
    assert not any(s.index('&table[index]')<s.index('b=next()') for _,s in evidence_repairs.variants(source,'f'))
    for unsafe in (source.replace('u16 index;', ''), source.replace('void f()', 'void f(u8 **table)')):
        assert not any(s.index('&table[index]')<s.index('b=next()') for _,s in evidence_repairs.variants(unsafe,'f','extern u8 *table[];'))


def test_signedness_and_assignment_reuse_are_one_bounded_hypothesis():
    source='int f(){s32 word; s32 diff; u32 c=read(); u32 d=read(); diff=c-d; if(diff)return diff; word=read(); return word;}'
    variants=evidence_repairs.variants(source,'f')
    assert any(f=='unsigned_assignment_reuse' and 'u32 word;' in s and '(word = c)-d' in s for f,s in variants)
    assert not any(f=='unsigned_assignment_reuse' for f,_ in evidence_repairs.variants(source.replace('word=read();','use(word); word=read();'),'f'))


def test_forward_guard_keeps_the_body_scope_and_both_return_paths():
    source='int f(int a){if(a){if(a>2)return 0;return 1;}return 0;}'
    generated=[s for f,s in evidence_repairs.variants(source,'f') if f=='guard_forward_jump']
    assert generated and 'goto __repair_guard_body;' in generated[0]
    assert '__repair_guard_body: {if(a>2)return 0;return 1;}' in generated[0]
    assert not any(f=='guard_forward_jump' for f,_ in evidence_repairs.variants(source.replace('return 1;','work();'),'f'))


def test_shift_reuse_does_not_change_the_shift_counts_precedence():
    source='int f(){int index=2; return index << 2 + 1;}'
    assert not any(f=='last_shift_reuse' for f,_ in register_reuse_repairs.variants(source,'f'))


def test_next_experiment_does_not_recommend_previously_compiled_misses(tmp_path):
    from decompnet.app import agent_targets, agent_tokens
    net,_=_game(tmp_path,[('f',10,'open')]);agent,_=net.local_agent('worker')
    source='int f(){int value;return value;}'
    with net.store.tx() as db: db.execute('UPDATE functions SET best_source=?',(source,))
    fn=net._function('f','game');scope=agent_tokens.target_scope(net,fn,'')
    for target in agent_targets.valid_targets(source,'signedness'):
        for patch in agent_tokens.family_patches('signedness',target['old']):
            candidate=agent_targets.replace_anchor(source,patch['old'],patch['new'])
            with net.store.tx() as db:
                db.execute('INSERT OR IGNORE INTO agent_family_targets VALUES(?,?,?,?)',
                           (fn['id'],scope,'compiled-miss',hashlib.sha256(candidate.encode()).hexdigest()))
    assert 'family="signedness"' not in agent_tokens.next_action(net,agent,fn,source,'revision')
