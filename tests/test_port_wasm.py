"""The WebAssembly build of a finished game (port/wasm): what it takes from the game's save repository."""

import json

import pytest

from decompnet.consoles import get
from decompnet.network import NetworkError
from decompnet.port.wasm.build import addresses, function_table, gather, linked, renames


def save(tmp_path):
    repo = tmp_path / "game"
    (repo / "progress" / "src").mkdir(parents=True)
    (repo / "progress" / "include").mkdir()
    (repo / "progress" / "include" / "common.h").write_text("typedef int s32;\n")
    for name, text in {"race_update.c": "void race_update(void) {}\n", "osCreateThread.c": "void osCreateThread(void) {}\n",
                       "tokenize.c": "void tokenize(void) { /* console stack layout */ }\n"}.items():
        (repo / "progress" / "src" / name).write_text(text)
    manifest = {"layout": [{"name": "race_update", "vram": 0x80001000, "size": 8, "status": "open"},
                           {"name": "tokenize", "vram": 0x80001008, "size": 8, "status": "open"}],
                "symbols": [{"name": "gRecords", "address": 0x800C35A0, "size": 0, "kind": "data"},
                            {"name": "list_find_node", "address": 0x80022318, "size": 92, "kind": "func"},
                            {"name": "race_update", "address": 0x80001000, "size": 8, "kind": "func"}]}
    manifest["function_headers"] = {"race_update": "local_source_599"}  # from the game's own source: its part's header
    (repo / "progress" / "decompdecomp.json").write_text(json.dumps(manifest))
    port = repo / "port" / "wasm"
    for folder in ("src", "overrides", "include"):
        (port / folder).mkdir(parents=True)
    (port / "exclude.txt").write_text("# libultra the host provides\nosCreateThread.c\n")
    (port / "src" / "func_8001E640.c").write_text('#include "common.h"\nvoid func_8001E640(void *t) {}\n')
    (port / "overrides" / "tokenize.c").write_text("void tokenize(void) { /* &tokens[n] */ }\n")
    (port / "include" / "gap.h").write_text("#define M2C_UNK s32\n")
    return repo, manifest


def test_the_build_takes_matched_c_less_what_the_host_provides_plus_the_ports_own(tmp_path):
    """A racing game: 2,092 matched files, 69 of them libultra the host provides, 4 functions only the port has C
    for (the split had left them as data) and 6 files the port needs its own version of."""
    repo, _ = save(tmp_path)
    out = tmp_path / "build"
    chosen = gather(repo, out)
    assert sorted(chosen) == ["func_8001E640.c", "race_update.c", "tokenize.c"]
    assert chosen["tokenize.c"].parent.name == "overrides"
    built = {p.name: p.read_text() for p in (out / "src").glob("*.c")}
    assert built["race_update.c"].startswith('#include "common_local_source_599.h"\n')  # its part's header
    assert built["func_8001E640.c"].count('#include "common') == 1  # the shared header, included once
    assert "&tokens[n]" in built["tokenize.c"]
    assert {p.name for p in (out / "include").glob("*.h")} == {"common.h", "gap.h"}
    (repo / "port" / "wasm" / "src" / "func_8001E640.c").unlink()  # a file that went goes from the build too
    gather(repo, out)
    assert not (out / "src" / "func_8001E640.c").exists()


def test_a_file_an_old_save_wrote_in_another_case_is_not_deleted_as_stale(tmp_path):
    """An old save wrote SelectStateReset.c into the build; a rename left selectStateReset.c. On a case-insensitive
    disk they are one file, and the build deleted it as stale: that function's C was gone and the build stopped."""
    repo, _ = save(tmp_path)
    out = tmp_path / "build"
    (out / "src").mkdir(parents=True)
    (out / "src" / "Race_Update.c").write_text("old")  # what an old save left, in another case
    gather(repo, out)
    names = {p.name.casefold() for p in (out / "src").glob("*.c")}
    assert "race_update.c" in names


