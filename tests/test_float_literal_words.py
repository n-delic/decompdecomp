"""float_literal_words: the words of a function's float constants, read from the game for float_literal_global.

m2c names a float constant from the compiler's literal pool by its address (`extern f32 D_8000930C;`); the library's
float_literal_global writes the literal (`500.0f`) when the app hands it the word. Three N64 functions built by SN GCC
2.8.1 went 10 -> 0 that way from their drafts (docs/learnings/finding/float-literal-through-global.md)."""
import struct

import pytest

from decompnet.app import draft_repairs, float_literal_words, library_registry
from decomprepair import registry
from decomprepair.symbols import Symbol, SymbolTable

pytest.importorskip('decomprepair.float_literal_global')

SOURCE = '''extern f32 D_8000930C;
extern f32 D_801700D0;
s32 check(void *);

void f(void *arg0) {
    f32 temp_fs0;

    temp_fs0 = D_801700D0;
    if ((temp_fs0 < D_8000930C) && (check(arg0) != 0)) {
        *(s32 *)((s8 *)arg0 + 0x64) = 0x64;
    }
}
'''
ASM = '''glabel f
/* 0 80077E80 3C028017 */  lui        $v0, %hi(D_801700D0)
/* 4 80077E84 C45400D0 */  lwc1       $fs0, %lo(D_801700D0)($v0)
/* 8 80077E88 3C018001 */  lui        $at, %hi(D_8000930C)
/* C 80077E8C C420930C */  lwc1       $fv0, %lo(D_8000930C)($at)
/* 10 80077E90 4600A03C */  c.lt.s     $fs0, $fv0
'''
VRAM = 0x80077E80


class Net:
    """The engine's read-only facts the reader asks for: symbols, cross-references, the ROM reader."""

    def __init__(self, memory, used_by, kind='rom', big_endian=True):
        self.memory, self.used_by, self.kind, self.big_endian = memory, used_by, kind, big_endian
        self.reads = 0

    def is_rom(self, proj):
        return self.kind == 'rom'

    def cfg(self, proj):
        return {'kind': self.kind, 'big_endian': self.big_endian}

    def symbols(self, project_id):
        return SymbolTable([Symbol('D_8000930C', 0x8000930C, 4, 'data'), Symbol('D_801700D0', 0x801700D0, 4, 'data')])

    def xrefs(self, proj):
        return {'used_by': self.used_by}

    def reader(self, proj, fn):
        def read(address, size):
            self.reads += 1
            return self.memory.get(address)
        return read


PROJ = {'id': 1, 'arch': 'mips'}
FN = {'name': 'f', 'vram': VRAM, 'asm': ASM}


def _net(word=0x43FA0000, users=(VRAM,), **kw):
    memory = {0x8000930C: struct.pack('>I', word), 0x801700D0: struct.pack('>I', 0x42C80000)}
    if not kw.get('big_endian', True):
        memory = {address: data[::-1] for address, data in memory.items()}
    return Net(memory, {0x8000930C: set(users), 0x801700D0: {VRAM, 0x80001000}}, **kw)


def test_a_constant_loaded_through_at_and_used_by_one_function_gives_its_word():
    net = _net()
    assert float_literal_words.read(net, PROJ, FN) == {'D_8000930C': 0x43FA0000}
    assert float_literal_words.read(net, PROJ, FN) == {'D_8000930C': 0x43FA0000} and net.reads == 1  # kept


def test_little_endian_games_are_read_in_their_byte_order():
    assert float_literal_words.read(_net(big_endian=False), PROJ, FN) == {'D_8000930C': 0x43FA0000}


def test_nothing_for_a_shared_word_an_implausible_word_or_another_kind_of_project():
    assert float_literal_words.read(_net(users=(VRAM, 0x80001000)), PROJ, FN) == {}  # a variable another function uses
    for word in (0x80123456, 0x00000064, 0, 0x7FC00000):  # a pointer, a small integer, zero, NaN
        assert float_literal_words.read(_net(word), PROJ, FN) == {}, hex(word)
    assert float_literal_words.read(_net(kind='fork'), PROJ, FN) == {}
    assert float_literal_words.read(_net(), {'id': 1, 'arch': 'ppc'}, FN) == {}
    assert float_literal_words.read(_net(), PROJ, dict(FN, asm=ASM.replace('($at)', '($v1)'))) == {}
    gp = ASM.replace('%lo(D_8000930C)($at)', '%gp_rel(D_8000930C)($gp)')
    assert float_literal_words.read(_net(), PROJ, dict(FN, asm=gp)) == {'D_8000930C': 0x43FA0000}


def test_a_reader_that_fails_gives_no_words():
    net = _net()
    net.xrefs = lambda proj: (_ for _ in ()).throw(OSError('no ROM'))
    assert float_literal_words.read(net, PROJ, FN) == {}


def test_the_draft_pass_hands_the_words_to_the_library_and_proposes_the_literal():
    words = float_literal_words.read(_net(), PROJ, FN)
    labels = {}
    out = draft_repairs.variants(SOURCE, 'f', 'mips', ASM, labels=labels, limit=None, words=words)
    children = [text for text in out if labels.get(text) == 'float_literal_global']
    assert children and all('temp_fs0 < 500.0f' in text for text in children)
    labels = {}
    out = draft_repairs.variants(SOURCE, 'f', 'mips', ASM, labels=labels, limit=None)
    assert not [text for text in out if labels.get(text) == 'float_literal_global']  # no words, no proposal


