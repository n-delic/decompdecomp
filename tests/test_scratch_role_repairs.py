"""Homemade disjoint phases; private game inputs are supplied only by an opt-in replay."""









import pytest

from decompnet.app import scratch_role_repairs as repairs

HEADER='typedef unsigned char u8; typedef unsigned short u16; typedef int s32;'


SOURCE='''int f(u8 *object, int key) {
 int answer;
 u8 *link;
 if ((*((u16 *)(object + 6))) == 3) {
   link = *((u8 **)(object + 12));
   key = read_key(key);
   answer = *(int *)(link + 8);
   if (key != 1234) { return 1; }
   answer = answer ^ 1234;
   goto done;
 }
 answer = key ^ 1234;
 done:
 return answer != 0;
}'''


ROWS=[(' ','lhu $v1, 6($a0)','lhu $v1, 6($a0)'),
      ('r','lw $v1, 12($a0)','lw $v0, 12($a0)'),
      ('r','lw $a1, 8($v1)','lw $a1, 8($v0)'),
      ('r','addiu $v1, $zero, 1234','addiu $v0, $zero, 1234')]


def variants(source=SOURCE,header=HEADER,arch='mips'):
    return repairs.variants(source,'f',header,arch)


def test_combined_candidate_stages_actual_reads_and_constant_only_after_pointer_dies():
    candidates=variants()
    assert len(candidates)<=repairs.LIMIT
    combined=candidates[0][1]
    assert 'repair_scratch = *((u16 *)(object + 6));' in combined
    assert 'repair_scratch = (int)(*((u8 **)(object + 12)));' in combined
    assert '((u8 *)repair_scratch) + 8' in combined
    assert 'repair_scratch = 1234;\n  if (key != repair_scratch)' in combined
    assert 'answer = answer ^ repair_scratch;' in combined
    assert 'answer = key ^ 1234;' in combined  # other arm is not dominated
    assert combined.count('*((u16 *)')==SOURCE.count('*((u16 *)')
    assert combined.count('read_key(key)')==1
    assert 'goto done;' in combined and 'return answer != 0;' in combined
    assert not any(x in combined for x in ('asm','register ','long long'))


def test_partial_improvement_can_resume_sentinel_phase_without_restarting_selector():
    partial=repairs.pointer_phases(SOURCE,'f',HEADER,'mips')[0][1]
    assert 'if (key != 1234)' in partial
    continuation=repairs.sentinel_phases(partial,'f',HEADER,'mips')
    assert continuation and continuation[0] in variants()
    assert continuation[0][1].count('repair_scratch = *((u16 *)')==1


@pytest.mark.parametrize('change',[
    ('link = *((u8 **)(object + 12));','use(link); link = *((u8 **)(object + 12));'),
    ('key = read_key(key);','use(&link); key = read_key(key);'),
    ('key = read_key(key);','{ int link; link = 1; use(link); } key = read_key(key);'),
    ('key = read_key(key);','++link; key = read_key(key);'),
    ('key = read_key(key);','++(link); key = read_key(key);'),
    ('key = read_key(key);','use(link = object); key = read_key(key);'),
    ('key = read_key(key);','link += 1; key = read_key(key);'),
    ('if ((*((u16 *)(object + 6))) == 3) {','if (key) if ((*((u16 *)(object + 6))) == 3) {'),
    ('*((u16 *)(object + 6))','*((u16 *)(get_object() + 6))'),
    ('*((u16 *)(object + 6))','*((u32 *)(object + 6))'),
    ('*((u16 *)(object + 6))','*((volatile u16 *)(object + 6))'),
    ('u8 *link;','u8 *link = object;'),
])
def test_unsafe_pointer_or_selector_shapes_are_declined(change):
    assert not repairs.pointer_phases(SOURCE.replace(*change),'f',HEADER,'mips')


def test_escaping_macros_observers_unknown_abi_and_alias_widths_are_declined():
    assert not variants(arch='unknown')
    assert not variants(header=HEADER.replace('unsigned char u8','unsigned long long u8'))
    assert not variants(header=HEADER+'\n#define link other')
    assert not variants(SOURCE.replace('key = read_key(key);','READ(link);'),HEADER+'\n#define READ(v) use(v)')
    assert not variants(SOURCE.replace('int answer;','int answer; int line = __LINE__;'))


def test_live_or_redefined_scratch_cannot_be_overwritten_by_sentinel():
    partial=repairs.pointer_phases(SOURCE,'f',HEADER,'mips')[0][1]
    assert not repairs.sentinel_phases(partial.replace('return answer != 0;','return answer != repair_scratch;'),'f',HEADER,'mips')
    assert not repairs.sentinel_phases(partial.replace('answer = answer ^ 1234;','repair_scratch = 5; answer = answer ^ 1234;'),'f',HEADER,'mips')
    assert not repairs.sentinel_phases(partial.replace('1234','0xFFFFFFFFU'),'f',HEADER,'mips')


def test_backedge_reaching_old_pointer_phase_prevents_sentinel_reuse():
    partial=repairs.pointer_phases(SOURCE,'f',HEADER,'mips')[0][1]
    partial=partial.replace('repair_scratch = (int)','again: repair_scratch = (int)').replace('answer = answer ^ 1234;','if (key) goto again; answer = answer ^ 1234;')
    assert not repairs.sentinel_phases(partial,'f',HEADER,'mips')