def test_an_object_an_old_save_named_in_another_case_is_still_linked(tmp_path):
    for name in ("SelectStateReset.o", "race_update.o", "gone.o"):
        (tmp_path / name).write_bytes(b"")
    assert [p.name for p in linked(tmp_path, {"selectStateReset.c": 0, "race_update.c": 0})] == [
        "SelectStateReset.o", "race_update.o"]


def test_a_folder_that_isnt_a_save_says_so(tmp_path):
    with pytest.raises(NetworkError, match="save repository"):
        gather(tmp_path, tmp_path / "build")


def test_every_function_and_datum_gets_its_console_address_from_the_manifest(tmp_path):
    _, manifest = save(tmp_path)
    rows, functions = addresses(manifest)
    assert "race_update 0x80001000 func" in rows and "gRecords 0x800C35A0 data" in rows
    assert "list_find_node 0x80022318 func" in rows  # a second name for a function resolves too
    assert functions == {"race_update", "tokenize", "list_find_node"}
    assert len([r for r in rows if r.startswith("race_update ")]) == 1


def test_calls_through_pointers_find_functions_by_their_console_address():
    header = ("/* export: 'race_update' */\nvoid w2c_game_race_update(w2c_game*);\n"
              "/* export: 'func_8001E640' */\nvoid w2c_game_func_8001E640(w2c_game*, u32);\n"
              "/* export: 'port_helper' */\nvoid w2c_game_port_helper(w2c_game*);\n")
    table = function_table(header, ["race_update 0x80001000 func", "gRecords 0x800C35A0 data"])
    assert table.index("0x80001000u") < table.index("0x8001E640u")  # sorted: the host searches it
    assert "w2c_game_port_helper" not in table and "port_func_count = 2;" in table


def test_the_n64_kit_has_the_hosts_part():
    kit = get("n64").port_kit
    assert (kit.wasm / "host.c").is_file() and (kit.wasm / "libultra.sigs").is_file()
    assert "127.0.0.1" in (kit.wasm / "live.c").read_text() or "INADDR_LOOPBACK" in (kit.wasm / "live.c").read_text()
    assert "port_audio_task" in (kit.wasm / "audio.c").read_text()
    for row in (kit.wasm / "libultra.sigs").read_text().splitlines():
        assert len(row.split("\t")) == 3, row


def test_a_call_by_a_functions_older_name_goes_to_the_function(tmp_path):
    """Polishing renamed func_8007F798; a caller's C still used the old name, which linked in the decomp through its
    symbol table but left the WebAssembly build calling a stub: no graphics task ever started."""
    a, b = tmp_path / "a.ll", tmp_path / "b.ll"
    a.write_text("define void @stream_poll(i32 %0) {\n  ret void\n}\n")
    b.write_text("declare void @func_8007F798(i32)\ndeclare void @func_80001234()\ndeclare i32 @osGetCount()\n"
                 "define void @caller() {\n  call void @func_8007F798(i32 1)\n  ret void\n}\n")
    fixed = ["stream_poll 0x8007F798 func", "caller 0x80001000 func"]
    assert renames([a, b], fixed) == {"func_8007F798": "stream_poll"}  # nothing defined at the others' addresses
    c = tmp_path / "c.ll"  # a call by a data-style automatic name goes to the function there too
    c.write_text("declare void @D_8007F798(i32)\ndefine void @hud() {\n  call void @D_8007F798(i32 1)\n  ret void\n}\n")
    assert renames([a, c], fixed)["D_8007F798"] == "stream_poll"