def test_the_local_lane_carries_the_words():
    context = library_registry.draft_context(SOURCE, 'f', '', 'mips', ASM, (), {'D_8000930C': 0x43FA0000})
    [call] = [call for call in library_registry.local_calls(context) if call[1] == ('float_literal_global',)]
    name, args, kwargs = call
    assert kwargs['words'] == {'D_8000930C': 0x43FA0000}
    assert '500.0f' in registry.run(*args, **kwargs)[0][1]
    assert library_registry.draft_context(SOURCE, 'f', '', 'mips', ASM, ())['words'] == {}



DOUBLE_ASM = '''glabel f
/* 0 80077E80 3C018010 */  lui        $at, %hi(D_800D2EF0)
/* 4 80077E84 D42A2EF0 */  ldc1       $ft3, %lo(D_800D2EF0)($at)
/* 8 80077E88 3C018001 */  lui        $at, %hi(D_8000930C)
/* C 80077E8C C420930C */  lwc1       $fv0, %lo(D_8000930C)($at)
'''


class DoubleNet(Net):
    def symbols(self, project_id):
        return SymbolTable([Symbol('D_800D2EF0', 0x800D2EF0, 8, 'data'), Symbol('D_8000930C', 0x8000930C, 4, 'data')])


def _double_net(value=0x3FE0000000000000, **kw):
    memory = {0x800D2EF0: struct.pack('>Q', value), 0x8000930C: struct.pack('>I', 0x43FA0000)}
    return DoubleNet(memory, {0x800D2EF0: {VRAM}, 0x8000930C: {VRAM}}, **kw)


def test_a_double_loaded_through_at_gives_its_eight_bytes_and_a_float_its_four():
    pytest.importorskip('decomprepair.double_literal_global')
    words = float_literal_words.read(_double_net(), PROJ, dict(FN, asm=DOUBLE_ASM))
    assert words == {'D_800D2EF0': 0x3FE0000000000000, 'D_8000930C': 0x43FA0000}
    assert float_literal_words.loads(DOUBLE_ASM) == {'D_800D2EF0': 8, 'D_8000930C': 4}


def test_a_double_that_is_zero_a_denormal_or_loaded_at_both_widths_is_not_a_constant():
    pytest.importorskip('decomprepair.double_literal_global')
    for value in (0, 0x80123456, 0x7FF0000000000000):
        assert 'D_800D2EF0' not in float_literal_words.read(_double_net(value), PROJ, dict(FN, asm=DOUBLE_ASM)), hex(value)
    both = DOUBLE_ASM + '/* 10 80077E90 D42A2EF0 */  lwc1       $fv1, %lo(D_800D2EF0)($at)\n'
    assert float_literal_words.loads(both) == {'D_8000930C': 4}


def test_the_draft_pass_proposes_the_double_literal():
    pytest.importorskip('decomprepair.double_literal_global')
    source = '''extern f64 D_800D2EF0;

void f(f32 *out, s32 arg1) {
    f32 temp_fv0;

    temp_fv0 = (f32) ((f64) (f32) arg1 + D_800D2EF0);
    out[0] = temp_fv0;
}
'''
    words = float_literal_words.read(_double_net(), PROJ, dict(FN, asm=DOUBLE_ASM))
    labels = {}
    out = draft_repairs.variants(source, 'f', 'mips', DOUBLE_ASM, labels=labels, limit=None, words=words)
    children = [text for text in out if labels.get(text) == 'double_literal_global']
    assert children and all('(f32) arg1 + 0.5)' in text for text in children)


def test_a_double_gas_loads_into_a_register_pair_is_eight_bytes():
    # A 1133-instruction effect spawner (KMC GCC 2.7.2) keeps a sine loop's phase offset in $s4/$s5 across the loop's
    # calls: GAS's `li.d` loads it with one `lui $at` and two `lw`. A global read into a pair uses its own destination
    # registers, and a jump table or indexed read has an `addu $at` between the `lui` and the `lw`.
    pair = '''glabel f
/* 0 80071DEC 3C01800D */  lui        $at, %hi(D_800D47F8)
/* 4 80071DF0 8C3447F8 */  lw         $s4, %lo(D_800D47F8)($at)
/* 8 80071DF4 8C3547FC */  lw         $s5, %lo(D_800D47FC)($at)
/* C 80071DF8 3C01800D */  lui        $at, %hi(D_800D4800)
/* 10 80071DFC D43E4800 */  ldc1       $fs5, %lo(D_800D4800)($at)
/* 14 80071E00 3C01800D */  lui        $at, %hi(jtbl_800D47E0)
/* 18 80071E04 00220821 */  addu       $at, $at, $v0
/* 1C 80071E08 8C2247E0 */  lw         $v0, %lo(jtbl_800D47E0)($at)
/* 20 80071E0C 3C02800D */  lui        $v0, %hi(D_800D4818)
/* 24 80071E10 8C424818 */  lw         $v0, %lo(D_800D4818)($v0)
'''
    assert float_literal_words.loads(pair) == {'D_800D47F8': 8, 'D_800D4800': 8}


def test_a_constant_with_no_symbol_is_read_at_the_address_its_name_says():
    """An N64 game's .rodata the import never listed had no symbols for its constants: the draft named them
    D_800042C0, and with no symbol of that name the words were empty, so no literal was ever proposed."""
    net = _net()
    net.symbols = lambda project_id: SymbolTable([])
    assert float_literal_words.read(net, PROJ, FN) == {'D_8000930C': 0x43FA0000}
