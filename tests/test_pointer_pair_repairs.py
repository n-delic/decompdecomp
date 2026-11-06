"""Synthetic pointer scheduling examples, without game data or changes to the judge."""
import shutil
import subprocess

import pytest

from decompnet.app import draft_repairs, field_order_repairs
from decompnet.app import pointer_pair_repairs as repairs

HEADER='typedef struct { int a; int b; } Pair;\n'
INLINE='''int work(Pair *input) {
 Pair *p;
 Pair *q;
 int x;
 int y;
 p = input;
 q = p;
 x = q->a;
 y = q->b;
 return x+y;
}'''
CALL='''void *take(void); void use(int, void *);
void work(void) {
 void *a;
 void *b;
 use(1, take());
 a = take();
 use(2, a);
 b = take();
 use(3, b);
}'''


def test_materialized_calls_keep_return_types_and_known_declarations():
    proposals=repairs.call_stages(CALL,'work')
    assert proposals and any('a = take();' in text for _,text in proposals)
    assert any('void * __repair_pointer_call;' in text for _,text in proposals)
    assert not repairs.call_stages(CALL.replace('void *take(void);','int take(void);'),'work')
    assert not repairs.call_stages(CALL.replace('use(1, take());','use(other(), take());'),'work')


def test_pairing_does_not_need_an_improving_first_step_and_is_bounded():
    proposals=repairs.variants(CALL,'work')
    assert proposals and 'paired_pointer_declarations' in {f for f,_ in proposals}
    assert len(proposals)<=repairs.LIMIT
    for arch in ('mips','ppc','x86','arm','thumb'):
        labels={};draft_repairs.variants(CALL,'work',arch,labels=labels,limit=None)
        assert 'paired_pointer_declarations' in labels.values()


def test_pointer_reuse_respects_cfg_liveness_and_address_escape():
    assert repairs.reuse(CALL,'work')
    live=CALL.replace('use(3, b);','use(3, b); use(4, a);')
    assert not repairs.reuse(live,'work')
    assert not repairs.reuse(CALL.replace('use(2, a);','use(2, &a);'),'work')


def test_load_inlining_rejects_intervening_stores_and_calls():
    source=INLINE.replace('q = p;','q = *(Pair **)p;')
    assert repairs.inline_loads(source,'work')
    assert not repairs.inline_loads(source.replace('y = q->b;','mutate(); y = q->b;'),'work')
    assert not repairs.inline_loads(source.replace('y = q->b;','p->a=4; y = q->b;'),'work')
    assert not repairs.inline_loads(source.replace('x = q->a;','x = q->a; p = input;'),'work')


def test_known_load_macros_are_checked_for_hidden_side_effects():
    source=INLINE.replace('q = p;','q = LOAD(p);')
    assert repairs.inline_loads(source,'work','#define LOAD(p) (*(Pair **)(p))')
    assert not repairs.inline_loads(source,'work','#define LOAD(p) side_effect(p)')
    assert not repairs.inline_loads(source,'work','#define LOAD(p) (*(Pair * volatile *)(p))')


def test_only_memory_independent_distinct_struct_fields_can_swap():
    source=HEADER+'void work(Pair *out, int x, int y) { out->a=x; out->b=y; }'
    assert field_order_repairs.variants(source,'work')
    assert not field_order_repairs.variants(source.replace('out->b=y','out->a=y'),'work')
    assert not field_order_repairs.variants(source.replace('out->b=y','out->b=out->a'),'work')
    assert not field_order_repairs.variants(source.replace('int b;','volatile int b;'),'work')
    assert not field_order_repairs.variants(source.replace('struct','union'),'work')


def test_synthetic_pointer_pairs_preserve_values_and_call_order(tmp_path):
    cc=shutil.which('cc')
    if not cc:pytest.skip('Host compiler needed for the synthetic execution check.')
    prefix='''#include <stdio.h>
int value;
void *take(void) { ++value; return &value; }
void use(int tag, void *p) { printf("%d:%d;",tag,*(int *)p); }
'''
    suffix='\nint main(void) { work(); return 0; }'
    outputs=[]
    for index,source in enumerate([CALL]+[text for _,text in repairs.variants(CALL,'work')]):
        path=tmp_path/f'case{index}.c';binary=tmp_path/f'case{index}'
        path.write_text(prefix+source+suffix)
        subprocess.run([cc,'-std=c89','-Werror',str(path),'-o',str(binary)],check=True,capture_output=True)
        outputs.append(subprocess.check_output([str(binary)]))
    assert outputs and all(out==outputs[0] for out in outputs)


def test_uninitialized_and_conditionally_initialized_pointer_lifetimes_are_not_merged():
    source='void use(void *); void *take(void); void work(int flag) { void *a; void *b; if(flag) a=take(); use(a); b=take(); use(b); }'
    assert not repairs.reuse(source,'work')


def test_macros_that_capture_a_local_are_not_treated_as_pure_loads():
    source=INLINE.replace('q = p;','q = LOAD(p);')
    assert not repairs.inline_loads(source,'work','#define LOAD(p) (*(Pair **)((char *)(p)+x))')


