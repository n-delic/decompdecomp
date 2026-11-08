import hashlib
import shutil
import subprocess
import time

import pytest

from decompnet.app import compiler_phase_routing as phases
from decompnet.app import dispatch_specialization as dispatch
from decompnet.app import layout_frontiers as layouts
from decompnet.app import repair_benchmark, repair_localisation, targeted_debug_layout

HEADER = 'typedef unsigned char u8; extern int result; int lookup(int); void callback(void);'
SOURCE = '''void f(void) {
    u8 i;
    int which;
    for (i=0; i<5; i++) {
        switch (i) { case 0: which=2; break; case 1: which=2; break; default: which=1; break; }
        result=lookup(which);
        callback();
    }
}'''


def test_effectful_dispatch_preserves_mapping_and_generates_independent_formals():
    choices=dispatch.variants(SOURCE,'f',HEADER,'mips')
    assert len(choices)==2
    candidate=choices[0][1]
    assert 'int map0, int last, int other' in candidate
    assert 'idx == 4' in candidate and 'lookup(last)' in candidate
    assert 'lookup(other)' in candidate and '(i, 2, 1, 1);' in candidate
    assert candidate.index('(i, 2, 1, 1);')<candidate.index('callback();')
    assert not dispatch.variants(candidate,'f',HEADER,'mips')
    from decompnet.app import draft_repairs
    labels={}
    draft_repairs.variants(SOURCE,'f','mips',header=HEADER,labels=labels,limit=None)
    assert dispatch.FAMILY in labels.values()


@pytest.mark.parametrize('source,header',[
    (SOURCE.replace('callback();','if (which) callback();'),HEADER),
    (SOURCE.replace('int which;', 'volatile int which;'),HEADER),
    (SOURCE.replace('int which;', 'int which; int *p=&which;'),HEADER),
    (SOURCE.replace('result=lookup(which);','callback(); result=lookup(which);'),HEADER),
    (SOURCE.replace('default: which=1; break;', ''),HEADER),
    (SOURCE.replace('which=2;', 'which=lookup(2);',1),HEADER),
    (SOURCE,HEADER+'\n#define lookup(x) other(x)'),
    (SOURCE,HEADER.replace('int lookup(int);','int lookup();')),
    (SOURCE,HEADER.replace('extern int result;', 'extern volatile int result;')),
    (SOURCE.replace('void f(void)','void f(int result)'),HEADER),
    (SOURCE.replace('u8 i;', 'u8 i; int result;'),HEADER),
    (SOURCE+'\nint line=__LINE__;',HEADER),
    (SOURCE.replace('callback();','HIDDEN_READ();'),HEADER+'\n#define HIDDEN_READ() result=which'),
    (SOURCE,HEADER+'\n#define map0 hidden'),
    (SOURCE.replace('result','idx'),HEADER.replace('result','idx')),
    (SOURCE.replace('u8 i;', 'u8 i; int (*lookup)(int);'),HEADER),
])
def test_dispatch_declines_live_selectors_effects_macros_and_shadowing(source,header):
    assert not dispatch.variants(source,'f',header,'mips')


def test_cfg_allows_overwritten_selector_but_rejects_a_bypass():
    assert dispatch.variants(SOURCE.replace('callback();','which=9; result=which; callback();'),'f',HEADER,'mips')
    assert not dispatch.variants(SOURCE.replace('callback();','if (i) which=9; result=which; callback();'),'f',HEADER,'mips')
    assert dispatch.variants(SOURCE.replace('int which;', 'int which, unused;'),'f',HEADER,'mips')
    assert not dispatch.variants(SOURCE,'f',HEADER,'unknown')


def test_host_execution_checks_every_u8_mapping_and_call_store_order(tmp_path):
    cc=shutil.which('cc')
    if not cc:pytest.skip('needs host C compiler')
    candidate=dispatch.variants(SOURCE,'f',HEADER,'mips')[0][1]
    helper='__repair_aggregate_call_f_dispatch'
    # Homemade control: lookup observes the preceding global, callback sees its new value.
    control='''
int result, calls, callbacks, error;
int lookup(int v) { calls++; return v+10; }
void callback(void) { callbacks++; if (result != (callbacks<=2 ? 12 : 11)) error=1; }
int main(void) {
    int k;
    f();
    if (calls!=5 || callbacks!=5 || error) return 1;
    for(k=0;k<256;k++) {
        HELPER((u8)k,2,1,1);
        if(result!=((k==0 || k==1) ? 12 : 11)) return 2;
        HELPER((u8)k,7,8,9);
        if(result!=(k<2 ? 17 : k==4 ? 18 : 19)) return 3;
    }
    return 0;
}'''.replace('HELPER',helper)
    path=tmp_path/'control.c';path.write_text(HEADER+'\n'+candidate+'\n'+control)
    binary=tmp_path/'control'
    subprocess.run([cc,'-std=gnu89','-O2',str(path),'-o',str(binary)],check=True,capture_output=True)
    subprocess.run([str(binary)],check=True)


