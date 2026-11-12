"""Small source fixtures cover scheduling candidates without publishing game code."""
from decompnet import m2cfix
from decompnet.app import draft_repairs, repair_ranking, search_frontier
from decomprepair import conversion_scheduling_repairs as repairs
from decomprepair import hardware_operation_repairs


def test_conversion_tail_preserves_assignment_type_and_distinct_physical_lines():
    source='int f(float x, int flag) { int y; y=0; if(flag) { y=x*2; } return y; }'
    choices=repairs.conversion_tails(source,'f','','mips','mfc1 $v0, $f0',['-O2','-g3'])
    assert len(choices)==1
    family,text=choices[0]
    assert family=='debug_conversion_tail'
    assert 'int decomp_conversion;\n decomp_conversion = x*2;\n y = decomp_conversion;\n }' in text
    assert repairs.conversion_tails(source,'f','','mips','mfc1 $v0, $f0',['-g0'])==[]
    assert repairs.conversion_tails(source,'f','','x86','mov eax, ebx',['-g3'])==[]
    assert repairs.conversion_tails(source,'f','#define HERE __LINE__','mips','mfc1',['-g3'])==[]
    assert repairs.conversion_tails(source,'f','','mips','addu',['-g3'])==[]


def test_capture_moves_only_existing_prototyped_narrow_conversion():
    source='void use(unsigned short, int); int f(int x) { unsigned int t=(unsigned short)x; int spare; use(t, 4); return t; }'
    choices=repairs.delayed_captures(source,'f','','mips')
    assert len(choices)==1
    text=choices[0][1]
    assert 'unsigned int t;' in text
    assert 'use(x, 4);\nt = (unsigned short)x;' in text
    for bad in (source.replace('unsigned short, int','int, int'),
                source.replace('use(t, 4)','use(t, x++)'),
                source.replace('int spare;','int spare; x++;'),
                source.replace('return t','return (int)&t'),
                source.replace('use(t, 4)','use(t, t)'),
                source.replace('use(t, 4)','use(t, helper())')):
        assert repairs.delayed_captures(bad,'f','','mips')==[]


def test_shared_shift_masks_only_known_zero_bits_and_shared_unchanged_local():
    source='unsigned f(unsigned x) { unsigned int b=x*4; unsigned int a=b|1; return a | (b << 8); }'
    choices=repairs.shift_masks(source,'f','','mips')
    assert len(choices)==1
    assert '(b & 0xFFFFFFFCU) << 8' in choices[0][1]
    for bad in (source.replace('x*4','x*3'),source.replace('return a','b++; return a'),
                source.replace('unsigned int a=b|1;','unsigned int a=1;'),
                source.replace('return a','escape(&b); return a')):
        assert repairs.shift_masks(bad,'f','','mips')==[]


def test_m2c_break_repair_requires_explicit_marker_and_target_opcode():
    source='int f(void) { M2C_BREAK(9); return 1; }'
    fixed=hardware_operation_repairs.variants(source,'f','mips','break 9')[0][1]
    assert '__builtin_mips_debug_break();' in fixed
    assert '__builtin_mips_debug_break();' in m2cfix.refine_draft(source,'f','break 9')
    assert hardware_operation_repairs.variants(source,'f','mips','break 7')==[]
    assert hardware_operation_repairs.variants(source.replace('9','7'),'f','mips','break 9')==[]
    assert hardware_operation_repairs.variants('int f(void) { return 1; }','f','mips','break 9')==[]
    assert hardware_operation_repairs.variants(source,'f','ppc','break 9')==[]
    assert hardware_operation_repairs.hints(source,'break 9','mips')
    assert not hardware_operation_repairs.hints(fixed,'break 9','mips')


def test_families_reach_shared_generator_and_ranking():
    source='int f(float x, int flag) { int y; y=0; if(flag) { y=x*2; } return y; }'
    labels={}
    choices=draft_repairs.variants(source,'f','mips','mfc1 $v0, $f0',['-g3'],labels,limit=None)
    assert any(labels[c]=='debug_conversion_tail' for c in choices)
    assert set(repairs.FAMILIES)<=set(draft_repairs.AGENT_FAMILIES)
    assert repair_ranking.order(['constant_local','mips_debug_break'],[('<','break 9','')])[0]=='mips_debug_break'


def test_frontier_keeps_missing_operation_recovery_despite_higher_scalar_score():
    baseline=[('<','break 9',''),('i','beq $a0, $zero, 0x40','beq $a0, $zero, 0x44')]
    recovered=[(' ','break 9','break 9'),('>','','andi $a0, $a0, 0xff')]
    frontier=search_frontier.Frontier()
    frontier.offer('numeric',100,baseline,baseline=baseline,baseline_score=100)
    frontier.offer('operation restored',110,recovered,baseline=baseline,baseline_score=100)
    assert {n['source'] for n in frontier.nodes}=={'numeric','operation restored'}
    assert search_frontier.missing_operations([('<','nop','')])==0


def test_source_pages_preserve_and_distinguish_physical_line_layout():
    from decompnet.app import repair_selection
    multiline='int f(float x) { int t;\n t=x;\n return t;\n }'
    single=multiline.replace('\n',' ')
    cursors={}
    proposals=[('debug_conversion_tail',multiline)]
    assert repair_selection.page(proposals,multiline,cursors,1)==proposals
    assert repair_selection.page(proposals,multiline,cursors,1)==[]
    assert repair_selection.page(proposals,single,cursors,1)==proposals
    assert repair_selection.order(['constant_local','debug_conversion_tail'],['delayed_narrow_capture'])[0]=='debug_conversion_tail'


def test_directives_and_other_functions_are_preserved():
    source='#define ALIAS y\nint f(float x) { int y; y=0; if(x) { y=x; } return y; }\nint g(void) { return 7; }'
    choices=repairs.conversion_tails(source,'f','','mips','mfc1',['-g3'])
    assert choices
    assert all(text.startswith('#define ALIAS y\n') and text.endswith('int g(void) { return 7; }') for _,text in choices)


def test_unnamed_and_named_multitoken_prototypes_keep_their_width():
    from decomprepair.branch_call_support import prototype
    assert prototype('void use(unsigned short, unsigned int);','use','mips')[1]==['unsigned short','unsigned int']
    assert prototype('void use(unsigned short x, unsigned int y);','use','mips')[1]==['unsigned short','unsigned int']


def test_missing_disassembly_does_not_break_deep_search_diagnostics():
    assert hardware_operation_repairs.hints('int f(void){return 1;}',None,'mips')==[]
    assert hardware_operation_repairs.variants('int f(void){M2C_BREAK(9);}','f','mips',None)==[]
    assert repairs.conversion_tails('int f(void){return 1;}','f','','mips',None,['-g3'])==[]
