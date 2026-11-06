from types import SimpleNamespace

import pytest

from decompnet.app import draft_repairs, repair_effects, return_contract_evidence
from decompnet.app import float_storage_repairs as storage
from decompnet.app import narrow_expression_repairs as inline
from decompnet.app import return_contract_repairs as returns
from decompnet.arch import mips_returns


def asm(*lines):
    return '\n'.join(f'/* {i*4:06X} {0x80001000+i*4:08X} 00000000 */ {line}' for i,line in enumerate(lines))

TARGET=asm('jal allocate','nop','move $s0, $v0','jal emit','nop','move $v0, $s0',
           'andi $a2, $v0, 31','jr $ra','nop')
CALLER=asm('jal f','nop','move $s1, $v0','jr $ra','nop')
SOURCE='void f(void) { int index; int other; index = allocate(); other = index & 31; emit(other); }\n'
INLINE='''int f(int n) {
 unsigned char b;
 int x;
 b = (n + 2) * 2;
 x = b | 123;
 x |= b << 8;
 return x;
}'''
FLOAT='''void f(int x) {
 volatile float a;
 volatile float b;
 volatile float c;
 a = x * 0.5f;
 c = x * 0.25f;
 b = x;
 emit(a, c);
}'''
DELAY=asm('jal allocate','swc1 $f0, 0x3c($sp)','jr $ra','nop')


def test_word_return_is_target_backed_and_caller_overwrite_is_not_consumption():
    assert mips_returns.result(TARGET,CALLER,'f')['caller_consumes']
    overwritten=asm('jal f','nop','li $v0, 0','move $s1, $v0','jr $ra','nop')
    assert not mips_returns.result(TARGET,overwritten,'f')['caller_consumes']
    assert mips_returns.result(asm('jal g','move $v0, $s0','jr $ra','nop')) is None
    assert mips_returns.result(asm('move $v0, $s0','jal g','nop','jr $ra','nop')) is None
    assert mips_returns.result(asm('move $v0, $s0','jr $ra','li $v0, 0')) is None
    assert mips_returns.result(asm('beqz $a0, .Ldone','nop','move $v0, $s0','jr $ra','nop')) is None


def test_loaded_result_requires_a_real_caller_and_never_enables_unbacked_rewrites():
    target=asm('lw $v0, 0($a0)','lw $t9, 0($v0)','jr $ra','sw $t9, 0($a0)')
    proof=mips_returns.result(target)
    assert proof['requires_caller'] and not proof['caller_consumes']
    assert not returns.variants(SOURCE,'f','','mips',target)
    assert not returns.hint(SOURCE,'f',target,'mips')
    assert 'loaded word' in returns.hint(SOURCE,'f',target,'mips',CALLER)
    overwritten=asm('jal f','nop','li $v0, 0','move $s1, $v0','jr $ra','nop')
    assert not returns.hint(SOURCE,'f',target,'mips',overwritten)
    assert mips_returns.result(target.replace('lw $v0','ld $v0')) is None
    assert mips_returns.result(target.replace('sw $t9, 0($a0)','move $v0, $zero')) is None
    fn=dict(name='f',vram=123);proj=dict(id=1,arch='mips')
    net=SimpleNamespace(xrefs=lambda p:dict(called_by={123:{10}}),
        _asm_text=lambda row,p:target if row is fn else CALLER,
        store=SimpleNamespace(one=lambda *a:dict(name='caller')))
    context=return_contract_evidence.m2c_context(net,proj,fn,'original','')
    assert 'int f();' in context
    assert return_contract_evidence.m2c_context(net,proj,fn,'original','void f(void);')=='original'
    net.xrefs=lambda p:dict(called_by={})
    assert return_contract_evidence.m2c_context(net,proj,fn,'original','')=='original'


def test_return_first_candidate_is_initialized_call_result_and_keeps_newline_inside_body():
    choices=returns.variants(SOURCE,'f','','mips',TARGET)
    assert choices and len(choices)<=returns.LIMIT
    assert 'int f(' in choices[0][1] and 'return index;\n}' in choices[0][1]
    assert returns.variants(SOURCE,'f','','unknown',TARGET)==[]
    assert returns.variants(SOURCE,'f','','mips','')==[]
    assert returns.variants(SOURCE,'f','void f(void);','mips',TARGET)==[]
    assert returns.variants(SOURCE.replace('emit(other);','f();'),'f','','mips',TARGET)==[]
    bad=returns.variants(SOURCE.replace('index = allocate();','index = index + 1;'),'f','','mips',TARGET)
    assert all('return index;' not in text for _,text in bad)
    assert 'caller consumes' in returns.hint(SOURCE,'f',TARGET,'mips',CALLER)


