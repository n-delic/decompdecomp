"""Synthetic shapes model isolated capture edits without including game source."""

import pytest

from decomprepair import address_association_repairs, branch_layout_repairs, pointer_pair_repairs, shared_offset_repairs


def shared(source):
    return shared_offset_repairs.variants(source, 'demo')


def test_shared_scaled_store_offset():
    source = 'void demo(int *p,int *q,int x){int i;i=x;*(int *)(i*32+(char *)p)=0;p=q;*(int *)(i*32+(char *)p)=0;}'
    proposals = shared(source)
    assert len(proposals) == 1
    assert proposals[0][1].count('i * 32') == 1
    assert proposals[0][1].count('__repair_scaled_offset') == 4
    assert 'p=q;' in proposals[0][1]


@pytest.mark.parametrize('middle', ['i=2;', 'i++;', 'if(x) p=q;', 'touch(&i);', 'touch(p);', 'goto done; done:'])
def test_shared_offset_does_not_cross_writes_or_control(middle):
    source = 'void demo(int *p,int x){int i;i=x;*(int *)(i*32+(char *)p)=0;'+middle+'*(int *)(i*32+(char *)p)=0;}'
    assert not shared(source)


def test_pointer_conditional_field_load_inline():
    source = 'void demo(Node *best){Node *next; next=best->next;if(next!=0) best=next;else use(best);}'
    proposals = pointer_pair_repairs.conditional_loads(source, 'demo')
    assert len(proposals) == 1
    assert 'if (best->next != 0) best = best->next;else use(best);' in proposals[0][1]


@pytest.mark.parametrize('body', [
    'next=best->next;touch(best);if(next!=0) best=next;',
    'next=best->next;if(next!=0) best=next;use(next);',
    'next=best->next;escape(&next);if(next!=0) best=next;',
    'next=best->next;if(next!=0){touch(best);best=next;}',
])
def test_pointer_load_inline_rejects_changed_read_context(body):
    assert not pointer_pair_repairs.conditional_loads('void demo(Node *best){Node *next;'+body+'}', 'demo')


def test_volatile_field_aliases_are_not_reloaded():
    source = 'void demo(Node *best){Node *next;next=best->next;if(next!=0)best=next;}'
    assert not pointer_pair_repairs.conditional_loads(source, 'demo', 'typedef struct {volatile int value; void *next;} Node;')


def test_array_subscript_reassociation_in_owned_inline_helper():
    prefix = 'static inline int flag(char *g,int i){return g[i+49]&8;}\n'
    source = prefix+'int demo(char *g,int i){return flag(g,i);}\nint neighbour(char *g,int i){return g[i+49];}'
    proposals = address_association_repairs.variants(source, 'demo')
    assert any('(g + i)[49]' in text for _,text in proposals)
    assert all(text.endswith('int neighbour(char *g,int i){return g[i+49];}') for _,text in proposals)


def test_branch_trailing_stores():
    source = 'void demo(Node *p,int x){int a;if(x){a=get();}else{a=other();}p->a=p->b=a;}'
    proposals = branch_layout_repairs.store_tails(source, 'demo')
    assert len(proposals) == 1
    assert proposals[0][1].count('p->a=p->b=a;') == 2
    assert 'a=get();\np->a=p->b=a;' in proposals[0][1]


@pytest.mark.parametrize('arms', [
    'if(x){return;}else{a=1;}',
    'if(x){int a;a=1;}else{a=2;}',
    'if(x){Node *p;p=q;}else{a=2;}',
    'if(x){goto end;}else{a=2;}',
    'if(x){int p[2];p[0]=1;}else{a=2;}',
])
def test_branch_store_skips_transfers_and_shadowed_names(arms):
    source = 'void demo(Node *p,Node *q,int x){int a;'+arms+'p->a=p->b=a;}'
    assert not branch_layout_repairs.store_tails(source, 'demo')


def test_shared_draft_integration():
    from decompnet.app.draft_repairs import AGENT_FAMILIES, variants
    assert {'shared_scaled_offset','pointer_conditional_load_inline','branch_store_tail'} <= set(AGENT_FAMILIES)
    examples = [
        ('void demo(int *p,int x){int i;i=x;*(int *)(i*32+(char *)p)=0;*(int *)(i*32+(char *)p)=0;}', 'shared_scaled_offset'),
        ('void demo(Node *best){Node *next;next=best->next;if(next!=0)best=next;}', 'pointer_conditional_load_inline'),
        ('int demo(char *g,int i){return g[i+49];}', 'address_association'),
        ('void demo(Node *p,int x){int a;if(x){a=1;}else{a=2;}p->a=a;}', 'branch_store_tail'),
    ]
    for source, family in examples:
        labels = {}
        variants(source, 'demo', 'mips', labels=labels, limit=None)
        assert family in labels.values()


