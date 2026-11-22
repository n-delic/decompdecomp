"""decomprepair 0.5.373's display-cursor, or-chain and plain-global families reach the draft repair path, and the cursor
form compiles, under the KMC compiler, to what the game's `g = cursor++; g->w0 = ..` macros compile to."""
import io

import pytest
from elftools.elf.elffile import ELFFile

from decompnet import toolchains
from decompnet.app import draft_repairs
from decompnet.compiler import compile_source
from decompnet.project import compiler_config

HEADER = 'typedef signed char s8; typedef short s16; typedef unsigned short u16; typedef int s32; typedef unsigned u32;'
DRAFT = '''extern void * volatile sCursor;
extern volatile unsigned short sHeight;
void f(void *arg0)
{
    void *a;
    s32 y;
    a = sCursor;
    *((s32 *) (((char *) a) + 0)) = 0xE7000000;
    *((s32 *) (((char *) a) + 4)) = 0;
    *((s32 *) (((char *) a) + 8)) = 0xE3000A01;
    *((s32 *) (((char *) a) + 0xC)) = 0x300000;
    *((s32 *) (((char *) a) + 0x10)) = 0xE200001C;
    *((s32 *) (((char *) a) + 0x14)) = 0x0F0A4000;
    *((s32 *) (((char *) a) + 0x18)) = 0xF7000000;
    *((s32 *) (((char *) a) + 0x1C)) = 0;
    sCursor = a + 8;
    sCursor = a + 0x10;
    sCursor = a + 0x18;
    sCursor = a + 0x20;
    y = *((volatile u16 *) (&sHeight));
    *((s32 *) (((char *) arg0) + 0)) = (s32) (((y * 4) | 0xF6000000) | y);
}
'''
# the same four commands as the game's macros wrote them
NATURAL = '''typedef struct { u32 w0; u32 w1; } Gfx;
extern Gfx *sCursor;
#define CMD(a, b) { Gfx *_g = sCursor++; _g->w0 = (a); _g->w1 = (b); }
void f(void *arg0)
{
    CMD(0xE7000000, 0); CMD(0xE3000A01, 0x300000); CMD(0xE200001C, 0x0F0A4000); CMD(0xF7000000, 0);
}
'''
CURSOR_ONLY = DRAFT.replace('extern volatile unsigned short sHeight;\n', '').replace(
    '    y = *((volatile u16 *) (&sHeight));\n    *((s32 *) (((char *) arg0) + 0)) = (s32) (((y * 4) | 0xF6000000) | y);\n', '')


def _proposals(source=DRAFT):
    labels = {}
    result = draft_repairs.variants(source, 'f', 'mips', labels=labels, limit=None)
    return result, labels


def test_the_families_are_proposed_labelled_and_known():
    result, labels = _proposals()
    families = set(labels.values())
    assert {'display_cursor_record', 'display_cursor_record_after', 'or_chain_order', 'volatile_global_plain'} <= families
    cursor = next(t for t in result if labels.get(t) == 'display_cursor_record')
    assert 'extern CursorRecord *sCursor;' in cursor and 'volatile void' not in cursor
    for family in ('display_cursor_record', 'or_chain_order', 'volatile_global_plain'):
        assert family in draft_repairs.AGENT_FAMILIES


def _text(obj):
    return ELFFile(io.BytesIO(obj)).get_section_by_name('.text').data()


def test_the_cursor_form_compiles_to_the_macro_form_under_kmc(tmp_path):
    if not toolchains.installed('gcc-2.7.2-kmc'):
        pytest.skip('GCC 2.7.2 KMC is not installed')
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': 'gcc-2.7.2-kmc',
                                                  'flags': ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2']}})
    result, labels = _proposals(CURSOR_ONLY)
    variant = next(t for t in result if labels.get(t) == 'display_cursor_record')
    built = {}
    for key, text in (('draft', CURSOR_ONLY), ('variant', variant), ('natural', NATURAL)):
        compiled = compile_source(cfg, HEADER, text, trusted=True)
        assert compiled.ok, compiled.log
        built[key] = _text(compiled.obj)
    assert built['variant'] == built['natural']
    assert built['draft'] != built['natural']
