"""The browser build and Export (port/wasm/web.py, webir.py, export.py): the game's IR rewritten for a browser, the
host's glue, and the exports a player opens."""

import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

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
    # two arguments, the second a float (its type above the count, for the host's interpreter: webir._type_code)
    assert f"call void @port_icall(ptr %0, ptr %port_slots, i32 {2 | 2 << 10})" in out
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
    launcher = (app / "Contents" / "Resources" / "play.sh").read_text()
    assert "choose file" in launcher and "PORT_SAVE" in launcher and "kill $game" in launcher
    assert '"$here/game"' in launcher  # the game sits beside the script, in Resources
    assert (app / "Contents" / "MacOS" / "play").stat().st_mode & 0o111
    files = sorted(p.name for p in app.rglob("*") if p.is_file() and p.parent.name != "_CodeSignature")  # signed on a Mac
    assert files == ["Info.plist", "game", "play", "play.sh"]
    assert "Open Anyway" in (tmp_path / "out" / "How to open on a Mac.txt").read_text()
    if shutil.which("bash"):
        assert subprocess.run(["bash", "-n", app / "Contents" / "Resources" / "play.sh"]).returncode == 0
    export.package_app(program, tmp_path / "out", "Other", platform="darwin")
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["How to open on a Mac.txt", "Other.app"]  # replaced


@pytest.mark.parametrize("platform, launcher", [("linux", "play.sh"), ("win32", "play.bat")])
def test_an_exported_app_elsewhere_is_the_program_and_a_launcher(tmp_path, platform, launcher):
    program = tmp_path / "game"
    program.write_text("x")
    folder = export.package_app(program, tmp_path / "out", "Game", platform=platform)
    assert sorted(p.name for p in folder.iterdir()) == sorted([launcher, "game.exe" if platform == "win32" else "game"])
    assert "PORT_LIVE" in (folder / launcher).read_text()


def test_export_says_what_it_can_make(tmp_path):
    with pytest.raises(NetworkError, match="app, window, web"):
        export.export(None, "game", "zip")


def test_the_browser_export_copies_the_build_and_never_a_rom(tmp_path):
    """The page asks the player for their ROM and keeps it in their own browser: the export copies only the build's
    own files, whatever else lies in the build folder."""
    built = tmp_path / "web"
    (built / "obj").mkdir(parents=True)
    for name in ("index.html", "play.js", "gl.js", "runtime.js", "build.json", "game-web.wasm", "host-web.wasm",
                 "app-asyncify.wasm", "game.z64", "obj/be.o", "app-asyncify-work/merged.wasm"):
        (built / name).parent.mkdir(exist_ok=True)
        (built / name).write_text("x")
    out = web.export(built, tmp_path / "export")
    assert sorted(p.name for p in out.iterdir()) == ["app-asyncify.wasm", "build.json", "game-web.wasm", "gl.js",
                                                     "host-web.wasm", "index.html", "play.js", "runtime.js"]
    kit = web.HERE.parent.parent / "consoles" / "n64" / "port_wasm" / "web"
    assert "indexedDB" in (kit / "play.js").read_text() and 'type="file"' in (kit / "index.html").read_text()