def test_independent_compiler_controls(tmp_path):
    from decompnet import toolchains
    from decompnet.compiler import compile_source
    from decompnet.elfobj import extract_function
    from decompnet.project import compiler_config
    if not toolchains.installed('gcc-2.7.2-kmc'):
        pytest.skip('needs pinned compiler')
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O2']}})
    header = 'typedef struct Node {struct Node *next; int a,b;} Node;'
    examples = [
        (shared_offset_repairs.variants,
         'void demo(int *p,int *q,int x){int i;i=x;*(int *)(i*32+(char *)p)=0;p=q;*(int *)(i*32+(char *)p)=0;}',
         'void demo(int *p,int *q,int x){int off;off=x*32;*(int *)((char *)p+off)=0;p=q;*(int *)((char *)p+off)=0;}'),
        (pointer_pair_repairs.conditional_loads,
         'Node *demo(Node *p){Node *next;next=p->next;if(next!=0)p=next;return p;}',
         'Node *demo(Node *p){if(p->next!=0)p=p->next;return p;}'),
        (address_association_repairs.variants,
         'static inline int helper(char *p,int i){return p[i+49];} int demo(char *p,int i){return helper(p,i);}',
         'static inline int helper(char *p,int i){return (p+i)[49];} int demo(char *p,int i){return helper(p,i);}'),
        (branch_layout_repairs.store_tails,
         'int first(void);int second(void);void demo(Node *p,int x){int a;if(x){a=first();}else{a=second();}p->a=p->b=a;}',
         'int first(void);int second(void);void demo(Node *p,int x){int a;if(x){a=first();p->a=p->b=a;}else{a=second();p->a=p->b=a;}}'),
    ]
    for generate, source, control in examples:
        result = compile_source(cfg, header, control)
        assert result.ok, result.log
        target = extract_function(result.obj, 'demo').code
        candidates = [compile_source(cfg, header, text) for _,text in generate(source,'demo')]
        assert candidates and all(c.ok for c in candidates), [c.log for c in candidates]
        assert any(extract_function(c.obj,'demo').code == target for c in candidates)


def test_narrow_offset_promotes_and_resolves_shadowed_loop_index():
    source = 'void demo(int *p,int x){unsigned char i;i=x;{unsigned char i;i=0;use(i);}*(int *)(i*32+(char *)p)=0;*(int *)(i*32+(char *)p)=0;}'
    proposals = shared(source)
    assert len(proposals) == 1
    assert 'int __repair_scaled_offset;' in proposals[0][1]
    assert '{unsigned char i;i=0;use(i);}' in proposals[0][1]


def test_conditional_pointer_with_earlier_logical_and_use():
    source = 'Node *demo(Node *best,int x){Node *next;next=best;if(x && ((next=best->next,next!=0)))use(next);next=best->next;if(next!=0)best=next;return best;}'
    proposals = pointer_pair_repairs.conditional_loads(source, 'demo')
    assert proposals and 'if (best->next != 0) best = best->next;' in proposals[0][1]
    assert not pointer_pair_repairs.conditional_loads(source.replace('use(next);', 'escape(&(next));'), 'demo')


def test_helper_reassociation_follows_wrapper_macro_and_preserves_it():
    source = 'static inline int helper(char *p,int i){return p[i+49];}\n#define FLAG(i) helper(p,i)\nint demo(char *p,int i){return FLAG(i);}'
    proposals = address_association_repairs.variants(source, 'demo')
    assert proposals and '(p + i)[49]' in proposals[0][1]
    assert '#define FLAG(i) helper(p,i)' in proposals[0][1]


def test_branch_store_works_outside_protected_macro_regions():
    from decompnet.app.draft_repairs import variants
    source = 'void demo(Node *p,int x){int a;\n#define alias x\na=alias;if(x){a=1;}else{a=2;}p->a=a;}'
    labels = {}
    variants(source,'demo','mips',labels=labels,limit=None)
    candidates = [s for s,f in labels.items() if f == 'branch_store_tail']
    assert candidates and all('#define alias x\na=alias;' in s for s in candidates)


def test_unknown_null_macro_and_arm_comma_declarations_are_opaque():
    source = 'void demo(Node *best){Node *next;next=best->next;if(next!=NULL)best=next;}'
    assert not pointer_pair_repairs.conditional_loads(source, 'demo', '#define NULL changing()')
    arms = 'if(x){int spare,*p;spare=1;}else{a=2;}'
    assert not branch_layout_repairs.store_tails('void demo(Node *p,int x){int a;'+arms+'p->a=a;}', 'demo')
