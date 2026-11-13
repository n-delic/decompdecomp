"""The browser build and Export (port/wasm/web.py, webir.py, export.py): the game's IR rewritten for a browser, the
host's glue, and the exports a player opens."""

import plistlib
import shutil
import subprocess
import sys

import pytest

from decompnet import toolchains
from decompnet.network import NetworkError
from decompnet.port.wasm import export, web

sys.path.insert(0, str(web.HERE))
import webir  # noqa: E402  (beir.py runs it as a script beside adapt.py)


def rewrite(ir, fixed=None):
    return webir.rewrite(ir, fixed or {}, "t")


def test_every_load_and_store_goes_through_the_big_endian_helpers():
    """In the browser the game's WebAssembly runs as it is: what bemem.py does to wasm2c's C, the IR does itself."""
    out = rewrite("define void @f(ptr %0) {\n  %2 = load volatile i32, ptr %0, align 4\n"
                  "  %3 = load i16, ptr %0, align 2\n  %4 = load volatile ptr, ptr %0, align 4\n"
                  "  store volatile i32 %2, ptr inttoptr (i32 -2146959360 to ptr), align 4\n"
                  "  store i8 1, ptr %0, align 1\n  store volatile ptr %4, ptr %0, align 4\n  ret void\n}\n")
    body = out.split("define internal")[0]
    assert "= load" not in body and "  store " not in body
    assert "%2 = call i32 @port_ld32(ptr %0)" in body and "%3 = call i16 @port_ld16(ptr %0)" in body
    assert "%4 = inttoptr i32" in body  # a pointer is a word
    assert "call void @port_st32(ptr inttoptr (i32 -2146959360 to ptr), i32 %2)" in body


def test_a_byte_or_halfword_reaches_its_helper_zero_extended():
    """An i16 passed with no extension kept a sign-extended -127's upper bits, and the helper's u16 parameter let the
    compiler assume them zero: 0xFF81 was swapped into 0xFFFF and a racing game's menu text came out as stray glyphs.
    The declarations say zeroext, and the helpers narrow a whole word themselves."""
    out = rewrite("define void @f(ptr %0) {\n  store i16 -127, ptr %0, align 2\n  ret void\n}\n")
    assert "declare void @port_st16(ptr, i16 zeroext)" in out and "declare zeroext i16 @port_ld16(ptr)" in out
    be = (web.RUNTIME / "be.c").read_text()
    assert "void port_st16(void *p, u32 v)" in be and "v &= 0xFFFF" in be


def test_varargs_are_big_endian_and_only_va_starts_slot_stays_as_the_backend_writes_it():
    """The backend fills a variadic call's buffer and va_start the va_list: the call passes its extra arguments
    byte-swapped so the buffer holds them as the N64 would (a printf core walks it as `char *args`), and the slot
    va_start writes is read as it is."""
    out = rewrite("define void @f(ptr %0, ...) {\n  %2 = alloca ptr, align 4\n  call void @llvm.va_start.p0(ptr %2)\n"
                  "  %3 = load volatile ptr, ptr %2, align 4\n  %4 = load volatile i32, ptr %3, align 4\n"
                  "  %5 = call i32 (ptr, ...) @printf(ptr %0, i32 %4, double 1.0)\n  ret void\n}\n")
    assert "%3 = load volatile ptr, ptr %2" in out  # the va_list itself
    assert "%4 = call i32 @port_ld32(ptr %3)" in out  # an argument: big-endian
    assert "@llvm.bswap.i32(i32 %4)" in out and "@llvm.bswap.i64(i64" in out
    assert "call i32 (ptr, ...) @printf(ptr %0, i32 %port_b" in out


def test_a_call_through_a_pointer_takes_its_arguments_from_slots():
    """A function pointer is the function's console address and wasm checks a callee's type where the N64 didn't:
    the call goes through port_icall and the callee's universal entry, whatever its own type."""
    out = rewrite("define i32 @f(ptr %0) {\n  %2 = call i32 %0(i8 zeroext -1, float 1.0)\n  ret i32 %2\n}\n")
    assert "%port_slots = alloca [17 x i64]" in out
    assert "zext i8 -1 to i64" in out and "bitcast float 1.0 to i32" in out
    assert "call void @port_icall(ptr %0, ptr %port_slots, i32 2)" in out
    assert "%2 = trunc i64" in out


def test_each_function_gets_a_universal_entry_and_registers_its_console_address():
    out = rewrite("define zeroext i8 @get(i32 %0, ptr byval(%struct.S) align 4 %1) {\n  ret i8 1\n}\n"
                  "define void @set(i32 %0) {\n  ret void\n}\n", {"get": (0x80001000, "func")})
    universal = out[out.index("@get.port_u"):]
    assert "select i1" in universal and "@port_ld32(ptr inttoptr (i32 589824" in universal  # $a0 when not passed
    assert "zext i8" in universal  # a u8 comes back as one
    assert "ptr byval(%struct.S) align 4 %port_c" in universal
    assert "inttoptr (i32 589840 to ptr)" in out[out.index("@set.port_u"):]  # a void function gives $v0
    assert "{ i32 -2147479552, ptr @get, ptr @get.port_u }" in out and "{ i32 0, ptr @set, ptr @set.port_u }" in out
    assert "@llvm.global_ctors" in out


