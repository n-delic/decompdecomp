from decompnet.app.expression_recipes import apply, template
from decompnet.app.repair_ablation import minimize
from decompnet.app.value_range_repairs import bounds, variants


def test_ablation_separates_edits_on_same_line():
    result=minimize('int a=1; int b=1;', 'int a=2; int b=2;',lambda s:'b=2' in s)
    assert result['source']=='int a=1; int b=2;'


def test_tokenization_keeps_quoted_text_and_comments_intact():
    result=minimize('char *s="old value"; /* old */', 'char *s="new value"; /* new */',lambda s:'"new value"' in s)
    assert result['source']=='char *s="new value"; /* old */'


def test_recipe_binds_new_names_but_requires_unique_context():
    recipe=template('int f(){return x + y;}', 'int f(){return y + x;}')
    assert recipe
    assert apply('int g(){return left + right;}',recipe)=='int g(){return right + left;}'
    assert apply('int g(){return left * right;}',recipe) is None


def test_recipe_rejects_new_names_calls_literals_and_control_flow():
    for replacement in ('other + y','helper(x)','x(y)','x + 42','x; return y'):
        assert template('int f(){return x + y;}',f'int f(){{return {replacement};}}') is None


def test_range_proof_propagates_bounds_and_rejects_overflow():
    assert bounds('(x & 255) >> 1',{'x':(0,65535)})==(0,127)
    assert bounds('x + 1',{'x':(2147483647,2147483647)}) is None
    assert bounds('x >> 1',{'x':(-1,10)}) is None
    assert bounds('unknown()',{}) is None
    assert bounds('0u - 1',{}) is None


def test_range_repair_handles_multiple_assignments():
    proposals=variants('int f(){int n; n=1000; n=n>>1; return n;}','f')
    assert len(proposals)==1 and 'short n;' in proposals[0][1]
    assert not variants('int f(){int n; n=1000; n=99999; return n;}','f')


def test_range_repair_rejects_escapes_branches_unknown_values_and_macros():
    for body in ('n=other; return n;', 'n=1; use(&n); return n;', 'if(x) n=1; return n;', 'n=1; return sizeof(n);'):
        assert not variants('int f(){int n;'+body+'}','f')
    assert not variants('int f(){int n; n=1;return n;}','f','#define n other')


def test_range_analysis_uses_parameter_types_and_masked_values():
    proposals=variants('int f(int input){int n; n=(input & 65535)>>1; return n;}','f')
    assert len(proposals)==1
    assert not variants('int f(int input){int n; n=input >> 1; return n;}','f')


def test_only_exact_connected_edges_train_profile_scoped_expression_recipes():
    from decompnet.app import expression_recipes, repair_sequences
    from tests.test_permute import FakeNet
    net=FakeNet();proj=net.project_row('game');fn=net.function()
    before='int f(){return x + y;}'; after='int f(){return y + x;}'
    lineage=repair_sequences.Lineage(net,proj,fn,before,[])
    lineage.retain(before,after,'reorder')
    assert expression_recipes.variants(net,lineage.profile,before)==[]
    lineage.retain('unknown',after,'reorder',exact=True)
    assert expression_recipes.variants(net,lineage.profile,before)==[]
    lineage.retain(before,after,'reorder',exact=True)
    assert expression_recipes.variants(net,lineage.profile,'int g(){return a + b;}')==['int g(){return b + a;}']
    assert expression_recipes.variants(net,'other-profile',before)==[]


def test_pinned_compiler_preserves_output_for_proven_mask_narrowing(tmp_path):
    import io

    import pytest
    from elftools.elf.elffile import ELFFile

    from decompnet import toolchains
    from decompnet.compiler import compile_source
    from decompnet.project import compiler_config
    if toolchains.installed('gcc-2.7.2-kmc') is None:
        pytest.skip('Needs pinned KMC compiler')
    config=compiler_config(tmp_path,{'compiler':{'toolchain':'gcc-2.7.2-kmc','flags':[
        '-G0','-mips3','-mgp32','-mfp32','-O2','-fno-strength-reduce','-msoft-float']}})
    source='int f(int input){int n; n=(input & 65535)>>1; return n;}'
    outputs=[]
    for candidate in (source,variants(source,'f')[0][1]):
        built=compile_source(config,'',candidate)
        assert built.ok,built.log
        outputs.append(ELFFile(io.BytesIO(built.obj)).get_section_by_name('.text').data())
    assert outputs[0]==outputs[1]


def test_recipe_rejects_ambiguous_repeated_context():
    source='int f(){return x + y;}'
    recipe=template(source,'int f(){return y + x;}')
    assert apply(source+'\nint g(){return a + b;}',recipe) is None


def test_new_recipe_invalidates_only_its_profile_frontier():
    from decompnet.app import expression_recipes
    from tests.test_permute import FakeNet
    net=FakeNet()
    before=expression_recipes.signature(net,'compiler-a')
    other=expression_recipes.signature(net,'compiler-b')
    expression_recipes.learn(net,'compiler-a','int f(){return x + y;}','int f(){return y + x;}')
    assert expression_recipes.signature(net,'compiler-a')!=before
    assert expression_recipes.signature(net,'compiler-b')==other