def test_the_page_places_data_segments_above_2_gb_itself():
    """Safari refuses a module whose data segment is above 2 GB (it reads the address as negative), and every
    segment of a browser build is: the runtime turns them passive and copies them in (web_runtime_data.mjs)."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node isn't installed")
    done = subprocess.run([node, str(Path(__file__).with_name("web_runtime_data.mjs"))], capture_output=True,
                          text=True, timeout=60)
    assert done.returncode == 0, done.stderr


def test_threads_give_way_when_the_next_retrace_is_due():
    """The page runs the game's threads until the next retrace is due, and tells the host so (port_web_deadline),
    which a thread that never waits gives way at (port_yield): web_runtime_retrace.mjs."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node isn't installed")
    done = subprocess.run([node, str(Path(__file__).with_name("web_runtime_retrace.mjs"))], capture_output=True,
                          text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    runtime = (web.RUNTIME / "runtime.js").read_text(encoding="utf-8")
    assert runtime.count("port_web_deadline(until)") == 2  # both modules: Promise Integration and Asyncify
    host = (web.HERE.parent.parent / "consoles" / "n64" / "port_wasm" / "host.c").read_text(encoding="utf-8")
    assert 'export_name("port_web_deadline")' in host and 'export_name("port_preempt_flag")' in host


def test_the_page_plays_the_simd_build_only_where_it_can():
    """An export "faster where it can" carries the host built with WebAssembly SIMD as well; the page loads it only
    where the browser validates SIMD (web_runtime_simd.mjs), as Safari before 16.4 can't load it at all."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node isn't installed")
    done = subprocess.run([node, str(Path(__file__).with_name("web_runtime_simd.mjs"))], capture_output=True,
                          text=True, timeout=60)
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize("simd", [False, True])
def test_the_browser_export_carries_simd_modules_only_when_built_with_them(tmp_path, simd):
    """The build folder keeps an earlier build's SIMD modules (they're its cache); an export takes them only when its
    build.json says this build made them, so a plain export never ships stale ones."""
    import json

    build = tmp_path / "web"
    build.mkdir()
    for name in ("index.html", "play.js", "runtime.js", "game-web.wasm", "host-web.wasm", web.ASYNCIFY,
                 *web.SIMD.values()):
        (build / name).write_text(name)
    (build / "build.json").write_text(json.dumps({"slots": [], "simd": simd}))
    dest = web.export(build, tmp_path / "out")
    names = {p.name for p in dest.iterdir()}
    assert ("host-web-simd.wasm" in names) is simd and ("app-asyncify-simd.wasm" in names) is simd
    assert {"host-web.wasm", web.ASYNCIFY, "build.json"} <= names
    assert web.SIMD_FLAGS == ["-msimd128"]


def _module(*sections) -> bytes:
    return web._module(list(sections))


def _imports(*entries) -> tuple[int, bytes]:
    return 2, web._leb(len(entries)) + b"".join(entries)


def _import(module: bytes, field: bytes, desc: bytes) -> bytes:
    return web._leb(len(module)) + module + web._leb(len(field)) + field + desc


def test_the_host_for_the_merge_imports_its_memory_from_js_and_exports_it():
    """In wasm-merge the host is named env, so its own env.memory import would point at itself: it takes the memory
    from "js" instead, declared exactly as the game declares it (4 GB, with a maximum), and exports it, so the game's
    env.memory import finds the host's. Its other imports and exports stay as they are."""
    host = _module(
        (1, bytes([1, 0x60, 0, 0])),  # one type: () -> ()
        _imports(_import(b"port_web", b"suspend", bytes([0, 0])),
                 _import(b"env", b"memory", bytes([2, 0]) + web._leb(65535))),
        (7, web._leb(1) + web._leb(3) + b"run" + bytes([0, 0])),
        (0, web._leb(11) + b".debug_line" + b"x"))
    merged = web._sections(web.host_for_merge(host))
    assert [i for i, _ in merged] == [1, 2, 7]  # no custom sections: Binaryen stopped on the host's DWARF
    sections = dict(merged)
    imports = sections[2]
    assert b"port_web" in imports and b"suspend" in imports
    assert _import(b"js", b"memory", bytes([2, 1]) + web._leb(65536) + web._leb(65536)) in imports
    assert b"env" not in imports
    assert sections[7] == web._leb(2) + web._leb(3) + b"run" + bytes([0, 0]) + web._leb(6) + b"memory" + bytes([2, 0])
    with pytest.raises(NetworkError, match="env.memory"):
        web.host_for_merge(_module((1, bytes([1, 0x60, 0, 0]))))


def test_the_game_for_the_merge_loses_its_linker_sections():
    game = _module((0, web._leb(7) + b"linking" + b"\x02"), (1, bytes([1, 0x60, 0, 0])),
                   (0, web._leb(4) + b"name" + b"x"))
    assert web._sections(web.without_custom_sections(game)) == [(1, bytes([1, 0x60, 0, 0]))]


