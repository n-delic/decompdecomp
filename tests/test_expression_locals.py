"""Synthetic C for bounded expression, buffer and induction hypotheses."""
import json

import pytest

from decompnet import m2cfix
from decompnet.app import agent_tokens, draft_repairs, expression_locals, loop_repairs, pattern_repairs, tools
from tests.test_claims import _game


def test_address_temporaries_handle_macros_and_plain_loads():
    source='void f(){s32 p;use(M2C_FIELD((p + M2C_FIELD(obj,s32*,0)*2),s16*,12));} void other(){}'
    out=[s for f,s in expression_locals.variants(source,'f') if f=='address_temporary']
    assert any('char *__repair_addr;' in s and 'M2C_FIELD(__repair_addr,s16*,12)' in s for s in out)
    assert any('M2C_FIELD((__repair_addr =' in s for s in out)
    assert all(s.endswith('void other(){}') for s in out)
    assert any(f=='address_temporary' for f,_ in expression_locals.variants('void f(){use(*(s16 *)(base + index*2));}','f'))
    for source in ('void f(){use(M2C_FIELD(next()+i,s16*,0));}',
                   'void f(){while(M2C_FIELD(p+i,s16*,0))work();}',
                   'void f(){volatile int p; use(M2C_FIELD(p+i,s16*,0));}'):
        assert not any(f=='address_temporary' for f,_ in expression_locals.variants(source,'f'))


def test_literals_are_values_not_offsets_masks_or_directives():
    source='int f(int x){return x;} void other(){if(x>0)run();}'
    assert not expression_locals.variants(source,'f')
    source='void f(){if(x>0){use(x*2);}}'
    out=[s for f,s in expression_locals.variants(source,'f') if f=='constant_local']
    assert any('x>__repair_literal' in s for s in out)
    assert any('x*__repair_literal' in s for s in out)
    assert not expression_locals.variants('void f(){use(M2C_FIELD(p,u8*,32));}','f')
    assert not expression_locals.variants('void f(){\n#define DO x*2\nuse(DO);}','f')


def test_float_load_variants_preserve_comparison_and_offer_both_load_orders():
    source='int f(){if(M2C_FIELD(*b,f32*,16)<M2C_FIELD(*a,f32*,16)){return -1;}return 1;}'
    out=[s for f,s in expression_locals.variants(source,'f') if f=='comparison_load']
    assert len(out)==4
    assert all('__repair_cmp_l <' in s and '>' not in s for s in out)
    assert any(s.index('__repair_cmp_r =') < s.index('__repair_cmp_l =') for s in out)
    assert any('__repair_cmp_c = __repair_cmp_r;' in s for s in out)
    assert not any(f=='comparison_load' for f,_ in expression_locals.variants(source.replace('f32*,16)<','f64*,16)<'),'f'))


def test_stack_array_uses_explicit_bound_and_fixes_address_scaling():
    source='void f(){u8 buf;s32 i;u8 *p;i=0;p=&buf;do{i+=1;*p=read();p=&(&buf)[i];}while(i<32);send(&buf);} void other(){}'
    out=m2cfix.stack_array(source,'f')
    assert 'u8 buf[32];' in out and 'p=buf;' in out and 'p=&buf[i];' in out and 'send(buf)' in out
    assert out.endswith('void other(){}')
    for broken in (source.replace('i<32','i<n'),source.replace('i=0','i=1'),source.replace('send(&buf)','send(buf)'),source.replace('u8 buf','volatile u8 buf')):
        assert m2cfix.stack_array(broken,'f') is None


def test_pointer_loop_can_use_index_and_reverse_without_changing_guard():
    source='void f(f32 *base,int n){int i=0;f32 *p=base;if(n>0)do{use(p[0],p[2]);i++;p+=3;}while(i<n);}'
    out=loop_repairs.variants(source,'f')
    assert any('base)[i*3+0]' in s and 'base)[i*3+2]' in s and 'p+=3' not in s and 'if(n>0)do' in s for s in out)
    indexed='void f(f32 *base,int n){int i=0;if(n<=0)return;do{use(base[i*3],base[i*3+2]);i++;}while(i<n);}'
    out=loop_repairs.variants(indexed,'f')
    assert any('__repair_cursor[0]' in s and '__repair_cursor[2]' in s and '__repair_cursor += 3' in s for s in out)
    assert all(s.index('if(n<=0)return;')<s.index('__repair_cursor =') for s in out)
    # Never insert initialization as the sole statement controlled by an unbraced entry guard.
    assert not loop_repairs.variants(indexed.replace('if(n<=0)return;','if(n>0)'), 'f')
    for bad in (source.replace('i++;','if(k)continue;i++;'),source.replace('p+=3;','p+=3;escape(p);'),source.replace('i=0','i=1')):
        assert not loop_repairs.variants(bad,'f')


