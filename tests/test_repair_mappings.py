"""Only exact donor provenance and still-verified independent pairs teach repair mappings."""
import json

from decompnet.app import repair_mappings as rm
from decompnet.app import similar_matches as sm

from .test_similar_matches import pair as pair_fixture

pair = pair_fixture


def rows(net):
    return net.store.one('SELECT * FROM functions WHERE id=1'), net.store.one('SELECT * FROM functions WHERE id=2')


def test_origins_reject_changed_donor_target_and_ambiguous_sources(pair):
    a,b=rows(pair);rm.remember(pair,a,b,'candidate',{'old':'new'})
    assert len(rm.origins(pair,b,'candidate')) == 1
    assert not rm.origins(pair,b,'different')
    with pair.store.tx() as db:
        db.execute("UPDATE functions SET target=? WHERE id=2",(b'changed',))
    assert not rm.origins(pair,rows(pair)[1],'candidate')
    with pair.store.tx() as db:
        db.execute('UPDATE functions SET target=? WHERE id=2',(b['target'],))
        db.execute("UPDATE functions SET matched_source='changed' WHERE id=1")
    assert not rm.origins(pair,b,'candidate')


def test_only_matched_repairs_create_pairs_and_reopened_evidence_is_ignored(pair):
    a,b=rows(pair);rm.remember(pair,a,b,'candidate',{'old':'new'})
    rm.accepted(pair,b,'candidate')
    assert not rm.pairs(pair,pair._project('destination'),pair._project('source'))
    with pair.store.tx() as db:
        db.execute("UPDATE functions SET status='matched',matched_source='verified' WHERE id=2")
    rm.accepted(pair,b,'candidate')
    assert len(rm.pairs(pair,pair._project('destination'),pair._project('source'))) == 1
    with pair.store.tx() as db:
        db.execute("UPDATE functions SET status='open' WHERE id=1")
    assert not rm.pairs(pair,pair._project('destination'),pair._project('source'))


def test_legacy_comment_requires_recorded_donor_and_exact_reconstruction(pair):
    a,b=rows(pair)
    source='/* adapted from similar verified C in old in source (matched), with its names changed by decompdecomp */\nint new(int x) { return x + 60; }'
    rm.recover(pair.twins,pair._project('destination'),b,source)
    assert not rm.origins(pair,b,source)
    with pair.store.tx() as db:
        db.execute("INSERT INTO cross_game_attempts(project_id,function_id,source_project_id,source_function_id,outcome,created_at) VALUES(2,2,1,1,'miss',0)")
    rm.recover(pair.twins,pair._project('destination'),b,source.replace('+ 60','+ 99'))
    assert not rm.origins(pair,b,source.replace('+ 60','+ 99'))
    rm.recover(pair.twins,pair._project('destination'),b,source)
    assert len(rm.origins(pair,b,source)) == 1


def test_data_mapping_needs_two_independent_pairs_and_preserves_comments(pair):
    with pair.store.tx() as db:
        for pid,name,addr in [(1,'data_a',0x80002000),(2,'data_b',0x80003000)]:
            db.execute('INSERT INTO symbols(project_id,name,address) VALUES(?,?,?)',(pid,name,addr))
            db.execute('UPDATE functions SET relocs=? WHERE project_id=?',(json.dumps([[0,5,addr],[4,6,addr]]),pid))
        db.execute("UPDATE functions SET matched_source='extern int data_a; int old(void) {return data_a;}' WHERE id=1")
        db.execute("UPDATE functions SET status='matched',matched_source='verified' WHERE id=2")
    a,b=rows(pair);rm.remember(pair,a,b,'seed',{'old':'new','data_a':'wrong'});rm.accepted(pair,b,'seed')
    proj,other=pair._project('destination'),pair._project('source')
    assert 'data_a' not in sm.regional_names(pair.twins,proj,other)
    with pair.store.tx() as db:
        for fid,original,name in [(3,1,'old2'),(4,2,'new2')]:
            db.execute('INSERT INTO functions(id,project_id,name,target,relocs,vram,size,n_insns,status,matched_source) '
                       "SELECT ?,project_id,?,target,relocs,vram+128,size,n_insns,status,matched_source FROM functions WHERE id=?",(fid,name,original))
    a2=pair.store.one('SELECT * FROM functions WHERE id=3');b2=pair.store.one('SELECT * FROM functions WHERE id=4')
    rm.remember(pair,a2,b2,'second',{});rm.accepted(pair,b2,'second')
    assert sm.regional_names(pair.twins,proj,other)['data_a'] == 'data_b'
    source='wrong; /* wrong */ "wrong";'
    rm.remember(pair,a,b,source,{'data_a':'wrong'})
    assert rm.variant(pair.twins,proj,b,source) == 'data_b; /* wrong */ "wrong";'
    with pair.store.tx() as db:
        db.execute("UPDATE functions SET status='open' WHERE id=3")
    assert 'data_a' not in sm.regional_names(pair.twins,proj,other)


