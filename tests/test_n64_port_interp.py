"""The port's interpreter for functions with no C yet (consoles/n64/port_wasm/mips.c), run on small MIPS programs
written here, and the bridges the build generates around it (port/wasm/interp.py)."""

from __future__ import annotations

import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from decompnet.port.wasm import interp

MIPS = Path(__file__).resolve().parents[1] / "src/decompnet/consoles/n64/port_wasm/mips.c"
CODE, DATA, C_FUNCTION = 0x80001000, 0x80002000, 0x80003000

HARNESS = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
static uint8_t *mem;
uint8_t *port_memory(void) { return mem; }
uint64_t port_io_load(uint64_t a) { return a; }
uint32_t port_stack_bus(uint32_t a) { return a >= 0x14000000u && a < 0x15000000u ? a : 0; }
unsigned port_tlb_n;
uint32_t port_tlb_canon(uint32_t a) { return a; }  /* (host.c's TLB windows: none here) */
uint32_t port_tlb_alias(uint32_t a) { return a; }
uint32_t port_tlb_jump(uint32_t slot, uint32_t imm) { return (slot & 0xF0000000u) | imm; }
uint32_t port_tlb_call(uint32_t a) { return a; }     /* (no window kept at its physical address here) */
uint32_t port_tlb_spelled(uint32_t a) { return a; }
uint32_t port_spell_lo = 0xFFFFFFFFu, port_spell_len, port_real_lo = 0xFFFFFFFFu, port_real_len; /* (no window to watch or keep here) */
void port_tlb_spell_store(uint32_t a) { (void)a; }
uint32_t port_tlb_virtual(uint32_t a) { return a; }
uint32_t port_tlb_virtual_of(uint32_t a) { (void)a; return 0; }
void port_tlb_write(uint32_t pm, uint32_t hi, uint32_t lo0, uint32_t lo1) { (void)pm; (void)hi; (void)lo0; (void)lo1; }
void port_tlb_set(unsigned i, uint32_t pm, uint32_t hi, uint32_t lo0, uint32_t lo1) { (void)i; (void)pm; (void)hi; (void)lo0; (void)lo1; }
uint32_t port_tlb_probe(uint32_t hi) { (void)hi; return 0x80000000u; }
int port_tlb_read(unsigned i, uint32_t *pm, uint32_t *hi, uint32_t *lo0, uint32_t *lo1) { (void)i; (void)pm; (void)hi; (void)lo0; (void)lo1; return 0; }
int port_tlb_invalid(uint32_t a) { (void)a; return 0; }
int port_tlb_fault(uint32_t e, uint32_t b, uint32_t c) { (void)e; (void)b; (void)c; return 0; }
uint32_t port_tlb_wired(int set, uint32_t v) { (void)set; (void)v; return 0; }
uint64_t port_io_store(uint64_t a, uint64_t v) { (void)v; return a; }
void port_safe_point(void) {}
void port_boot_spin(void) {}
/* a thread whose code jumps to itself is parked for good (host.c): here, say so with the word at DATA, and end */
void port_idle_forever(void) {
    uint32_t w; memcpy(&w, mem + 0x%(data)08Xu, 4);
    printf("idle %%08X\n", __builtin_bswap32(w));
    exit(0);
}
int port_thread_number(void) { return -1; }
struct w2c_env;
uint32_t w2c_env_0x5F_builtin_mips_read_count(struct w2c_env *e) { (void)e; return 1234; }
void port_mips_run(uint32_t entry, uint64_t *r, uint32_t *f);
int port_mips_enter(uint64_t *r, uint32_t *f, int words);
uint32_t port_mips_varargs(const uint64_t *r, int first);
uint64_t port_mips_typed_call(uint32_t entry, const uint8_t *types, const uint64_t *vals, unsigned n, int ret);
/* the one function "with C": returns $a0 * 10 + $a1, and says it was called */
static int c_calls;
static void c_function(uint64_t *r, uint32_t *f) { (void)f; c_calls++; r[2] = (uint64_t)(int64_t)(int32_t)((uint32_t)r[4] * 10 + (uint32_t)r[5]); }
typedef void (*PortMipsCall)(uint64_t *r, uint32_t *f);
typedef struct { uint32_t vram; PortMipsCall call; } PortMipsEntry;
const PortMipsEntry port_mips_calls[] = {{0x%(c)08Xu, c_function}};
const unsigned port_mips_call_count = 1;
/* argv: the program (big-endian words) at CODE, then $a0-$a3; prints $v0 $v1, the f registers 0-3, the words at
   DATA (8 of them), how often the C function ran, and a varargs buffer of $a2 on */
