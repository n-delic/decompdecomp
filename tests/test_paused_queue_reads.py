"""Large paused queues use bounded metadata reads, without making old verdicts current."""
from types import SimpleNamespace

import pytest

from decompnet import candidate_scope, claim_stalls
from decompnet.request_timing import capture
from tests.test_model_pauses import setup


def pause_all(net, agent):
    for fn in net.store.all('SELECT * FROM functions'):
        claim_stalls.released(net,fn,agent=agent,exhausted=True)


def test_paused_model_status_shares_inputs_and_warm_result_across_workers(tmp_path):
    net,(a,_,c)=setup(tmp_path,40);proj=net._project('game');pause_all(net,a)
    net.store.read_cache.entries.clear()
    with capture() as cold:
        first=claim_stalls.blocked(net,proj,a)
    assert len(first)==40 and cold['queries']<105
    with capture() as warm:
        second=claim_stalls.blocked(net,proj,c)
    assert first==second and warm['queries']<15
    second.append(-1)
    assert -1 not in claim_stalls.blocked(net,proj,c)


def test_batched_signatures_match_ordinary_symbol_header_and_callee_scopes(tmp_path):
    net,(a,_,_)=setup(tmp_path,3)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source='int f0(){return helper(D_80001000);}',vram=2147487744 WHERE name='f0'")
        db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(1,'D_80001000',2147487744,4,'data')")
        db.execute("INSERT INTO functions(project_id,name,status,matched_source,unit) VALUES(1,'helper','matched','int helper(int x){return x;}','a')")
    proj=net._project('game');rows=net.store.all('SELECT * FROM functions ORDER BY id')
    common=claim_stalls.common_state(net,proj);shared=candidate_scope.batch_context(net,proj)
    for fn in rows:
        assert claim_stalls.signature(net,proj,fn,common)==claim_stalls.signature(net,proj,fn,common,shared=shared)


def test_cached_pause_clears_immediately_for_source_symbols_header_and_model_changes(tmp_path):
    net,(a,b,c)=setup(tmp_path,2);proj=net._project('game')
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source='void f(){ use(D_80001000); }'")
        db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(1,'D_80001000',2147487744,4,'data')")
    pause_all(net,a)
    assert len(claim_stalls.blocked(net,proj,c))==2
    with net.store.tx() as db:db.execute("UPDATE functions SET best_source='void f(){ changed(); }' WHERE name='f0'")
    assert claim_stalls.blocked(net,proj,c)==[net._function('f1','game')['id']]
    with net.store.tx() as db:db.execute("UPDATE symbols SET address=2147487748 WHERE name='D_80001000'")
    assert claim_stalls.blocked(net,proj,c)==[]
    pause_all(net,a)
    assert len(claim_stalls.blocked(net,proj,c))==2
    context=net._context(proj['id'],'a')
    with net.store.tx() as db:
        db.execute('INSERT INTO context_versions(project_id,version,header,unit) VALUES(?,?,?,?)',
                   (proj['id'],(context['version'] if context else 0)+1,'typedef int Updated;','a'))
    assert claim_stalls.blocked(net,proj,c)==[]
    pause_all(net,a)
    assert len(claim_stalls.blocked(net,proj,c))==2
    assert claim_stalls.blocked(net,proj,b)==[]
    with net.store.tx() as db:db.execute("UPDATE helpers SET model='beta' WHERE name='c'")
    assert claim_stalls.blocked(net,proj,c)==[]


def test_resume_retry_and_repair_rule_edits_invalidate_cached_pauses(tmp_path,monkeypatch):
    net,(a,_,_)=setup(tmp_path,2);proj=net._project('game');pause_all(net,a)
    assert len(claim_stalls.blocked(net,proj,a))==2
    fn=net._function('f0','game');claim_stalls.resume(net,fn,a,single_turn=True)
    assert claim_stalls.blocked(net,proj,a)==[net._function('f1','game')['id']]
    monkeypatch.setattr(claim_stalls,'repair_rules',lambda:[('new-repair','digest')])
    assert claim_stalls.blocked(net,proj,a)==[]


def test_matched_callee_changes_invalidate_cached_pause_evidence(tmp_path):
    net,(a,_,_)=setup(tmp_path);proj=net._project('game')
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source='void f0(){ helper(); }'")
        db.execute("INSERT INTO functions(project_id,name,status,matched_source,unit) VALUES(1,'helper','matched','void helper(){}','a')")
    pause_all(net,a)
    assert claim_stalls.blocked(net,proj,a)
    with net.store.tx() as db:db.execute("UPDATE functions SET matched_source='void helper(){ changed(); }' WHERE name='helper'")
    assert not claim_stalls.blocked(net,proj,a)


