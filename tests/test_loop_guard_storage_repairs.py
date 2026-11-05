"""Synthetic source fixtures: no extracted target code or data."""
import shutil
import subprocess

from decompnet.app import draft_repairs, loop_guard_storage_repairs

SOURCE = '''typedef signed char s8;
typedef unsigned char u8;
typedef int s32;
extern u8 count;
extern int read_input(u8);
int sample(void) {
  s32 scratch;
  s32 result;
  s32 copy;
  s8 index;
  index = 0;
  copy = index;
first:
  if (index < count) {
    if ((scratch = result == copy)) { result = read_input(copy); }
    index++;
    if (!result) goto first;
  }
  index = 0;
second:
  if (index < count) {
    if ((scratch = result == index)) {
      scratch = read_input(index);
      result |= scratch;
    }
    index++;
    if (!result) goto second;
  }
  return result;
}
'''
# A small invented instruction shape establishes the changing byte index as a call argument.
EVIDENCE = '''sll $v0, $s1, 24
andi $s0, $s1, 0xFF
addu $a0, $s0, $zero
jal input
addiu $s1, $s1, 0x1
'''


def test_sequenced_guard_and_counter_copy():
    variants = loop_guard_storage_repairs.variants(SOURCE, 'sample', EVIDENCE)
    assert len(variants) == 3
    text = variants[1][1]
    assert 'read_input(copy)' not in text
    assert '__repair_guard = count;\n  if ((__repair_guard = index < __repair_guard))' in text
    assert '(__repair_guard = result == index)' in text
    labels = {}
    broad = draft_repairs.variants(SOURCE, 'sample', 'mips', EVIDENCE, labels=labels, limit=40)
    assert text in broad
    # This checks C90 sequence rules independently of the source generator.
    if compiler := shutil.which('cc'):
        subprocess.run([compiler, '-x', 'c', '-std=c90', '-Werror=sequence-point', '-fsyntax-only', '-'],
                       input=text, text=True, capture_output=True, check=True)


def test_stale_copy_needs_complete_counter_argument_evidence():
    assert not loop_guard_storage_repairs.variants(SOURCE, 'sample')
    assert not loop_guard_storage_repairs.variants(SOURCE, 'sample', EVIDENCE.replace('$a0', '$a1'))
    assert not loop_guard_storage_repairs.variants(SOURCE, 'sample', EVIDENCE.replace('sll', 'srl'))


def test_escaped_shadowed_qualified_and_member_alias_are_skipped():
    for source in (SOURCE.replace('  index = 0;', '  escape(&index);\n  index = 0;', 1),
                   SOURCE.replace('  s8 index;', '  s8 index;\n  { s8 index; }'),
                   SOURCE.replace('extern u8 count;', 'extern volatile u8 count;'),
                   SOURCE.replace('  s8 index;', '  s8 index;\n  void *count;'),
                   SOURCE.replace('read_input(copy)', 'read_input(object.copy)'),
                   SOURCE.replace('read_input(copy)', 'copy(result)')):
        assert not loop_guard_storage_repairs.variants(source, 'sample', EVIDENCE)


def test_live_equality_result_cannot_be_redirected():
    source = SOURCE.replace('      scratch = read_input(index);', '      result |= scratch;\n      scratch = read_input(index);')
    assert len(loop_guard_storage_repairs.variants(source, 'sample', EVIDENCE)) == 2


def test_guard_must_be_a_complete_statement():
    for prefix in ('if (result) ', 'else ', 'while (result) '):
        source = SOURCE.replace('  if (index < count)', '  '+prefix+'if (index < count)', 1)
        assert not loop_guard_storage_repairs.variants(source, 'sample', EVIDENCE)


def test_equality_result_read_in_condition_or_after_loop_is_preserved():
    for source in (SOURCE.replace('if ((scratch = result == index))', 'if ((scratch = result == index) && scratch)'),
                   SOURCE.replace('  return result;', '  return result | scratch;')):
        assert len(loop_guard_storage_repairs.variants(source, 'sample', EVIDENCE)) == 2


def test_mutated_or_escaped_alias_and_binding_macros_are_skipped():
    for source in (SOURCE.replace('read_input(copy)', 'read_input(copy++)'),
                   SOURCE.replace('read_input(copy)', 'read_input(&copy)'),
                   '#define count replacement\n'+SOURCE,
                   '#define copy replacement\n'+SOURCE):
        assert not loop_guard_storage_repairs.variants(source, 'sample', EVIDENCE)


def test_header_macro_and_volatile_typedef_are_skipped():
    for header in ('#define count replacement', '#define copy replacement',
                   '#define scratch replacement', 'typedef volatile unsigned char u8;'):
        variants = loop_guard_storage_repairs.variants(SOURCE, 'sample', EVIDENCE, header)
        if 'scratch' in header:
            assert len(variants) == 2
        else:
            assert not variants
    assert not loop_guard_storage_repairs.variants('#if FEATURE\n'+SOURCE+'#endif\n', 'sample', EVIDENCE)


def test_targeted_guard_family_is_registered_and_discovered():
    from types import SimpleNamespace

    from decompnet.app import repair_generation

    assert set(loop_guard_storage_repairs.FAMILIES) <= set(draft_repairs.AGENT_FAMILIES)
    with repair_generation.session(SimpleNamespace(), 'synthetic', None, {'loop_guard_storage'}):
        labels = {}
        variants = draft_repairs.variants(SOURCE, 'sample', 'mips', EVIDENCE, labels=labels, limit=8)
    assert any(labels[v] == 'loop_guard_storage' and '(__repair_guard = result == index)' in v for v in variants)