int main(int argc, char **argv) {
    mem = mmap(NULL, 1ull << 32, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON | MAP_NORESERVE, -1, 0);
    if (mem == MAP_FAILED) return 3;
    FILE *in = fopen(argv[1], "rb");
    size_t n = fread(mem + 0x%(code)08Xu, 1, 0x1000, in);
    fclose(in);
    (void)n;
    if (argc > 2 && !strncmp(argv[2], "typed:", 6)) { /* typed:RET:CODES, then each argument's bits (hex) */
        int ret = argv[2][6] - '0';
        const char *codes = argv[2] + 8;
        uint8_t t[16]; uint64_t v[16]; unsigned k = 0;
        for (; codes[k] && k < 16; k++) { t[k] = (uint8_t)(codes[k] - '0'); v[k] = strtoull(argv[3 + k], NULL, 16); }
        printf("%%016llX\n", (unsigned long long)port_mips_typed_call(0x%(code)08Xu, t, v, k, ret));
        return 0;
    }
    uint64_t r[32]; uint32_t f[32];
    port_mips_enter(r, f, 8);
    for (int i = 0; i < 4 && 2 + i < argc; i++) r[4 + i] = (uint64_t)(int64_t)(int32_t)strtoul(argv[2 + i], NULL, 0);
    port_mips_run(0x%(code)08Xu, r, f);
    printf("%%08X %%08X %%08X %%08X %%08X %%08X", (uint32_t)r[2], (uint32_t)r[3], f[0], f[1], f[2], f[3]);
    for (int i = 0; i < 8; i++) { uint32_t w; memcpy(&w, mem + 0x%(data)08Xu + 4 * i, 4); printf(" %%08X", __builtin_bswap32(w)); }
    uint32_t buf = port_mips_varargs(r, 2), w0, w1;
    memcpy(&w0, mem + buf, 4); memcpy(&w1, mem + buf + 4, 4);
    printf(" %%d %%08X %%08X\n", c_calls, __builtin_bswap32(w0), __builtin_bswap32(w1));
    return 0;
}
"""

REG = {n: i for i, n in enumerate("zero at v0 v1 a0 a1 a2 a3 t0 t1 t2 t3 t4 t5 t6 t7 s0 s1 s2 s3 s4 s5 s6 s7 t8 t9 k0 k1 gp sp fp ra".split())}


def r_type(funct, rd=0, rs=0, rt=0, sa=0, op=0):
    return op << 26 | REG.get(rs, rs) << 21 | REG.get(rt, rt) << 16 | REG.get(rd, rd) << 11 | sa << 6 | funct


def i_type(op, rt, rs, imm):
    return op << 26 | REG.get(rs, rs) << 21 | REG.get(rt, rt) << 16 | (imm & 0xFFFF)


def cop1(fmt, funct, fd=0, fs=0, ft=0):
    return 17 << 26 | fmt << 21 | ft << 16 | fs << 11 | fd << 6 | funct


NOP = 0
JR_RA = r_type(8, rs="ra")
def addiu(rt, rs, imm): return i_type(9, rt, rs, imm)
def lui(rt, imm): return i_type(15, rt, 0, imm)
def ori(rt, rs, imm): return i_type(13, rt, rs, imm)
def addu(rd, rs, rt): return r_type(33, rd, rs, rt)
def sw(rt, off, base): return i_type(43, rt, base, off)
def lw(rt, off, base): return i_type(35, rt, base, off)
def bne(rs, rt, off): return i_type(5, rt, rs, off)
def beql(rs, rt, off): return i_type(20, rt, rs, off)
def jal(target): return 3 << 26 | (target >> 2 & 0x3FFFFFF)
def mtc1(rt, fs): return 17 << 26 | 4 << 21 | REG.get(rt, rt) << 16 | fs << 11


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler")
    folder = tmp_path_factory.mktemp("interp")
    (folder / "harness.c").write_text(HARNESS % {"c": C_FUNCTION, "code": CODE & 0xFFFFFFFF, "data": DATA})
    program = folder / "interp"
    done = subprocess.run([cc, "-O1", "-o", str(program), str(folder / "harness.c"), str(MIPS), "-lm"],
                          capture_output=True, text=True, timeout=120)
    if done.returncode:
        pytest.skip(f"the harness doesn't build here: {done.stderr[-300:]}")

    def typed(words, ret, codes, *values):
        (folder / "code.bin").write_bytes(b"".join(struct.pack(">I", w & 0xFFFFFFFF) for w in words))
        out = subprocess.run([str(program), str(folder / "code.bin"), f"typed:{ret}:{codes}", *(f"{v:X}" for v in values)],
                             capture_output=True, text=True, timeout=30, check=True).stdout.strip()
        return int(out, 16)

    def go(words, *args):
        (folder / "code.bin").write_bytes(b"".join(struct.pack(">I", w & 0xFFFFFFFF) for w in words))
        out = subprocess.run([str(program), str(folder / "code.bin"), *(str(a) for a in args)], capture_output=True,
                             text=True, timeout=30, check=True).stdout.split()
        return {"v0": int(out[0], 16), "v1": int(out[1], 16), "f": [int(x, 16) for x in out[2:6]],
                "data": [int(x, 16) for x in out[6:14]], "c_calls": int(out[14]), "varargs": [int(x, 16) for x in out[15:17]]}
    def raw(words, *args):
        (folder / "code.bin").write_bytes(b"".join(struct.pack(">I", w & 0xFFFFFFFF) for w in words))
        return subprocess.run([str(program), str(folder / "code.bin"), *(str(a) for a in args)], capture_output=True,
                              text=True, timeout=30, check=True).stdout.strip()
    def entries(words, path, *args):
        """Run with PORT_ENTRIES=path; the file's addresses."""
        import os
        (folder / "code.bin").write_bytes(b"".join(struct.pack(">I", w & 0xFFFFFFFF) for w in words))
        subprocess.run([str(program), str(folder / "code.bin"), *(str(a) for a in args)], capture_output=True,
                       text=True, timeout=30, check=True, env={**os.environ, "PORT_ENTRIES": str(path)})
        return [int(x, 16) for x in path.read_text().split()] if path.is_file() else []
    go.typed = typed
    go.raw = raw
    go.entries = entries
    return go


def _data_base():  # $t9 = DATA
    return [lui("t9", DATA >> 16), ori("t9", "t9", DATA & 0xFFFF)]


def test_a_loop_with_its_delay_slots(run):
    """Sum 1..$a0: the branch's delay slot runs every time, taken or not; the return's too."""
    prog = [addiu("v0", "zero", 0), addiu("t0", "zero", 0),
            addiu("t0", "t0", 1),            # loop:
            bne("t0", "a0", -2),             #   back to loop
            addu("v0", "v0", "t0"),          #   (delay slot) v0 += t0
            JR_RA, addiu("v1", "zero", 7)]   # (delay slot) v1 = 7
    out = run(prog, 10)
    assert out["v0"] == 55 and out["v1"] == 7


def test_a_likely_branch_runs_its_delay_slot_only_when_taken(run):
    prog = [addiu("v0", "zero", 0),
            beql("a0", "a1", 2), addiu("v0", "v0", 1),   # +1 only when $a0 == $a1
            NOP,
            JR_RA, NOP]
    assert run(prog, 3, 3)["v0"] == 1
    assert run(prog, 3, 4)["v0"] == 0


def test_a_call_to_a_function_with_c_runs_the_c_and_goes_on(run):
    """jal to an address in the table: its C with the arguments as they are after the delay slot, then the code
    after it, at $ra; the interpreted function's own $ra (the caller's sentinel) saved and restored on its stack."""
    prog = [addiu("sp", "sp", -16), sw("ra", 12, "sp"),
            jal(C_FUNCTION), addiu("a1", "zero", 5),     # (delay slot) a1 = 5
            addiu("v0", "v0", 1),
            lw("ra", 12, "sp"), JR_RA, addiu("sp", "sp", 16)]
    out = run(prog, 4, 9)
    assert out["c_calls"] == 1 and out["v0"] == 4 * 10 + 5 + 1


def test_unaligned_words_and_halves_of_memory(run):
    """lwl/lwr read an unaligned word, swl/swr write one, big-endian as the console's memory is."""
    prog = [*_data_base(),
            lui("t0", 0x1122), ori("t0", "t0", 0x3344), sw("t0", 0, "t9"),
            lui("t0", 0x5566), ori("t0", "t0", 0x7788), sw("t0", 4, "t9"),
            i_type(34, "v0", "t9", 1), i_type(38, "v0", "t9", 4),      # lwl/lwr: the word at DATA + 1
            lui("t1", 0xAABB), ori("t1", "t1", 0xCCDD),
            i_type(42, "t1", "t9", 9), i_type(46, "t1", "t9", 12),     # swl/swr: it at DATA + 9
            JR_RA, NOP]
    out = run(prog)
    assert out["v0"] == 0x22334455
    assert out["data"][2:4] == [0x00AABBCC, 0xDD000000]


def test_multiply_divide_and_64_bit_shifts(run):
    prog = [addiu("t0", "zero", -7), addiu("t1", "zero", 3),
            r_type(26, rs="t0", rt="t1"),                 # div: lo -2, hi -1
            r_type(18, "v0"), r_type(16, "v1"),
            *_data_base(),
            r_type(24, rs="t0", rt="t1"), r_type(18, "t2"), sw("t2", 0, "t9"),   # mult: -21
            addiu("t3", "zero", 1), r_type(60, "t3", rt="t3", sa=4),             # dsll32 1 by 36
            r_type(62, "t4", rt="t3", sa=0), sw("t4", 4, "t9"),                  # dsrl32: the high word, 16
            JR_RA, NOP]
    out = run(prog)
    assert out["v0"] == 0xFFFFFFFE and out["v1"] == 0xFFFFFFFF
    assert out["data"][:2] == [0xFFFFFFEB, 16]


