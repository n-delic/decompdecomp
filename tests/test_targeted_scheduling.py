import pytest

from decompnet.app import address_association_repairs as addresses
from decompnet.app import call_initialization_repairs as initialization
from decompnet.app import draft_repairs, local_experiments, stack_layout


def test_byte_pointer_address_association_preserves_other_definitions_and_own_helpers():
    prefix='int other(char *p,int i){return *(short *)(p+(i+24));}\n'
    helper='static inline int field(char *base,int index){return *(short *)((base+index)+24);}\n'
    source=prefix+helper+'int f(char *p,int i){return field(p,i);}'
    choices=addresses.variants(source,'f')
    assert choices and all(text.startswith(prefix) for _,text in choices)
    assert any('(base + 24) + (index)' in text for _,text in choices)
    unused=source.replace('return field(p,i);','return 0;')
    assert not addresses.variants(unused,'f')
    assert not addresses.variants(source.replace('char *base','volatile char *base'),'f')


@pytest.mark.parametrize('expression',['base + (index++ + 24)','base + (read() + 24)',
                                        'base + (index << 2) + 24','base + index + 24 * 2'])
def test_address_rule_leaves_side_effects_and_other_operand_precedence_alone(expression):
    source='int f(char *base,int index){return *(short *)('+expression+');}'
    assert not addresses.variants(source,'f')


def test_constant_reset_can_move_to_both_arms_and_drop_only_a_redundant_later_reset():
    source='void f(int flag){int n;n=0;if(flag){emit(p,lookup());}else{emit(q,lookup());}work();n=0;use(n);}'
    choices=initialization.variants(source,'f')
    assert any(text.count('n = 0,')==2 and 'n=0;' not in text for _,text in choices)
    assert all(text.count('lookup()')==2 and 'work();' in text for _,text in choices)
    assert any('call_initialization'==family for family,_ in choices)


@pytest.mark.parametrize('body',[
    'n=0;if(flag){emit(n);}else{emit(q);}use(n);',
    'n=0;if(flag){emit(p);}else{return;}use(n);',
    'n=0;while(flag){emit(p);}use(n);',
    'n=0;escape(&n);emit(p);use(n);',
    'if(flag)n=0;emit(p);use(n);',
    'n=0;if(flag){emit(p);}else{other(q);}use(n);',
])
def test_reset_rule_preserves_observations_and_all_path_initialization(body):
    assert not initialization.variants('void f(int flag){int n;'+body+'}','f')


def test_stack_diagnostics_separate_save_slots_and_local_operands_without_forcing_padding():
    rows=[('s','addiu $sp, $sp, -0xf0','addiu $sp, $sp, -0xe0'),
          ('s','sw $ra, 0xe4($sp)','sw $ra, 0xd4($sp)'),
          (' ','lw $a1, 0xac($sp)','lw $a1, 0xac($sp)'),
          ('s','lw $ra, 0xe4($sp)','lw $ra, 0xd4($sp)'),
          ('s','addiu $sp, $sp, 0xf0','addiu $sp, $sp, 0xe0')]
    report=stack_layout.diagnose('mips',rows)
    assert report['frame_delta']==16 and report['saved_deltas']==[16] and report['local_deltas']==[0]
    assert 'growing the frame alone' in stack_layout.text('mips',rows)
    assert stack_layout.diagnose('ppc',rows) is None


def test_bitfield_helper_is_an_explicit_experiment_not_an_automatic_family():
    source='static inline int scale(short v){return v*2;} int f(short x){return scale(x);}'
    experimental=stack_layout.experimental_variants(source,'f')
    assert experimental and 'signed low:16' in experimental[0][1]
    labels={};choices=draft_repairs.variants(source,'f','mips',labels=labels,limit=None)
    assert 'experimental_bitfield_frame' not in [labels[text] for text in choices]
    assert local_experiments.EXPERIMENTAL==('experimental_bitfield_frame',)
    assert not stack_layout.experimental_variants(source.replace('short v','int v'),'f')


def test_shared_paths_offer_the_new_families_without_game_specific_rules():
    source='void f(char *base,int index){int n;n=0;emit(base+(index+24));use(n);}'
    for arch in ['mips','ppc','x86','arm','thumb']:
        labels={};choices=draft_repairs.variants(source,'f',arch,labels=labels,limit=None)
        assert {'address_association','call_initialization'} <= {labels[text] for text in choices}
    assert {'address_association','call_initialization'} <= set(local_experiments.FAMILIES)
