import json

import pytest

from decompnet.app import source_lead_analysis as analysis
from decompnet.arch.mips_runtime import incoming_arguments
from decompnet.network import NetworkError
from tests.test_source_preparation import prepared_net


def test_incoming_float_integer_and_stack_slots_are_evidence_not_parameter_counts():
    info=incoming_arguments('addiu $sp, $sp, -0x18\nsra $t9, $a0, 3\nsll $t6, $a1, 16\nmov.s $fv0, $fa0\nlw $t8, 0x28($sp)\njr $ra')
    assert info['mixed'] and info['floating']==['$f12'] and info['incoming_stack_slots']==[5]
    assert not incoming_arguments('mtc1 $a1, $fa0\nlw $v0, 0($a0)\njr $ra')['mixed']
    assert not incoming_arguments('lw $v0, 0($a1)\nmul.s $fv0, $fa0, $ft0\njr $ra')['mixed']


def test_source_lead_support_uses_calls_fields_and_control_not_name_similarity():
    source='void candidate(int a,int (*f)(int,int),void *p){ f(a,0); }'
    good=[(' ','lw $v0, 0x18($a0)','lw $v0, 0x18($a0)'),(' ','jal save','jal save'),(' ','jr $ra','jr $ra')]
    bad=[('|','lw $v0, 0x18($a0)','lw $v0, 4($a0)'),('|','jal save','jal load'),('<','beqz $v0, 4',''),(' ','jr $ra','jr $ra')]
    a=analysis.compare(good,'mips',source,'candidate');b=analysis.compare(bad,'mips',source,'candidate')
    assert a['support']>b['support'] and a['source_parameters']==3
    assert b['missing_calls']=={'save':1} and b['extra_calls']=={'load':1}
    assert b['target']['branches'] and not b['candidate']['branches']
    assert 'not automatic retail-source edits' in b['note']


def test_records_deduplicate_rejections_and_context_scoped_register_misses():
    net,proj,fn=prepared_net();rows=[('r','move $v0, $t6','move $v0, $t0')]
    for i in range(3):
        source=f'int f(void){{return {i};}}'
        analysis.record(net,proj,fn,source,'private header',50,rows,{'symbol':'reference','file':'local.c'},20)
        analysis.record(net,proj,fn,source,'private header',50,rows,{'symbol':'reference','file':'local.c'},20)
    result=analysis.listing(net,proj)
    assert result['families'][0]['tested']==3 and result['families'][0]['improved']==0
    assert '3 distinct prepared' in analysis.brief(net,proj,fn)
    assert 'Already measured without improvement' in analysis.brief(net,proj,fn)
    item=result['leads'][0]
    analysis.reject(net,proj,item['id'],'Call and field evidence contradict this identity.')
    assert any(r.get('rejected') for r in analysis.listing(net,proj)['leads'])
    with pytest.raises(NetworkError):analysis.reject(net,proj,item['id'],'')
    with net.store.tx() as db:db.execute("UPDATE functions SET best_source='better seed' WHERE id=1")
    assert '3 distinct prepared' not in analysis.brief(net,proj,net.store.one('SELECT * FROM functions WHERE id=1'))
    assert 'Already measured without improvement' not in analysis.brief(net,proj,net.store.one('SELECT * FROM functions WHERE id=1'))
    with net.store.tx() as db:db.execute("UPDATE context_versions SET header='changed'")
    assert '3 distinct prepared' not in analysis.brief(net,proj,fn)


def test_cached_probes_not_counted_as_new_wins_or_globally_shared():
    net,proj,fn=prepared_net()
    for family in ['original-context','compiler-profile']:
        for source in ['int f(void){return 0;}','int f(void){ return 0; }']:
            analysis.record(net,proj,fn,source,'',0,[],{'family':family},10)
    groups=analysis.listing(net,proj)['families']
    assert all(g['tested']==2 and g['matched']==1 for g in groups)
    with net.store.tx() as db:
        db.execute("INSERT INTO projects(id,slug,name,arch,root,config) VALUES(2,'other','Other','mips','.',?)",(json.dumps({'compiler':{}}),))
    assert analysis.listing(net,net.store.one('SELECT * FROM projects WHERE id=2'))['families']==[]