def test_the_fpu_singles_doubles_and_its_condition(run):
    """add.s, cvt.d.s and mul.d into an even/odd pair, trunc.w.s, and c.lt.s with bc1t."""
    one_half, three = 0x3FC00000, 0x40400000  # 1.5, 3.0
    prog = [lui("t0", one_half >> 16), mtc1("t0", 2), lui("t0", three >> 16), mtc1("t0", 4),
            cop1(16, 0, fd=0, fs=2, ft=4),            # f0 = 1.5 + 3.0 = 4.5
            cop1(16, 33, fd=2, fs=0),                 # f2/f3 = (double) 4.5
            cop1(17, 2, fd=2, fs=2, ft=2),            # f2/f3 = 20.25
            cop1(16, 13, fd=1, fs=0),                 # f1 = trunc.w(4.5) = 4
            cop1(16, 60, fs=2, ft=4) & ~(31 << 6),    # c.lt.s f2, f4 (f2 as a single: not below 3.0 here? see below)
            17 << 26 | 8 << 21 | 1 << 16 | 2,         # bc1t +2
            addiu("v0", "zero", 1),                   # (delay slot)
            addiu("v0", "zero", 2),
            JR_RA, NOP]
    out = run(prog)
    assert out["f"][0] == 0x40900000  # 4.5
    assert out["f"][1] == 4
    assert struct.unpack("<d", struct.pack("<II", out["f"][2], out["f"][3]))[0] == 20.25
    # f2 read as a single is the low word of 20.25 (0): below 3.0, so the branch is taken past v0 = 2
    assert out["v0"] == 1


def test_a_variadic_callees_buffer_is_the_words_after_the_fixed_ones(run):
    """port_mips_varargs: the argument words from the given one on, big-endian, as the native build's C reads them."""
    out = run([JR_RA, NOP], 1, 2, 0x11, 0x22)
    assert out["varargs"] == [0x11, 0x22]


def test_running_off_into_empty_memory_ends_the_run(run):
    """No code where it was asked to run (no ROM there): the function returns rather than running nothing forever."""
    assert run([NOP] * 4)["v0"] == 0


def j(target): return 2 << 26 | (target >> 2 & 0x3FFFFFF)


@pytest.mark.parametrize("loop", ["j", "b"])
def test_a_jump_to_itself_parks_the_thread_instead_of_spinning(run, loop):
    """An idle thread's `for (;;);` (`j .` or `b .`, a nop in its delay slot) took 41% of a racing game's interpreted
    instructions: the interpreter hands the thread to the host to park after what came before it ran."""
    here = CODE + 4 * 4
    jump = j(here) if loop == "j" else i_type(4, "zero", "zero", -1)  # beq $zero, $zero, .
    prog = [*_data_base(), addiu("t0", "zero", 42), sw("t0", 0, "t9"), jump, NOP]
    assert run.raw(prog) == "idle 0000002A"


def test_an_eret_parks_the_thread_instead_of_running_into_the_next_function(run):
    """libultra's thread dispatch ends with `eret`, which resumes another thread and never falls through. The game's own
    copy of osCreateThread/osStartThread (library detection did not name it for its SDK build) ran into the next
    routine, the thread cleanup, which called osDestroyThread and came back for ever at 100% CPU: the thread parks."""
    prog = [*_data_base(), addiu("t0", "zero", 42), sw("t0", 0, "t9"), 0x42000018, NOP]
    assert run.raw(prog) == "idle 0000002A"


def test_the_addresses_the_game_starts_running_are_written_down_once(run, tmp_path):
    """PORT_ENTRIES: each function entry the interpreter sees (where a run starts, each call's target), once, for the
    importer's evidence of where functions start (consoles/n64/code.py, runtime_entries)."""
    prog = [addiu("sp", "sp", -16), sw("ra", 12, "sp"),
            jal(C_FUNCTION), NOP, jal(C_FUNCTION), NOP,
            lw("ra", 12, "sp"), JR_RA, addiu("sp", "sp", 16)]
    path = tmp_path / "entries.txt"
    assert run.entries(prog, path, 1, 2) == [CODE, C_FUNCTION]
    assert run.entries(prog, path, 1, 2) == [CODE, C_FUNCTION] * 2  # (appended: one run's own each once)


# ---------------------------------------------------------------- the generated bridges

GAME_H = """/* export: 'func_80003000' */
u32 w2c_game_func_80003000(w2c_game*, u32, f32);
/* export: 'func_80004000' */
u32 w2c_game_func_80004000(w2c_game*, u32, u32, u32);
/* export: 'dmaCopy' */
void w2c_game_dmaCopy(w2c_game*, u32, u32);
/* import: 'env' 'func_80005000' */
u32 w2c_env_func_80005000(struct w2c_env*, u32, u32);
/* import: 'env' 'func_80006000' */
void w2c_env_func_80006000(struct w2c_env*, u32, u32);
/* import: 'env' 'osSendMesg' */
u32 w2c_env_osSendMesg(struct w2c_env*, u32, u32, u32);
/* import: 'env' 'pfsRead' */
u32 w2c_env_pfsRead(struct w2c_env*, u32);
"""


def test_the_bridges_say_where_o32_passes_each_argument(tmp_path):
    host = tmp_path / "host.c"
    host.write_text("u32 w2c_env_osSendMesg(struct w2c_env *e, u32 q, u32 m, u32 f) {\n  return 0;\n}\n")
    fixed = ["dmaCopy 0x80007000 func", "osSendMesg 0x80008000 func", "pfsRead 0x80009000 func"]
    text, counts = interp.generate(GAME_H, fixed, [host], {}, (), {"pfsRead"}, varargs={"func_80004000", "func_80006000"},
                                   byval={"dmaCopy": {1: 1}})
    assert counts == {"game": 3, "host": 1, "imports": 2}  # (osSendMesg the host's; pfsRead left out on purpose)
    # a leading float in $f12; a variadic function's buffer from the word after its fixed ones; a structure by value
    assert "w2c_game_func_80003000(port_game(), W(r, 0), FB(f[14]))" not in text  # (not leading: $a1's word)
    assert "w2c_game_func_80003000(port_game(), W(r, 0), FB(W(r, 1)))" in text
    assert "w2c_game_func_80004000(port_game(), W(r, 0), W(r, 1), port_mips_varargs(r, 2))" in text
    assert "w2c_game_dmaCopy(port_game(), W(r, 0), port_mips_slot(r, 1))" in text
    assert "{0x80008000u, H_80008000}" in text and "w2c_env_osSendMesg(NULL, W(r, 0), W(r, 1), W(r, 2))" in text
    # the game's C calling what has no C: the interpreter runs it, its buffer spread into the words after
    assert "port_mips_run(0x80005000u, r, f)" in text
    assert "port_mips_spread(r, 1, (uint32_t)a1)" in text
    assert "w2c_env_pfsRead" not in text and "w2c_env_osSendMesg(struct w2c_env *e" not in text


