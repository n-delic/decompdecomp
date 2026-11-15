from types import SimpleNamespace

import pytest

from decompnet.app import helper_expansion, prepared_evidence, source_preparation, switch_tables
from decompnet.app.source_storage import typed_object_hints
from decompnet.network import NetworkError
from tests.test_source_preparation import config


def test_preserve_macros_from_authorized_include_tree_with_explicit_context(tmp_path):
    cfg=config(tmp_path)
    (tmp_path/'types.h').write_text('typedef struct Set Set;\nvoid *BLOCK(Set*,int);\n')
    (tmp_path/'sdk.h').write_text('#define BLOCK(p,n) access((p),(p)->data,(n))\n#define MATRIX(p,m) convert((p),&(m))\n')
    (tmp_path/'game.c').write_text('#include "sdk.h"\nvoid original(Set*p){BLOCK(p,2);MATRIX(p,p->matrix);}\n')
    source,header=source_preparation.prepare(source_preparation.Reader(cfg),cfg,('f','original','game.c'),{'original':'f'},'')
    assert 'BLOCK(p,2)' in source and '#define BLOCK(p,n) access((p),(p)->data,(n))' in header
    assert '#define MATRIX(p,m) convert((p),&(m))' in header
    (tmp_path/'types.h').write_text('#define BLOCK(p,n) wrong((p),(n))\n')
    with pytest.raises(NetworkError,match='Conflicting reference macro'):
        source_preparation.prepare(source_preparation.Reader(cfg),cfg,('f','original','game.c'),{'original':'f'},'')


def test_small_initializer_expansion_is_bounded_and_rejects_repeated_side_effects():
    header='static void setup(Item *p){p->head=0; p->node.kind=2; p->owner=&owner;}'
    source='void f(Item *x){setup(x);}'
    variants=helper_expansion.variants(source,'f',header)
    assert len(variants)==1 and '(x)->node.kind=2' in variants[0][1]
    assert not helper_expansion.variants(source.replace('setup(x)','setup(x++)'),'f',header)
    assert not helper_expansion.variants(source,'f',header.replace('p->head=0','p->head=read()'))
    assert not helper_expansion.variants(source,'f',header.replace('p->head=0','p=other'))
    assert not helper_expansion.variants(source.replace('Item *x','volatile Item *x'),'f',header)
    assert helper_expansion.variants(source,'f','#define KIND 2\n'+header.replace('kind=2','kind=KIND'))
    assert not helper_expansion.variants(source,'f','#define KIND read()\n'+header.replace('kind=2','kind=KIND'))


def test_folded_object_base_diagnostic_requires_byte_access_and_changed_bases():
    rows=[['i','lui $a2, %hi(D_80100000)','lui $a2, %hi(D_80108000)']]
    assert typed_object_hints(rows,'x=*(int*)((char*)&app+0x8000);','')
    assert not typed_object_hints(rows,'x=app.mode;','')
    assert not typed_object_hints([[' ','lui $a2, %hi(D_80100000)','lui $a2, %hi(D_80100000)']],'char*p;','')


def test_retail_table_advisory_names_missing_nonmodal_arm_without_patching():
    source='int f(int x){switch(x){case A:return 1; case B:return 2; default:return 0;}}'
    hints=switch_tables.hints(source,'f',[(301,[100,200,200,200,300])],'#define A 301\n#define B 302\n')
    assert len(hints)==1 and '305' in hints[0] and 'No case body' in hints[0]
    assert not switch_tables.hints(source,'f',[(301,[100,200])],'#define A 301\n#define B 302\n')
    assert not switch_tables.hints(source,'f',[(301,[100,200,200,200,300])],'')
    grouped=source.replace('case A:return 1; case B:', 'case A: case B:')
    assert any('grouped source labels' in x for x in switch_tables.hints(grouped,'f',[(301,[100,200,200,200,300])],'#define A 301\n#define B 302\n'))


def test_batch_recheck_deduplicates_and_continues_after_claim_failure(monkeypatch):
    items=[{'id':str(i),'function':n,'reviewable':True} for i,n in enumerate(['a','a','b','c'])]
    monkeypatch.setattr(prepared_evidence,'listing',lambda *a:items)
    visited=[]
    def apply(net,proj,key,log,refresh=False):
        assert refresh
        visited.append(key)
        if key=='2':raise NetworkError('Function is busy.')
        return {'matched':True}
    monkeypatch.setattr(prepared_evidence,'apply',apply)
    net=SimpleNamespace(project_row=lambda slug:{'slug':slug})
    result=prepared_evidence.recheck_exact(net,{'slug':'demo'},lambda text:None)
    assert visited==['0','2','3'] and result['matched']==2 and len(result['errors'])==1
    with pytest.raises(NetworkError):prepared_evidence.recheck_exact(net,{'slug':'demo'},print,21)


def test_explicit_private_header_still_works_with_unavailable_original_sdk(tmp_path):
    cfg=config(tmp_path)
    (tmp_path/'types.h').write_text('int helper(int);')
    (tmp_path/'game.c').write_text('#include "missing-sdk.h"\nint original(int n){return helper(n);}')
    reader=source_preparation.Reader(cfg)
    source,header=source_preparation.prepare(reader,cfg,('f','original','game.c'),{'original':'f'},'')
    assert 'helper(n)' in source and 'int helper(int)' in header
    assert any('Reference include macros could not be read' in x for x in reader.advisories)
