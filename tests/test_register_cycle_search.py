
import pytest

from decompnet.app import (
    allocation_evidence,
    allocation_search,
    compiler_diagnostics,
    deep_permuter,
    gcc_allocation,
    search_frontier,
)
from decomprepair import identity_lifetime_repairs
from tests.test_deep_permuter import Log
from tests.test_permute import FakeNet

HEADER = 'typedef unsigned int u32; typedef int s32; typedef signed short s16;'
SOURCE = '''void f(s32 ctx,s32 x,s32 y){u32 z;put(ctx,x,y);
z=(u32)x^(u32)ctx;z^=(u32)ctx;z^=(u32)x;ctx=(u32)ctx+(u32)(s16)y*z;
put(ctx,x,y);put(ctx,x,y);}'''
ROWS = [(' ', 'addiu $sp, $sp, -0x40', 'addiu $sp, $sp, -0x40'),
        ('r','addiu $s4, $s7, -1','addiu $s5, $s7, -1'),
        ('r','sra $s5, $v1, 16','sra $s4, $v1, 16')]


def test_closed_cycles_require_coherent_register_only_evidence():
    result = allocation_evidence.diagnose(ROWS,'mips')
    assert result['register_cycles']==[['$s4','$s5']]
    assert 'closed register cycles $s4 ↔ $s5' in allocation_evidence.text(ROWS,'mips')
    assert allocation_evidence.cycles([('a','b'),('b','c'),('c','a')])==[['a','b','c']]
    for extra in [[('r','move $s4, $s7','move $s2, $s7')], [('o','nop','addiu $v0, $v0, 1')],
                  [('>','','nop')], [('s','addiu $sp, $sp, -64','addiu $sp, $sp, -72')]]:
        assert not allocation_evidence.diagnose(ROWS+extra,'mips')['register_cycles']
    assert not allocation_evidence.cycles([('a','b')])
    assert not allocation_evidence.cycles([('a','b'),('c','b')])


def test_identity_relocations_keep_values_and_do_not_invent_reads():
    candidates = identity_lifetime_repairs.variants(SOURCE,'f',HEADER,'mips')
    assert candidates and len(candidates)<=identity_lifetime_repairs.LIMIT
    assert all(c.count('put(ctx,x,y);')==3 and c.count('ctx=(u32)ctx+')==1 for _,c in candidates)
    assert candidates[0][1].rfind('ctx=(u32)ctx+')>candidates[0][1].rfind('put(ctx,x,y);')
    assert not identity_lifetime_repairs.variants(SOURCE,'f',HEADER,'ppc64')
    assert not identity_lifetime_repairs.variants(SOURCE,'f',HEADER.replace('unsigned int u32','unsigned short u32'),'mips')
    assert not identity_lifetime_repairs.variants(SOURCE,'f',HEADER+'\n#define s16 volatile short','mips')
    for changed in [SOURCE.replace('ctx=(u32)ctx+', 'ctx=(u32)other+'),
                    SOURCE.replace('z^=(u32)x','z^=(u32)y'),
                    SOURCE.replace('u32 z;','volatile u32 z;'),
                    SOURCE.replace('put(ctx,x,y);}', 'escape(&ctx);put(ctx,x,y);}'),
                    SOURCE.replace('put(ctx,x,y);}', 'use(z);put(ctx,x,y);}'),
                    SOURCE.replace('u32 z;','u32 z; s32 x;')]:
        assert not identity_lifetime_repairs.variants(changed,'f',HEADER,'mips')


def test_relocation_requires_dominating_initialization_and_stays_in_branch():
    source = SOURCE.replace('s32 x,s32 y','s32 y').replace('u32 z;','u32 z; s32 x; if(y)x=7;')
    assert not identity_lifetime_repairs.variants(source,'f',HEADER,'mips')
    source = SOURCE.replace('s32 x,s32 y','s32 y').replace('u32 z;','u32 z; s32 x; x=7;')
    assert identity_lifetime_repairs.variants(source,'f',HEADER,'mips')
    conditional = SOURCE.replace('z=(u32)', 'if(y){z=(u32)').replace('put(ctx,x,y);}', 'put(ctx,x,y);}put(ctx,x,y);}')
    assert all(c.rfind('ctx=(u32)ctx+')<c.rfind('}put(ctx,x,y);')
               for _,c in identity_lifetime_repairs.variants(conditional,'f',HEADER,'mips'))


