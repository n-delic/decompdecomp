"""Recompiled C where the decomp has none yet, in the port's native build (port/wasm/recomp.py): homemade recompiler
output, never a game's.

A racing game's port stopped at the first function it ran with no C (its first thread's code). N64Recomp's C for it
runs in the host instead, on the port's memory, bridged to the game's C and the host both ways.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

from decompnet.port.wasm import build, interp, recomp

HEADER = Path.home() / "Library/Application Support/decompnet/toolchains/n64recomp/include/recomp.h"


def _header():
    try:
        from decompnet.consoles.n64.recomp import toolchain_folder
        found = toolchain_folder() / "include" / "recomp.h"
    except Exception:  # noqa: BLE001
        found = HEADER
    if not found.is_file():
        pytest.skip("N64Recomp isn't installed (its recomp.h)")
    return found


def test_stores_become_calls_on_the_big_endian_memory():
    text = ("    MEM_W(0X10, ctx->r29) = ctx->r31;\n    MEM_B(0X0, ctx->r4) = ctx->r5;\n"
            "    ctx->r2 = MEM_W(ctx->r4, 0X14);\n")
    out = recomp.stores(text)
    assert "STORE_W(0X10, ctx->r29, ctx->r31);" in out and "STORE_B(0X0, ctx->r4, ctx->r5);" in out
    assert "ctx->r2 = MEM_W(ctx->r4, 0X14);" in out  # a load stays a load


def test_o32_places_arguments_as_the_invokers_read_them():
    assert recomp.o32(["f32", "f32"]) == [("fpr", 12), ("fpr", 14)]
    assert recomp.o32(["u32", "f32"]) == [("gpr", 0), ("gprf", 1)]  # a float after a word: in a word
    assert recomp.o32(["f64", "u32"]) == [("dfpr", 12), ("gpr", 2)]
    assert recomp.o32(["u32", "u64", "u32"]) == [("gpr", 0), ("gpr64", 2), ("gpr", 4)]  # aligned pair; then the stack


def test_the_port_header_has_no_lvalue_memory(tmp_path):
    text = recomp.header(_header().read_text())
    assert "STORE_W(" in text and "port_lw(" in text and "0xFFFFFFFF80000000" not in text


def test_the_port_header_multiplies_64_bits_without_a_128_bit_integer(tmp_path):
    """The Miyoo Mini's 32-bit ARM has no __int128, and N64Recomp's header stops there: the port's header gives
    DMULT and DMULTU from 32-bit halves, checked here against a 128-bit product where the computer has one."""
    text = recomp.header(_header().read_text())
    assert '#error "128-bit integer type not found"' not in text and recomp.MULTIPLY_32 in text
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if cc is None:
        pytest.skip("no C compiler")
    (tmp_path / "check.c").write_text("#include <stdint.h>\n#include <stdio.h>\n" + recomp.MULTIPLY_32 + r"""