def test_a_fixed_function_the_c_declared_variadic_gets_its_arguments():
    """A strip-drawing function declared its texture loader `f(void *, u32, s32, s32, ...)` and passed 17
    arguments; the loader takes 17 fixed ones. Called as variadic, 13 went into a varargs buffer it never read: every
    strip of the intro's picture loaded with width and height 0."""
    from decompnet.port.wasm.adapt import adapt
    from decompnet.port.wasm.beir import Types
    ir = ("declare ptr @load(ptr, i32, i32, i32, i32)\n"
          "define void @caller(ptr %p) {\n"
          "  %r = call ptr (ptr, i32, ...) @load(ptr noundef %p, i32 noundef 1, i32 noundef 2, i32 noundef 3, i32 noundef 4)\n"
          "  ret void\n}\n")
    out = adapt(ir, [], Types(ir), {})
    assert "call ptr @load(ptr noundef %p, i32 noundef 1, i32 noundef 2, i32 noundef 3, i32 noundef 4)" in out
    assert "..." not in out.split("define")[1]


def test_every_game_load_and_store_is_volatile_so_none_is_narrowed():
    """`v0 = v & 0xFFFF` compiled to a 16-bit load at v's own address: the high half in big-endian memory. A racing
    game's vertex index came back 0 and its intro's picture drew one row in four."""
    from decompnet.port.wasm.beir import volatile
    lines = ["  %1 = load i32, ptr %v, align 4", "  store i32 %2, ptr %w, align 4", "  %3 = load volatile i8, ptr %x",
             "  %4 = load atomic i32, ptr %y seq_cst, align 4", "  call void @f(ptr %v)"]
    assert volatile(lines) == ["  %1 = load volatile i32, ptr %v, align 4", "  store volatile i32 %2, ptr %w, align 4",
                               "  %3 = load volatile i8, ptr %x", "  %4 = load atomic i32, ptr %y seq_cst, align 4",
                               "  call void @f(ptr %v)"]


def test_a_variadic_function_the_c_declared_fixed_gets_its_arguments_in_its_buffer():
    """A text-table loader declared its sprintf `f(u8 *, u8 *, u8 *)`; the sprintf is variadic, so its third
    argument was read from a varargs buffer that wasn't there: a texture pack's name came out wrong, its size 0, and
    the heap stopped the game."""
    from decompnet.port.wasm.adapt import adapt
    from decompnet.port.wasm.beir import Types
    ir = ("declare void @fmt(ptr, ptr, ...)\n"
          "define void @caller(ptr %b, ptr %f, ptr %s) {\n"
          "  call void @fmt(ptr noundef %b, ptr noundef %f, ptr noundef %s)\n"
          "  ret void\n}\n")
    out = adapt(ir, [], Types(ir), {})
    assert "call void (ptr, ptr, ...) @fmt(ptr noundef %b, ptr noundef %f, ptr noundef %s)" in out
    unprototyped = ("declare i32 @read_status(...)\ndefine i32 @caller() {\n  %r = call i32 @read_status()\n"
                    "  ret i32 %r\n}\n")  # C's `f()`: no varargs buffer for it
    assert "call i32 @read_status()" in adapt(unprototyped, [], Types(unprototyped), {})


def test_a_call_through_a_pointer_leaves_its_arguments_in_the_register_words():
    """A text-table loader was called through a pointer with the table's record in $a0, declared no parameter, and
    passed $a0 on to the next call: its table came from a stale register word."""
    from decompnet.port.wasm.adapt import REGS, V0, adapt
    from decompnet.port.wasm.beir import Types
    ir = ("define void @run(ptr %h, ptr %rec, i16 %n) {\n"
          "  call void %h(ptr noundef %rec, i16 noundef %n)\n"
          "  ret void\n}\n")
    out = adapt(ir, [], Types(ir), {})
    stores = [line for line in out.splitlines() if line.strip().startswith("store i32") and V0 not in line]
    assert len(stores) == 2 and f"inttoptr (i32 {REGS} to ptr)" in stores[0]
    assert out.index("store i32") < out.index("call void %h(")


