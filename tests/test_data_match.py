"""The data judge (rom/data_match.py): C that defines one of the game's data symbols, placed at its address and
compared with the game's bytes. Objects come from the pinned KMC GCC; the "game" is a few words written here."""
import pytest

from decompnet import toolchains
from decompnet.rom.data_match import link_data
from decompnet.rom.symbols import Symbol, SymbolTable

HEADER = "typedef int s32; typedef short s16; typedef unsigned char u8;\n" \
         "typedef struct { char *name; s32 *score; s16 lap; } Entry;\nextern s32 D_80010000;\n"
TABLE, STRINGS, SCORE = 0x80020000, 0x80050000, 0x80010000
# The game's table of two entries: a name, a pointer to a score, a lap count (and the struct's 2 bytes of padding)
GAME = {0x80030000: bytes(16), TABLE: b"".join(w.to_bytes(4, "big") for w in (STRINGS, SCORE, 0x00030000, STRINGS + 4, 0, 0x00040000)),
        STRINGS: b"ACE\0BOB\0", 0x80040000: bytes([1, 2, 3, 4, 5, 0, 0, 0])}


def read(addr, n):
    for start, data in GAME.items():
        if start <= addr and addr + n <= start + len(data):
            return data[addr - start:addr - start + n]
    return None


@pytest.fixture(scope="module")
def compile_c(tmp_path_factory):
    if not toolchains.installed("gcc-2.7.2-kmc"):
        pytest.skip("Pinned KMC compiler is not installed; never download it here.")
    from decompnet.compiler import compile_source
    from decompnet.project import compiler_config
    cfg = compiler_config(tmp_path_factory.mktemp("kmc"),
                          {"compiler": {"toolchain": "gcc-2.7.2-kmc", "flags": ["-G0", "-mips3", "-mgp32", "-O2"]}})

    def run(source):
        result = compile_source(cfg, HEADER, source, trusted=True)
        assert result.ok, result.log
        return result.obj
    return run


def judge(obj, name, **kw):
    return link_data(obj, name, SymbolTable([Symbol("g_score", SCORE, 4, "data")]), read, **kw)


def test_a_table_of_strings_and_pointers_matches_where_the_game_keeps_it(compile_c):
    obj = compile_c('Entry D_80020000[2] = { {"ACE", &D_80010000, 3}, {"BOB", 0, 4} };')
    result = judge(obj, "D_80020000", ends=frozenset({TABLE + 24}))
    assert result.exact, result.problems
    assert (result.address, result.size, result.span) == (TABLE, 24, 24)


def test_a_wrong_value_a_wrong_string_and_a_wrong_pointer_are_each_told(compile_c):
    lap = judge(compile_c('Entry D_80020000[2] = { {"ACE", &D_80010000, 3}, {"BOB", 0, 5} };'), "D_80020000")
    assert not lap.exact and "differs from the game's bytes from +0x15" in lap.problems[0]
    name = judge(compile_c('Entry D_80020000[2] = { {"ACE", &D_80010000, 3}, {"BOX", 0, 4} };'), "D_80020000")
    assert not name.exact and any("differs from the ROM" in p for p in name.problems)
    pointer = judge(compile_c('Entry D_80020000[2] = { {"ACE", 0, 3}, {"BOB", 0, 4} };'), "D_80020000")
    assert not pointer.exact


def test_a_definition_must_cover_the_whole_datum_and_not_run_into_the_next(compile_c):
    short = judge(compile_c('Entry D_80020000[1] = { {"ACE", &D_80010000, 3} };'), "D_80020000",
                  ends=frozenset({TABLE + 24}))
    assert not short.exact and "runs on to" in short.problems[0]
    # a field the game refers to inside the table doesn't cut it short
    whole = judge(compile_c('Entry D_80020000[2] = { {"ACE", &D_80010000, 3}, {"BOB", 0, 4} };'), "D_80020000",
                  ends=frozenset({TABLE + 8, TABLE + 24}))
    assert whole.exact, whole.problems
    long = judge(compile_c('Entry D_80020000[2] = { {"ACE", &D_80010000, 3}, {"BOB", 0, 4} };'), "D_80020000",
                 end=TABLE + 12)
    assert not long.exact and "the next datum" in long.problems[0]


def test_padding_up_to_the_next_datum_and_const_data(compile_c):
    result = judge(compile_c("const u8 D_80040000[5] = {1, 2, 3, 4, 5};"), "D_80040000", end=0x80040008)
    assert result.exact and result.span == 8, result.problems
    # Zeros as long as the next datum's alignment are the definition's own, not padding: half a table of zeros
    # doesn't stand for all of it.
    half = judge(compile_c("s32 D_80030000[1] = {0};"), "D_80030000", end=0x80030010)
    assert not half.exact and "runs on to" in half.problems[0]


def test_bss_has_no_bytes_but_the_game_s_values_need_an_initialiser(compile_c):
    bss = judge(compile_c("s32 D_80060000;"), "D_80060000")
    assert bss.exact and not bss.initialised
    # zeros the game keeps in initialised data need `= {0}`: without it, the compiler puts the datum in .bss
    zero = judge(compile_c("s32 D_80030000;"), "D_80030000")
    assert not zero.exact and "`= {0}` for zeros" in zero.problems[0]
    assert judge(compile_c("s32 D_80030000[4] = {0};"), "D_80030000").exact
    held = judge(compile_c("s32 D_80020000;"), "D_80020000")
    assert not held.exact and "give it an initialiser" in held.problems[0]


def test_one_datum_per_definition_and_only_definitions(compile_c):
    two = judge(compile_c("s32 D_80030000 = 0; s32 D_80030004 = 0;"), "D_80030000")
    assert not two.exact and "D_80030004 is a datum of its own" in two.problems[0]
    assert not judge(compile_c("extern s32 D_80030000; s32 f(void) { return D_80030000; }"), "D_80030000").exact
    unknown = judge(compile_c("s32 speed = 1;"), "speed")
    assert not unknown.exact and "not a known symbol" in unknown.problems[0]
    renamed = judge(compile_c("s32 g_score = 0;"), "g_score")
    assert renamed.address == SCORE
