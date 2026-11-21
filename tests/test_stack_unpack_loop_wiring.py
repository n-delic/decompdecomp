"""m2c's pointer-walk unpacker reaches the repairs the deep pass tries, and IDO builds what it produces (decomprepair 0.5.151).

The fixture is a homemade unpacker of the shape of an N64 audio-header function that sat at score 11,654 under GCC-shaped
drafts: built with IDO, its unmodified m2c draft repaired by this family was exact (a real game's code, checked in the
app's judge, not committed here)."""
import io
import tempfile
from pathlib import Path

import pytest

from decompnet import toolchains
from decompnet.app import draft_repairs

HEADER = 'typedef unsigned char u8;\ntypedef short s16;\ntypedef int s32;\n'
FLAGS = ['-O2', '-mips2', '-G', '0', '-non_shared', '-Xcpluscomm', '-Wab,-r4300_mul']
SOURCE = '''s32 g();

s32 f(s32 arg0, void *arg1, s32 arg2) {
    u8 temp_t4;
    s32 sp28;
    s16 sp30;
    s16 sp38;
    s16 sp48;
    s16 sp4A;
    s32 *var_a;
    s32 *var_b;
    s32 *var_e;
    void *var_src;

    var_src = arg1;
    sp48 = (*(u8 *)((char *)(arg1) + 0)) >> 2;
    sp4A = (*(u8 *)((char *)(arg1) + 1)) & 0x3F;
    var_a = &sp28;
    var_b = &sp30;
    var_e = &sp38;
    loop: {
        var_a += 2;
        temp_t4 = (*(u8 *)((char *)(var_src) + 2));
        var_src += 3;
        *(s16 *)((char *)(var_a) + -2) = (s16) (temp_t4 >> 3);
        *(s16 *)var_b = (s16) ((*(u8 *)((char *)(var_src) + -1)) & 7);
        var_b += 2;
        *(s16 *)((char *)(var_e) + 2) = (s16) ((*(u8 *)((char *)(var_src) + 0)) & 0xF);
        *(s16 *)var_e = (s16) ((*(u8 *)((char *)(var_src) + 0)) >> 4);
        var_e += 4;
    }
    if (var_a != &sp30) goto loop;
    g(arg0, &sp48, &sp28, &sp30);
    return 0;
}
'''
ASM = '''addiu      $sp, $sp, -0x70
sw         $ra, 0x1C($sp)
sw         $a0, 0x70($sp)
sw         $a2, 0x78($sp)
addiu      $t5, $sp, 0x38
sw         $t5, 0x10($sp)
lw         $t8, 0x78($sp)
lw         $a0, 0x70($sp)
addiu      $a1, $sp, 0x48
addiu      $a2, $sp, 0x28
addiu      $a3, $sp, 0x30
jal        g
sw         $t8, 0x14($sp)
'''




def _variants():
    labels = {}
    texts = draft_repairs.variants(SOURCE, 'f', 'mips', ASM, FLAGS, labels, limit=None, header=HEADER)
    return [t for t in texts if labels.get(t) == 'stack_unpack_loop']


def test_the_family_is_known_to_the_agents_and_the_deep_pass():
    assert 'stack_unpack_loop' in draft_repairs.AGENT_FAMILIES


def test_the_unpacker_is_offered_through_draft_repairs():
    found = _variants()
    assert found and all('for (i = 0; i < 4; i++)' in t and 'var_' not in t for t in found)


def test_ido_builds_the_indexed_unpacker_with_a_shifted_loaded_byte():
    if not toolchains.installed('ido-7.1'):
        pytest.skip('The pinned IDO compiler is not installed; never download it here.')
    from elftools.elf.elffile import ELFFile

    from decompnet.app import move_fingerprints
    from decompnet.compiler import compile_source
    from decompnet.project import compiler_config
    with tempfile.TemporaryDirectory() as work:
        cfg = compiler_config(Path(work), {'compiler': {'toolchain': 'ido-7.1', 'flags': FLAGS}})
        for text in _variants():
            result = compile_source(cfg, '', HEADER + text)
            assert result.ok, result.log
            code = ELFFile(io.BytesIO(result.obj)).get_section_by_name('.text').data()
            assert move_fingerprints.shifted_loads(code, True) >= 4  # lbu then sra, the signal that named IDO