def test_imported_external_source_is_rechecked_without_database_revision_change(tmp_path,monkeypatch):
    net,(a,_,_)=setup(tmp_path);proj=net._project('game')
    source=tmp_path/'source.c';source.write_text('void f0(){}')
    fp=SimpleNamespace(root=tmp_path,unit=lambda _:SimpleNamespace(source='source.c'))
    monkeypatch.setattr(net,'fork_slug',lambda _:'game')
    monkeypatch.setattr(net.forks,'get',lambda _:(None,fp,None))
    pause_all(net,a)
    assert claim_stalls.blocked(net,proj,a)
    revision=net.store.read_version('inputs',proj['id'])
    source.write_text('void f0(){ /* external change */ }')
    assert net.store.read_version('inputs',proj['id'])==revision
    assert not claim_stalls.blocked(net,proj,a)


@pytest.mark.parametrize('helper', ['constant_initializer_staging.py', 'integer_abi.py',
                                     'integer_repair_support.py', 'mips_live_return_address.py',
                                     'mips_addresses.py'])
def test_changed_hardware_or_initializer_helper_reopens_cached_candidate(tmp_path,monkeypatch,helper):
    original=claim_stalls.file_digest
    changed=False
    def digest(path):
        if changed and path.name==helper:return 'updated-helper'
        return original(path)
    monkeypatch.setattr(claim_stalls,'file_digest',digest)
    net,(agent,_,_)=setup(tmp_path,1);proj=net._project('game');pause_all(net,agent)
    assert claim_stalls.blocked(net,proj,agent)
    changed=True
    assert not claim_stalls.blocked(net,proj,agent)


def test_large_claim_batches_keep_the_complete_scheduling_result(tmp_path,monkeypatch):
    # More candidates than the shared cache capacity: per-function callee entries
    # used to evict the complete paused queue and rebuild it for every batch slot.
    net,(a,_,c)=setup(tmp_path,160)
    pause_all(net,a)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source='void f(){ changed(); }'")
    monkeypatch.setattr(net,'sdk_functions',lambda _: {})
    monkeypatch.setattr(net,'_brief',lambda fn,agent,header: f"## CLAIMED: {fn['name']}")
    net.store.read_cache.entries.clear()
    signatures=[]
    original=claim_stalls.signature
    def signature(*args,**kwargs):
        signatures.append(args[2]['id'])
        return original(*args,**kwargs)
    monkeypatch.setattr(claim_stalls,'signature',signature)
    net.claim_many(a,'game',4)
    assert len(signatures)==160
    first={r['function_id'] for r in net.store.all('SELECT function_id FROM claims WHERE agent_id=?',a.id)}
    assert len(first)==4
    net.claim_many(c,'game',4)  # another worker of the same model uses the same current result
    second={r['function_id'] for r in net.store.all('SELECT function_id FROM claims WHERE agent_id=?',c.id)}
    assert len(second)==4 and not first & second
    assert len(signatures)==160
    # Restoring one unchanged failed candidate invalidates the result immediately.
    fn=net._function('f159','game')
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source='void f(){}' WHERE id=?",(fn['id'],))
    assert claim_stalls.blocked(net,net._project('game'),a)==[fn['id']]
    assert len(signatures)==320


def test_standalone_callee_cache_and_batch_signatures_keep_current_evidence(tmp_path,monkeypatch):
    net,(_,_,_)=setup(tmp_path,1)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source='void f0(){ helper(); }'")
        db.execute("INSERT INTO functions(project_id,name,status,matched_source,unit) "
                   "VALUES(1,'helper','matched','void helper(){}','a')")
    proj=net._project('game');fn=net._function('f0','game')
    calls=[];original=candidate_scope.callees
    def callees(*args,**kwargs):
        calls.append(1)
        return original(*args,**kwargs)
    monkeypatch.setattr(candidate_scope,'callees',callees)
    first=candidate_scope.key(net,proj,fn)
    assert candidate_scope.key(net,proj,fn)==first and len(calls)==1
    assert candidate_scope.key(net,proj,fn,candidate_scope.batch_context(net,proj))==first
    with net.store.tx() as db:
        db.execute("UPDATE functions SET matched_source='void helper(){ changed(); }' WHERE name='helper'")
    changed=candidate_scope.key(net,proj,fn)
    assert changed!=first
    assert candidate_scope.key(net,proj,fn,candidate_scope.batch_context(net,proj))==changed