@pytest.mark.parametrize('arch',['mips','ppc','arm','thumb','x86'])
def test_inline_keeps_implicit_byte_conversion_at_both_uses_on_supported_abis(arch):
    choices=inline.variants(INLINE,'f','',arch)
    assert len(choices)==1
    text=choices[0][1]
    assert 'b = ' not in text and text.count('((unsigned char)((n + 2) * 2))')==2
    assert inline.FAMILY in draft_repairs.AGENT_FAMILIES


@pytest.mark.parametrize('change',[
 lambda s:s.replace('(n + 2) * 2','side_effect(n)'),
 lambda s:s.replace('(n + 2) * 2','global_value + 2'),
 lambda s:s.replace('(n + 2) * 2','*(int*)n'),
 lambda s:s.replace('x = b | 123;','n++; x = b | 123;'),
 lambda s:s.replace('x = b | 123;','mutate(&(n)); x = b | 123;'),
 lambda s:s.replace('x = b | 123;','mutate(&(b)); x = b | 123;'),
 lambda s:s.replace('x = b | 123;','if(n) { x = b | 123; }'),
 lambda s:s.replace('int f(int n)','int f(volatile int n)'),
 lambda s:s.replace('unsigned char b;','volatile unsigned char b;'),
])
def test_side_effects_escapes_changed_inputs_and_control_flow_are_not_duplicated(change):
    assert inline.variants(change(INLINE),'f','','mips')==[]


def test_float_hypothesis_is_local_unescaped_and_needs_real_call_delay_evidence():
    choices=storage.variants(FLOAT,'f','','mips',DELAY)
    assert len(choices)==1
    text=choices[0][1]
    assert 'float __repair_float_slots[3];' in text and 'volatile float' not in text
    assert '__repair_float_slots[2] = x * 0.25f' in text
    assert storage.variants(FLOAT,'f','','mips',TARGET)==[]
    assert storage.variants(FLOAT.replace('emit(a, c);','emit(&a, c);'),'f','','mips',DELAY)==[]
    assert storage.variants(FLOAT,'f','','ppc',DELAY)==[]
    mmio=FLOAT.replace('volatile float a;','volatile float *a;')
    assert storage.variants(mmio,'f','','mips',DELAY)==[]


def test_output_stalls_redirect_to_contract_shape_families_without_discarding_work():
    state={'pending':[],'counts':{'scored':4}}
    parent=dict(parent_source='p',parent_output='same',path=['copies'])
    for _ in range(3):repair_effects.observe(state,parent,'same')
    nodes=[dict(parent_source='q',parent_output='same',path=[f]) for f in ['new','return_contract','narrow_expression_inline','other']]
    state['pending']=nodes.copy();repair_effects.rotate(state)
    assert state['pending'][0]['path']==['return_contract']
    assert len(state['pending'])==4
    state['counts']['scored']=7;state['pending']=nodes.copy();repair_effects.rotate(state)
    assert state['pending'][0]==nodes[0]  # ordinary exploration every fourth slot
    repair_effects.observe(state,parent,'changed');state['counts']['scored']=8;state['pending']=nodes.copy();repair_effects.rotate(state)
    assert state['pending']==nodes


def test_m2c_context_hint_is_caller_backed_and_never_changes_shared_prototypes():
    fn=dict(name='f',vram=123);proj=dict(id=1,arch='mips')
    net=SimpleNamespace(xrefs=lambda p:dict(called_by={123:{10}}),
        _asm_text=lambda row,p:TARGET if row is fn else CALLER,
        store=SimpleNamespace(one=lambda *a:dict(name='caller')))
    context=return_contract_evidence.m2c_context(net,proj,fn,'int allocate();','')
    assert 'int f();' in context
    assert return_contract_evidence.m2c_context(net,proj,fn,'original','void f(void);')=='original'
    net._asm_text=lambda row,p:TARGET
    assert return_contract_evidence.m2c_context(net,proj,fn,'original','')=='original'


def test_new_families_reach_the_shared_generator_and_rank_before_register_permutations():
    from decompnet.app import repair_ranking
    labels={}
    draft_repairs.variants(SOURCE,'f','mips',TARGET,labels=labels,limit=None)
    assert returns.FAMILY in labels.values()
    labels={}
    draft_repairs.variants(INLINE,'f','ppc','',labels=labels,limit=None)
    assert inline.FAMILY in labels.values()
    families=['declaration_order',returns.FAMILY,inline.FAMILY,'plain_float_slots']
    ordered=repair_ranking.order(families,[('r','move $v0, $s0','move $v0, $s1')])
    assert ordered.index(returns.FAMILY)<ordered.index('declaration_order')


def test_return_contract_does_not_cross_uninitialized_paths_or_existing_returns():
    assert not returns.variants(SOURCE.replace('index = allocate();','if (1) index = allocate();'),'f','','mips',TARGET)
    assert not returns.variants(SOURCE.replace('emit(other);','emit(other); return;'),'f','','mips',TARGET)
    assert not returns.variants(SOURCE,'f','int f(void);','mips',TARGET)
    assert mips_returns.result(None) is None
    assert mips_returns.result(asm('move $v0, $s0','syscall','jr $ra','nop')) is None