def test_propagated_miss_retains_bindings_and_rejects_ambiguous_rewrites(pair):
    a,b=rows(pair)
    seed='int old(void) { return old; } /* old */'
    rm.remember(pair,a,b,seed,{'old':'old'})
    candidate='int new(void) { return new; } /* old */'
    rm.propagate(pair,b,seed,candidate)
    evidence=rm.origins(pair,b,candidate)
    assert json.loads(evidence[0][0]['names']) == {'old':'new'}
    ambiguous='int new(void) { return other; } /* old */'
    rm.propagate(pair,b,seed,ambiguous)
    assert not rm.origins(pair,b,ambiguous)


def test_ambiguous_provenance_does_not_create_a_verified_pair(pair):
    a,b=rows(pair)
    with pair.store.tx() as db:
        db.execute("INSERT INTO functions(id,project_id,name,target,relocs,vram,size,n_insns,status,matched_source) "
                   "SELECT 3,project_id,'another',target,relocs,vram+128,size,n_insns,status,matched_source FROM functions WHERE id=1")
        db.execute("UPDATE functions SET status='matched',matched_source='verified' WHERE id=2")
    rm.remember(pair,a,b,'same candidate',{})
    other=pair.store.one('SELECT * FROM functions WHERE id=3')
    rm.remember(pair,other,b,'same candidate',{})
    assert not rm.origins(pair,b,'same candidate')
    rm.accepted(pair,b,'same candidate')
    assert not rm.pairs(pair,pair._project('destination'),pair._project('source'))


def test_source_symbols_include_exact_address_identifiers_without_changing_shared_table():
    from decompnet.rom.symbols import Symbol, SymbolTable
    shared=SymbolTable([Symbol('D_80000000',0x80000000)])
    symbols=rm.source_symbols(shared,'D_80000010; /* D_80000020 */ "D_80000030";')
    assert symbols.resolve('D_80000010') == 0x80000010
    assert 'D_80000010' not in shared.by_name
    assert 'D_80000020' not in symbols.by_name and 'D_80000030' not in symbols.by_name


def test_learned_candidates_can_chain_existing_address_repairs(pair, monkeypatch):
    from decompnet.app import address_repairs, free_candidates
    from decompnet.app.twins import Job
    with pair.store.tx() as db:
        db.execute("UPDATE functions SET best_source='seed',best_score=10 WHERE id=2")
    monkeypatch.setattr(rm,'recover',lambda *a:None)
    monkeypatch.setattr(rm,'variant',lambda *a:'learned')
    monkeypatch.setattr(address_repairs,'variants',lambda source,*a: ['combined'] if source=='learned' else [])
    calls=[]
    def judge(agent,proj,fn,source):
        calls.append(source)
        return dict(matched=source=='combined',score=0 if source=='combined' else 10)
    monkeypatch.setattr(pair,'try_quietly',judge)
    assert free_candidates.run(pair.twins,'destination',Job()) == 1
    assert calls[0]=='learned' and 'combined' in calls


def test_verified_agent_symbol_substitution_completes_recorded_transfer(pair):
    with pair.store.tx() as db:
        db.execute("UPDATE functions SET matched_source='extern int D_80002000; int old(void){return D_80002000;}' WHERE id=1")
        db.execute("INSERT INTO cross_game_attempts(project_id,function_id,source_project_id,source_function_id,outcome,created_at) VALUES(2,2,1,1,'miss',0)")
    a,b=rows(pair)
    seed='extern int D_80002000; int new(void){return D_80002000;}'
    result=seed.replace('D_80002000','D_80003000')
    rm.remember(pair,a,b,seed,{'old':'new','D_80002000':'D_80002000'})
    agent,_=pair.local_agent('solver')
    pair._record(b,agent,20,seed,1)
    pair._record(b,agent,0,result,1,matched=True)
    rm.harvest(pair.twins,pair._project('destination'),b['id'])
    assert len(rm.origins(pair,rows(pair)[1],result))==1
    assert len(rm.pairs(pair,pair._project('destination'),pair._project('source')))==1
    bindings=json.loads(rm.origins(pair,rows(pair)[1],result)[0][0]['names'])
    assert bindings['D_80002000']=='D_80003000'
    # One pair still does not establish a generally reusable data mapping.
    assert 'D_80002000' not in sm.regional_names(pair.twins,pair._project('destination'),pair._project('source'))


def test_agent_mapping_harvest_rejects_literal_edits_or_comments_as_provenance(pair):
    a,b=rows(pair)
    seed='extern int D_80002000; int new(void){return D_80002000 + 1;}'
    changed=seed.replace('D_80002000','D_80003000').replace('+ 1','+ 2')
    rm.remember(pair,a,b,seed,{})
    with pair.store.tx() as db:
        db.execute("INSERT INTO cross_game_attempts(project_id,function_id,source_project_id,source_function_id,outcome,created_at) VALUES(2,2,1,1,'miss',0)")
    agent,_=pair.local_agent('solver');pair._record(b,agent,20,seed,1)
    pair._record(b,agent,0,changed,1,matched=True)
    rm.harvest(pair.twins,pair._project('destination'),b['id'])
    assert not rm.pairs(pair,pair._project('destination'),pair._project('source'))
    assert not rm.symbol_only_change(seed,changed)
    assert rm.symbol_only_change(seed,seed.replace('D_80002000','D_80003000'))