GAME_H = """/* import: 'env' 'osRecvMesg' */
u32 w2c_env_osRecvMesg(struct w2c_env*, u32, u32, u32);
/* import: 'env' '__builtin_mips_read_count' */
u32 w2c_env_0x5F_builtin_mips_read_count(struct w2c_env*);
/* export: 'boot_start' */
void w2c_game_boot_start(w2c_game*);
/* export: 'format_to_buffer' */
void w2c_game_format_to_buffer(w2c_game*, u32, u32, u32);
/* export: 'unused' */
void w2c_game_unused(w2c_game*);
"""


def test_the_host_exports_each_import_by_its_name_and_calls_the_game_through_its_table():
    header, source, slots = web.glue(GAME_H, ["w2c_game_boot_start(&game); w2c_game_format_to_buffer(&game, a, b, c);"])
    assert slots == ["boot_start", "format_to_buffer", "port_call1", "port_sp", "port_set_sp"]
    assert '__attribute__((export_name("__builtin_mips_read_count"))) u32 port_web_import_1(void) ' \
           "{ return w2c_env_0x5F_builtin_mips_read_count((struct w2c_env *)0); }" in source
    assert "u32 w2c_env_osRecvMesg(struct w2c_env*, u32, u32, u32);" in header
    assert "static inline void w2c_game_format_to_buffer(w2c_game *g, u32 a0, u32 a1, u32 a2)" in header
    assert "port_web_slots[1])(a0, a1, a2)" in header and "w2c_game_unused" not in header
    assert source.count("static void port_web_slot_") == len(slots)  # a table entry each, for the page to point


def test_the_runtime_compiles_for_the_browser():
    if not toolchains.installed("zig"):
        pytest.skip("zig isn't installed")
    zig = toolchains.binary("zig")
    for target, source, extra in (("wasm32-freestanding", "be.c", []), ("wasm32-wasi", "co.c", ["-I", "include"])):
        done = subprocess.run([str(zig), "cc", "-target", target, "-O1", *extra, "-c", source, "-o", "/dev/null"],
                              cwd=web.RUNTIME, capture_output=True, text=True)
        assert done.returncode == 0, done.stderr


def test_an_exported_app_is_a_mac_app_without_the_rom(tmp_path):
    program = tmp_path / "game"
    program.write_text("#!/bin/sh\n")
    app = export.package_app(program, tmp_path / "out", 'A <Racing> "Game": 99', platform="darwin")
    assert app.name == "A Racing Game 99.app"
    info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleExecutable"] == "play" and info["LSMinimumSystemVersion"] == "11.0"
    launcher = (app / "Contents" / "MacOS" / "play").read_text()
    assert "choose file" in launcher and "PORT_SAVE" in launcher and "kill $game" in launcher
    assert (app / "Contents" / "MacOS" / "play").stat().st_mode & 0o111
    assert sorted(p.name for p in app.rglob("*") if p.is_file()) == ["Info.plist", "game", "play"]
    if shutil.which("bash"):
        assert subprocess.run(["bash", "-n", app / "Contents" / "MacOS" / "play"]).returncode == 0
    export.package_app(program, tmp_path / "out", "Other", platform="darwin")
    assert [p.name for p in (tmp_path / "out").iterdir()] == ["Other.app"]  # exporting again replaces it


@pytest.mark.parametrize("platform, launcher", [("linux", "play.sh"), ("win32", "play.bat")])
def test_an_exported_app_elsewhere_is_the_program_and_a_launcher(tmp_path, platform, launcher):
    program = tmp_path / "game"
    program.write_text("x")
    folder = export.package_app(program, tmp_path / "out", "Game", platform=platform)
    assert sorted(p.name for p in folder.iterdir()) == sorted([launcher, "game.exe" if platform == "win32" else "game"])
    assert "PORT_LIVE" in (folder / launcher).read_text()


def test_export_says_what_it_can_make(tmp_path):
    with pytest.raises(NetworkError, match="app, web"):
        export.export(None, "game", "zip")


def test_the_browser_export_copies_the_build_and_never_a_rom(tmp_path):
    """The page asks the player for their ROM and keeps it in their own browser: the export copies only the build's
    own files, whatever else lies in the build folder."""
    built = tmp_path / "web"
    (built / "obj").mkdir(parents=True)
    for name in ("index.html", "play.js", "runtime.js", "build.json", "game-web.wasm", "host-web.wasm", "game.z64",
                 "obj/be.o"):
        (built / name).write_text("x")
    out = web.export(built, tmp_path / "export")
    assert sorted(p.name for p in out.iterdir()) == ["build.json", "game-web.wasm", "host-web.wasm", "index.html",
                                                     "play.js", "runtime.js"]
    kit = web.HERE.parent.parent / "consoles" / "n64" / "port_wasm" / "web"
    assert "indexedDB" in (kit / "play.js").read_text() and 'type="file"' in (kit / "index.html").read_text()
