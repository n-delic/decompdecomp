



from decompnet.app import operand_focus


def test_offset_evidence_is_advisory_and_malformed_feedback_is_ignored():
    evidence=operand_focus.offsets([('r','lh $v0, 0x18($s1)','lh $v1, 0x18($s1)'),(' ','lh $v0, 20($s1)','')])
    assert evidence == {24}
    assert operand_focus.weight('*((short *)(p + 24))', evidence) == 1
    assert operand_focus.weight('a[0]', evidence) == 0
    assert not operand_focus.offsets([['bad']])


def test_focused_operand_clusters_keep_whole_function_alternatives():
    from decompnet.app import affine_operand_repairs
    source='''void f(char *p, char *a, char *b) {
 int scratch; int old;
 *((short *)(p+24)) = *((short *)(a+24)) - *((short *)(b+24));
 *((short *)(p+26)) = *((short *)(a+24)) - *((short *)(b+24));
 *((short *)(p+100)) = *((short *)(a+100)) - *((short *)(b+100));
 }'''
    rows=[('r','lh $v0, 24($a0)','lh $v1, 24($a0)')]
    variants=affine_operand_repairs.operand_variants(source,'f',rows)
    assert variants
    assert '*((short *)(p+100)) = *((short *)(a+100)) - *((short *)(b+100));' in variants[0][1]
    assert any('*((short *)(p+100)) = scratch - old;' in text for _,text in variants)
