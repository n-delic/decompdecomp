"""Homemade call pairs exercise lexical scope and evaluation without private game data."""
import shutil
import subprocess

import pytest

from decompnet.app import call_lifetime_repairs as repairs
from decompnet.app import draft_repairs, permuter_focus, repair_ranking

SOURCE = '''void f(int condition, int a, int b) {
 int x;
 if (condition) { x = a; } else { x = b; }
 draw(x, 1);
 draw(x, 2);
}'''


def test_local_blocks_and_staging_are_bounded_and_available_to_all_supported_architectures():
    proposals = repairs.variants(SOURCE,'f')
    assert set(f for f,_ in proposals) == set(repairs.FAMILIES)
    assert len(proposals) <= repairs.LIMIT
    for arch in ('mips','ppc','x86','arm','thumb'):
        labels = {}
        draft_repairs.variants(SOURCE,'f',arch,labels=labels,limit=None)
        assert set(repairs.FAMILIES) <= set(labels.values())
    rows = [('r','jalr $t9','jalr $t8')]
    assert permuter_focus.classify(rows) == 'register'
    assert repair_ranking.order(['constant_local',*repairs.FAMILIES],rows)[0] == 'combined_call_lifetimes'


@pytest.mark.parametrize('source',[
    SOURCE.replace('x = a;', 'x = x + a;'),
    SOURCE.replace('if (condition)', 'if (x)'),
    SOURCE.replace('int x;', 'volatile int x;'),
    SOURCE.replace('draw(x, 2);', 'draw(&x, 2);'),
    SOURCE.replace('x = a;', 'goto done; x = a;')+'\ndone:;',
    SOURCE.replace('draw(x, 2);', 'if (a) draw(x, 2);'),
])
def test_ambiguous_or_escaped_tail_is_not_duplicated(source):
    assert not repairs.tails(source,'f')


def test_comments_and_other_definitions_are_preserved():
    source = SOURCE.replace('draw(x, 1);','/* x must stay spelled x */ draw(x, 1);')+'\nvoid other(void) { int x; x = 4; }'
    for _,candidate in repairs.tails(source,'f'):
        assert '/* x must stay spelled x */' in candidate
        assert candidate.endswith('void other(void) { int x; x = 4; }')


@pytest.mark.parametrize('args',['x++, x', 'read(), x', '*p, x', 'obj->x, x', 'x, 0xffffffff', 'x, 1U'])
def test_staging_does_not_choose_an_order_for_side_effects_or_guess_types(args):
    assert not repairs.staged('void f(void) { int x; x = 1; draw('+args+'); }','f')


def test_every_proposal_preserves_call_values_and_order_with_a_host_compiler(tmp_path):
    cc = shutil.which('cc')
    if not cc:
        pytest.skip('A host C compiler is needed for the synthetic execution check.')
    prefix = '#include <stdio.h>\nvoid draw(int x, int y) { printf("%d,%d;",x,y); }\n'
    suffix = '\nint main(void) { int c,a,b; for(c=0;c<2;c++) for(a=-3;a<4;a++) for(b=-3;b<4;b++) f(c,a,b); return 0; }'
    outputs = []
    for i,source in enumerate([SOURCE]+[s for _,s in repairs.variants(SOURCE,'f')]):
        path = tmp_path/f'case{i}.c';exe = tmp_path/f'case{i}'
        path.write_text(prefix+source+suffix)
        subprocess.run([cc,'-std=c89','-Werror',str(path),'-o',str(exe)],check=True,capture_output=True)
        outputs.append(subprocess.check_output([str(exe)]))
    assert outputs[0] and all(output == outputs[0] for output in outputs)


def test_known_macros_are_not_treated_as_ordinary_calls():
    header = '#define draw(x,y) record(#x, y)'
    assert not repairs.variants(SOURCE,'f',header)


def test_agents_get_focused_register_advice_without_a_false_verdict():
    from decompnet.app.source_diagnostics import hints
    notes = ' '.join(hints(SOURCE,'f',rows=[('r','move $s0, $a0','move $s1, $a0')]))
    assert 'combined_call_lifetimes' in notes and 'not an exact verdict' in notes
    assert 'combined_call_lifetimes' not in ' '.join(hints(SOURCE,'f',rows=[('i','li $a0, 1','li $a0, 2')]))


def test_staging_does_not_infer_types_from_out_of_scope_locals():
    assert not repairs.staged('void f(void *x) { { int x; x=1; } draw(x,1); }','f')
    assert not repairs.staged('void f(void) { { int x; x=1; } draw(x,1); }','f')
    assert not repairs.staged('void f(void) { draw(x,1); { int x; x=1; } }','f')