def test_only_a_definition_or_fixed_parameters_make_a_function_variadic(tmp_path):
    """An unprototyped declaration (`s32 taskOpen();`) is `(...)` in the IR too: taken for variadic, a racing game's
    taskOpen got its third argument as a buffer's address and returned no queue."""
    (tmp_path / "a.ll").write_text(
        "define i32 @taskOpen(i32 noundef %0, i32 noundef %1, i32 noundef %2) {\n}\n"
        "define i32 @alloc(i32 noundef %0, i32 noundef %1, ...) {\n}\n"
        "declare i32 @printf(ptr noundef, ...)\n"
        "declare i32 @unprototyped(...)\n")
    (tmp_path / "b.ll").write_text("declare i32 @taskOpen(...)\n")
    assert interp.variadic([tmp_path / "a.ll", tmp_path / "b.ll"]) == {"alloc", "printf"}


def test_structures_passed_by_value_take_all_their_words(tmp_path):
    """A structure by value is found by position with its size in words (from the module's type definitions), and
    the arguments after it go in the words after all of it, as o32 passes them (a racing game's menu function takes
    a 4-word structure, then its 5th and 6th words)."""
    (tmp_path / "a.ll").write_text(
        "%struct.Args = type { [4 x i32] }\n%struct.Pair = type { i8, double }\n"
        "define void @menu(ptr noundef byval(%struct.Args) align 4 %0, i32 noundef %1, i32 noundef %2) {\n}\n"
        "define void @pair(ptr noundef byval(%struct.Pair) align 8 %0) {\n}\n")
    found = interp.by_value([tmp_path / "a.ll"])
    assert found == {"menu": {0: 4}, "pair": {0: 4}}
    game_h = "/* export: 'menu' */\nvoid w2c_game_menu(w2c_game*, u32, u32, u32);\n"
    text, _ = interp.generate(game_h, ["menu 0x80001000 func"], [], byval=found)
    assert "w2c_game_menu(port_game(), port_mips_slot(r, 0), W(r, 4), W(r, 5))" in text


def _f32(x: float) -> int:
    return struct.unpack(">I", struct.pack(">f", x))[0]


def _f64(x: float) -> int:
    return struct.unpack(">Q", struct.pack(">d", x))[0]


def mfc1(rt, fs): return 17 << 26 | 0 << 21 | REG.get(rt, rt) << 16 | fs << 11


def test_a_call_with_types_puts_floats_where_o32_does(run):
    """A call through a pointer with its types (port_mips_typed_call): the first two arguments in $f12 and $f14
    while they're floats, a float after a word in a word, a double in an even/odd pair, 64 bits in an aligned pair
    of words; a float result from $f0. A float through a pointer once went in an integer word."""
    add_s = [cop1(16, 0, fd=0, fs=12, ft=14), JR_RA, NOP]                 # f0 = f12 + f14
    assert run.typed(add_s, 2, "22", _f32(1.5), _f32(2.25)) == _f32(3.75)
    word_then_float = [mtc1("a1", 0), JR_RA, NOP]                          # (u32, f32): the float in $a1
    assert run.typed(word_then_float, 2, "02", 7, _f32(-4.0)) == _f32(-4.0)
    double_first = [cop1(17, 0, fd=0, fs=12, ft=12), JR_RA, NOP]          # f0/f1 = d12 + d12
    assert run.typed(double_first, 3, "3", _f64(1.25)) == _f64(2.5)
    pair = [addu("v0", "a2", "zero"), JR_RA, addu("v1", "a3", "zero")]    # (u32, u64): the pair in $a2, $a3
    assert run.typed(pair, 1, "01", 1, 0x1122334455667788) == 0x1122334455667788


def test_each_type_of_call_through_a_pointer_gets_an_entry():
    """The native build's entries for the interpreter, one per C type the game calls through pointers as."""
    c = ("x = CALL_INDIRECT(instance->w2c_0x5F_indirect_function_table, u32 (*)(void*, u32, f32), w2c_game_t3, v, (void*)instance, a, b);\n"
         "CALL_INDIRECT(instance->w2c_0x5F_indirect_function_table, void (*)(void*), w2c_game_t1, v, (void*)instance);\n")
    types = interp.pointer_types([c, c])
    assert types == ["u32 (*)(void*, u32, f32)", "void (*)(void*)"]
    text, _ = interp.generate("", [], [], pointer_types=types)
    assert "static u32 P_0(void *g, u32 a0, f32 a1) { (void)g; static const uint8_t t_[] = {0, 2};" in text
    assert "const uint64_t a_[] = {(uint64_t)a0, BF(a1)}" in text and "return (u32)v_;" in text
    assert '{"u32 (*)(void*, u32, f32)", (void *)P_0}' in text and "port_mips_pointer_type_count = 2;" in text


def test_a_structure_the_game_passes_by_value_is_copied_into_the_argument_words(tmp_path):
    """The game's C passing a structure by value to a function with no C: its callers' declarations give its size,
    the bridge copies its words in where o32 passes them, the arguments after it after all of it."""
    (tmp_path / "a.ll").write_text("%struct.Args = type { [4 x i32] }\n"
                                   "declare void @func_80005000(ptr noundef byval(%struct.Args) align 4, i32 noundef)\n")
    calls = interp.by_value([tmp_path / "a.ll"], declared=True)
    assert calls == {"func_80005000": {0: 4}}
    game_h = "/* import: 'env' 'func_80005000' */\nvoid w2c_env_func_80005000(struct w2c_env*, u32, u32);\n"
    text, counts = interp.generate(game_h, [], [], byval_calls=calls)
    assert counts["imports"] == 1
    assert "port_mips_enter(r, f, 5)" in text
    assert "port_mips_copy_in(r, 0, (uint32_t)a0, 4); r[8] = S((uint32_t)(a1));" not in text  # (word 4: the stack)
    assert "port_mips_copy_in(r, 0, (uint32_t)a0, 4); port_mips_sw((uint32_t)r[29] + 16u, (uint32_t)(a1));" in text
    assert interp.mips_signatures([tmp_path / "a.ll"], [], [], by_value_calls=calls) == []


def test_the_browser_build_says_each_pointer_calls_types():
    """webir.py: a call through a pointer carries its arguments' and result's types above its count."""
    import sys

    from decompnet.port.wasm import web
    sys.path.insert(0, str(web.HERE))
    import webir  # (beir.py runs it as a script beside adapt.py)
    out = []
    ok = webir._indirect("  %r = call float %fp(i32 noundef 1, float noundef %x, double noundef %y)", webir.Fresh(), out)
    assert ok
    call = next(line for line in out if "@port_icall" in line)
    count = int(call.rsplit("i32 ", 1)[1].rstrip(")")) & 0xFFFFFFFF
    assert count & 0xFF == 3 and (count >> 8) & 0x3F == 0 | 2 << 2 | 3 << 4 and (count >> 24) & 3 == 2


def test_tlb_probe_maps_a_compatibility_stack_pair(run):
    def mtc0(rt, rd):
        return r_type(0, rd=rd, rs=4, rt=rt, op=16)

    def mfc0(rt, rd):
        return r_type(0, rd=rd, rt=rt, op=16)

    words = [mtc0("a0", 10), 0x42000008, mfc0("v0", 0), 0x42000001,
             mfc0("v1", 3), JR_RA, NOP]
    result = run(words, 0x1403E000)
    assert result["v0"] == 0
    assert result["v1"] == (0x1403F000 >> 6) | 2
    assert run(words, 0x20000000)["v0"] == 0x80000000