def test_unknown_and_64_bit_abis_do_not_inherit_host_width_for_narrow_inlining():
    assert not inline.variants(INLINE,'f','','x86_64')
    assert not inline.variants(INLINE,'f','','unknown')


def test_third_direct_probe_survives_strategy_redirection():
    state={'pending':[],'counts':{'scored':4}}
    ordinary=dict(parent_source='original',parent_output='same',path=['flat'])
    for _ in range(2):repair_effects.observe(state,ordinary,'same')
    repair_effects.observe(state,dict(parent_source='another',parent_output='same',path=['other']),'same')
    alternative=dict(parent_source='original',parent_output='same',path=['return_contract'])
    state['pending']=[ordinary,alternative]
    repair_effects.rotate(state)
    assert state['pending'][0] is ordinary


def test_real_m2c_accepts_caller_result_context_without_touching_shared_header():
    from decompnet import seeds, toolchains
    if not toolchains.installed('m2c'):pytest.skip('Needs installed pinned m2c')
    header='int allocate(void); void emit(void);'
    source=seeds.generate_from_asm('glabel f\n'+TARGET+'\nendlabel f\n','f','gcc-2.7.2-kmc',header+'\nint f();')
    assert source and 'return ' in source and 'void f(' not in source
    assert header=='int allocate(void); void emit(void);'


def test_new_rule_modules_invalidate_paused_candidates():
    from decompnet.claim_stalls import repair_rules
    names={name for name,_ in repair_rules()}
    assert {'return_contract_repairs.py','return_contract_evidence.py','float_storage_repairs.py','narrow_expression_repairs.py'}<=names


def test_opaque_macros_cannot_hide_operand_mutation_or_local_escapes():
    text=INLINE.replace('x = b | 123;','CHANGE; x = b | 123;')
    assert not inline.variants(text,'f','#define CHANGE n++','mips')
    text=FLOAT.replace('emit(a, c);','ESCAPE(a); emit(a,c);')
    assert not storage.variants(text,'f','#define ESCAPE(x) expose(&(x))','mips',DELAY)
    text=SOURCE.replace('emit(other);','EXIT; emit(other);')
    assert not returns.variants(text,'f','#define EXIT if (flag) return','mips',TARGET)


def test_return_conflict_is_visible_in_editor_diagnostics_without_a_source_mutation():
    from decompnet.app.source_diagnostics import hints
    result=hints(SOURCE,'f',TARGET,'mips',[('r','move $v0, $s0','move $v0, $s1')])
    assert any(s.startswith('Return-contract hypothesis:') for s in result)


@pytest.mark.parametrize('imported',[False,True])
def test_deep_pipeline_connects_storage_return_and_expression_repairs(monkeypatch,imported):
    from decompnet.app import deep_permuter
    from decompnet.app.permute import Permuter
    from tests.test_deep_permuter import Log, setup
    from tests.test_permute import AGENT
    generate=draft_repairs.variants
    net=setup(monkeypatch,imported)
    monkeypatch.setattr(draft_repairs,'variants',generate)
    source='''void f(int n) {
 volatile float a;
 volatile float b;
 volatile float c;
 int index;
 int word;
 unsigned char byte;
 a=n*0.5f; c=n*0.25f; b=n;
 index=allocate(); emit(a,c);
 word=index&31;
 byte=(word+2)*2;
 store(byte);
 store(byte<<8);
}'''
    target=TARGET.replace('nop\n/* 000008','swc1 $f0, 0x3c($sp)\n/* 000008',1)
    with net.store.tx() as db:db.execute('UPDATE functions SET best_source=?,asm=?,best_score=40',(source,target))
    monkeypatch.setattr(deep_permuter.Search,'_repair_preflight',lambda self,a,p,f,b,k:b)
    tested=[]
    def judge(self,agent,proj,fn,text,best,fork):
        tested.append(text)
        flags=('volatile float' not in text,'return index;' in text,'byte=' not in text)
        score=40-10*sum(flags) if not all(flags) else 0
        self._trial_output=text;self._trial_output_granularity='function'
        if score<net.function()['best_score']:
            with net.store.tx() as db:
                db.execute('UPDATE functions SET best_source=?,best_score=?,status=?',
                           (text,score,'matched' if score==0 else 'open'))
        return score
    monkeypatch.setattr(Permuter,'_score',judge)
    search=deep_permuter.Search(net,1,40,Log())
    best,_=search.local_repairs(AGENT,net.project_row('game'),net.function(),40,'fork' if imported else None,max_trials=16)
    assert best==0 and len(tested)<=16
    assert '((unsigned char)((word+2)*2))' in net.function()['best_source']