@pytest.mark.parametrize('family,source',[
    ('address_temporary','void f(){use(M2C_FIELD(p+i,u16*,4));}'),
    ('constant_local','void f(){if(x>0)run();}'),
    ('comparison_load','void f(){if(M2C_FIELD(a,f32*,4)<M2C_FIELD(b,f32*,4))run();}'),
    ('stack_array','void f(){u8 buf;int i;u8 *p;i=0;p=&buf;do{i++;*p=1;p=&(&buf)[i];}while(i<8);send(&buf);}'),
    ('loop_induction','void f(f32 *base,int n){int i=0;f32 *p=base;do{use(p[0]);i++;p+=3;}while(i<n);}')])
@pytest.mark.parametrize('mode',['rom','fork'])
def test_new_families_reach_normal_agent_judge(tmp_path,monkeypatch,family,source,mode):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a')
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?',(json.dumps({'kind':mode,'compiler':{'toolchain':'gcc','flags':[]}}),))
        db.execute('UPDATE functions SET best_source=?,best_score=10',(source,))
    net.claim_row(a,'game','f');seen=[]
    def compile(agent,function,text,slug):
        seen.append(text);net._record(net._function('f','game'),agent,9,text,1)
        return 'score 9'
    monkeypatch.setattr(net,'compile_and_diff',compile)
    # This is a tool-dispatch test with a substituted judge, not an imported build fixture.
    monkeypatch.setattr('decompnet.app.agent_evidence.preflight',lambda *args:None)
    _,revision=agent_tokens.candidate(net,a,'game','f')
    result=tools.call(net,a,'game','compile_family',{'function':'f','revision':revision,'family':family})
    assert seen and len(seen)<=6 and 'Current candidate revision' in result


def test_family_rotation_and_labels_stay_bounded():
    source='void f(){if(M2C_FIELD(p+i,u16*,4)>0){use(x*2);}}'
    labels={};out=draft_repairs.variants(source,'f','mips',labels=labels)
    assert len(out)<=draft_repairs.LIMIT
    assert all(s in labels for s in out)
    assert 'address_temporary' in [labels[s] for s in out[:6]]
    assert 'constant_local' in [labels[s] for s in out[:6]]
    assert all(f in ('address_temporary','constant_local') or '__repair_' not in s or f in ('branch_orientation','typed_temporary') for f,s in pattern_repairs.variants(source,'f'))


def test_stack_array_canonical_fill_preserves_byte_index_order():
    source='void f(){u8 buf;s32 i;u8 *p;i=0;p=&buf;do{i+=1;*p=read();p=&(&buf)[i];}while(i<32);send(&buf);}'
    out=m2cfix.stack_array(source,'f',canonical=True)
    assert 'do { buf[i] = read(); i++; }' in out and '*p' not in out
    assert m2cfix.stack_array(source.replace('send(&buf);','send(&buf);use(p);'),'f',canonical=True) is None


def test_string_latch_repair_requires_the_shared_return_and_unique_label():
    source='u8 *f(u8 *p,int n){u8 c=*p;if(c!=n){again:p+=1;if(c==0)return NULL;c=*p;if(c==n)return p;goto again;}return p;}'
    out=loop_repairs.variants(source,'f')
    assert len(out)==1 and 'if(c==0) return NULL; p++; c=*p; if(c!=n) goto again;' in out[0]
    assert not loop_repairs.variants(source.replace('}return p;','}return other;'),'f')
    assert not loop_repairs.variants(source.replace('if(c!=n){','if(k)goto again;if(c!=n){'),'f')


def test_autopilot_offers_stack_array_as_alternative_without_paid_helpers():
    # The helper used by both saved-draft repairs and fresh m2c autopilot has independent alternatives.
    source='void f(){u8 buf;int i;u8 *p;i=0;p=&buf;do{i++;*p=1;p=&(&buf)[i];}while(i<8);send(&buf);}'
    out=pattern_repairs.variants(source,'f')
    assert len([s for f,s in out if f=='stack_array'])==2
    assert 'u8 buf;' in source
