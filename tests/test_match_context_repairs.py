from types import SimpleNamespace

from decompnet.app import match_shape_repairs as repairs
from decompnet.app.matched_context import brief, declarations


def test_arithmetic_staging_keeps_call_and_expression():
    source='int next(void); void f(void){int angle; int x; angle=4; x=(angle - 2) + next() % 7;}'
    variants=repairs.variants(source,'f')
    staged=next(s for family,s in variants if family=='arithmetic_before_call')
    assert '__repair_call_arithmetic = angle - 2;' in staged
    assert 'x=__repair_call_arithmetic + next() % 7;' in staged
    assert staged.count('next()')==1


def test_staging_does_not_hoist_controlled_or_macro_calls():
    for statement in ['if (angle) x=(angle - 2) + next();','while (x=(angle - 2) + next()) {}']:
        source='void f(void){int angle; int x;'+statement+'}'
        assert not repairs.variants(source,'f')
    source='void f(void){int angle; int x; x=(angle - 2) + next();}'
    assert not repairs.variants(source,'f','#define next() 3')
    assert not repairs.variants(source.replace('int angle','volatile int angle'),'f')


def test_element_address_requires_actual_array_type():
    types='typedef struct {int state;} Slot; typedef struct {Slot slots[4];} Bank;'
    source=types+' void f(Bank *bank,int i){bank->slots[i].state=3;}'
    staged=next(s for family,s in repairs.variants(source,'f') if family=='typed_element_store')
    assert 'Slot *__repair_element;' in staged
    assert '__repair_element = &bank->slots[i];' in staged
    assert '__repair_element->state = 3;' in staged
    assert not repairs.variants(source.replace(types,''),'f')
    assert not repairs.variants(source.replace('i].state','i++].state'),'f')


def test_narrow_reuse_retains_cast_and_stays_within_statement():
    source='void f(void){int x; int y; y=(u16)(x + 3) + (u16)(x + 3);}'
    staged=next(s for family,s in repairs.variants(source,'f') if family=='narrow_value_reuse')
    assert staged.count('(u16)(x + 3)')==1
    assert 'y=__repair_narrow + __repair_narrow;' in staged
    assert not repairs.variants(source.replace('int x;', 'volatile int x;'),'f')
    across='void f(void){int x; send((u16)(x + 3)); use((u16)(x + 3));}'
    assert any(f=='narrow_value_reuse' for f,_ in repairs.variants(across,'f'))
    assert not repairs.variants(across.replace('; use','; x++; use'),'f')
    assert not repairs.variants(across.replace('; use','; escape(&x); use'),'f')


def test_matched_context_preserves_real_types_prototype_and_macro():
    text='typedef struct {int count;} Stream;\n#define STEP(s) ((s)->count++)\nint consume(Stream *s){return STEP(s);}'
    context=declarations(text,'consume','void f(Stream *s){consume(s); STEP(s);}')
    assert 'typedef struct {int count;} Stream;' in context
    assert 'int consume(Stream *s);' in context
    assert '#define STEP(s) ((s)->count++)' in context
    assert 'return STEP(s)' not in context
    assert not declarations('#pragma GLOBAL_ASM("asm/consume.s")','consume','consume();')
    assert not declarations('static int consume(void){return 1;}','consume','consume();')


def test_brief_queries_only_called_matched_functions_and_attributes_context():
    queries=[]
    def all_(sql,*args):
        queries.append((sql,args))
        return [{'name':'consume','unit':'unit1','source':'int consume(int n){return n;}'}]
    net=SimpleNamespace(store=SimpleNamespace(all=all_))
    fn={'name':'f','project_id':2,'unit':'unit1'}
    result='\n'.join(brief(net,fn,'void f(void){consume(1);}'))
    assert "status='matched'" in queries[0][0]
    assert queries[0][1]==(2,'consume','unit1')
    assert 'exact-matched consume (unit1)' in result
    assert 'No header was changed' in result
    assert not brief(net,fn,'')


