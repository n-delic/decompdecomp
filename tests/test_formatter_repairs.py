from decompnet.app import formatter_repairs


def family(source, kind):
    return [text for label,text in formatter_repairs.variants(source,'f') if label==kind]


def test_assignment_keeps_postincrement_once():
    source='int f(char *p) { char c; c=*p++; if (c==42) return 1; return c; }'
    values=family(source,'condition_assignment')
    assert values and 'if ((c=*p++)==' in values[0]
    assert values[0].count('p++')==1
    assert not family('int f(char *p) { volatile char c; c=*p++; if(c==42) return 1; return c; }','condition_assignment')


def test_macro_result_must_be_discarded():
    macro='#define PUT(x) do { if (--n==0) flush(x); else *out++=(x); } while(0)\n'
    assert family(macro+'int f(void) { PUT(42); return 0; }','conditional_output')
    assert not family(macro+'int f(void) { return g( PUT(42)); }','conditional_output')


def test_flag_loop_rewrite_and_nested_loop_guard():
    source="int f(char *p) { char c; for(;;) { c=*p++; switch(c) { case 1: continue; case 2: continue; } break; } return c; }"
    values=family(source,'switch_flag_loop')
    assert values and 'switch (c=*p++)' in values[0]
    assert 'continue;' not in values[0] and 'goto decomp_flag_loop;' in values[0]
    assert not family(source.replace('case 1: continue;', 'case 1: while(1) { continue; }'),'switch_flag_loop')


def test_shared_tail_and_combined_repairs():
    source='int f(char *p) { char c; c=*p++; if(c==1) goto done; return 0; done: c=1; goto end; end: return c; }'
    assert family(source,'shared_tail_copy')
    assert family(source,'formatter_bundle')


def test_initialization_order_requires_unescaped_ordinary_locals():
    safe='int f(void) { int a, b, c; a=0; b=0; return a+b; }'
    assert family(safe,'scalar_initialization_order')
    assert not family(safe.replace('int a, b, c;', 'volatile int a, b, c;'),'scalar_initialization_order')
    assert not family(safe.replace('return a+b;', 'g(&a); return a+b;'),'scalar_initialization_order')


def test_cpp_overloads_are_not_rewritten():
    assert not formatter_repairs.variants('int C::f() { int a,b,c; a=0; b=0; return a; }','C::f')
