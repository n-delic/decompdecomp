from decompnet.app import block_accumulator_repairs

HEADER = 'typedef struct Game Game;\nextern unsigned char D_800C0000[];\n#define game (*(Game **)(D_800C0000 + 0x100))\n'


SOURCE = 'void f(void) { use(game); use((*(Game **)0x800C0100)); }'


ASM = '/* 0 80001000 3C02800C */ lui $v0, 0x800c\n/* 4 80001004 8C420100 */ lw $v0, 0x100($v0)'


def test_block_accumulator_requires_dead_value_and_independent_entry():
    source = 'int f(int n) { int t; int total; total=0; for (;n;n--) { t=n*2; t+=n; total+=t; } return total; }'
    rows = block_accumulator_repairs.variants(source,'f')
    assert rows and '__block_t=n*2; __block_t+=n;' in rows[0][1]
    assert not block_accumulator_repairs.variants(source.replace('t=n*2','t+=n*2'),'f')
    assert not block_accumulator_repairs.variants(source.replace('return total','return total+t'),'f')
    assert not block_accumulator_repairs.variants(source.replace('t=n*2','if(n>2) t=n*2'),'f')


def test_sibling_shadow_does_not_hide_independent_later_accumulator():
    source = 'int f(int n) { int t; int sum; sum=0; if(n) { int t; t=1; sum+=t; } for(;n;n--) { t=n; t+=2; sum+=t; } return sum; }'
    candidates = block_accumulator_repairs.variants(source,'f')
    assert any('__block_t=n; __block_t+=2;' in c for _,c in candidates)
    assert all('int t; t=1; sum+=t;' in c for _,c in candidates)


def test_outgoing_value_not_assumed_dead_when_jump_skips_next_assignment():
    source = 'int f(int n) { int t; if(n) { t=n; t+=2; } goto done; t=0; done: return t; }'
    assert not block_accumulator_repairs.variants(source,'f')
