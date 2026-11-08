from decompnet.app import affine_operand_repairs as repairs

OPERANDS = '''void f(short *a, short *b, short *out, int *wide) {
 int unused; int old;
 old = 7;
 out[0] = a[0] - b[0];
 out[1] = a[1] - b[1];
 wide[0] = *((short *) a) - *((short *) b);
 wide[1] = *((short *) (a+1)) - *((short *) (b+1));
 old = 8;
 use(old);
}'''


COUNTER = '''void f(void) {
 unsigned i; int stride; int result; int flag = 0;
 for (i = 0, stride = 0; i < 4; i++, stride += 5) {
   if (skip()) goto next;
   result = stride; result += stride;
   if (flag) { use(result); } else { use(result); }
 next: ;
 }
}'''


def test_pairs_reuse_dead_words_and_preserve_signed_wide_loads():
    variants = repairs.operand_variants(OPERANDS, 'f')
    assert variants
    both = variants[0][1]
    assert 'unused = *((short *) a); old = *((short *) b);' in both
    assert 'wide[0] = unused - old;' in both
    assert 'old = 8;' in both


def test_live_scratch_and_backedge_values_are_not_overwritten():
    live = OPERANDS.replace('old = 8;', '')
    assert not repairs.operand_variants(live, 'f')
    loop = OPERANDS.replace('old = 7;', 'old = 7; again: use(old);').replace('old = 8;', 'goto again;')
    assert not repairs.operand_variants(loop, 'f')


def test_escaped_shadowed_volatile_and_side_effectful_loads_are_rejected():
    for source in (OPERANDS.replace('old = 7;', 'take(&old);'),
                   OPERANDS.replace('int old;', 'volatile int old;'),
                   OPERANDS.replace('int old;', 'int old; { int old; }'),
                   OPERANDS.replace('a[0]', 'fetch()').replace('a[1]', 'fetch()')
                   .replace('*((short *) a)', 'fetch()').replace('*((short *) (a+1))', 'fetch()')):
        assert not repairs.operand_variants(source, 'f')


def test_affine_counter_with_internal_goto_and_single_guard_bundles():
    variants = repairs.counter_variants(COUNTER, 'f')
    assert [f for f, _ in variants] == ['affine_counter', 'affine_counter_guard']
    plain, bundled = [s for _, s in variants]
    assert 'for (i = 0; i < 4; i++)' in plain
    assert 'stride = i * 5; result = stride;' in plain
    assert 'goto next;' in bundled and 'if (flag)' not in bundled
    assert bundled.count('use(result);') == 1


def test_affine_rejects_live_out_mutations_overflow_and_incoming_jump():
    bad = [COUNTER.replace('next: ;', 'next: stride++;'),
           COUNTER.replace('next: ;', 'next: i += 2;'),
           COUNTER.replace('next: ;', 'next: take(&(i));'),
           COUNTER.replace('result = stride;', 'Index i; result = stride;'),
           COUNTER.replace('\n}', '\n use(stride);\n}'),
           COUNTER.replace('stride += 5', 'stride += 99999'),
           COUNTER.replace('for (', 'goto next; for ('),
           COUNTER.replace('result = stride;', 'if (stride) use(1); result = stride;'),
           COUNTER.replace('result = stride;', 'again: result = stride;').replace('next: ;', 'next: goto again;')]
    for source in bad:
        # An internal backedge through the insertion point is allowed; that is the safe case.
        if 'again:' in source:
            assert repairs.counter_variants(source, 'f')
        else:
            assert not repairs.counter_variants(source, 'f')


def test_guard_cleanup_is_individual_and_keeps_other_guard():
    source = COUNTER.replace('next: ;', 'if (flag) { use(0); } else { use(0); } next: ;')
    bundled = [s for family, s in repairs.counter_variants(source, 'f') if family == 'affine_counter_guard']
    assert len(bundled) == 2
    assert all(s.count('if (flag)') == 1 for s in bundled)


def test_load_recognition_does_not_narrow_wider_expressions_or_unsigned_arrays():
    assert repairs.narrow_load('(*((short *) ptr))', '')
    assert not repairs.narrow_load('(*((short *) ptr)) + wide', '')
    assert not repairs.narrow_load('a[0]', 'unsigned   short a[3];')
    assert not repairs.narrow_load('a[0]', 'u16 a[3];')
    assert not repairs.narrow_load('a[0]', 'short a[3]; { short a[3]; }')


def test_narrow_store_uses_modular_operands_but_wide_store_remains_signed():
    source = OPERANDS.replace('out[0]', '*((short *) out)').replace('out[1]', '*((short *) (out+1))')
    candidates = repairs.operand_variants(source, 'f')
    assert any('(unsigned short)(a[0])' in text and 'unused = *((short *) a);' in text for _, text in candidates)


def test_generated_operand_candidates_preserve_boundary_values_with_host_compiler(tmp_path):
    import shutil
    import subprocess

    import pytest

    cc = shutil.which('cc')
    if not cc:
        pytest.skip('A host C compiler is needed for synthetic value checks.')
    source = OPERANDS.replace('out[0]', '*((short *) out)').replace('out[1]', '*((short *) (out+1))')
    prefix = '#include <stdio.h>\nvoid use(int x) { (void)x; }\n'
    main = '''int main(void) {
 short values[5] = {-32768,-1,0,1,32767};
 short a[2],b[2],out[2]; int wide[2],i,j;
 for(i=0;i<5;i++) for(j=0;j<5;j++) {
 a[0]=a[1]=values[i]; b[0]=b[1]=values[j]; f(a,b,out,wide);
 printf("%d,%d,%d,%d;",out[0],out[1],wide[0],wide[1]);
 } return 0;
}'''
    outputs = []
    for i, candidate in enumerate([source]+[text for _,text in repairs.operand_variants(source,'f')]):
        path, exe = tmp_path/f'case{i}.c', tmp_path/f'case{i}'
        path.write_text(prefix+candidate+main)
        subprocess.run([cc,'-std=c89','-Werror',str(path),'-o',str(exe)],check=True,capture_output=True)
        outputs.append(subprocess.check_output([str(exe)]))
    assert len(outputs) > 1 and all(output == outputs[0] for output in outputs)


def test_context_with_only_global_guard_is_not_unwrapped():
    source = COUNTER.replace('int flag = 0;', '').replace('void f(void)', 'extern int flag; void f(void)')
    assert all(family != 'affine_counter_guard' for family,_ in repairs.counter_variants(source,'f'))
