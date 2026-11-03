from decompnet.app import block_accumulator_repairs, global_identity_repairs, search_frontier
from decompnet.diff_summary import advice

HEADER = 'typedef struct Game Game;\nextern unsigned char D_800C0000[];\n#define game (*(Game **)(D_800C0000 + 0x100))\n'
SOURCE = 'void f(void) { use(game); use((*(Game **)0x800C0100)); }'
ASM = '/* 0 80001000 3C02800C */ lui $v0, 0x800c\n/* 4 80001004 8C420100 */ lw $v0, 0x100($v0)'


def test_typed_cell_unifies_macro_and_literal_without_editing_shared_header():
    candidates = global_identity_repairs.variants(SOURCE,'f','mips',ASM,HEADER)
    assert len(candidates)==1
    family, text = candidates[0]
    assert family=='typed_global_identity'
    assert 'extern Game *D_800C0100;' in text
    assert text.count('use(D_800C0100)')==2
    assert 'D_800C0000 + 0x100' in HEADER
    assert not global_identity_repairs.variants(SOURCE,'f','ppc',ASM,HEADER)
    assert not global_identity_repairs.variants(SOURCE,'f','mips','',HEADER)


def test_ambiguous_qualified_or_nonbyte_global_is_not_rewritten():
    for header in (HEADER.replace('unsigned char','unsigned int'),
                   HEADER+'\nextern int D_800C0100;',
                   HEADER.replace('Game **','volatile Game **')):
        assert not global_identity_repairs.variants(SOURCE,'f','mips',ASM,header)


def test_block_accumulator_requires_dead_value_and_independent_entry():
    source = 'int f(int n) { int t; int total; total=0; for (;n;n--) { t=n*2; t+=n; total+=t; } return total; }'
    rows = block_accumulator_repairs.variants(source,'f')
    assert rows and '__block_t=n*2; __block_t+=n;' in rows[0][1]
    assert not block_accumulator_repairs.variants(source.replace('t=n*2','t+=n*2'),'f')
    assert not block_accumulator_repairs.variants(source.replace('return total','return total+t'),'f')
    assert not block_accumulator_repairs.variants(source.replace('t=n*2','if(n>2) t=n*2'),'f')


def test_frontier_keeps_higher_scoring_distinct_failure_kind():
    frontier = search_frontier.Frontier()
    frontier.offer('moved',80,[('o','lbu $v0, 2($a0)','lbu $v0, 2($a0)')])
    frontier.offer('clean',440,[('>','','nop'),('i','beq $v0, $zero, 0x80001000','beq $v0, $zero, 0x80001004')])
    for i in range(15):
        frontier.offer(str(i),100+i,[('r','move $a0, $v0','move $a1, $v0')]*2)
    assert {'moved','clean'} <= {n['source'] for n in frontier.nodes}


def test_nop_branch_cascade_advice_does_not_change_verdict():
    rows = [('>','','nop')]+[('i','beq $v0, $zero, 0x80001000','beq $v0, $zero, 0x80001004')]*3
    assert 'downstream branch' in advice(rows)
    assert rows[0][0]=='>'


def test_family_is_available_through_shared_pipeline():
    from decompnet.app import draft_repairs
    labels = {}
    candidates = draft_repairs.variants(SOURCE,'f','mips',ASM,header=HEADER,labels=labels,limit=None)
    assert any(labels.get(c)=='typed_global_identity' for c in candidates)
    assert 'typed_global_identity' in draft_repairs.AGENT_FAMILIES
    assert 'block_accumulator_lifetime' in draft_repairs.AGENT_FAMILIES


def test_sibling_shadow_does_not_hide_independent_later_accumulator():
    source = 'int f(int n) { int t; int sum; sum=0; if(n) { int t; t=1; sum+=t; } for(;n;n--) { t=n; t+=2; sum+=t; } return sum; }'
    candidates = block_accumulator_repairs.variants(source,'f')
    assert any('__block_t=n; __block_t+=2;' in c for _,c in candidates)
    assert all('int t; t=1; sum+=t;' in c for _,c in candidates)


def test_outgoing_value_not_assumed_dead_when_jump_skips_next_assignment():
    source = 'int f(int n) { int t; if(n) { t=n; t+=2; } goto done; t=0; done: return t; }'
    assert not block_accumulator_repairs.variants(source,'f')