def test_layout_projection_changes_only_whitespace_and_preserves_comments_directives():
    source='#define BASE 4\nint f(int a) {\n int x;\n x=a;\n /* keep */ return x+BASE;\n}\n'
    donor='#define BASE 4\nint f(int a) { int x; x=a; /* keep */ return x+BASE; }\n'
    projected=layouts.project(source,donor)
    assert projected and layouts.equivalent(source,projected)
    assert projected.startswith('#define BASE 4\n') and '/* keep */' in projected
    assert not layouts.project(source+'int n=__LINE__;',donor)
    assert not layouts.project(source,donor,'#define where __LINE__')
    assert not layouts.project(source,'void unrelated(void) {}')


def test_structural_edit_replays_on_a_worse_layout_in_the_ordinary_beam():
    donor=SOURCE.replace('        callback();','        callback();\n')
    calls=[]
    def evaluate(source):
        calls.append(source)
        repaired=dispatch.PREFIX in source
        exact=repaired and 'callback();\n\n' in source
        return dict(score=0 if exact else 440 if repaired else 630,matched=exact,
                    outcome='exact' if exact else 'scored',rows=[])
    result=repair_benchmark.run(SOURCE,630,evaluate,lambda source:dispatch.variants(source,'f',HEADER,'mips'),
        trials=6,deadline=time.monotonic()+10,header=HEADER,layouts=[donor])
    assert result['first_exact_trial']==2
    assert result['winning_path']==[dispatch.FAMILY,layouts.FAMILY]
    assert len(calls)==3  # baseline plus two ordinary judged candidates


def test_layout_donors_survive_many_unrelated_trials_and_reservoir_is_bounded():
    state={}
    layouts.offer(state,SOURCE)
    for n in range(30):layouts.offer(state,SOURCE+'\n'*n)
    assert SOURCE in state['layout_frontiers'] and len(state['layout_frontiers'])==8


def test_targeted_debug_lines_require_debug_nop_and_current_source_mapping():
    source='int f(int a) {\n int b;\n b=a;\n return b;\n}\n'
    report={'rows':[('>','','nop')],'repair_locations':repair_localisation.bind(source,{'available':True,'lines':[{'line':3}]})}
    choices=targeted_debug_layout.variants(source,'f','','mips',['-g3'],report)
    assert choices and all(layouts.equivalent(source,text) for _,text in choices)
    assert not targeted_debug_layout.variants(source,'f','','mips',[],report)
    assert not targeted_debug_layout.variants(source,'f','','mips',['-g3'],{'rows':report['rows']})
    assert not targeted_debug_layout.variants(source+'\n','f','','mips',['-g3'],report)


def test_early_cse_evidence_routes_but_does_not_remove_candidates():
    summary=phases.constants({'rtl':';; Function f\n(insn (const_int 4))\n','cse':';; Function f\n(insn (const_int 5))\n'},'f')
    assert summary['removed_before_allocation']==[4]
    source='int f(void) { return 4; }'
    trace={'source_sha256':hashlib.sha256(source.encode()).hexdigest(),'header_sha256':hashlib.sha256(b'').hexdigest(),'early_constants':summary}
    proposals=[('constant',source.replace('4','5')),('other',source+'\n'),(dispatch.FAMILY,source+' ')]
    ordered=phases.order(source,proposals,trace)
    assert ordered[0][0]==dispatch.FAMILY and ordered[-1][0]=='constant'
    assert set(ordered)==set(proposals)
    assert phases.order(source+' ',proposals,trace)==proposals
    assert phases.order(source,proposals,{**trace,'truncated':True})==proposals
    assert not phases.constants({'rtl':';; Function f\n(const_int 4)'},'f')