def test_local_yield_order_keeps_exploration_and_every_variant(monkeypatch):
    net,proj,fn=prepared_net();pairs=[('a','h'),('b','h'),('c','h')]
    families={pairs[0]:'weak',pairs[1]:'unknown',pairs[2]:'strong'}
    monkeypatch.setattr(analysis,'listing',lambda *args:{'families':[
        {'family':'weak','profile':{},'tested':32,'improved':0,'matched':0},
        {'family':'strong','profile':{},'tested':32,'improved':20,'matched':5}]})
    ranked=analysis.rank_pairs(net,proj,fn,pairs,families)
    assert ranked==[pairs[0],pairs[2],pairs[1]]


def test_rejection_only_skips_same_inputs_and_preserves_reason():
    net,proj,fn=prepared_net();source='int f(void){return 1;}'
    lead={'symbol':'reference','file':'one.c','family':'original-context'}
    analysis.record(net,proj,fn,source,'header',30,[],lead)
    item=analysis.listing(net,proj)['leads'][0]
    analysis.reject(net,proj,item['id'],'Wrong argument shape')
    assert analysis.rejected(net,fn,analysis.destination(net,proj,fn),source,'header',lead,{})
    assert not analysis.rejected(net,fn,analysis.destination(net,proj,fn),source,'changed',lead,{})
    assert analysis.listing(net,proj)['leads'][0]['rejected']=='Wrong argument shape'


def test_caller_setup_is_shown_for_mixed_entry_lead():
    net,proj,fn=prepared_net()
    with net.store.tx() as db:
        db.execute("UPDATE functions SET asm=? WHERE id=1",('lw $v0, 0($a0)\nmul.s $fv0, $fa0, $ft0\njr $ra',))
        db.execute("INSERT INTO functions(project_id,name,n_insns,vram,asm) VALUES(1,'caller',4,8192,?)",('move $a0, $s0\nmov.s $f12, $f2\njal f\nnop',))
    net.xrefs=lambda p:{'called_by':{4096:{8192}}}
    text=analysis.brief(net,proj,net.store.one('SELECT * FROM functions WHERE id=1'))
    assert 'Caller caller:' in text and 'mov.s $f12, $f2' in text and 'no ABI is inferred' in text


def test_coherent_alternatives_keep_source_identity_and_rename_the_entry(tmp_path):
    from decompnet.app import source_preparation as prep
    (tmp_path/'one.c').write_text('int original(void){return 1;}')
    (tmp_path/'two.c').write_text('int alternate(void){return 2;}')
    (tmp_path/'types.h').write_text('')
    cfg={'root':str(tmp_path),'contexts':'f = alternate @ two.c | types.h'}
    reader=prep.Reader(cfg)
    pairs=prep.prepare_variants(reader,cfg,('f','original','one.c'),{'original':'f'},'')
    assert len(pairs)==2 and 'int f(void){return 2;}' in pairs[1][0]
    assert reader.origins[pairs[1]]['symbol']=='alternate'
    assert reader.origins[pairs[0]]['file']=='one.c'


def test_identity_ranking_is_scoped_to_complete_measured_pair(monkeypatch):
    import hashlib
    net,proj,fn=prepared_net()
    pairs=[('wrong','h'),('new','h'),('supported','h')]
    digest=lambda text:hashlib.sha256(text.encode()).hexdigest()
    bad={'target':{'calls':['a','b'],'offsets':[4,8]},'candidate':{'calls':['c','d'],'offsets':[12,16]},'support':0}
    leads=[{'function':fn['name'],'scope':'current','profile':{},'source_sha256':digest('wrong'),
            'header_sha256':digest('h'),'comparison':bad},
           {'function':fn['name'],'scope':'current','profile':{},'source_sha256':digest('supported'),
            'header_sha256':digest('h'),'comparison':{'support':80}}]
    monkeypatch.setattr(analysis,'listing',lambda *a:{'families':[],'leads':leads})
    monkeypatch.setattr(analysis,'destination',lambda *a:'current')
    assert analysis.rank_pairs(net,proj,fn,pairs,{})==[pairs[2],pairs[1],pairs[0]]
    monkeypatch.setattr(analysis,'destination',lambda *a:'changed')
    assert analysis.rank_pairs(net,proj,fn,pairs,{})==pairs
