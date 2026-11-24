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


def test_runtime_alias_reaches_the_original_when_recompilation_refuses_it(tmp_path):
    folder = tmp_path / "recomp"
    _output(folder)
    symbols = folder / "syms.toml"
    symbols.write_text(symbols.read_text().replace('name = "func_80003000"', 'name = "queue_pop"'))
    source = folder / "funcs/funcs_0.c"
    source.write_text(source.read_text().replace("func_80003000", "queue_pop").replace(
        "__osSiRawStartDma_recomp(rdram, ctx);", "queue_pop_recomp(rdram, ctx);"))
    (folder / "funcs/runtime.h").write_text("void queue_pop_recomp(uint8_t* rdram, recomp_context* ctx);\n")
    (folder / "recomp.json").write_text('{"refused": [{"name": "queue_pop"}]}')
    out = tmp_path / "build"
    recomp.prepare(folder, out, "", [], [], _header(), {}, lambda *a: None)
    bridges = (out / "bridges.c").read_text()
    assert 'port_recomp_missing(0x80003000u, "queue_pop"' in bridges
    assert "R_queue_pop_recomp(uint8_t* rdram, recomp_context* ctx) { (void)rdram; R_queue_pop(rdram, ctx); }" in bridges


def test_recompiled_automatic_name_uses_host_for_named_address(tmp_path):
    folder = tmp_path / "recomp"
    _output(folder)
    host = tmp_path / "host.c"
    host.write_text(HOST_C)
    out = tmp_path / "build"
    recomp.prepare(folder, out, "", ["osSendMesg 0x80003000 func"], [host], _header(), {}, lambda *a: None)
    bridges = (out / "bridges.c").read_text()
    assert "void R_func_80003000(uint8_t* rdram, recomp_context* ctx)" in bridges
    assert "w2c_env_osSendMesg(NULL, (uint32_t)ctx->r4, (uint32_t)ctx->r5, (uint32_t)ctx->r6)" in bridges


def test_recompiled_address_with_ambiguous_host_names_keeps_game_code(tmp_path):
    folder = tmp_path / "recomp"
    _output(folder)
    host = tmp_path / "host.c"
    host.write_text(HOST_C + "u32 w2c_env_other(struct w2c_env *e, u32 a) { return 0; }\n")
    out = tmp_path / "build"
    prepared = recomp.prepare(folder, out, "", ["osSendMesg 0x80003000 func", "other 0x80003000 func"],
                              [host], _header(), {}, lambda *a: None)
    assert "RECOMP_FUNC void R_func_80003000(" in (out / "rec_funcs_0.c").read_text()
    assert prepared["counts"]["recomp"] == 2


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


def test_a_call_from_the_games_c_into_recompiled_code_is_a_safe_point():
    """A game thread that loops calling one recompiled function (no call through a pointer inside it) never let the
    higher-priority thread the retrace woke run, and nothing was drawn: entering recompiled code from the game's C
    (port_recomp_frame) is a safe point."""
    text = recomp.BRIDGE_RUNTIME
    body = text[text.index("static void port_recomp_frame("):]
    assert "port_safe_point();" in body[:body.index("memset(c, 0")]


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
unsigned port_tlb_n;
uint32_t port_tlb_canon(uint32_t a) { return a; }
void port_safe_point(void) {}
void port_boot_spin(void) {}
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


def test_link_branches_also_set_ra_for_jal_and_jalr():
    """A routine that copies its own $ra and ends in `jr $a1` jumped to 0 in the native build: N64Recomp's C call
    for `jal`/`jalr` never wrote $ra. `jalr $ra` (target and link in one register) and `jalr` are left to the call."""
    from decompnet.port.wasm.recomp import link_branches

    generated = ("    // 0x800CA2C0: jal         0x800137F4\n    // 0x800CA2C4: nop\n    R_func_800137F4(rdram, ctx);\n"
                 "    // 0x800CA300: jalr        $t9\n    // 0x800CA304: nop\n    LOOKUP_FUNC(ctx->r25)(rdram, ctx);\n"
                 "    // 0x800CA400: jalr        $ra\n    LOOKUP_FUNC(ctx->r31)(rdram, ctx);\n"
                 "    // 0x800CA500: j           0x80001000\n")
    fixed = link_branches(generated)
    assert "// 0x800CA2C0: jal         0x800137F4\n    ctx->r31 = (gpr)(int32_t)0x800CA2C8u;" in fixed
    assert "// 0x800CA300: jalr        $t9\n    ctx->r31 = (gpr)(int32_t)0x800CA308u;" in fixed
    assert fixed.count("ctx->r31 = (gpr)") == 2  # not `jalr $ra`, not a plain `j`