def test_dump_join_uses_actual_order_and_arch_gprs_without_guessing_names(tmp_path):
    from tests.test_rtl_value_trace import RTL
    (tmp_path/'func.c.rtl').write_text(RTL)
    (tmp_path/'func.c.flow').write_text(RTL)
    (tmp_path/'func.c.lreg').write_text(';; Function f\nRegister 72 used 12 times across 62 insns; crosses 3 calls.\n')
    (tmp_path/'func.c.greg').write_text(';; Function f\n;; 1 regs to allocate: 72\n;; Register dispositions:\n72 in 20\n;; Hard regs used: 20\n')
    result = compiler_diagnostics.phase_summary(tmp_path,'f','mips')
    value = result['allocation']['values'][0]
    assert value['aliases'][0]=='s4' and value['references']==12 and value['live_insns']==62
    assert value['allocation_rank']==0 and value['legacy_weight']==36/62
    assert 'pseudo 72 → s4' in gcc_allocation.brief(result['allocation'],[['$s4','$s5']])
    assert result['value_trace']['values'][0]['aliases']==['s4','20']
    assert not compiler_diagnostics.phase_summary(tmp_path,'other','mips')['allocation']['values']


def test_diagnostic_cache_invalidates_source_header_target_and_installation(monkeypatch):
    net = FakeNet();proj=net.project_row('game');fn=net.function();calls=[]
    monkeypatch.setattr(allocation_search.toolchains,'installed',lambda _:True)
    def diagnose(*args,**kwargs):
        calls.append(1);kwargs['on_compile']();kwargs['on_compile']()
        return dict(output_unchanged=True,allocation={'values':[]},value_trace={})
    monkeypatch.setattr(compiler_diagnostics,'diagnose',diagnose)
    spent=[]
    for _ in range(2):allocation_search.capture(net,proj,fn,'source',on_compile=lambda:spent.append(1))
    assert len(calls)==1 and len(spent)==2
    allocation_search.capture(net,proj,fn,'changed',on_compile=lambda:None)
    net._context=lambda *args:dict(header='different',version=2)
    allocation_search.capture(net,proj,fn,'source',on_compile=lambda:None)
    with net.store.tx() as db:db.execute("UPDATE functions SET target=? WHERE id=1",(b'changed',))
    allocation_search.capture(net,proj,net.function(),'source',on_compile=lambda:None)
    monkeypatch.setattr(allocation_search.toolchains,'installed',lambda _:False)
    allocation_search.capture(net,proj,net.function(),'source',on_compile=lambda:None)
    assert len(calls)==5


def test_changed_diagnostic_output_never_supplies_trace(monkeypatch):
    net=FakeNet();monkeypatch.setattr(allocation_search.toolchains,'installed',lambda _:True)
    monkeypatch.setattr(compiler_diagnostics,'diagnose',lambda *a,**k:dict(output_unchanged=False,allocation={'values':['unsafe']}))
    result=allocation_search.capture(net,net.project_row('game'),net.function(),'s',on_compile=lambda:None)
    assert 'allocation' not in result and 'ignored' in result['skipped']


def test_deep_lane_charges_capture_to_existing_limit_and_reuses_cache(monkeypatch):
    net=FakeNet();net.is_rom=lambda _:True
    search=deep_permuter.Search(net,1,20,Log(),net.function())
    monkeypatch.setattr(allocation_search,'saved',lambda *a:None)
    def capture(*args,on_compile,rows=()):
        on_compile();on_compile();return {}
    monkeypatch.setattr(allocation_search,'capture',capture)
    monkeypatch.setattr(allocation_search,'proposals',lambda *a:[('split_integer_phase','focused')])
    choices,lane=search.allocation_choices(net.project_row('game'),net.function(),'source',ROWS,['broad'],{},'',None)
    assert choices==['focused','broad'] and lane=={'focused'}
    assert search.tested==2 and search.allocation_diagnostic_compiles==2
    search.allocation_trials=allocation_search.LIMIT
    assert search.allocation_choices(net.project_row('game'),net.function(),'source',ROWS,['broad'],{},'',None)==(['broad'],set())
    # No capture when fewer than two diagnostic slots plus a judge slot remain.
    search=deep_permuter.Search(net,1,2,Log(),net.function())
    search.allocation_choices(net.project_row('game'),net.function(),'source',ROWS,['broad'],{},'',None)
    assert search.tested==0


def test_frame_recovery_retains_bounded_worse_alternative_without_acceptance():
    before=[('s','addiu $sp, $sp, -0x40','addiu $sp, $sp, -0x48')]+ROWS[1:]
    assert search_frontier.structure_recovery(before,ROWS,'mips')
    frontier=search_frontier.Frontier();frontier.offer('numeric',100,before)
    frontier.offer('correct-frame',150,ROWS,arch='mips',baseline=before,baseline_score=100)
    assert 'correct-frame' in {n['source'] for n in frontier.nodes}
    assert not search_frontier.structure_recovery(ROWS,ROWS+[('>','','nop')],'mips')