def test_a_void_function_leaves_its_last_calls_result_for_a_caller_that_takes_one():
    """A list helper was declared void and ended with the call that made the new node; its caller took the node from
    it, as $v0 still held it on the console. The port returned 0 and a racing game's HUD wrote its texture through
    address 0x0C: the race stopped on the first frame it drew."""
    from decompnet.port.wasm.adapt import V0, adapt
    from decompnet.port.wasm.beir import Types
    ir = ("declare ptr @alloc_node(i32)\n"
          "define void @append(i32 %h) {\n  %1 = call ptr @alloc_node(i32 noundef %h) #3\n  ret void\n}\n"
          "define void @caller() {\n  %n = call ptr @append(i32 noundef 1)\n  ret void\n}\n")
    out = adapt(ir, [], Types(ir), {})
    append = out.split("define void @append")[1].split("}")[0]
    assert f"ptr {V0}" in append and append.index("store i32") < append.index("ret void")
    thunk = out.split("define internal ptr @append.port_thunk")[1]
    assert f"load i32, ptr {V0}" in thunk and "ret ptr null" not in thunk


def test_bitfields_get_the_consoles_bits():
    """A big-endian MIPS compiler gives a unit's first bitfield its top bits; wasm32 its bottom ones. A racing
    game's track setup read `b0 : 1` from the wrong bit, ran off the end of its segment table and corrupted the
    next one."""
    from decompnet.port.wasm.build import big_endian_bitfields
    text = ("typedef struct {\n  u32 pad : 27;\n  u32 b4 : 1;\n  u32 b3 : 1;\n  u32 b2 : 1;\n  u32 b1 : 1;\n"
            "  u32 b0 : 1;\n} Flags;\n")
    fixed = big_endian_bitfields(text)
    assert [line.strip() for line in fixed.splitlines()[1:8]] == [
        "u32 b0 : 1;", "u32 b1 : 1;", "u32 b2 : 1;", "u32 b3 : 1;", "u32 b4 : 1;", "u32 pad : 27;", "u32 : 0;"]
    two_units = big_endian_bitfields("struct G {\n  u16 flag : 1;\n  u16 letter : 5;\n  u16 track : 4;\n"
                                     "  u16 values : 12;\n};\n")
    assert [line.strip() for line in two_units.splitlines()[1:9]] == [  # values doesn't fit the first unit
        "u16 : 6;", "u16 track : 4;", "u16 letter : 5;", "u16 flag : 1;", "u16 : 0;",
        "u16 : 4;", "u16 values : 12;", "u16 : 0;"]
    padding = "struct M {\n  s16 h[9];\n  unsigned int :16;\n  unsigned int :32;\n};\n"
    assert big_endian_bitfields(padding) == padding  # padding alone may start mid-unit: as it was


def test_a_struct_passed_by_value_keeps_every_word_the_caller_passed():
    """A loader's C declared its by-value block 640 bytes; its caller passed 708, and on MIPS the loader read the
    words past 640 from the caller's stack. The thunk copied 640: the last texture name was cut short."""
    from decompnet.port.wasm.adapt import word_thunk
    out = word_thunk("t", "load", "void", ["ptr"], {0: 8}, "void", [("ptr", "%b")], [16])
    assert "alloca [4 x i32]" in out and out.count("store i32") == 4


def test_an_unsigned_byte_argument_widens_without_its_sign():
    """A road polygon's texture coordinates were u8 in the caller and s32 in the callee. Widened with their sign,
    255 became -1 and every road quad sampled one texel: the road came out as a checkerboard."""
    from decompnet.port.wasm.adapt import adapt
    from decompnet.port.wasm.beir import Types
    ir = ("declare void @set_uv(ptr, i32)\n"
          "define void @caller(ptr %p, i8 %s) {\n  call void @set_uv(ptr noundef %p, i8 noundef zeroext %s)\n"
          "  ret void\n}\n")
    out = adapt(ir, [], Types(ir), {})
    assert "zext i8" in out and "sext i8" not in out