def test_a_games_float_mode_is_fr1_only_when_its_code_uses_odd_registers():
    """libultra's threads start with FR=0, where `mtc1 $zero,$f5; mtc1 $zero,$f4` makes a double zero: a fabs-like
    function of a platformer called itself for ever when every frame started in FR=1. A game whose recompiled C does
    arithmetic on an odd register (N64Recomp's CHECK_FR(ctx, odd): a tank game's `mul.s $f1`) runs with FR=1."""
    even = "    CHECK_FR(ctx, 2);\n    CHECK_FR(ctx, 12);\n    ctx->f2.d = CVT_D_S(ctx->f12.fl);\n"
    odd = "    CHECK_FR(ctx, 1);\n    CHECK_FR(ctx, 10);\n    CHECK_FR(ctx, 13);\n"
    assert not recomp.uses_odd_float_registers([even, "int x;"])
    assert recomp.uses_odd_float_registers([even, odd]) and recomp.uses_odd_float_registers([odd.replace("1)", "31)")])
    assert not recomp.uses_odd_float_registers([])
    assert "%(fr_default)d" in recomp.BRIDGE_RUNTIME


def test_a_status_write_keeps_an_odd_register_game_in_fr1():
    """A game whose recompiled C computes on odd registers has its threads' Status written without the FR bit (a
    thread's saved Status, as libultra makes it): the write switched the layout to FR=0 and the first `mul.s $f1` hit
    N64Recomp's CHECK_FR assertion (a shooter, in recompiled func_8029A7D8). The write keeps the game's default."""
    text = recomp.BRIDGE_RUNTIME
    write = text[text.index("void cop0_status_write("):text.index("gpr cop0_status_read(")]
    assert "| %(fr_default)du" in write


def test_recompiled_floats_follow_the_games_status_fr_bit(tmp_path):
    """A tank game's recompiled vector code uses odd single-precision registers (`mul.s $f1`), which N64Recomp's
    CHECK_FR allows only in FR=1: every frame started in FR=0 and stopped on the first one. A frame now starts from
    the host's Status, a Status write switches the layout, and switching keeps each odd register's value."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if cc is None:
        pytest.skip("no C compiler")
    text = recomp.BRIDGE_RUNTIME
    start = text.index("uint32_t port_cop0_status(void);")
    end = text.index("/* C called by the interpreter can call back")
    assert "port_recomp_fr(c, ((c->status_reg >> 26) & 1u) | %(fr_default)du);" in text[text.index("static void port_recomp_frame("):]
    assert "port_recomp_fr(ctx, (((uint32_t)value >> 26) & 1u) | %(fr_default)du);" in recomp.__loader__.get_source(recomp.__name__)
    (tmp_path / "t.c").write_text(r"""#include <stdint.h>