def test_the_merged_module_pauses_where_the_host_suspends(tmp_path):
    """A host that suspends and a game that calls it, merged and rewritten with Asyncify: one module, importing its
    memory from js and only the page's own imports, exporting Asyncify's controls beside both modules' functions."""
    if not (toolchains.installed("binaryen") and toolchains.installed("wabt")):
        pytest.skip("binaryen or wabt isn't installed")
    wat2wasm = toolchains.path("wabt") / "bin" / "wat2wasm"
    host = tmp_path / "host.wat"
    host.write_text("""(module
      (import "port_web" "suspend" (func $suspend))
      (import "env" "memory" (memory 65535))
      (func (export "wait") (call $suspend))
      (func (export "co_main") (param i32) (call $suspend)))""")
    game = tmp_path / "game.wat"
    game.write_text("""(module
      (import "env" "memory" (memory 65536 65536))
      (import "env" "wait" (func $wait))
      (func (export "thread") (call $wait)))""")
    for wat in (host, game):
        done = subprocess.run([str(wat2wasm), str(wat), "-o", str(wat.with_suffix(".wasm"))], capture_output=True,
                              text=True)
        assert done.returncode == 0, done.stderr
    out = tmp_path / "app-asyncify.wasm"
    web.merge_for_asyncify(host.with_suffix(".wasm"), game.with_suffix(".wasm"), out, toolchains.path("binaryen"),
                           log=lambda *a: None)
    text = subprocess.run([str(toolchains.path("binaryen") / "bin" / "wasm-dis"), str(out)], capture_output=True,
                          text=True).stdout
    imports = set(re.findall(r'\(import "([^"]+)" "([^"]+)"', text))
    assert imports == {("js", "memory"), ("port_web", "suspend")}
    exports = set(re.findall(r'\(export "([^"]+)"', text))
    assert {"thread", "co_main", "asyncify_start_unwind", "asyncify_start_rewind", "asyncify_stop_unwind",
            "asyncify_stop_rewind", "asyncify_get_state"} <= exports
    assert not (tmp_path / "app-asyncify-work").exists()


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("xcrun") or not shutil.which("codesign"),
                    reason="needs macOS with Xcode's Command Line Tools")
def test_a_mac_app_has_a_compiled_launcher_for_both_macs_and_a_sealed_signature(tmp_path):
    """#1225's app was a bash script with no signature, and an Intel Mac that got it by AirDrop called it unopenable:
    the app's program is now a compiled universal stub and the whole bundle is signed, so a copy stays verifiable."""
    source = tmp_path / "game.c"
    source.write_text("int main(void) { return 0; }\n")
    program = tmp_path / "game"
    subprocess.run(["xcrun", "clang", "-o", str(program), str(source)], check=True)
    app = export.package_app(program, tmp_path / "out", "Racer", platform="darwin")
    stub = app / "Contents" / "MacOS" / "play"
    assert sorted(subprocess.run(["lipo", "-archs", str(stub)], capture_output=True, text=True).stdout.split()) \
        == ["arm64", "x86_64"]
    verify = subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], capture_output=True, text=True)
    assert verify.returncode == 0, verify.stderr
    # What a player gets: a zip of the app, unpacked again by a tool that keeps no extended attributes (a bash script
    # as the app's program lost its signature this way).
    copy = tmp_path / "copy.zip"
    subprocess.run(["zip", "-qry", "-X", str(copy), "Racer.app"], cwd=app.parent, check=True)
    subprocess.run(["unzip", "-q", str(copy), "-d", str(tmp_path / "unzipped")], check=True)
    again = subprocess.run(["codesign", "--verify", "--deep", "--strict", str(tmp_path / "unzipped" / "Racer.app")],
                           capture_output=True, text=True)
    assert again.returncode == 0, again.stderr


def _sdl_folder(tmp_path):
    """A stand-in for the `sdl3` toolchain's folder: a real library on a Mac (it gets signed), bytes elsewhere."""
    sdl = tmp_path / "sdl3"
    sdl.mkdir()
    (sdl / "LICENSE.txt").write_text("zlib licence\n")
    if sys.platform == "darwin":
        source = tmp_path / "lib.c"
        source.write_text("int SDL_Init(unsigned flags) { return (int)flags; }\n")
        subprocess.run(["xcrun", "clang", "-dynamiclib", "-o", str(sdl / "libSDL3.0.dylib"), str(source)],
                       check=True)
    else:
        (sdl / "libSDL3.0.dylib").write_bytes(b"\xcf\xfa\xed\xfe")
    return sdl