def test_zero_only_reuse_keeps_an_unused_signed_local_and_the_later_loop_phase():
    from decompnet.app import zero_test_repairs
    source='void work(unsigned int n) { unsigned int t; int r; t=n; n--; if(t != 0) { do { t=n; n--; } while(t != 0); } }'
    candidates=zero_test_repairs.variants(source,'work','mips')
    assert candidates and any('r=n; n--; if(r != 0)' in text for _,text in candidates)
    assert all('unsigned int t;' in text for _,text in candidates)
    assert any('r=n; n--; if(r != 0)' in text and 'while(t != 0)' in text for _,text in candidates)
    assert all('if(t < 0)' in text for _,text in zero_test_repairs.variants(source.replace('if(t != 0)','if(t < 0)'),'work','mips'))
    guarded=source.replace('t=n; n--; if(t != 0)','if(n) t=n; n--; if(t != 0)',1)
    assert not zero_test_repairs.variants(guarded,'work','mips')


def test_volatile_aliases_nested_records_and_escaped_rhs_stay_unchanged():
    header='typedef volatile int Register; typedef struct { Register a; Register b; } Pair;'
    source='void work(Pair *out, int x, int y) { out->a=x; out->b=y; }'
    assert not field_order_repairs.variants(source,'work',header)
    assert not field_order_repairs.variants(HEADER+source.replace('out->a=x;','escape(&(x)); out->a=x;'),'work')
    nested='typedef struct { struct { volatile int reg; } inner; } Inner; typedef struct { Inner inner; } Pair;'
    assert 'Pair' in repairs.qualified_types(nested)
    assert not repairs.variants(INLINE,'work',nested)


def test_zero_test_reuse_preserves_high_bit_values_with_a_host_compiler(tmp_path):
    from decompnet.app import zero_test_repairs
    cc=shutil.which('cc')
    if not cc:pytest.skip('Host compiler needed for the synthetic execution check.')
    source='int work(unsigned int n) { unsigned int t; int r; t=n; if(t != 0) { return 1; } return 0; }'
    suffix='\nint main(void) { return work(0)!=0 || work(1)!=1 || work(0x80000000U)!=1 || work(~0U)!=1; }'
    proposals=zero_test_repairs.variants(source,'work','mips')
    assert proposals
    for index,(_,text) in enumerate(proposals):
        path=tmp_path/f'zero{index}.c';binary=tmp_path/f'zero{index}'
        path.write_text(text+suffix)
        subprocess.run([cc,'-std=c89','-Werror',str(path),'-o',str(binary)],check=True,capture_output=True)
        subprocess.run([str(binary)],check=True)


def test_macro_indirection_stringification_and_shadowed_callees_are_conservative():
    source=INLINE.replace('q = p;','q = LOAD(p);')
    assert not repairs.inline_loads(source,'work','#define HIDDEN x\n#define LOAD(p) (*(Pair **)((char *)(p)+HIDDEN))')
    assert not repairs.variants(CALL.replace('use(2, a);','NAME(a); use(2, a);'),'work','#define NAME(v) puts(#v)')
    shadow=CALL.replace('void work(void)','void work(void *(*take)(void))')
    assert not repairs.call_stages(shadow,'work')
    assert not repairs.call_stages(CALL.replace('use(1, take());','use(HIDDEN, take());'),'work','#define HIDDEN side_effect()')


def test_field_store_order_supports_plain_pointer_members_and_known_array_fields():
    source='typedef struct { int a[0x4]; int b[4]; void *pointer; } Pair; void work(Pair *out, int x, int y, int i) { out->a[i]=x; out->b[i]=y; }'
    proposals=field_order_repairs.variants(source,'work')
    assert proposals and 'out->b[i]=y;' in proposals[0][1]
    assert not field_order_repairs.variants(source.replace('int a[0x4]','int a[0]'),'work')


def test_one_line_declarations_do_not_become_part_of_the_repaired_definition():
    from decompnet.forks.csource import find
    source='typedef struct { int a; int b; } Pair; int global; void work(Pair *p, int x, int y) { p->a=x; p->b=y; }'
    start,end=find(source,'work')
    assert source[:start].endswith(';') and 'typedef' not in source[start:end]
    for _,candidate in field_order_repairs.variants(source,'work'):
        assert candidate[:start]==source[:start]
    assert field_order_repairs.variants(source,'work')


def test_pointer_call_staging_does_not_drop_return_qualifiers_or_unsigned_types():
    source=CALL.replace('void *take(void);','const void *take(void);')
    candidates=repairs.call_stages(source,'work')
    assert candidates and all('const void * __repair_pointer_call;' in text for _,text in candidates)
    source=CALL.replace('void *take(void);','unsigned char *take(void);')
    candidates=repairs.call_stages(source,'work')
    assert candidates and all('unsigned char * __repair_pointer_call;' in text for _,text in candidates)


def test_cpp_header_types_are_not_assumed_to_have_builtin_c_semantics():
    header='class Value { public: volatile int field; };'
    assert not repairs.variants(CALL,'work',header)
    assert not field_order_repairs.variants(HEADER+'void work(Pair *p,int x,int y) { p->a=x; p->b=y; }','work',header)
