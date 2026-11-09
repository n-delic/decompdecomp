
import pytest

from decompnet.app import source_context
from decompnet.app import source_preparation as prep
from decompnet.forks import csource
from decompnet.network import NetworkError


def test_definition_not_a_for_increment_or_nested_call():
    text='void run(void) { for (i=0; i<4; i=next(i)) { work(); } }\nstatic int next(int x) { return x+1; }\n'
    assert csource.get(text,'next')=='static int next(int x) { return x+1; }\n'
    assert csource.find(text.split('static')[0],'next') is None
    assert csource.find('void f(void){ if (g()) { work(); } }','g') is None


def test_prototypes_preserve_float_abi_and_no_guesses():
    original='static float mod(int x) { return x; }\nvoid f(void) { mod(1); absent(2); }'
    result=source_context.prototypes(original,csource.get(original,'f'),'','f')
    assert 'extern float mod(int x);' in result and 'absent' not in result
    assert not source_context.prototypes(original,csource.get(original,'f'),'float mod(int);','f')


def test_context_advisories_do_not_invent_macros_or_layouts():
    hints=source_context.context_hints('void f(void){ SWAP(a,b); }','typedef int Item;\ntypedef float Item;')
    assert any('Conflicting typedef Item' in h for h in hints)
    assert any('SWAP' in h for h in hints)
    assert not source_context.context_hints('void f(void){ SWAP(a,b); }','#define SWAP(a,b) swap(a,b)')
    assert 'prototypes' in source_context.compiler_hints('warning: implicit declaration of function thing')[0]


def test_paired_variants_and_exact_object_constants(tmp_path):
    (tmp_path/'a.c').write_text('int old(void){return 1;}')
    (tmp_path/'a.h').write_text('typedef int Old;')
    (tmp_path/'b.c').write_text('int newer(void){return 2;}')
    (tmp_path/'b.h').write_text('typedef float New;')
    cfg={'root':str(tmp_path),'header':str(tmp_path/'a.h'), 'contexts':'dest = newer @ b.c | b.h'}
    variants=prep.prepare_variants(prep.Reader(cfg),cfg,('dest','old','a.c'),{'old':'dest','newer':'dest'},'')
    assert len(variants)==2 and 'return 2' in variants[1][0] and 'typedef float New' in variants[1][1]
    assert 'Old' not in variants[1][1]
    from decompnet.app.source_definitions import extract
    original='static union Bits { unsigned words[2]; double value; } coeff = {{0x3ff00000, 0x00000001}};'
    assert '{{0x3ff00000, 0x00000001}}' in extract(original,'coeff')
    assert prep.contexts('dest = newer @ b.c | b.h')[0]==('dest','newer','b.c','b.h')
    with pytest.raises(NetworkError):prep.contexts('dest = newer @ b.c')


def test_explicit_static_helpers_keep_source_and_reject_nonstatic(tmp_path):
    (tmp_path/'helpers.c').write_text('static float helper(int x)\n{ return x * 0.25f; }\nfloat other(void){ return 1; }')
    cfg={'root':str(tmp_path),'helpers':'helper @ helpers.c'}
    result=source_context.helpers(prep.Reader(cfg),cfg,'float f(void){return helper(2);}','',{})
    assert 'static float helper(int x)' in result and 'x * 0.25f' in result and 'other' not in result
    cfg['helpers']='other @ helpers.c'
    with pytest.raises(NetworkError):source_context.helpers(prep.Reader(cfg),cfg,'void f(void){other();}','',{})


def test_stale_trial_retried_once_but_claim_loss_is_not(tmp_path,monkeypatch):
    import json

    from decompnet.app.permute import Permuter, State
    from decompnet.network import Agent
    from tests.test_source_preparation import config, prepared_net
    net,proj,fn=prepared_net();net.cfg=lambda p:json.loads(p['config'])
    net.project_row=lambda s:net.store.one('SELECT * FROM projects WHERE id=1')
    net.fork_slug=lambda s:None;net.links={};net.local_agent=lambda n:(Agent(1,'autopilot',None),'')
    cfg=config(tmp_path);(tmp_path/'types.h').write_text('');(tmp_path/'game.c').write_text('int original(void){return 1;}')
    prep.configure(net,proj,cfg)
    with net.store.tx() as db:db.execute('DELETE FROM claims')
    calls=[]
    def attempt(*args):
        calls.append(True)
        raise NetworkError('The function, header or build inputs changed during preparation. Run the pass again.')
    monkeypatch.setattr(prep,'attempt',attempt)
    report=prep.run(Permuter(net),proj,State(),budget=4)
    assert len(calls)==2 and report['tested']==2 and len(report['errors'])==1
    assert not net.store.one('SELECT 1 FROM reuse_trials')
    assert not net.store.one('SELECT 1 FROM claims')
    calls.clear()
    def lost(*args):
        calls.append(True);raise NetworkError('The function claim changed during preparation. Run the pass again.')
    monkeypatch.setattr(prep,'attempt',lost)
    prep.run(Permuter(net),proj,State(),budget=4)
    assert len(calls)==1


def test_helpers_do_not_conflict_with_automatic_externs(tmp_path):
    (tmp_path/'f.c').write_text('static float helper(int x){return x;}\nfloat original(void){return helper(1);}')
    cfg={'root':str(tmp_path),'helpers':'helper @ f.c'}
    source,header=prep.prepare(prep.Reader(cfg),cfg,('dest','original','f.c'),{'original':'dest'},'')
    assert 'static float helper' in header and 'extern float helper' not in header
    assert 'float dest(void)' in source


def test_record_revision_differences_are_advisory_not_guessed_fields():
    original='typedef struct { int x; int (*callback)(int); } Item;'
    header='typedef struct { int x; } Item;'
    hints=source_context.compatibility(original,'void f(Item *p){p->x=0;}',header)
    assert len(hints)==1 and 'declare Item differently' in hints[0] and 'no fields were changed' in hints[0]
    assert not source_context.compatibility(original,'void f(void){}',header)
    assert not source_context.compatibility(original,'void f(Item *p){}',original)


def test_selected_helper_constants_are_kept_verbatim(tmp_path):
    (tmp_path/'f.c').write_text('static unsigned coeff = 0x3f800001;\nstatic unsigned helper(void){return coeff;}\nunsigned original(void){return helper();}')
    cfg={'root':str(tmp_path),'helpers':'helper @ f.c','definitions':'coeff @ f.c'}
    source,header=prep.prepare(prep.Reader(cfg),cfg,('dest','original','f.c'),{'original':'dest'},'extern unsigned coeff;')
    assert 'coeff = 0x3f800001;' in source and 'return coeff;' in header


def test_parenthesized_switch_case_is_not_a_private_helper_prototype():
    from decompnet.app.source_context import prototypes
    source='int f(int x){switch(x){case (3): if(x){return 1;} default: return 0;}}'
    assert prototypes(source,source,'','f') == ''