def test_exact_recheck_is_distinct_from_new_credit_and_adoption(tmp_path,monkeypatch):
    from decompnet.compiler import CompileResult
    from decompnet.elfobj import FunctionObj
    from decompnet.network import Network
    net=Network(tmp_path/'test.db')
    target=FunctionObj('f','mips',True,bytes.fromhex('03e0000800000000'),[])
    with net.store.tx() as db:
        db.execute("INSERT INTO projects(id,slug,name,arch,root,config) VALUES(1,'game','Game','mips','.', '{}')")
        db.execute("INSERT INTO context_versions(project_id,version,header,message,created_at,unit) VALUES(1,1,'','initial',0,'')")
        db.execute("INSERT INTO functions(id,project_id,name,size,n_insns,target,relocs,unit) VALUES(1,1,'f',8,2,?,'[]','')",(target.code,))
    agent=net.authenticate(net.register_agent('alice','test'))
    net._attempt_compilation=lambda *a:CompileResult(True,b'object','')
    verdict=[True]
    net._evaluate=lambda *a:(target,verdict[0],[])
    net._target_obj=lambda *a:target
    net._candidate_checks=lambda *a:{'linked_bytes':{'exact':verdict[0]}}
    monkeypatch.setattr('decompnet.source_lines.mismatches',lambda *a,**kw:{'available':False})
    source='void f(void) {}'
    initial=net.attempt(agent,'f',source,'game')
    assert initial['exact'] and initial['new_match'] and initial['adopted'] and initial['matched']
    repeated=net.attempt(agent,'f',source,'game')
    assert repeated['exact'] and not repeated['new_match'] and not repeated['adopted'] and not repeated['matched']
    assert 'EXACT recheck' in repeated['text']
    verdict[0]=False  # even identical printed rows cannot override a failed link/data verdict
    failed=net.attempt(agent,'f',source+' ','game')
    assert not failed['exact'] and failed['score']>0 and not failed['adopted']
    selected=net.store.one('SELECT status,best_source FROM functions WHERE id=1')
    assert selected['status']=='matched' and selected['best_source']==source


def test_frozen_dispatch_and_layout_rediscovery_with_original_linker():
    """Private ROM/context inputs are optional and never copied into the repository."""
    import base64
    import json
    import os
    from pathlib import Path

    from decompnet import consoles, scoring, toolchains
    from decompnet.compiler import compile_source
    from decompnet.project import compiler_config
    from decompnet.rom import match
    from decompnet.rom.symbols import Symbol, SymbolTable
    case=os.environ.get('DECOMPNET_DISPATCH_CASE')
    if not case:pytest.skip('needs private frozen dispatch case')
    root=Path(case);state=json.loads((root/'inputs.json').read_text())
    profile=state['profile']
    if not toolchains.installed(profile['toolchain']):pytest.skip('needs installed pinned case compiler')
    fn=state['function'];fn['target']=base64.b64decode(fn['target']['base64'])
    cfg=json.loads(state['project']['config']);header=state['header']
    symbols=SymbolTable([Symbol(r['name'],r['address'],r['size'],r['kind']) for r in state['symbols']])
    console=consoles.of(cfg);target=console.target_from_row(fn,cfg);target_obj=console.target_object(fn,cfg,symbols)
    image=(root/'rom.z64').read_bytes()
    assert hashlib.sha256(image).hexdigest()==state['rom_sha256']
    cc=compiler_config(root,{'compiler':profile})
    def read(address,size):
        for segment in cfg['segments']:
            if segment['vram']<=address and address+size<=segment['vram']+segment['rom_end']-segment['rom_start']:
                start=segment['rom_start']+address-segment['vram'];return image[start:start+size]
        return None
    exact=[]
    def evaluate(source):
        built=compile_source(cc,header,source)
        assert built.ok,built.log
        linked=match.link(built.obj,fn['name'],target,symbols,read)
        assert linked.candidate is not None and not linked.problems
        if linked.exact:
            from decompnet.object_emission import inspect
            emission=inspect(built.obj,fn['name'])
            assert emission['function_count']==1 and not emission['extra_function_count'] and emission['allocated_data_bytes']==0
            assert linked.linked_code==target.code
            exact.append(source)
        return dict(score=0 if linked.exact else max(1,scoring.diff(target_obj,linked.candidate).score),
                    matched=linked.exact,outcome='exact' if linked.exact else 'scored',rows=scoring.diff(target_obj,linked.candidate).rows)
    baseline=(root/'baseline.c').read_text();donor=(root/'layout.c').read_text()
    from decompnet.app import iterative_repairs
    selection={};pool={}
    def generate(source,report):
        return iterative_repairs.targeted_variants(source,report,fn,console.arch,profile['flags'],header,
            tagged=True,selection=selection,pool_cache=pool,limit=8)
    report=repair_benchmark.run(baseline,state['baseline_score'],evaluate,generate,
        trials=6,deadline=time.monotonic()+60,header=header,layouts=[donor],generate_report=True,arch=console.arch)
    assert report['first_exact_trial']==2 and report['winning_path']==[dispatch.FAMILY,layouts.FAMILY]
    assert exact