#include <stdio.h>
typedef union { double d; struct { uint32_t u32l; uint32_t u32h; }; uint64_t u64; } fpr;
typedef struct { fpr f0, f1, f2, f3; uint32_t *f_odd; uint8_t mips3_float_mode; } recomp_context;
""" + text[start:end].replace("i < 32", "i < 4") + r"""
uint32_t port_cop0_status(void) { return 0; } void port_cop0_status_set(uint32_t v) { (void)v; }
int main(void) {
    recomp_context c = {0}; c.f_odd = &c.f0.u32h;
    c.f_odd[0] = 0x11111111u; c.f0.u32l = 7;            /* FR=0: $f1 is the upper half of $f0 */
    port_recomp_fr(&c, 1);
    printf("%d %X %X ", c.mips3_float_mode, c.f1.u32l, c.f_odd[0]);  /* FR=1: $f1 has its own register */
    c.f_odd[0] = 0x22222222u; c.f0.u32h = 0x99u;
    port_recomp_fr(&c, 0);
    printf("%d %X\n", c.mips3_float_mode, c.f_odd[0]);
    return 0;
}
""")
    done = subprocess.run([cc, "-w", str(tmp_path / "t.c"), "-o", str(tmp_path / "t")], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    out = subprocess.run([str(tmp_path / "t")], capture_output=True, text=True).stdout.split()
    assert out == ["1", "11111111", "11111111", "0", "22222222"]


def test_a_function_found_by_calls_with_a_branch_past_its_end_is_left_to_the_interpreter(tmp_path):
    """N64Recomp writes functions it finds by the calls into them (`static_4_80171B34`) with no symbol of the split;
    one that branches to a label outside itself stopped a port's host build (`undeclared label`) because only named
    functions were dropped. It goes, and its callers reach it by the address in its name."""
    folder = tmp_path / "recomp"
    _output(folder)
    source = folder / "funcs/funcs_0.c"
    source.write_text(source.read_text() + (
        "RECOMP_FUNC void static_4_80171B34(uint8_t* rdram, recomp_context* ctx) {\n"
        "    if (ctx->r2 == 0) {\n        goto L_80171BB4;\n    }\n    return;\n;}\n"))
    header = folder / "funcs/funcs.h"
    header.write_text(header.read_text() + "void static_4_80171B34(uint8_t* rdram, recomp_context* ctx);\n")
    source.write_text(source.read_text().replace("    return;\n;}\nRECOMP_FUNC void osSendMesg",
                                                 "    static_4_80171B34(rdram, ctx);\n    return;\n;}\nRECOMP_FUNC void osSendMesg", 1))
    out = tmp_path / "build"
    recomp.prepare(folder, out, "", [], [], _header(), {}, lambda *a: None)
    text = (out / "rec_funcs_0.c").read_text()
    assert "RECOMP_FUNC void R_static_4_80171B34" not in text and "L_80171BB4" not in text
    assert 'port_recomp_missing(0x80171B34u, "static_4_80171B34"' in (out / "bridges.c").read_text()


def _two_overlays(tmp_path):
    """Two overlays the game loads into one slot (one address, two places in the ROM), and a function of its own."""
    image = tmp_path / "rom.z64"
    words = [0x27BDFFE0, 0xAFBF001C, 0x00000000, 0x03E00008, 0x24020001, 0x24020002, 0x00000000, 0x00000000]
    other = [0x3C048008, 0x8C83A590, 0x8C82A590, 0x00641821, 0x0043102B, 0x03E00008, 0x00000000, 0x00000000]
    blob = bytearray(0x1000)
    for at, ws in ((0x100, words), (0x200, other), (0x300, words[:4] + [0x24020009, 0, 0, 0])):
        for i, w in enumerate(ws):
            blob[at + 4 * i:at + 4 * i + 4] = w.to_bytes(4, "big")
    image.write_bytes(bytes(blob))
    folder = tmp_path / "recomp"
    (folder / "funcs").mkdir(parents=True)
    (folder / "recomp.json").write_text("{}")
    (folder / "syms.toml").write_text(
        '[[section]]\nname = "slot_a"\nrom = 0x100\nvram = 0x80126480\nsize = 0x20\n\nfunctions = [\n'
        '    { name = "func_80126480_a", vram = 0x80126480, size = 0x20 },\n]\n\n'
        '[[section]]\nname = "slot_b"\nrom = 0x200\nvram = 0x80126480\nsize = 0x20\n\nfunctions = [\n'
        '    { name = "func_80126480_b", vram = 0x80126480, size = 0x20 },\n]\n\n'
        '[[section]]\nname = "code"\nrom = 0x300\nvram = 0x80001000\nsize = 0x20\n\nfunctions = [\n'
        '    { name = "func_80001000", vram = 0x80001000, size = 0x20 },\n]\n')
    (folder / "funcs/funcs.h").write_text('#include "recomp.h"\n' + "".join(
        f"void {n}(uint8_t* rdram, recomp_context* ctx);\n" for n in ("func_80126480_a", "func_80126480_b", "func_80001000")))
    body = "    // 0x80126480: jr $ra\n    return;\n;}\n"
    (folder / "funcs/funcs_0.c").write_text(
        '#include "recomp.h"\n#include "funcs.h"\n\n' + "".join(
            f"RECOMP_FUNC void {n}(uint8_t* rdram, recomp_context* ctx) {{\n{body}"
            for n in ("func_80126480_a", "func_80126480_b", "func_80001000")))
    return folder, image, words, other


def test_functions_sharing_an_address_between_overlays_are_guarded_by_their_code(tmp_path):
    """A tank game's main thread spun in one overlay's delay routine while another overlay was loaded in the slot:
    the recompiler wrote both overlays' functions at one address and the call by address ran the first listed. Only
    functions whose addresses another part of the ROM shares get the check (a function of its own doesn't)."""
    folder, image, words, other = _two_overlays(tmp_path)
    guards = recomp.overlay_guards(folder, image, {"func_80126480_a", "func_80126480_b", "func_80001000"})
    assert set(guards) == {"func_80126480_a", "func_80126480_b"}
    assert guards["func_80126480_a"] == (0x80126480, 8, recomp.code_hash(words))
    assert guards["func_80126480_b"] == (0x80126480, 8, recomp.code_hash(other))
    assert recomp.overlay_guards(folder, None, {"func_80126480_a"}) == {}  # no ROM to read: no guards


def test_a_guarded_function_runs_what_ram_holds_or_the_interpreter(tmp_path):
    folder, image, words, other = _two_overlays(tmp_path)
    header = tmp_path / "recomp.h"
    header.write_text(_header().read_text())
    host = tmp_path / "host.c"
    host.write_text("")
    out = tmp_path / "build"
    recomp.prepare(folder, out, "", [], [host], header, {}, lambda *a: None, image=image)
    rec = (out / "rec_funcs_0.c").read_text()
    code = f"port_recomp_guard(0x80126480u, 8u, 0x{recomp.code_hash(words):08X}u)"
    assert code in rec and "port_recomp_other(0x80126480u, R_func_80126480_a, rdram, ctx)" in rec
    assert rec.count("port_recomp_guard(") == 2  # (the function of its own has none)
    bridges = (out / "bridges.c").read_text()
    assert f"{{0x80126480u, 8u, 0x{recomp.code_hash(other):08X}u, R_func_80126480_b}}" in bridges
    assert "if (port_recomp_shared((uint32_t)vram) && port_mips_has_code((uint32_t)vram))" in bridges
    assert "int port_recomp_guard(" in (out / "funcs.h").read_text()
    # RECOMP_FUNC is weak: a stub bridges.c defines for a name a recompiled file defines overrides its real body. A
    # puzzle game's overlays at one address (all but the first listed) then ran as empty functions and the main loop
    # reloaded the overlays for ever.
    for name in ("func_80126480_a", "func_80126480_b"):
        assert f"RECOMP_FUNC void R_{name}(" in "".join(p.read_text() for p in out.glob("rec_funcs_*.c"))
        assert f"void R_{name}(uint8_t* rdram, recomp_context* ctx) {{ (void)rdram;" not in bridges


def test_the_ram_hash_in_c_is_the_roms_hash_in_python(tmp_path):
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if cc is None:
        pytest.skip("no C compiler")
    start = recomp.LOOKUPS.index("static uint32_t port_recomp_ram_hash")
    function = recomp.LOOKUPS[start:recomp.LOOKUPS.index("\n}\n", start) + 3]
    words = [0x27BDFFE0, 0xAFBF001C, 0x03E00008, 0x24020001]
    (tmp_path / "hash.c").write_text(
        "#include <stdint.h>\n#include <stdio.h>\n"
        f"static const uint32_t ram[] = {{{', '.join(hex(w) for w in words)}}};\n"
        "uint32_t port_mips_lw(uint32_t a) { return ram[(a - 0x80001000u) / 4]; }\n" + function +
        'int main(void) { printf("%08X\\n", port_recomp_ram_hash(0x80001000u, 4)); return 0; }\n')
    subprocess.run([cc, "-o", str(tmp_path / "hash"), str(tmp_path / "hash.c")], check=True)
    done = subprocess.run([str(tmp_path / "hash")], capture_output=True, text=True, check=True)
    assert int(done.stdout, 16) == recomp.code_hash(words)


def test_every_function_of_a_slot_the_game_fills_at_run_time_is_guarded(tmp_path):
    """A puzzle game's main thread spun in the first function of a raw slot that no other overlay's listed function
    overlapped, while the game had loaded another overlay there: the guard covers every function of a section named
    as a raw slot, and of sections whose addresses overlap another's, not only functions that overlap in the table."""
    image = tmp_path / "rom.z64"
    blob = bytearray(0x1000)
    for at, w in ((0x100, 0x27BDFFE0), (0x120, 0x03E00008), (0x200, 0x3C048008)):
        blob[at:at + 4] = w.to_bytes(4, "big")
    image.write_bytes(bytes(blob))
    folder = tmp_path / "recomp"
    folder.mkdir()
    (folder / "syms.toml").write_text(
        '[[section]]\nname = "main"\nrom = 0x400\nvram = 0x80001000\nsize = 0x20\n\nfunctions = [\n'
        '    { name = "func_80001000", vram = 0x80001000, size = 0x20 },\n]\n\n'
        # a raw slot alone, with two functions, none overlapping another section's
        '[[section]]\nname = "raw_slot_00"\nrom = 0x100\nvram = 0x80300000\nsize = 0x40\n\nfunctions = [\n'
        '    { name = "func_80300000", vram = 0x80300000, size = 0x20 },\n'
        '    { name = "func_80300020", vram = 0x80300020, size = 0x20 },\n]\n\n'
        # two sections of one address range with other bytes (function boundaries differ)
        '[[section]]\nname = "ovl_a"\nrom = 0x200\nvram = 0x80400000\nsize = 0x40\n\nfunctions = [\n'
        '    { name = "func_80400000", vram = 0x80400000, size = 0x20 },\n]\n\n'
        '[[section]]\nname = "ovl_b"\nrom = 0x240\nvram = 0x80400000\nsize = 0x40\n\nfunctions = [\n'
        '    { name = "func_80400020", vram = 0x80400020, size = 0x20 },\n]\n')
    names = {"func_80001000", "func_80300000", "func_80300020", "func_80400000", "func_80400020"}
    guards = recomp.overlay_guards(folder, image, names)
    assert set(guards) == names - {"func_80001000"}  # (the game's own code at a fixed address has none)
    assert guards["func_80300000"][0] == 0x80300000 and guards["func_80300020"][0] == 0x80300020


def test_a_call_at_a_tlb_mapped_address_finds_the_code_at_its_window():
    """A game maps overlay code at 0x0F000000 with osMapTLB and calls through a table of such addresses: `recompiled
    code called 0F000000, where no function is`. get_function names the address through the host's TLB windows first,
    as the interpreter does for its reads."""
    text = recomp.LOOKUPS
    body = text[text.index("recomp_func_t *get_function(int32_t vram) {"):]
    head = body[:body.index("unsigned lo = 0")]
    assert "port_tlb_canon((uint32_t)vram)" in head and "port_tlb_n" in head
    assert "uint32_t port_tlb_canon(uint32_t a);" in text[:text.index("recomp_func_t *get_function(")]


def test_a_function_that_raises_a_syscall_is_left_to_the_interpreter():
    """An overlay game's thunks are `syscall N` words answered by its own exception handler: recompiled C has no
    exception to take (the host printed 'reached a syscall' and did nothing, so every call into an overlay did
    nothing), the interpreter runs such a function from RAM."""
    thunk = ("RECOMP_FUNC void func_80088310(uint8_t* rdram, recomp_context* ctx) {\n"
             "    // 0x80088310: syscall     5\n    recomp_syscall_handler(rdram, ctx, 0x80088310);\n    return;\n;}\n")
    plain = ("RECOMP_FUNC void func_80088320(uint8_t* rdram, recomp_context* ctx) {\n"
             "    ctx->r2 = 1;\n    return;\n;}\n")
    assert recomp.unfinished([thunk + plain]) == {"func_80088310"}


def test_a_call_into_the_games_c_starts_the_v0_word_from_the_callers_v0():
    """`void f(void) {}` is `jr $ra; nop`: $v0 is what the caller had. A loader polled `while (!send(...))` through
    such a stub and stopped for ever on the 0 the port's register word held."""
    from decompnet.port.wasm import recomp

    runtime = recomp.BRIDGE_RUNTIME
    call = runtime.index("port_calls[lo].invoke(")
    assert "STORE_W(16, 0x%(regs)Xu, ctx->r2);" in runtime[:call][-300:]


def test_a_jr_through_a_copy_of_ra_returns_instead_of_calling_the_callers_next_instruction():
    """A platformer's sine routine copies $ra (`or $a1, $ra, $zero`) and ends in `jr $a1`: compiled as a call to the
    caller's next instruction, the interpreter ran the game's main thread from there (and the recompiled frames ran
    it again). A function holding its entry $ra in the register returns; a switch's `jr` is still a jump."""
    from decompnet.port.wasm.recomp import return_through_copy

    generated = ("RECOMP_FUNC void func_800137F4(uint8_t* rdram, recomp_context* ctx) {\n"
                 "    uint64_t hi = 0, lo = 0, result = 0;\n    int c1cs = 0;\n"
                 "    // 0x80013804: or          $a1, $ra, $zero\n    ctx->r5 = ctx->r31 | 0;\n"
                 "    // 0x80013810: jr          $a1\n    LOOKUP_FUNC(ctx->r5)(rdram, ctx);\n    return;\n\n;}\n"
                 "RECOMP_FUNC void func_80013900(uint8_t* rdram, recomp_context* ctx) {\n"
                 "    uint64_t hi = 0, lo = 0, result = 0;\n    LOOKUP_FUNC(ctx->r31)(rdram, ctx);\n    return;\n\n;}\n")
    fixed = return_through_copy(generated)
    first, second = fixed.split("RECOMP_FUNC void func_80013900")
    assert "gpr entry_ra = ctx->r31;" in first
    assert "if ((uint32_t)ctx->r5 == (uint32_t)entry_ra) return;" in first
    assert first.index("entry_ra) return;") < first.index("LOOKUP_FUNC(ctx->r5)")
    assert "entry_ra" not in second  # `jr $ra` is already a return; a function with no other `jr` is left as it is


def test_a_longjmp_and_everything_that_calls_it_is_left_to_the_interpreter():
    """A game's cooperative threads switch contexts with setjmp and longjmp: longjmp loads $sp and $ra from the
    buffer, which C can't jump to, so as recompiled C the switch returned to longjmp's caller and no game context ever
    ran. The function that sets $sp from memory and its callers (the yield, the task loop) run in the interpreter; a
    function that only calls an ordinary one, and a normal epilogue (`addiu $sp`), stay recompiled."""
    def fn(name, body):
        return f"RECOMP_FUNC void {name}(uint8_t* rdram, recomp_context* ctx) {{\n{body}    return;\n;}}\n"
    longjmp = fn("func_80297C7C", "    ctx->r31 = MEM_W(ctx->r4, 0XC);\n    ctx->r29 = MEM_W(ctx->r4, 0X8);\n")
    yield_ = fn("func_8023903C", "    ctx->r29 = ADD32(ctx->r29, -0X20);\n    func_80297C7C(rdram, ctx);\n")
    loop = fn("func_8028E614", "    func_8023903C(rdram, ctx);\n")
    other = fn("func_80001000", "    ctx->r29 = ADD32(ctx->r29, -0X18);\n    func_80001100(rdram, ctx);\n    ctx->r29 = ADD32(ctx->r29, 0X18);\n")
    assert recomp.unfinished([longjmp + yield_ + loop + other]) == {"func_80297C7C", "func_8023903C", "func_8028E614"}


def test_code_loaded_at_run_time_into_another_address_is_guarded_but_the_boot_copy_in_a_window_is_not(tmp_path):
    """A fighting game's main loop calls the entry of the overlay for its mode by address: the split names one overlay
    (ROM 0x3000, at 0x80169000) of the several the game loads into that slot, and the call ran that copy while another
    mode's overlay was in RAM, so its loop spun on zero-length reads. The boot chip copies ROM 0x1000-0x101000 to the
    entry address: a section anywhere else, or past that megabyte, only gets its address by the game's own loading. A
    section a whole number of megabytes from where the boot copy put it is the same code spelled through a TLB window
    (a shooter's program at virtual 0x104400): not an overlay."""
    image = tmp_path / "rom.z64"
    blob = bytearray(0x210000)
    for at in (0x3000, 0x5000, 0x7000, 0x200000):
        blob[at:at + 4] = (0x27BDFFE0).to_bytes(4, "big")
    image.write_bytes(bytes(blob))
    folder = tmp_path / "recomp"
    folder.mkdir()

    def section(name, rom, vram, fn):
        return (f'[[section]]\nname = "{name}"\nrom = 0x{rom:X}\nvram = 0x{vram:X}\nsize = 0x20\n\nfunctions = [\n'
                f'    {{ name = "{fn}", vram = 0x{vram:X}, size = 0x20 }},\n]\n\n')
    (folder / "syms.toml").write_text(
        section("entry", 0x1000, 0x80000400, "func_80000400")
        + section("code_80169000", 0x3000, 0x80169000, "func_80169000")      # an overlay inside the boot megabyte
        + section("code_80104400", 0x5000, 0x80104400, "func_80104400")      # 0x5000 boots at 0x80004400: +1 MB
        + section("code_80006400", 0x7000, 0x80006400, "func_80006400")      # the boot copy's own address (plain)
        + section("code_80200000", 0x200000, 0x80200000, "func_80200000"))   # past the boot copy
    names = {"func_80000400", "func_80169000", "func_80104400", "func_80006400", "func_80200000"}
    guards = recomp.overlay_guards(folder, image, names)
    assert set(guards) == {"func_80169000", "func_80200000"}


def test_recompiled_loads_and_stores_go_through_the_games_tlb_windows(tmp_path):
    """A shooter's startup scanned a table at 0x002021A0 (a TLB window's virtual address) in recompiled code, which
    read whatever sat at that place in the host's memory, so the list it walks never ended. port_at translates an
    address the windows map, as the interpreter and the compiled C do."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if cc is None:
        pytest.skip("no C compiler")
    (tmp_path / "t.c").write_text("#include <stdint.h>\n#include <stdio.h>\n#include <string.h>\ntypedef uint64_t gpr;\n"
                                  + recomp.LOADS + r"""
uint64_t port_io_store(uint64_t a, uint64_t v) { (void)v; return a; }
uint64_t port_io_load(uint64_t a) { return a; }
void port_safe_point(void) {}
unsigned port_tlb_n = 1;
uint32_t port_tlb_canon(uint32_t a) { return a >= 0x200000u && a < 0x300000u ? 0x80000000u | (a - 0x200000u + 0x1000u) : a; }
/* the offsets port_at hands back from a base of 0: no memory is read */
int main(void) {
    uint8_t *base = 0;
    printf("%lX %lX %lX\n", (unsigned long)(port_at(base, 0x2021A0, 0, 0) - base),
           (unsigned long)(port_at(base, 0x80000100u, 0, 0) - base), (unsigned long)(port_at(base, 0x1000, 0, 0) - base));
    return 0;
}
""")
    subprocess.run([cc, "-o", str(tmp_path / "t"), str(tmp_path / "t.c")], check=True)
    done = subprocess.run([str(tmp_path / "t")], capture_output=True, text=True, check=True)
    # the window's address is where it maps; KSEG0 and an address no window covers are as they were
    assert done.stdout.split() == ["800031A0", "80000100", "1000"]