def test_cross_game_reference_is_attributed_and_never_invented():
    from decompnet.app.matched_context import reference_hints
    net=SimpleNamespace(store=SimpleNamespace(one=lambda *args: {
        'name':'library_read','source':'typedef struct {int n;} Stream; int library_read(Stream *s){return s->n;}'}))
    items=[{'name':'library_read','game':'another game','function_id':4,'kind':'similar code; unverified'},
           {'name':'sdk_name','game':'local archive','kind':'SDK object signature; name only'}]
    result=reference_hints(net,items)
    assert 'Stream' in result[0]['reference_context']
    assert result[0]['kind']=='similar code; unverified'
    assert 'reference_context' not in result[1]
    assert 'reference_context' not in items[0]


def test_generated_shapes_compile_like_independent_controls(tmp_path):
    import pytest

    from decompnet import toolchains
    from decompnet.compiler import compile_source
    from decompnet.elfobj import extract_function
    from decompnet.project import compiler_config
    if not toolchains.installed('gcc-2.7.2-kmc'):
        pytest.skip('needs pinned compiler')
    cfg=compiler_config(tmp_path,{'compiler':{'toolchain':'gcc-2.7.2-kmc','flags':['-O2']}})
    types='typedef unsigned short u16;'
    cases=[
        ('int next(void); int demo(void){int angle; int x; angle=next(); x=(angle - 2) + next() % 7; return x;}',
         'int next(void); int demo(void){int angle; int x; int r; angle=next(); r=angle-2; x=r+next()%7; return x;}',
         'arithmetic_before_call'),
        ('typedef struct {int state;} Slot; typedef struct {Slot slots[4];} Bank; void demo(Bank *b,int i){b->slots[i].state=3;}',
         'typedef struct {int state;} Slot; typedef struct {Slot slots[4];} Bank; void demo(Bank *b,int i){Slot *p; p=&b->slots[i]; p->state=3;}',
         'typed_element_store'),
        ('void send(unsigned int); void demo(void){int x; x=get(); send((u16)(x + 3)); send((u16)(x + 3));}',
         'void send(unsigned int); void demo(void){int x; unsigned int v; x=get(); v=(u16)(x+3); send(v); send(v);}',
         'narrow_value_reuse'),
    ]
    for before,control,family in cases:
        target=compile_source(cfg,types,control)
        assert target.ok,target.log
        generated=[s for f,s in repairs.variants(before,'demo') if f==family]
        assert generated
        outputs=[compile_source(cfg,types,s) for s in generated]
        assert any(r.ok and extract_function(r.obj,'demo').code==extract_function(target.obj,'demo').code for r in outputs)


def test_element_staging_does_not_change_rhs_call_order():
    source='typedef struct {int state;} Slot; typedef struct {Slot slots[4];} Bank; void f(Bank *b,int i){b->slots[i].state=next();}'
    assert not repairs.variants(source,'f')


def test_function_view_skips_same_line_type_body():
    from decompnet.app.integer_repair_support import view
    source='typedef struct {int field;} Item; void f(void){int x; x=1;}'
    part=view(source,'f')
    assert part.body=='int x; x=1;'


def test_new_families_reach_shared_repair_pipeline():
    from decompnet.app.draft_repairs import AGENT_FAMILIES, variants
    assert set(repairs.FAMILIES)<=set(AGENT_FAMILIES)
    source='int next(void); void f(void){int angle; int x; angle=4; x=(angle - 2) + next();}'
    assert any('__repair_call_arithmetic' in s for s in variants(source,'f','mips',limit=100))


def test_arithmetic_can_stage_inside_branch_with_outer_local():
    source='void f(void){int n; int x; n=4; if(n){x=(n - 2) + next();}}'
    assert any(f=='arithmetic_before_call' for f,_ in repairs.variants(source,'f','extern volatile int clock;'))


def test_element_can_reuse_dead_pointer_but_not_a_live_one_or_loop():
    types='typedef struct {int state;} Slot; typedef struct {Slot slots[4];} Bank;'
    source=types+' void f(Bank *b,int i){Slot *p; p=get(); use(p); b->slots[i].state=3;}'
    assert any('p = &b->slots[i]; p->state' in s for _,s in repairs.variants(source,'f'))
    live=source.replace('state=3;', 'state=3; use(p);')
    assert not any('p = &b->slots[i]; p->state' in s for _,s in repairs.variants(live,'f'))
    loop=source.replace('b->slots[i].state=3;', 'while(i){b->slots[i].state=3;}')
    assert not any('p = &b->slots[i]; p->state' in s for _,s in repairs.variants(loop,'f'))
