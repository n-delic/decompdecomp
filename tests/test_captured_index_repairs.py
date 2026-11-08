import pytest

from decompnet.app.captured_index_repairs import variants

CASES = [
    ('u32 i; i=x; p[i * 4]=7;', 'dead_index_scale', '[(i *= 4)]'),
    ('int *v; int *r; v=*(int *volatile *)q; r=v; r[0]=7;', 'dead_pointer_clear', 'r=v;\nv = 0;'),
    ('int i; i=4; p[a[0]]=7;', 'store_index_reuse', 'i = a[0];'),
    ('int i; i=0; do { p[i]=7; i++; } while(i<5);', 'constant_count_for', 'for (i = 0; i < 5; i++)'),
    ('u32 a; u32 b; a=x; b=a & -(x & 1U); p[0]=b;', 'split_mask_assignment', 'b = a;\nb = b & -(x & 1U);'),
]


@pytest.mark.parametrize('body,family,expected', CASES)
def test_captured_shapes(body, family, expected):
    source = 'void demo(int x,int *p,int *a,int **q){'+body+'}\nint neighbour(void){return 7;}'
    proposals = variants(source, 'demo')
    assert any(f == family and expected in text for f, text in proposals)
    assert all(text.endswith('int neighbour(void){return 7;}') for _, text in proposals)
    assert len(proposals) <= 24


@pytest.mark.parametrize('body,family', [
    ('u32 i; i=x; p[i*4]=7; use(i);', 'dead_index_scale'),
    ('u32 i; i=x; do {p[i*4]=7;}while(i);', 'dead_index_scale'),
    ('u32 i; i=x; escape(&i); p[i*4]=7;', 'dead_index_scale'),
    ('u32 i; i=x; p[i*4]=i;', 'dead_index_scale'),
    ('int i; i=x; p[i*4]=7;', 'dead_index_scale'),
    ('int *v; v=p; escape(&v); use(v);', 'dead_pointer_clear'),
    ('int *v; v=p; do {use(v);}while(x);', 'dead_pointer_clear'),
    ('volatile int *v; v=p; use(v);', 'dead_pointer_clear'),
    ('int i; p[a[0]]=7; if(x) use(i);', 'store_index_reuse'),
    ('int i; escape(&i); p[a[0]]=7;', 'store_index_reuse'),
    ('int i; if(x) p[a[0]]=7;', 'store_index_reuse'),
    ('int i; i=0; do {if(x) continue; p[i]=7; i++;}while(i<5);', 'constant_count_for'),
    ('int i; i=0; do {i+=2; i++;}while(i<5);', 'constant_count_for'),
    ('int i; i=0; do {use(i); i++;}while(i<0);', 'constant_count_for'),
    ('int i; i=0; do {escape(&i); i++;}while(i<5);', 'constant_count_for'),
    ('int a; u32 b; a=x; b=a & -(x&1);', 'split_mask_assignment'),
    ('u32 a; u32 b; a=x; b=a & -(b&1);', 'split_mask_assignment'),
    ('u32 a; u32 b; a=x; b=a & get();', 'split_mask_assignment'),
])
def test_unsafe_shapes(body, family):
    assert family not in {f for f, _ in variants('void demo(int x,int *p,int *a){'+body+'}', 'demo')}


def test_macro_inputs_are_opaque():
    source = 'void demo(int *p){u32 i;i=1;p[i*4]=7;}'
    assert not variants(source, 'demo', '#define i shared')
    assert not variants(source.replace('p[i*4]=7;', 'MACRO(p[i*4]);'), 'demo')


def test_pointer_clear_waits_for_all_branch_successors():
    source = 'void demo(int x,int *p){int *v; v=p; if(x) use(v); else use(v); use(v);}'
    proposals = [text for f,text in variants(source,'demo') if f == 'dead_pointer_clear']
    assert proposals
    assert all('else use(v); use(v);\nv = 0;' in text for text in proposals)


def test_generated_shapes_compile_to_independent_controls(tmp_path):
    from decompnet import toolchains
    from decompnet.compiler import compile_source
    from decompnet.elfobj import extract_function
    from decompnet.project import compiler_config

    if not toolchains.installed('gcc-2.7.2-kmc'):
        pytest.skip('needs pinned compiler')
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O2']}})
    header = 'typedef unsigned int u32;'
    for body, family, expected in CASES:
        before = 'void demo(int x,int *p,int *a,int **q){'+body+'}'
        controls = {
            'dead_index_scale': body.replace('i * 4', '(i *= 4)'),
            'dead_pointer_clear': body.replace('r=v;', 'r=v; v=0;'),
            'store_index_reuse': body.replace('p[a[0]]=7;', 'i=a[0]; p[i]=7;'),
            'constant_count_for': 'int i; for(i=0;i<5;i++){p[i]=7;}',
            'split_mask_assignment': body.replace('b=a & -(x & 1U);', 'b=a; b=b & -(x & 1U);'),
        }
        target = compile_source(cfg, header, 'void demo(int x,int *p,int *a,int **q){'+controls[family]+'}')
        assert target.ok, target.log
        code = extract_function(target.obj, 'demo').code
        candidates = [compile_source(cfg, header, text) for f,text in variants(before,'demo') if f == family]
        assert candidates and all(c.ok for c in candidates), [c.log for c in candidates]
        assert any(extract_function(c.obj,'demo').code == code for c in candidates)


def test_index_reuse_does_not_resurrect_out_of_scope_local_or_convert_width():
    for body in ('{int i; i=1;} p[a[0]]=7;', 'u32 i; i=1; p[a[0]]=7;'):
        assert 'store_index_reuse' not in {f for f,_ in variants('void demo(int *p,int *a){'+body+'}', 'demo')}


def test_unused_header_macros_do_not_disable_plain_c_repairs():
    assert variants('void demo(int *p){u32 i;i=1;p[i*4]=7;}', 'demo', '#define UNUSED 1')