def test_interpreter_preserves_its_frames_when_c_reenters_recompiled_code(tmp_path):
    """A C callback can re-enter recompiled code while an interpreted caller has a live return address."""
    from decompnet.port.wasm import recomp

    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler")
    # Use the runtime's actual stack handoff, with a small stand-in for a recompiled callee that writes its frame.
    runtime = recomp.BRIDGE_RUNTIME.split("void port_recomp_thread", 1)[0]
    source = HARNESS % {"c": C_FUNCTION, "code": CODE, "data": DATA}
    # the types the runtime's float-register switch (the game's Status.FR) needs from N64Recomp's header, stood in
    stand_in = (
        "typedef union { double d; struct { uint32_t u32l, u32h; }; } fpr;\n"
        "typedef struct { fpr f0, f1; uint32_t *f_odd; int mips3_float_mode; } recomp_context;\n"
        "static uint32_t cop0_status_value = 0x04000000u;\n"
        "uint32_t port_cop0_status(void) { return cop0_status_value; }\n"
        "void port_cop0_status_set(uint32_t value) { cop0_status_value = value; }\n")
    source = source.replace("static int c_calls;", stand_in + runtime + "\nstatic int c_calls;")
    source = source.replace("(void)f; c_calls++; r[2]", "(void)f; c_calls++; memset(mem + port_recomp_sp - 4, 0, 4); r[2]")
    source = source.replace("port_mips_enter(r, f, 8);", "port_mips_enter(r, f, 8); port_recomp_sp = (uint32_t)r[29];")
    source = source.replace("printf(\" %d %08X %08X\\n\", c_calls, __builtin_bswap32(w0), __builtin_bswap32(w1));",
                            "printf(\" %d %08X %08X %d\\n\", c_calls, __builtin_bswap32(w0), __builtin_bswap32(w1), "
                            "port_recomp_sp == (uint32_t)r[29] && (uint32_t)r[31] == 0xFFFFFFF0u);")
    harness = tmp_path / "harness.c"
    harness.write_text(source)
    program = tmp_path / "interp"
    done = subprocess.run([cc, "-O1", "-o", str(program), str(harness), str(MIPS), "-lm"],
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    words = [addiu("sp", "sp", -16), sw("ra", 12, "sp"), jal(C_FUNCTION), NOP,
             addiu("v0", "zero", 77), lw("ra", 12, "sp"), JR_RA, addiu("sp", "sp", 16)]
    code = tmp_path / "code.bin"
    code.write_bytes(b"".join(struct.pack(">I", word) for word in words))
    done = subprocess.run([str(program), str(code)], capture_output=True, text=True, timeout=30, check=True)
    fields = done.stdout.split()
    assert int(fields[0], 16) == 77
    assert fields[-1] == "1"  # both the recompiler's stack pointer and the interpreted return address survived


def test_relocated_sdk_pattern_is_rechecked_after_overlay_changes(tmp_path):
    """A matched module call uses the host, but replacing that memory restores normal interpretation."""
    cc = shutil.which('cc') or shutil.which('clang') or shutil.which('gcc')
    if not cc:
        pytest.skip('no C compiler')
    target = CODE + 0x100
    pattern = [addiu('v0', 'zero', 42), JR_RA, NOP, NOP, NOP, NOP, NOP, NOP]
    declarations = r'''
typedef struct { const uint32_t *words, *masks; uint32_t count; PortMipsCall call; } PortMipsPattern;
static const uint32_t words[] = {%s};
static const uint32_t masks[] = {%s};
const PortMipsPattern port_mips_patterns[] = {{words, masks, 8, c_function}};
const unsigned port_mips_pattern_count = 1;
''' % (', '.join(str(w) + 'u' for w in pattern), ', '.join(['0xFFFFFFFFu'] * len(pattern)))
    source = (HARNESS % {'c': C_FUNCTION, 'code': CODE, 'data': DATA}).replace(
        'const unsigned port_mips_call_count = 1;', 'const unsigned port_mips_call_count = 1;\n' + declarations)
    harness = tmp_path / 'harness.c'
    harness.write_text(source)
    binary = tmp_path / 'interp'
    built = subprocess.run([cc, '-O1', '-o', str(binary), str(harness), str(MIPS), '-lm'],
                           capture_output=True, text=True, timeout=120)
    assert built.returncode == 0, built.stderr
    # The first call matches the SDK pattern (3 * 10 + 5). Change one instruction and call the same address again.
    program = [addiu('sp', 'sp', -16), sw('ra', 12, 'sp'), addiu('a0', 'zero', 3), addiu('a1', 'zero', 5),
               jal(target), NOP, *_data_base(), sw('v0', 0, 't9'),
               lui('t0', target >> 16), ori('t0', 't0', target & 0xFFFF),
               lui('t1', addiu('v0', 'zero', 43) >> 16), ori('t1', 't1', addiu('v0', 'zero', 43) & 0xFFFF),
               sw('t1', 0, 't0'), jal(target), NOP, sw('v0', 4, 't9'),
               lui('t1', addiu('v0', 'zero', 42) >> 16), ori('t1', 't1', addiu('v0', 'zero', 42) & 0xFFFF),
               sw('t1', 0, 't0'), jal(target), NOP, lw('ra', 12, 'sp'), JR_RA, addiu('sp', 'sp', 16)]
    program += [NOP] * (0x100 // 4 - len(program)) + pattern
    data = tmp_path / 'code.bin'
    data.write_bytes(b''.join(struct.pack('>I', word) for word in program))
    result = subprocess.run([str(binary), str(data)], capture_output=True, text=True, timeout=30, check=True)
    fields = result.stdout.split()
    assert int(fields[0], 16) == 35
    assert int(fields[6], 16) == 35 and int(fields[7], 16) == 43
    assert fields[14] == '2'
    # A recompiled caller can enter the same function directly, without an interpreted jump first.
    data.write_bytes(b''.join(struct.pack('>I', word) for word in pattern))
    result = subprocess.run([str(binary), str(data), '3', '5'], capture_output=True, text=True, timeout=30, check=True)
    fields = result.stdout.split()
    assert int(fields[0], 16) == 35 and fields[14] == '1'


def mfc0(rt, rd): return 0x40000000 | REG.get(rt, rt) << 16 | rd << 11
def mtc0(rt, rd): return 0x40800000 | REG.get(rt, rt) << 16 | rd << 11
SYSCALL, ERET = 12, 0x42000018


def test_a_syscall_is_an_exception_the_games_own_handler_answers(run):
    """An overlay game's thunks are `syscall N` words and its exception handler (the jump libultra's osInitialize
    copies to 0x80000180) sends EPC on to a dispatcher; the interpreter skipped the syscall, so every call into an
    overlay did nothing. The syscall takes the exception (EPC is its address, Cause says syscall), the handler runs
    from RAM, and eret goes where EPC says: here, past the syscall and its slot."""
    handler = CODE + 0x200
    jump = 0x08000000 | (handler & 0x0FFFFFFF) >> 2
    prog = [*_data_base(), lui("t0", 0x8000), ori("t0", "t0", 0x0180), lui("t1", jump >> 16), ori("t1", "t1", jump & 0xFFFF),
            sw("t1", 0, "t0"), SYSCALL, NOP, addiu("v0", "zero", 77), JR_RA, NOP]
    prog += [NOP] * (0x200 // 4 - len(prog))
    prog += [mfc0("t2", 13), sw("t2", 0, "t9"), mfc0("t3", 14), addiu("t3", "t3", 8), mtc0("t3", 14), ERET, NOP]
    out = run(prog)
    assert out["v0"] == 77  # the handler's eret resumed past the syscall
    assert out["data"][0] == 8 << 2  # Cause.ExcCode: syscall


def test_a_function_in_a_tlb_window_has_code_where_the_interpreter_fetches_it(tmp_path):
    """A game linked at 0x200000 and loaded at 0 spells its functions 0x80200000 | offset, where RAM holds nothing:
    its bytes are at the physical alias, which the interpreter's fetch reads. `port_mips_has_code` asked RAM at the
    spelling alone, said no code and skipped every such function the recompiler couldn't translate, so none of an
    Armorines-like game's threads ever did their work."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler")
    source = HARNESS % {"c": C_FUNCTION, "code": CODE, "data": DATA}
    source = source.replace("unsigned port_tlb_n;", "unsigned port_tlb_n = 1;")
    source = source.replace("uint32_t port_tlb_canon(uint32_t a) { return a; }",
                            "uint32_t port_tlb_canon(uint32_t a) { return a >= 0x200000u && a < 0x300000u ? a | 0x80000000u : a; }")
    source = source.replace("uint32_t port_tlb_alias(uint32_t a) { return a; }",
                            "uint32_t port_tlb_alias(uint32_t a) { return a >= 0x80200000u && a < 0x80300000u ? "
                            "0x80000000u + (a - 0x80200000u) : 0; }")
    source = source.replace("int main(int argc, char **argv) {", """int port_mips_has_code(uint32_t address);
int main(int argc, char **argv) {
    if (argc > 2 && !strncmp(argv[2], "has:", 4)) {
        mem = mmap(NULL, 1ull << 32, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON | MAP_NORESERVE, -1, 0);
        uint32_t w = __builtin_bswap32(0x24020001u); /* addiu $v0, $zero, 1: at physical 0x500 only */
        memcpy(mem + 0x80000500u, &w, 4);
        printf("%d\\n", port_mips_has_code((uint32_t)strtoul(argv[2] + 4, NULL, 16)));
        return 0;
    }""", 1)
    harness = tmp_path / "harness.c"
    harness.write_text(source)
    program = tmp_path / "interp"
    done = subprocess.run([cc, "-O1", "-o", str(program), str(harness), str(MIPS), "-lm"],
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    code = tmp_path / "unused.bin"
    code.write_bytes(b"")

    def has(address):
        out = subprocess.run([str(program), str(code), f"has:{address:08X}"], capture_output=True, text=True,
                             timeout=30, check=True)
        return out.stdout.strip()

    assert has(0x80000500) == "1"  # code at its own address
    assert has(0x80200500) == "1"  # the window's spelling: the bytes are at 0x80000500
    assert has(0x00200500) == "1"  # as the game's own pointer has it, below KSEG0
    assert has(0x80200600) == "0"  # nothing at the alias either
    assert has(0x80100000) == "0"  # outside any window


def test_a_function_spelled_at_a_window_kept_at_its_physical_address_has_code_there(tmp_path):
    """A game linked at virtual 0x100000 and loaded at physical 0 spells its functions 0x80100000 | offset, where RAM
    holds zeros: the console runs them at the physical alias, which the host keeps the window at (port_tlb_call). The
    interpreter's entry went there but `port_mips_has_code` asked the spelling, found nothing and skipped, as a stub,
    every function the recompiler could not translate (a switch through a table): Shadow Man's main thread called one
    and never got an answer."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler")
    source = HARNESS % {"c": C_FUNCTION, "code": CODE, "data": DATA}
    source = source.replace("unsigned port_tlb_n;", "unsigned port_tlb_n = 1;")
    source = source.replace("uint32_t port_tlb_call(uint32_t a) { return a; }",
                            "uint32_t port_tlb_call(uint32_t a) { return a >= 0x80100000u && a < 0x80200000u ? "
                            "0x80000000u + (a - 0x80100000u) : a; }")
    source = source.replace("int main(int argc, char **argv) {", """int port_mips_has_code(uint32_t address);
int main(int argc, char **argv) {
    if (argc > 2 && !strncmp(argv[2], "has:", 4)) {
        mem = mmap(NULL, 1ull << 32, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON | MAP_NORESERVE, -1, 0);
        uint32_t w = __builtin_bswap32(0x24020001u); /* addiu $v0, $zero, 1: at physical 0x12470 only */
        memcpy(mem + 0x80012470u, &w, 4);
        printf("%d\\n", port_mips_has_code((uint32_t)strtoul(argv[2] + 4, NULL, 16)));
        return 0;
    }""", 1)
    harness = tmp_path / "harness.c"
    harness.write_text(source)
    program = tmp_path / "interp"
    done = subprocess.run([cc, "-O1", "-o", str(program), str(harness), str(MIPS), "-lm"],
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    code = tmp_path / "unused.bin"
    code.write_bytes(b"")

    def has(address):
        out = subprocess.run([str(program), str(code), f"has:{address:08X}"], capture_output=True, text=True,
                             timeout=30, check=True)
        return out.stdout.strip()

    assert has(0x80012470) == "1"  # the physical alias itself
    assert has(0x80112470) == "1"  # the spelling: the code is at the alias the window is kept at
    assert has(0x80112474) == "0"  # nothing next to it


def test_a_fetch_from_an_invalid_page_takes_the_general_vector_with_the_tlbl_cause(tmp_path):
    """A page an entry covers with its valid bit clear is TLB Invalid: the console enters the general exception vector
    (0x80000180, not the refill vector 0x80000000), with Cause TLBL (code 2), BadVAddr and EPC the fetch. The game's
    handler there pages the code in (here: `tlbwi` of a valid entry) and returns with eret to retry the fetch."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler")
    target = CODE + 0x300
    source = HARNESS % {"c": C_FUNCTION, "code": CODE, "data": DATA}
    source = source.replace("unsigned port_tlb_n;", "unsigned port_tlb_n = 1; static int tlb_mapped;")
    source = source.replace("uint32_t port_tlb_canon(uint32_t a) { return a; }",
                            "uint32_t port_tlb_canon(uint32_t a) { return tlb_mapped && a >= 0x400000u && a < 0x401000u ? "
                            "a - 0x400000u + 0x%08X : a; }" % target)
    source = source.replace("void port_tlb_set(unsigned i, uint32_t pm, uint32_t hi, uint32_t lo0, uint32_t lo1) { (void)i; (void)pm; "
                            "(void)hi; (void)lo0; (void)lo1; }",
                            "void port_tlb_set(unsigned i, uint32_t pm, uint32_t hi, uint32_t lo0, uint32_t lo1) { (void)i; (void)pm; "
                            "(void)hi; (void)lo0; (void)lo1; tlb_mapped = 1; }")
    source = source.replace("int port_tlb_invalid(uint32_t a) { (void)a; return 0; }",
                            "int port_tlb_invalid(uint32_t a) { return !tlb_mapped && a >= 0x400000u && a < 0x401000u; }")
    harness = tmp_path / "harness.c"
    harness.write_text(source)
    program = tmp_path / "interp"
    done = subprocess.run([cc, "-o", str(program), str(harness), str(MIPS), "-lm"], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    handler = CODE + 0x200
    jump = 0x08000000 | (handler & 0x0FFFFFFF) >> 2
    # a jump at the general vector 0x80000180 to the handler, and the refill vector 0x80000000 left empty (a refill
    # would run zeros: it must not be entered)
    prog = [*_data_base(), lui("t0", 0x8000), lui("t1", jump >> 16), ori("t1", "t1", jump & 0xFFFF), sw("t1", 0x180, "t0"),
            lui("t2", 0x40), r_type(8, rs="t2"), NOP]  # jr 0x00400000: its page is invalid
    prog += [NOP] * (0x200 // 4 - len(prog))
    prog += [mfc0("t3", 13), sw("t3", 0, "t9"), mfc0("t4", 8), sw("t4", 4, "t9"), 0x42000002, NOP, ERET, NOP]
    prog += [NOP] * (0x300 // 4 - len(prog))
    prog += [addiu("v0", "zero", 66), JR_RA, NOP]  # the page's code, once the handler mapped it
    code = tmp_path / "code.bin"
    code.write_bytes(b"".join(struct.pack(">I", w & 0xFFFFFFFF) for w in prog))
    out = subprocess.run([str(program), str(code)], capture_output=True, text=True, timeout=30, check=True).stdout.split()
    assert int(out[0], 16) == 66  # the general handler ran, mapped the page, and eret retried the fetch
    assert int(out[6], 16) == 2 << 2  # Cause: TLBL
    assert int(out[7], 16) == 0x400000  # BadVAddr


def test_a_fetch_from_an_unmapped_page_takes_the_games_tlb_refill_handler(tmp_path):
    """A game that maps its main program page by page (a shooter linking it at 0x7F000000 and bringing 4 KB pages in as
    the code first runs) installs a refill handler at the vector 0x80000000: it reads Context, loads the page pair's
    EntryLo words, writes them with tlbwr and returns with eret to retry. The interpreter had no refill exception,
    so the fetch read zeros. Here the handler reads Context and BadVAddr, `tlbwr` maps the page, eret retries."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler")
    target = CODE + 0x300
    source = HARNESS % {"c": C_FUNCTION, "code": CODE, "data": DATA}
    source = source.replace("unsigned port_tlb_n;", "unsigned port_tlb_n = 1; static int tlb_mapped;")
    source = source.replace("uint32_t port_tlb_canon(uint32_t a) { return a; }",
                            "uint32_t port_tlb_canon(uint32_t a) { return tlb_mapped && a >= 0x400000u && a < 0x401000u ? "
                            "a - 0x400000u + 0x%08X : a; }" % target)
    source = source.replace("void port_tlb_write(uint32_t pm, uint32_t hi, uint32_t lo0, uint32_t lo1) { (void)pm; (void)hi; "
                            "(void)lo0; (void)lo1; }",
                            "void port_tlb_write(uint32_t pm, uint32_t hi, uint32_t lo0, uint32_t lo1) { (void)pm; (void)hi; "
                            "(void)lo0; (void)lo1; tlb_mapped = 1; }")
    harness = tmp_path / "harness.c"
    harness.write_text(source)
    program = tmp_path / "interp"
    done = subprocess.run([cc, "-O1", "-o", str(program), str(harness), str(MIPS), "-lm"],
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    handler = CODE + 0x200
    jump = 0x08000000 | (handler & 0x0FFFFFFF) >> 2
    prog = [*_data_base(), lui("t0", 0x8000), lui("t1", jump >> 16), ori("t1", "t1", jump & 0xFFFF), sw("t1", 0, "t0"),
            lui("t2", 0x40), r_type(8, rs="t2"), NOP]  # jr 0x00400000: no TLB entry yet
    prog += [NOP] * (0x200 // 4 - len(prog))
    prog += [mfc0("t3", 4), sw("t3", 0, "t9"), mfc0("t4", 8), sw("t4", 4, "t9"), 0x42000006, NOP, ERET, NOP]
    prog += [NOP] * (0x300 // 4 - len(prog))
    prog += [addiu("v0", "zero", 55), JR_RA, NOP]  # the page's code, once mapped
    code = tmp_path / "code.bin"
    code.write_bytes(b"".join(struct.pack(">I", w & 0xFFFFFFFF) for w in prog))
    out = subprocess.run([str(program), str(code)], capture_output=True, text=True, timeout=30, check=True).stdout.split()
    assert int(out[0], 16) == 55  # the handler mapped the page and the retried fetch ran the code
    assert int(out[6], 16) == 0x400000 >> 13 << 4  # Context: the faulting page number in bits 4 and up
    assert int(out[7], 16) == 0x400000  # BadVAddr


def test_trace_level_3_keeps_the_last_jumps_for_a_wild_one():
    """A thread that ends up running data (a jump to a ROM table, a call through a stale pointer) shows where it went
    wrong only in the jumps before it: PORT_INTERP_TRACE=3 prints each thread's last 40 when the interpreter meets an
    instruction it can't run or no code at all."""
    text = MIPS.read_text()
    assert "PORT_INTERP_TRACE=3" in text and "jump_history(\"an unknown instruction\", pc)" in text
    assert "jump_history(\"no instructions\", pc)" in text


def test_code_reached_through_a_window_goes_on_in_the_next_virtual_page_not_the_next_frame(tmp_path):
    """A game that pages its code in puts consecutive virtual pages in any frames. The interpreter runs the code at its
    spelling in RAM (the frame), so an instruction at the end of a page was followed by the next frame's: here the page at
    virtual 0x400000 sits at 0x80000400 and the next virtual page 0x401000 at 0x80001600; the instruction after the
    page's last word must come from the second."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler")
    source = HARNESS % {"c": C_FUNCTION, "code": CODE, "data": DATA}
    source = source.replace("unsigned port_tlb_n;", "unsigned port_tlb_n = 1;")
    source = source.replace("uint32_t port_tlb_canon(uint32_t a) { return a; }",
                            "uint32_t port_tlb_canon(uint32_t a) { return a >= 0x400000u && a < 0x401000u ? a - 0x400000u + 0x80000400u : "
                            "a >= 0x401000u && a < 0x402000u ? a - 0x401000u + 0x80001600u : a; }")
    harness = tmp_path / "harness.c"
    harness.write_text(source)
    program = tmp_path / "interp"
    done = subprocess.run([cc, "-o", str(program), str(harness), str(MIPS), "-lm"], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    prog = [lui("t2", 0x40), ori("t2", "t2", 0x0FF8), r_type(8, rs="t2"), NOP]  # jr 0x00400FF8: two words before the page's end
    prog += [NOP] * (0x3F8 // 4 - len(prog))
    prog += [addiu("v0", "zero", 10), addiu("v0", "v0", 5)]  # the page's last two words (frame offsets 0x3F8, 0x3FC)
    prog += [addiu("v0", "zero", 99), JR_RA, NOP]            # the NEXT FRAME's code: the wrong place to go on at
    prog += [NOP] * (0x600 // 4 - len(prog))
    prog += [JR_RA, NOP]                                     # the next virtual page's first word: where it goes on
    code = tmp_path / "code.bin"
    code.write_bytes(b"".join(struct.pack(">I", w & 0xFFFFFFFF) for w in prog))
    out = subprocess.run([str(program), str(code)], capture_output=True, text=True, timeout=30, check=True).stdout.split()
    assert int(out[0], 16) == 15


def test_a_page_the_refill_handler_mapped_goes_on_in_the_next_virtual_page(tmp_path):
    """A refill returns with eret to EPC, the virtual address of the fetch that missed. The interpreter ran on at the
    page's frame with no virtual address kept, so a relative branch or the end of the page landed in the next
    *frame* (the neighbouring physical page: "no instructions" in a game that pages its code in through a plain
    refill handler). Here a fetch in virtual page 0x400000 faults; the handler maps it (`tlbwr`) and returns. The
    code takes a relative branch (which moves the interpreter to the page's frame), and a second branch at the
    page's end must land in the next virtual page, not the neighbouring frame."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler")
    source = HARNESS % {"c": C_FUNCTION, "code": CODE, "data": DATA}
    source = source.replace("unsigned port_tlb_n;", "unsigned port_tlb_n = 1; static int tlb_mapped;")
    source = source.replace("uint32_t port_tlb_canon(uint32_t a) { return a; }",
                            "uint32_t port_tlb_canon(uint32_t a) { return !tlb_mapped ? a : a >= 0x400000u && a < 0x401000u ? "
                            "a - 0x400000u + 0x80000400u : a >= 0x401000u && a < 0x402000u ? a - 0x401000u + 0x80001600u : a; }")
    source = source.replace("void port_tlb_write(uint32_t pm, uint32_t hi, uint32_t lo0, uint32_t lo1) { (void)pm; (void)hi; "
                            "(void)lo0; (void)lo1; }",
                            "void port_tlb_write(uint32_t pm, uint32_t hi, uint32_t lo0, uint32_t lo1) { (void)pm; (void)hi; "
                            "(void)lo0; (void)lo1; tlb_mapped = 1; }")
    harness = tmp_path / "harness.c"
    harness.write_text(source)
    program = tmp_path / "interp"
    done = subprocess.run([cc, "-O1", "-o", str(program), str(harness), str(MIPS), "-lm"],
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    handler = CODE + 0x100
    jump = 0x08000000 | (handler & 0x0FFFFFFF) >> 2
    prog = [lui("t0", 0x8000), lui("t1", jump >> 16), ori("t1", "t1", jump & 0xFFFF), sw("t1", 0, "t0"),
            lui("t2", 0x40), ori("t2", "t2", 0x0FE0), r_type(8, rs="t2"), NOP]  # jr 0x00400FE0: no TLB entry yet
    prog += [NOP] * (0x100 // 4 - len(prog))
    prog += [0x42000006, NOP, ERET, NOP]  # the refill handler: tlbwr, eret
    prog += [NOP] * (0x3E0 // 4 - len(prog))
    beq = lambda off: i_type(4, "zero", "zero", off)
    prog += [beq(2), NOP, addiu("v0", "zero", 77), addiu("v0", "zero", 10)]  # a taken branch, over the 77
    prog += [NOP, NOP, beq(1), NOP]                                          # at virtual 0x400FF8: into the next page
    prog += [addiu("v0", "zero", 99), JR_RA, NOP]            # the next FRAME's code: the wrong place to go on at
    prog += [NOP] * (0x600 // 4 - len(prog))
    prog += [JR_RA, NOP]                                     # the next virtual page's first word
    code = tmp_path / "code.bin"
    code.write_bytes(b"".join(struct.pack(">I", w & 0xFFFFFFFF) for w in prog))
    out = subprocess.run([str(program), str(code)], capture_output=True, text=True, timeout=30, check=True).stdout.split()
    assert int(out[0], 16) == 10  # (99: the neighbouring frame; the next virtual page's code returns with $v0 as it was)


def test_a_call_from_window_code_returns_to_the_virtual_address(tmp_path):
    """$ra of a call made from code reached through a window is the virtual address after the call, as on the console:
    the frame it sits in may hold another page by the time the callee returns."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler")
    source = HARNESS % {"c": C_FUNCTION, "code": CODE, "data": DATA}
    source = source.replace("unsigned port_tlb_n;", "unsigned port_tlb_n = 1;")
    source = source.replace("uint32_t port_tlb_canon(uint32_t a) { return a; }",
                            "uint32_t port_tlb_canon(uint32_t a) { return a >= 0x400000u && a < 0x401000u ? a - 0x400000u + 0x80000400u : a; }")
    harness = tmp_path / "harness.c"
    harness.write_text(source)
    program = tmp_path / "interp"
    done = subprocess.run([cc, "-o", str(program), str(harness), str(MIPS), "-lm"], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    prog = [lui("t2", 0x40), ori("t2", "t2", 0x0F00), r_type(8, rs="t2"), NOP]  # jr 0x00400F00 (frame offset 0x300)
    prog += [NOP] * (0x300 // 4 - len(prog))
    prog += [r_type(0x25, rd="s0", rs="ra", rt="zero"), jal(0x400F40), NOP, r_type(8, rs="s0"), NOP]  # keep $ra; jal the callee (virtual 0x400F40); return
    prog += [NOP] * (0x340 // 4 - len(prog))
    prog += [r_type(0x25, rd="v0", rs="ra", rt="zero"), JR_RA, NOP]             # the callee: $v0 = $ra
    code = tmp_path / "code.bin"
    code.write_bytes(b"".join(struct.pack(">I", w & 0xFFFFFFFF) for w in prog))
    out = subprocess.run([str(program), str(code)], capture_output=True, text=True, timeout=30, check=True).stdout.split()
    assert int(out[0], 16) == 0x400F0C


def test_the_interpreter_reports_its_rate_and_a_nop_does_not_search_the_windows():
    """A game that spins in interpreted code and one that is slow look alike from outside: PORT_INTERP_STATS=1 prints
    the instructions run and their rate (a racing game's decompressor and a puzzle game's main loop both ran at
    30-125 M/s, so neither was too slow: the second just never blocked). And a nop (a zero word, common in delay
    slots) first looks at its block of four, so the search through the game's TLB windows (port_tlb_alias) happens
    only where the block really is empty."""
    text = MIPS.read_text()
    assert 'getenv("PORT_INTERP_STATS")' in text and "interpreter stats:" in text and "interp_stats(0x10000)" in text
    fetch = text[text.index("HOT uint32_t fetch("):text.index("HOT uint64_t ld(")]
    assert fetch.index("!lw(blk)") < fetch.index("port_tlb_alias(")