def test_relocation_on_host_preserves_function_results(tmp_path):
    import shutil
    import subprocess
    cc=shutil.which('cc')
    if not cc:pytest.skip('host C compiler unavailable')
    source=SOURCE.replace('void f(', 's32 f(').replace('put(ctx,x,y);}', 'put(ctx,x,y);return ctx;}')
    candidates=identity_lifetime_repairs.variants(source,'f',HEADER,'mips')
    assert candidates
    harness=HEADER+'\nvoid put(s32 a,s32 b,s32 c){(void)a;(void)b;(void)c;}\n'
    harness+='\nint main(void){int i;for(i=-32000;i<32000;i+=137)if(f(i,i/2,-i)!=i)return 1;return 0;}'
    for i,candidate in enumerate([source,candidates[0][1]]):
        path=tmp_path/f'c{i}.c';out=tmp_path/f'c{i}';path.write_text(harness.split('\nint main')[0]+candidate+'\nint main'+harness.split('\nint main')[1])
        subprocess.run([cc,'-O2','-Wall','-Werror',str(path),'-o',str(out)],check=True,capture_output=True)
        subprocess.run([str(out)],check=True)


def test_linked_operands_supply_advice_without_changing_judge_verdict(monkeypatch):
    import struct
    from types import SimpleNamespace

    from decompnet.app import candidate_diagnostics
    from decompnet.app.engine.judging import JudgeMixin
    from decompnet.elfobj import FunctionObj

    target_code=struct.pack('>III',0x27bdffc0,0x26f4ffff,0x0003ac03)
    linked_code=struct.pack('>III',0x27bdffc0,0x26f5ffff,0x0003a403)
    net=object.__new__(JudgeMixin)
    net.is_rom=lambda _:True
    net._rom_target=lambda _:SimpleNamespace(code=target_code)
    net.symbols=lambda _:None
    net.reader=lambda *a:None
    net.console=lambda _:SimpleNamespace(cpu_variant='')
    net._target_obj=lambda *a:FunctionObj('f','mips',True,target_code)
    verdict=SimpleNamespace(exact=False,instructions_exact=False,placements={},problems=[],
                            candidate=FunctionObj('f','mips',True,linked_code),linked_code=linked_code)
    monkeypatch.setattr('decompnet.app.engine.judging.match.link',lambda *a:verdict)
    monkeypatch.setattr(candidate_diagnostics,'export_layout',lambda *a:dict(ready=True,state='layout_checked',message='layout checked'))
    fn=dict(id=1,name='f');proj=dict(id=1,arch='mips',big_endian=True)
    _,exact,problems=net._evaluate(fn,proj,b'object')
    assert exact is False and not problems
    checks=net._candidate_checks(fn,proj,b'object')
    assert checks['allocation']['register_cycles']==[['$s4','$s5']]
    assert checks['linked_bytes']['exact'] is False
    assert net._search_rows(fn,proj,b'object',[]) and not net._search_rows(fn,proj,b'different-object',[])
    verdict.problems=['your data for symbol differs']
    net._evaluate(fn,proj,b'data-error')
    assert 'allocation' not in net._candidate_checks(fn,proj,b'data-error')
    assert net._search_rows(fn,proj,b'data-error',['fallback'])==['fallback']


def test_input_change_during_capture_cannot_be_cached_under_new_context(monkeypatch):
    net=FakeNet();monkeypatch.setattr(allocation_search.toolchains,'installed',lambda _:True)
    def diagnose(*a,**k):
        net._context=lambda *a:dict(header='new header',version=2)
        return dict(output_unchanged=True,allocation={'values':[]},value_trace={})
    monkeypatch.setattr(compiler_diagnostics,'diagnose',diagnose)
    result=allocation_search.capture(net,net.project_row('game'),net.function(),'s',on_compile=lambda:None)
    assert 'changed during' in result['skipped']
    assert not net.store.one('SELECT 1 FROM reuse_trials')


def test_identity_placement_never_crosses_declaration_or_unbraced_control():
    source=SOURCE.replace('u32 z;put(ctx,x,y);','put(ctx,x,y);u32 z;')
    for _,candidate in identity_lifetime_repairs.variants(source,'f',HEADER,'mips'):
        assert candidate.index('u32 z;') < candidate.index('z=(u32)')
    source=SOURCE.replace('put(ctx,x,y);put(ctx,x,y);}', 'if(y)put(ctx,x,y);else put(ctx,x,y);}')
    candidates=identity_lifetime_repairs.variants(source,'f',HEADER,'mips')
    assert all('if(y)put(ctx,x,y);else put(ctx,x,y);' in c for _,c in candidates)


def test_malformed_rows_and_missing_baseline_do_not_claim_recovered_structure():
    assert not allocation_evidence.diagnose(ROWS+[('incomplete',)],'mips')['register_cycles']
    assert not allocation_evidence.diagnose(ROWS+[('r',3,4)],'mips')['register_only']
    assert not search_frontier.structure_recovery(None,ROWS,'mips')


def test_read_only_evidence_without_compiler_and_missing_object_is_safe():
    from types import SimpleNamespace

    from decompnet.app.engine.judging import JudgeMixin
    assert allocation_search.saved(SimpleNamespace(cfg=lambda _:{}),{}, {},'source') is None
    net=object.__new__(JudgeMixin)
    assert net._search_rows({'id':1},{},object(),ROWS) is ROWS
