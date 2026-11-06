from decompnet.app import matching_investigations as evidence
from decompnet.app import repair_ranking, source_predicates, source_signedness
from decompnet.app.source_next_steps import recommendations


def test_original_range_predicate_is_preserved_as_private_typed_helper():
    reference='int type_of(Obj*,int);\nint predicate(Obj *p,int n){int t; t=type_of(p,n); return ((t >= 230) && (t <= 249));}'
    source='void f(Obj*p,int n){if(predicate(p,n)) go(p);}'
    result=source_predicates.variants(source,'f','typedef struct Obj Obj;\nint predicate(Obj*,int);\n',reference)
    assert len(result)==1 and 'static int predicate' in result[0][1]
    assert 'type_of(Obj*,int)' in result[0][1] and 't <= 249' in result[0][1]
    assert not source_predicates.variants(source,'f','#define predicate(x,y) other(x,y)\n',reference)
    assert not source_predicates.variants(source,'f','',reference.replace('t=type_of(p,n)','t=type_of(p,n++)'))
    assert not source_predicates.variants(source,'f','',reference.replace('t <= 249','side_effect()'))


def test_signedness_hypotheses_keep_width_and_private_context_only():
    source='void f(App*p){sink((p->flags & 1) == 1);}'
    header='typedef struct {int flags; volatile int mask;} App;'
    values=source_signedness.variants(source,header)
    assert values==[(source,header.replace('int flags','unsigned int flags'))]
    assert not source_signedness.variants('void f(void){sink(flags);}','extern int flags;')
    assert not source_signedness.variants(source,'typedef struct {int *flags;} App;')
    assert source_signedness.variants('int f(void){int mask=2;return mask & 1;}','')[0][0].startswith('int f(void){unsigned int mask')


def test_two_load_near_miss_gets_focused_priority_not_mutation():
    rows=[['i','lw $t0, 0($a0)','lw $t1, 4($a0)'],['i','lw $t1, 4($a0)','lw $t0, 0($a0)']]
    assert evidence.paired_loads(rows)
    families=['constant_local','paired_load_order','scalar_schedule_order']
    assert repair_ranking.order(families,rows)==['paired_load_order','scalar_schedule_order','constant_local']
    assert 'volatile' in evidence.hints(rows,'mips')[0]
    assert not evidence.paired_loads(rows+[['i','move $a0,$zero','move $a0,$s0']])


def test_candidate_only_restore_epilogue_is_an_advisory_not_a_byte_rule():
    rows=[[' ','b loop','b loop'],['>','','lw $ra, 0x14($sp)'],['>','','jr $ra'],['>','','addiu $sp, $sp, 0x18']]
    assert evidence.epilogue(rows,'mips')
    assert 'Do not trim candidate bytes' in evidence.hints(rows,'mips')[0]
    assert not evidence.epilogue(rows,'ppc')
    assert not evidence.epilogue(rows[:-1]+[['>','','nop']],'mips')
    assert not evidence.epilogue(rows+[['i','sw $v0, 0($a0)','sw $v1, 0($a0)']],'mips')


def test_reference_comparison_constants_are_concrete_revision_leads_only():
    hints=recommendations('int f(int r){if(r==4)return 1;return 0;}','int f(int r){if(r!=0)return 1;return 0;}',{})
    assert any('r==4' in h and 'r!=0' in h and 'retail instructions' in h for h in hints)


def test_predicate_and_signedness_contexts_reach_preparation(tmp_path):
    from decompnet.app import source_preparation as prep
    from tests.test_source_preparation import config
    cfg=config(tmp_path)
    (tmp_path/'types.h').write_text('typedef struct Obj Obj;\nint type_of(Obj*,int);\nint predicate(Obj*,int);\n')
    (tmp_path/'game.c').write_text('int predicate(Obj*p,int n){int t;t=type_of(p,n);return ((t>=2)&&(t<=5));}\nvoid original(Obj*p,int n){if(predicate(p,n)) type_of(p,n);}')
    values=prep.prepare_variants(prep.Reader(cfg),cfg,('f','original','game.c'),{'original':'f'},'')
    assert any('static int predicate' in header for _,header in values)
    owned=prep.prepare_variants(prep.Reader(cfg),{**cfg,'_build_owned':True},('f','original','game.c'),{'original':'f'},'')
    assert not any('static int predicate' in header for _,header in owned)


def test_compiled_immediate_differences_are_reported_without_branch_edits():
    from decompnet.app.source_lead_analysis import compare
    rows=[['i','addiu $at, $zero, 4','addiu $at, $zero, 1']]
    comparison=compare(rows,'mips','int f(void){return 1;}','f')
    assert comparison['target']['immediates']==[('addiu',4)]
    assert any('Compiled immediate differences' in hint for hint in recommendations('','',comparison))
