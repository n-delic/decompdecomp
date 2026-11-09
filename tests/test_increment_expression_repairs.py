import pytest

from decompnet.app.increment_expression_repairs import variants


@pytest.mark.parametrize('body,expected,family', [
    ('s16 i; s32 v; i=2; i += 1; v = i; return v;', 'v = ++i;', 'prefix_increment_result'),
    ('u16 i; i=2; p->index = i; i = i + 1; return i;', 'p->index = i++;', 'postincrement_store'),
])
def test_increment_shapes(body, expected, family):
    source = 'int demo(struct Node *p) {'+body+'}\nint neighbour(void){return 2;}'
    out = variants(source, 'demo')
    assert any(f == family and expected in s for f,s in out)
    assert all(s.endswith('int neighbour(void){return 2;}') for _,s in out)
    assert len(out) <= 8


@pytest.mark.parametrize('body', [
    's16 i; s32 v; if (p) i+=1; v=i;',
    's16 i; if(p) p->index=i; i+=1;',
    's16 i; s32 v; escape(&i); i+=1; v=i;',
    'volatile s16 i; s32 v; i+=1; v=i;',
    's16 i; i+=1; global=i;',
    's16 i; i+=1; i=i;',
    's16 i; p[i]=i; i+=1;',
    's16 i; p->index=i; use(i); i+=1;',
])
def test_unsafe_or_ambiguous_shapes_are_not_proposed(body):
    assert variants('void demo(struct Node *p){'+body+'}', 'demo') == []


def test_shared_draft_generator_labels_increment_candidates():
    from decompnet.app.draft_repairs import variants as drafts
    labels = {}
    source = 'int demo(void){s16 i; s32 v; i=2; i+=1; v=i; return v;}'
    drafts(source, 'demo', 'mips', labels=labels, limit=200)
    assert 'prefix_increment_result' in labels.values()


def test_generated_shapes_compile_to_independent_controls(tmp_path):
    from decompnet import toolchains
    from decompnet.compiler import compile_source
    from decompnet.elfobj import extract_function
    from decompnet.project import compiler_config

    if not toolchains.installed('gcc-2.7.2-kmc'):
        pytest.skip('needs pinned compiler')
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O2']}})
    header = 'typedef short s16; typedef int s32; struct Node { s16 index; };'
    for before, after, family in (
        ('int demo(int x){s16 i; s32 v; i=x; i+=1; v=i; return v;}',
         'int demo(int x){s16 i; s32 v; i=x; v=++i; return v;}', 'prefix_increment_result'),
        ('int demo(struct Node *p,int x){s16 i; i=x; p->index=i; i+=1; return i;}',
         'int demo(struct Node *p,int x){s16 i; i=x; p->index=i++; return i;}', 'postincrement_store'),
    ):
        target = compile_source(cfg, header, after)
        assert target.ok, target.log
        code = extract_function(target.obj, 'demo').code
        candidates = [compile_source(cfg, header, text) for f,text in variants(before, 'demo') if f == family]
        assert any(c.ok and extract_function(c.obj, 'demo').code == code for c in candidates)
