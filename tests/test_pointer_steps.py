import pytest

from decompnet.app.pointer_steps import variants

SOURCE = '''typedef struct { int value; char extra[44]; } Item;
void f(Item *p) {
    /* p + 1 and p++ stay in this comment */
    use(p + 1);
    while (p->value)
        p++;
}
'''


def inputs(old=48, new=4, big=True, base=4):
    order = 'big' if big else 'little'
    op = (9 << 26) | (base << 21) | (base << 16)
    def words(n):
        return b''.join((op | (v & 65535)).to_bytes(4, order) for v in (n, -n))
    return {'candidate_target': words(old).hex(), 'candidate_relocs': '[]'}, {'target': words(new), 'relocs': '[]'}


@pytest.mark.parametrize('big', [True, False])
def test_agreeing_forward_and_backward_steps_propose_local_byte_arithmetic(big):
    report, target = inputs(big=big)
    result = variants(SOURCE, report, target, big)
    assert len(result) == 1
    assert 'use(((Item *)((char *)p + 4)))' in result[0]
    assert 'p = ((Item *)((char *)p + 4));' in result[0]
    assert 'char extra[44]' in result[0]
    assert '/* p + 1 and p++ stay in this comment */' in result[0]


@pytest.mark.parametrize('source', [
    SOURCE.replace('        p++;', '        value = p++;'),
    SOURCE.replace('        p++;', '        value =\n        p++;'),
    SOURCE.replace('use(p + 1);', 'use(p + 2);'),
    SOURCE.replace('p + 1);', 'p /* preserve */ + 1);'),
    SOURCE.replace('typedef struct { int value; char extra[44]; } Item;', ''),
    SOURCE.replace('int value;', 'int value; union { int x; } u;'),
])
def test_ambiguous_or_nonstandalone_source_steps_are_skipped(source):
    r, t = inputs()
    assert variants(source, r, t, True) == []


@pytest.mark.parametrize('base', [0, 28, 29, 30])
def test_global_stack_and_frame_adjustments_do_not_establish_stride(base):
    r, t = inputs(base=base)
    assert variants(SOURCE, r, t, True) == []


def test_incomplete_conflicting_relocated_or_changed_register_evidence_is_skipped():
    r, t = inputs()
    r['candidate_relocs'] = '[[4,6,0]]'
    assert variants(SOURCE, r, t, True) == []
    r, t = inputs()
    t['target'] = t['target'][:4] + t['target'][:4]
    assert variants(SOURCE, r, t, True) == []
    r, t = inputs()
    t['target'] = (0x24a50004).to_bytes(4, 'big') + t['target'][4:]
    assert variants(SOURCE, r, t, True) == []
    r, t = inputs()
    t['target'] = t['target'][:4]
    assert variants(SOURCE, r, t, True) == []


def test_shared_address_repair_pipeline_includes_stride_proposals():
    from decompnet.app.address_repairs import variants as repairs
    r, t = inputs()
    assert any('((char *)p + 4)' in s for s in repairs(SOURCE, r, t, True, set()))
