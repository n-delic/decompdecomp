import struct

from decompnet.app import reference_context_recovery as recovery
from decompnet.app import scalar_pool_recovery as pools
from decompnet.app import source_preparation as prep


def test_static_helper_keeps_lines_and_replaces_external_prototype():
    reference='\n'*20+'static void helper(int *x){ *x=3; }\nvoid entry(int *x){helper(x);}'
    result=recovery.static_helpers('void entry(int *x){helper(x);}','extern void helper(int *x);',reference)
    assert len(result)==1
    private=result[0][2]
    assert '#line 21 "reference-static.c"' in private
    assert 'static void helper(int *x){ *x=3; }' in private
    assert 'extern void helper' not in private
    assert not recovery.static_helpers('void entry(void){unknown();}','','void unknown(void){}')


def test_boolean_config_is_private_and_only_reveals_real_calls(tmp_path):
    (tmp_path/'a.c').write_text('#define FEATURE 0\nint entry(void){\n#if FEATURE\n return actual();\n#else\n return 0;\n#endif\n}')
    cfg={'root':str(tmp_path)};reader=prep.Reader(cfg)
    result=recovery.configurations(reader,cfg,('dest','entry','a.c'),{'entry':'dest'},'int actual(void);',prep.prepare)
    assert len(result)==1 and 'actual()' in result[0][1]
    assert reader.overrides=={}
    cfg['overrides']='FEATURE=0';reader=prep.Reader(cfg)
    assert not recovery.configurations(reader,cfg,('dest','entry','a.c'),{'entry':'dest'},'',prep.prepare)


def test_callbacks_need_two_independent_exact_agreements_and_target_operand():
    source='void entry(void){request(Ready);}'
    header='#define Ready func_80001000\n'
    actual='#define Ready func_80002000\n'
    result=recovery.binding_variants(source,header,'addiu $a2, %lo(func_80002000)',[(1,actual),(2,actual)])
    assert result[0][2]=='#define Ready func_80002000\n'
    assert not recovery.binding_variants(source,header,'func_80002000',[(1,actual),(1,actual)])
    assert not recovery.binding_variants(source,header,'',[(1,actual),(2,actual)])
    assert not recovery.binding_variants(source,header,'func_80002000',[(1,actual),(2,actual),(3,header)])


def test_scalar_pool_references_proven_rom_values_and_retains_a_pool():
    source='void f(void){use(0.25);use((5*SCALE));}'
    header='#define SCALE 2.0\n'
    asm='ldc1 $f2, %lo(D_80001000)($at)\nldc1 $f4, %lo(D_80001008)($at)'
    memory={0x80001000:struct.pack('>d',0.25),0x80001008:struct.pack('>d',10.0)}
    result=pools.variants(source,header,asm,lambda a,n:memory[a],lambda s:int(s[2:],16))
    assert len(result)==3
    assert 'use(D_80001000);use(D_80001008)' in result[0][1]
    assert any('use(0.25);use(D_80001008)' in r[1] for r in result)
    assert not pools.variants(source,header,'',lambda a,n:b'',lambda s:0)
    assert not pools.variants('void f(void){use(call());}',header,asm,lambda a,n:memory[a],lambda s:int(s[2:],16))


def test_scalar_pool_does_not_overwrite_conflicting_types_or_strings():
    asm='lwc1 $f2, %lo(D_80001000)($at)'
    read=lambda a,n:struct.pack('>f',0.25)
    assert not pools.variants('void f(void){use(0.25);}','extern int D_80001000;',asm,read,lambda s:0)
    assert not pools.variants('void f(void){use("0.25");}','',asm,read,lambda s:0)


def test_actual_pointer_return_and_aggregate_parameter_replace_wrong_abi():
    from decompnet.app.cpu_source_context import variants
    result=variants('void f(void){lookup(v);}','int lookup(int v);','Boss *lookup(Vector v);')
    assert 'Boss *lookup(Vector v);' in result[0][2]


def test_include_prototype_recovers_real_float_abi(tmp_path):
    (tmp_path/'a.c').write_text('#include "sound.h"\nvoid original(void){pitch(1,0.25f);}')
    (tmp_path/'sound.h').write_text('void pitch(int handle,float value);')
    (tmp_path/'old.h').write_text('void pitch(int handle,double value);')
    cfg={'root':str(tmp_path),'header':str(tmp_path/'old.h')}
    pairs=prep.prepare_variants(prep.Reader(cfg),cfg,('dest','original','a.c'),{'original':'dest'},'')
    assert any('void pitch(int handle,float value);' in h and 'double value' not in h for _,h in pairs)


def test_export_recovery_does_not_publish_a_stale_or_failed_candidate(monkeypatch):
    import time
    from contextlib import nullcontext
    from types import SimpleNamespace

    import pytest

    from decompnet.app import export_source_recovery as exports
    from decompnet.network import NetworkError
    from decompnet.rom import export
    fn={'id':1,'name':'f','unit':'','status':'matched','matched_source':'old','asm':''}
    proj={'id':1,'slug':'game','big_endian':True}
    published=[];released=[];state=['stable'];calls=[]
    class Store:
        def one(self,sql,*args):
            if sql.startswith('SELECT *'):return fn
            if 'agent_id' in sql:return {'agent_id':1,'expires_at':time.time()+60}
            return None
        def tx(self):return nullcontext(self)
        def execute(self,*args):calls.append(args)
    class Net:
        store=Store()
        progress=SimpleNamespace(judging=lambda s:nullcontext(),_project_lock=lambda s:nullcontext())
        permuter=SimpleNamespace(_hold=lambda *a,**kw:True,_let_go=lambda *a:released.append(True))
        def is_rom(self,p):return True
        def fork_slug(self,s):return None
        def local_agent(self,n):return SimpleNamespace(id=1),''
        def project_row(self,s):return proj
        def _function(self,*a):return fn
        def _context(self,*a):return {'header':'','version':1}
        def reader(self,*a):return lambda a,n:b''
        def symbols(self,*a):return SimpleNamespace(resolve=lambda s:None)
        def _compile(self,p,h,s,f):return SimpleNamespace(ok=True,obj=s,effective_settings={})
        def _candidate_checks(self,f,p,obj):return {'export':{'ready':obj!='old'}}
        def _evaluate(self,*a):return object(),True,[]
        def _record(self,*a,**kw):
            kw['before_record']();published.append(a[3])
    monkeypatch.setattr(exports,'inputs',lambda *a:state[0])
    monkeypatch.setattr(pools,'variants',lambda *a:[('cpu-rom-scalar-pools','new','')])
    monkeypatch.setattr(export,'_single_definition',lambda *a:True)
    st=SimpleNamespace(stop=SimpleNamespace(is_set=lambda:False),add=lambda **k:None)
    report=exports.run(Net(),proj,['f'],st,lambda s:None)
    assert report=={'tested':1,'export_repaired':1} and published==['new'] and released
    def stale(*a):state[0]='changed';return object(),True,[]
    net=Net();net._evaluate=stale
    with pytest.raises(NetworkError,match='changed'):exports.run(net,proj,['f'],st,lambda s:None)
    assert published==['new']
    state[0]='stable';net=Net();net._evaluate=lambda *a:(None,False,[])
    assert exports.run(net,proj,['f'],st,lambda s:None)['export_repaired']==0
    assert calls and len(released)==3