int main(void) {
    const uint64_t v[] = {0, 1, 2, 0x7FFFFFFFull, 0x80000000ull, 0xFFFFFFFFull, 0x100000000ull, 0x123456789ABCDEFull,
                          0x7FFFFFFFFFFFFFFFull, 0x8000000000000000ull, 0xFFFFFFFFFFFFFFFFull, 0xDEADBEEFCAFEF00Dull};
    int bad = 0;
    for (unsigned i = 0; i < sizeof v / sizeof *v; i++) for (unsigned j = 0; j < sizeof v / sizeof *v; j++) {
        uint64_t lo, hi; int64_t slo, shi;
        unsigned __int128 u = (unsigned __int128)v[i] * v[j];
        __int128 s = (__int128)(int64_t)v[i] * (int64_t)v[j];
        DMULTU(v[i], v[j], &lo, &hi);
        DMULT((int64_t)v[i], (int64_t)v[j], &slo, &shi);
        bad += lo != (uint64_t)u || hi != (uint64_t)(u >> 64) || slo != (int64_t)s || shi != (int64_t)(s >> 64);
    }
    printf("%d\n", bad);
    return 0;
}
""")
    done = subprocess.run([cc, "-O1", str(tmp_path / "check.c"), "-o", str(tmp_path / "check")], capture_output=True,
                          text=True)
    if done.returncode:
        pytest.skip(f"this computer's C compiler has no __int128 to check against: {done.stderr[:200]}")
    assert subprocess.run([str(tmp_path / "check")], capture_output=True, text=True).stdout.strip() == "0"


def _output(folder: Path):
    """A homemade Recompile: three functions, one the game has C for, one the host has, one only recompiled."""
    funcs = folder / "funcs"
    funcs.mkdir(parents=True)
    (folder / "recomp.json").write_text("{}")
    (folder / "syms.toml").write_text(
        '[[section]]\nfunctions = [\n'
        '    { name = "func_80001000", vram = 0x80001000, size = 0x10 },\n'
        '    { name = "osSendMesg", vram = 0x80002000, size = 0x10 },\n'
        '    { name = "func_80003000", vram = 0x80003000, size = 0x10 },\n]\n')
    (funcs / "funcs.h").write_text('#include "recomp.h"\n'
                                   "void func_80001000(uint8_t* rdram, recomp_context* ctx);\n"
                                   "void osSendMesg(uint8_t* rdram, recomp_context* ctx);\n"
                                   "void func_80003000(uint8_t* rdram, recomp_context* ctx);\n")
    body = "    ctx->r2 = MEM_W(ctx->r4, 0X0);\n    MEM_W(0X4, ctx->r4) = ctx->r2;\n    return;\n;}\n"
    (funcs / "funcs_0.c").write_text(
        '#include "recomp.h"\n#include "funcs.h"\n\n'
        "RECOMP_FUNC void func_80001000(uint8_t* rdram, recomp_context* ctx) {\n" + body +
        "RECOMP_FUNC void osSendMesg(uint8_t* rdram, recomp_context* ctx) {\n" + body +
        "RECOMP_FUNC void func_80003000(uint8_t* rdram, recomp_context* ctx) {\n"
        "    func_80001000(rdram, ctx);\n    osSendMesg(rdram, ctx);\n    __osSiRawStartDma_recomp(rdram, ctx);\n"
        + body)


GAME_H = ("/* export: 'func_80001000' */\nu32 w2c_game_func_80001000(w2c_game*, u32);\n"
          "/* import: 'env' 'func_80003000' */\nu32 w2c_env_func_80003000(struct w2c_env*, u32, f32);\n")
HOST_C = ("u32 w2c_env_osSendMesg(struct w2c_env *e, u32 mq, u32 msg, u32 flag) { return 0; }\n"
          "u32 w2c_env_0x5F_osSiRawStartDma(struct w2c_env *e, u32 direction, u32 dram) {\n    return 0;\n}\n")


def test_each_function_runs_as_the_games_c_the_hosts_or_recompiled(tmp_path):
    folder = tmp_path / "recomp"
    _output(folder)
    host = tmp_path / "host.c"
    host.write_text(HOST_C)
    header = tmp_path / "recomp.h"
    header.write_text(_header().read_text())
    out = tmp_path / "build"
    prepared = recomp.prepare(folder, out, GAME_H, [], [host], header, {}, lambda *a: None)
    assert prepared["counts"] == {"decomp": 1, "host": 1, "recomp": 1, "missing": 0, "imports": 1}
    rec = (out / "rec_funcs_0.c").read_text()
    # only the function with no C left as recompiled C, renamed; its calls go to the R_ names
    assert "RECOMP_FUNC void R_func_80003000(" in rec and "R_func_80001000(rdram, ctx);" in rec
    assert "void R_func_80001000(uint8_t* rdram, recomp_context* ctx) {" not in rec and "R_osSendMesg(rdram" in rec
    assert "STORE_W(0X4, ctx->r4, ctx->r2);" in rec
    # PORT_RECOMP_CALLS and PORT_RECOMP_CHANGED: each recompiled function tells the host it was entered, when asked
    assert "if (port_recomp_watch) port_recomp_entered(0x80003000u, ctx); R_func_80003000_body(rdram, ctx);" in rec
    bridges = (out / "bridges.c").read_text()
    assert "void R_func_80001000(uint8_t* rdram, recomp_context* ctx) { (void)rdram; port_recomp_decomp(0x80001000u" in bridges
    assert "w2c_env_osSendMesg(NULL, (uint32_t)ctx->r4, (uint32_t)ctx->r5, (uint32_t)ctx->r6)" in bridges
    assert "void R___osSiRawStartDma_recomp(" in bridges and "w2c_env_0x5F_osSiRawStartDma(NULL" in bridges
    # the game's C calling the recompiled function: its import, typed, arguments as o32 passes them
    assert "u32 w2c_env_func_80003000(struct w2c_env *e, u32 a0, f32 a1)" in bridges and "port_rb32(a1)" in bridges
    assert "{0x80003000u, (void *)G_func_80003000}" in bridges  # through a pointer, or as a thread's entry
    # a call through a pointer gives way to a thread the retrace woke, as the game's C does at port_func
    assert "recomp_func_t *get_function(int32_t vram) {\n    port_safe_point();" in bridges
    assert "R___osSiRawStartDma_recomp(uint8_t* rdram" in (out / "funcs.h").read_text()
    # a function no listed one starts (the split missed it), reached through a pointer: the interpreter runs it
    assert "if (port_mips_has_code((uint32_t)vram)) {" in bridges and "return port_recomp_interpret;" in bridges
    # `j .`: the host parks the thread, as the interpreter's
    assert "void pause_self(uint8_t *rdram) { (void)rdram; port_idle_forever();" in bridges
    # into the game's C: $a0-$a3 in adapt.py's register words, for a callee that passes on what it doesn't declare
    assert "for (int i = 0; i < 4; i++) STORE_W(4 * i, 0x90000u, w[i]);" in bridges
    assert 'getenv("PORT_RECOMP_CALLS")' in bridges and 'getenv("PORT_RECOMP_CHANGED")' in bridges
    cc = shutil.which("cc") or shutil.which("clang")
    if cc:
        for unit in prepared["units"]:
            done = subprocess.run([cc, "-c", "-w", "-I", str(out), str(unit), "-o", str(unit.with_suffix(".o"))],
                                  capture_output=True, text=True)
            assert done.returncode == 0, done.stderr[-1500:]


def test_each_loop_of_recompiled_c_gives_other_threads_their_turn():
    """A racing game's boot thread (priority 10) spins until a higher thread sets a flag; in recompiled C the loop had
    no safe point, so the host never switched to that thread and the native build drew nothing. Backward branches
    get one; forward ones stay as they were."""
    text = ("L_80001000:\n    ctx->r2 = MEM_HU(ctx->r2, -0X1A8);\n    if (ctx->r2 == ctx->r3) {\n"
            "        goto L_80001000;\n    }\n    goto L_80001010;\n    ctx->r2 = 0;\nL_80001010:\n    return;\n")
    out = recomp.loops(text)
    assert "{ port_recomp_poll(); goto L_80001000; }" in out
    assert "    goto L_80001010;" in out and "port_recomp_poll(); goto L_80001010" not in out


def test_renamed_library_import_keeps_its_address_for_either_fallback(tmp_path):
    folder = tmp_path / "recomp"
    _output(folder)
    caller = tmp_path / "caller.ll"
    caller.write_text("declare double @func_80003000(i64)\n")
    library_names = {"func_80003000": "convertFixed"}
    renames = build.renames([caller], [], library_names)
    assert renames == library_names
    game_h = ("/* import: 'env' 'convertFixed' */\n"
              "f64 w2c_env_convertFixed(struct w2c_env*, u64);\n")

    interpreted, counts = interp.generate(game_h, [], [], library_names, renames=renames)
    assert counts["imports"] == 1
    assert "f64 w2c_env_convertFixed(struct w2c_env *e, u64 a0)" in interpreted
    assert "port_mips_run(0x80003000u, r, f)" in interpreted

    out = tmp_path / "build"
    prepared = recomp.prepare(folder, out, game_h, [], [], _header(), library_names, renames=renames)
    assert prepared["counts"]["imports"] == 1
    bridges = (out / "bridges.c").read_text()
    assert "f64 w2c_env_convertFixed(struct w2c_env *e, u64 a0)" in bridges
    assert "R_func_80003000(rdram, &c);" in bridges
    assert interp.generate(game_h, [], [], library_names, {"w2c_env_convertFixed"},
                           renames=renames)[1]["imports"] == 0


def test_a_library_import_can_only_inherit_one_address():
    renames = {"func_80003000": "convertFixed", "func_80004000": "convertFixed"}
    assert "convertFixed" not in recomp._import_addresses({}, renames)
    assert recomp._import_addresses({"convertFixed": 0x80003000}, renames)["convertFixed"] == 0x80003000


def test_the_port_header_has_the_loops_safe_point():
    text = recomp.header(_header().read_text())
    assert "static inline void port_recomp_poll(void)" in text and "port_safe_point();" in text


def test_a_function_named_as_the_c_programs_entry_is_renamed():
    """A racing game names its first C function `main`: as C that's a program's entry, and N64Recomp's output for it
    didn't compile until the port renamed every recompiled function R_<name>."""
    text = "RECOMP_FUNC void main(uint8_t* rdram, recomp_context* ctx) {\n    main(rdram, ctx);\n}\n"
    renamed = recomp.DEFINED.sub(lambda m: f"RECOMP_FUNC void R_{m.group(1)}(uint8_t* rdram, recomp_context* ctx)", text)
    assert recomp.CALL.sub(lambda m: f"R_{m.group(1)}", renamed).count("R_main") == 2


