import hashlib

from decompnet.app import value_trace_repairs as repairs

SOURCE='''void f(S *p, int a, int b) {
 p->value = 125;
 if (a == b) use(a);
}
'''


def test_bundles_two_distinct_small_edits_at_explicit_traced_lines():
    trace=dict(source_sha256=hashlib.sha256(SOURCE.encode()).hexdigest(),header_sha256=hashlib.sha256(b'').hexdigest(),
               values=[dict(aliases=['v0'],source_lines=[2])])
    rows=[('r','move $v1, $a0','move $v0, $a0')]
    result=repairs.variants(SOURCE,'f',trace,rows)
    assert result and len(result)<=repairs.LIMIT
    assert all('repair_store_value' in s and 'b == a' in s for f,s in result)
    assert not repairs.variants(SOURCE,'f',trace,rows,'different header')
    assert not repairs.variants(SOURCE,'f',None,rows)


def test_inert_trace_does_not_enable_broad_priority_without_heldout_approval(monkeypatch):
    from decompnet.app import (
        draft_repairs,
        first_difference,
        iterative_repairs,
        normalization_repairs,
        stack_padding_repairs,
    )
    def candidates(s,n,a,asm,flags,labels,**kw):labels['ordinary']='ordinary';return ['ordinary']
    monkeypatch.setattr(draft_repairs,'variants',candidates)
    monkeypatch.setattr(draft_repairs.affine_operand_repairs,'operand_variants',lambda *a:[])
    monkeypatch.setattr(stack_padding_repairs,'grow',lambda *a:[])
    monkeypatch.setattr(normalization_repairs,'frame_variants',lambda *a:[])
    calls=[]
    monkeypatch.setattr(first_difference,'order',lambda s,p,r:calls.append(1) or p)
    fn=dict(name='f',asm='')
    assert iterative_repairs.targeted_pool(SOURCE,{},fn,'mips',[])==[('ordinary','ordinary')]
    assert not calls
    iterative_repairs.targeted_pool(SOURCE,dict(broad_focus_approved=True),fn,'mips',[])
    assert calls==[1]
    iterative_repairs.targeted_pool(SOURCE,dict(broad_focus_approved=True),fn,'mips',[],focused=False)
    assert calls==[1]