@pytest.mark.skipif(sys.platform == "darwin" and not shutil.which("xcrun"), reason="needs Xcode's Command Line Tools")
def test_an_app_with_its_own_window_is_the_game_itself_with_sdl3_inside(tmp_path):
    """The window kind: the app's program is the game (Contents/MacOS/game), with no script, so the Dock shows the
    app; its settings are port.env beside it (live.c reads them when no ROM is named); SDL3 and its licence travel in
    the app."""
    source = tmp_path / "game.c"
    program = tmp_path / "game"
    if sys.platform == "darwin":
        source.write_text("int main(void) { return 0; }\n")
        subprocess.run(["xcrun", "clang", "-o", str(program), str(source)], check=True)
    else:
        program.write_text("#!/bin/sh\n")
    app = export.package_app(program, tmp_path / "out", "Racer", platform="darwin", sdl=_sdl_folder(tmp_path))
    files = sorted(str(p.relative_to(app)) for p in app.rglob("*") if p.is_file() and "_CodeSignature" not in p.parts)
    assert files == ["Contents/Frameworks/libSDL3.0.dylib", "Contents/Info.plist", "Contents/MacOS/game",
                     "Contents/Resources/SDL3-LICENSE.txt", "Contents/Resources/port.env"]
    assert plistlib.loads((app / "Contents" / "Info.plist").read_bytes())["CFBundleExecutable"] == "game"
    settings = dict(line.split("=", 1) for line in (app / "Contents" / "Resources" / "port.env").read_text().split("\n")
                    if line)
    assert settings == {"PORT_WINDOW": "1", "PORT_TITLE": "Racer", "PORT_APP": "com.decompdecomp.port.racer"}
    if sys.platform == "darwin":
        verify = subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], capture_output=True, text=True)
        assert verify.returncode == 0, verify.stderr


def test_the_window_kind_is_offered_and_made_on_a_mac_only(tmp_path, monkeypatch):
    from decompnet import toolchains
    from decompnet.port.wasm import build, play
    assert "window" in export.KINDS
    repo, program, asked = tmp_path / "save", tmp_path / "game", {}
    repo.mkdir()
    program.write_bytes(b"x")
    monkeypatch.setattr(play, "game", lambda net, slug: (repo, {"platform": "n64", "rom": {"title": "RALLY"}}, None))
    monkeypatch.setattr(play, "library", lambda net, slug, log=print: set())
    monkeypatch.setattr(play, "unverified", lambda net, slug, log=print: set())  # (the save is whole)
    monkeypatch.setattr(play, "relative_bank_code", lambda net, slug, log=print: set())
    monkeypatch.setattr(play, "library_c", lambda net, slug: {})  # (no ultralib units: port/ultralib.py)
    monkeypatch.setattr(play, "library_names", lambda net, slug: {})  # (no library names to rename)
    monkeypatch.setattr(play, "recompiled", lambda net, slug, log=print: None)  # (no recompiled C)
    monkeypatch.setattr(play, "function_headers", lambda net, slug: {})  # (headers as the save names them)
    monkeypatch.setattr(build, "build", lambda *a, **k: asked.update(k) or program)
    monkeypatch.setattr(toolchains, "ensure", lambda tid, log=print: asked.setdefault("tool", tid) and tmp_path)
    monkeypatch.setattr(export, "package_app", lambda program, dest, title, sdl=None: asked.update(sdl=sdl, dest=dest)
                        or (dest.mkdir(parents=True), dest)[1])
    monkeypatch.setattr(export.sys, "platform", "linux")
    with pytest.raises(NetworkError, match="on a Mac"):
        export.export(None, "rally", "window", log=lambda *a: None)
    monkeypatch.setattr(export.sys, "platform", "darwin")
    result = export.export(None, "rally", "window", log=lambda *a: None)
    assert asked["tool"] == "sdl3" and asked["sdl"] == tmp_path and asked["universal"] is True
    assert asked["dest"].name == "window" and result["kind"] == "window"
    assert any("full screen" in step for step in result["instructions"])