def test_a_hardware_register_gets_the_value_and_memory_its_big_endian_bytes(tmp_path):
    """A racing game's audio interface was told its buffer lengths byte-swapped (0xFC0 as 0xC00F0000): the word store
    swapped the value for memory before handing it to the register too. The length the game read back overflowed its
    sample count and its audio command list, which wrote over the synthesizer."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if cc is None:
        pytest.skip("no C compiler")
    (tmp_path / "t.c").write_text("#include <stdint.h>\n#include <stdio.h>\n#include <string.h>\ntypedef uint64_t gpr;\n"
                                  + recomp.LOADS + r"""
static uint64_t told[4]; static int n;
uint64_t port_io_store(uint64_t addr, uint64_t value) { (void)addr; told[n++] = value; return 48; }
uint64_t port_io_load(uint64_t addr) { (void)addr; return 48; }
void port_safe_point(void) {}
int main(void) {
    static uint8_t ram[64];
    port_sw(ram, 0xA4500004u, 0xFC0);
    port_sh(ram, 0xA4500008u, 0x1234);
    port_sw(ram, 8, 0x11223344u);
    printf("%llX %llX %02X%02X%02X%02X\n", (unsigned long long)told[0], (unsigned long long)told[1],
           ram[8], ram[9], ram[10], ram[11]);
    return 0;
}
""")
    done = subprocess.run([cc, str(tmp_path / "t.c"), "-o", str(tmp_path / "t")], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    out = subprocess.run([str(tmp_path / "t")], capture_output=True, text=True).stdout.split()
    assert out == ["FC0", "1234", "11223344"]


def test_a_function_that_runs_on_into_the_next_calls_it(tmp_path):
    """Hand-written assembly: a racing game's cosine adds a quarter turn and runs on into its sine, which the split
    lists as the next function. N64Recomp stops at the end of the first, so cosine computed nothing and the game's
    view matrices were all zeros. Its C now calls the next; one that ends in a return doesn't."""
    folder = tmp_path / "recomp"
    funcs = folder / "funcs"
    funcs.mkdir(parents=True)
    (folder / "recomp.json").write_text("{}")
    (folder / "syms.toml").write_text(
        '[[section]]\nfunctions = [\n'
        '    { name = "func_80001000", vram = 0x80001000, size = 0x4 },\n'
        '    { name = "func_80001004", vram = 0x80001004, size = 0x8 },\n'
        '    { name = "func_8000100C", vram = 0x8000100C, size = 0x8 },\n]\n')
    (funcs / "funcs.h").write_text('#include "recomp.h"\n' + "".join(
        f"void func_{a}(uint8_t* rdram, recomp_context* ctx);\n" for a in ("80001000", "80001004", "8000100C")))
    (funcs / "funcs_0.c").write_text(
        '#include "recomp.h"\n#include "funcs.h"\n\n'
        "RECOMP_FUNC void func_80001000(uint8_t* rdram, recomp_context* ctx) {\n"
        "    // 0x80001000: addiu       $a0, $a0, 0x4000\n    ctx->r4 = ADD32(ctx->r4, 0X4000);\n;}\n"
        "RECOMP_FUNC void func_80001004(uint8_t* rdram, recomp_context* ctx) {\n"
        "    // 0x80001004: jr          $ra\n    // 0x80001008: addiu       $v0, $a0, 0x1\n"
        "    ctx->r2 = ADD32(ctx->r4, 0X1);\n    return;\n"
        "    // 0x80001008: addiu       $v0, $a0, 0x1\n    ctx->r2 = ADD32(ctx->r4, 0X1);\n;}\n"
        "RECOMP_FUNC void func_8000100C(uint8_t* rdram, recomp_context* ctx) {\n"
        "    // 0x8000100C: jr          $ra\n    // 0x80001010: nop\n\n    return;\n    // 0x80001010: nop\n\n;}\n")
    header = tmp_path / "recomp.h"
    header.write_text(_header().read_text())
    host = tmp_path / "host.c"
    host.write_text("")
    out = tmp_path / "build"
    recomp.prepare(folder, out, "", [], [host], header, {}, lambda *a: None)
    rec = (out / "rec_funcs_0.c").read_text()
    assert rec.count("/* falls into the next function */") == 1
    first = rec[rec.index("R_func_80001000_body(uint8_t* rdram, recomp_context* ctx) {"):]
    assert "R_func_80001004(rdram, ctx); /* falls into the next function */" in first[:first.index(";}")]


def test_a_nan_is_reported_not_asserted():
    """N64Recomp asserts no float operand is NaN; the console computes with one (a racing game's frustum planes from
    a camera not set up yet), so the port says so once a function and goes on."""
    text = recomp.header(_header().read_text())
    assert "assert(val == val)" not in text and "port_recomp_nan(__func__)" in text


def test_link_branches_set_ra_before_the_condition_and_delay_slot():
    from decompnet.port.wasm.recomp import link_branches

    generated = """    // 0x80012380: bltzal      $zero, L_80012400
    if (SIGNED(0) < 0) {
        // 0x80012384: nop
        target(rdram, ctx);
    }
    // 0x80012388: bgezal      $zero, L_80012410
    if (SIGNED(0) >= 0) {
        // 0x8001238C: nop
        other(rdram, ctx);
    }
"""
    fixed = link_branches(generated)
    assert "ctx->r31 = (gpr)(int32_t)0x80012388u;\n    if (SIGNED(0) < 0)" in fixed
    assert "ctx->r31 = (gpr)(int32_t)0x80012390u;\n    if (SIGNED(0) >= 0)" in fixed
