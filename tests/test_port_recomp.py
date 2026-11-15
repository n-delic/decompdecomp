"""Recompiled C where the decomp has none yet, in the port's native build (port/wasm/recomp.py): homemade recompiler
output, never a game's.

A racing game's port stopped at the first function it ran with no C (its first thread's code). N64Recomp's C for it
runs in the host instead, on the port's memory, bridged to the game's C and the host both ways.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

from decompnet.port.wasm import recomp

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
    bridges = (out / "bridges.c").read_text()
    assert "void R_func_80001000(uint8_t* rdram, recomp_context* ctx) { (void)rdram; port_recomp_decomp(0x80001000u" in bridges
    assert "w2c_env_osSendMesg(NULL, (uint32_t)ctx->r4, (uint32_t)ctx->r5, (uint32_t)ctx->r6)" in bridges
    assert "void R___osSiRawStartDma_recomp(" in bridges and "w2c_env_0x5F_osSiRawStartDma(NULL" in bridges
    # the game's C calling the recompiled function: its import, typed, arguments as o32 passes them
    assert "u32 w2c_env_func_80003000(struct w2c_env *e, u32 a0, f32 a1)" in bridges and "port_rb32(a1)" in bridges
    assert "{0x80003000u, (void *)G_func_80003000}" in bridges  # through a pointer, or as a thread's entry
    assert "R___osSiRawStartDma_recomp(uint8_t* rdram" in (out / "funcs.h").read_text()
    # into the game's C: $a0-$a3 in adapt.py's register words, for a callee that passes on what it doesn't declare
    assert "for (int i = 0; i < 4; i++) STORE_W(4 * i, 0x90000u, w[i]);" in bridges
    cc = shutil.which("cc") or shutil.which("clang")
    if cc:
        for unit in prepared["units"]:
            done = subprocess.run([cc, "-c", "-w", "-I", str(out), str(unit), "-o", str(unit.with_suffix(".o"))],
                                  capture_output=True, text=True)
            assert done.returncode == 0, done.stderr[-1500:]


def test_a_function_named_as_the_c_programs_entry_is_renamed():
    """A racing game names its first C function `main`: as C that's a program's entry, and N64Recomp's output for it
    didn't compile until the port renamed every recompiled function R_<name>."""
    text = "RECOMP_FUNC void main(uint8_t* rdram, recomp_context* ctx) {\n    main(rdram, ctx);\n}\n"
    renamed = recomp.DEFINED.sub(lambda m: f"RECOMP_FUNC void R_{m.group(1)}(uint8_t* rdram, recomp_context* ctx)", text)
    assert recomp.CALL.sub(lambda m: f"R_{m.group(1)}", renamed).count("R_main") == 2
