"""The WebAssembly build of a finished game (port/wasm): what it takes from the game's save repository."""

import json
import re

import pytest

from decompnet.consoles import get
from decompnet.network import NetworkError
from decompnet.port.wasm.build import addresses, function_table, gather, linked, renames


def save(tmp_path):
    repo = tmp_path / "game"
    (repo / "progress" / "src").mkdir(parents=True)
    (repo / "progress" / "include").mkdir()
    (repo / "progress" / "include" / "common.h").write_text("typedef int s32;\n")
    (repo / "progress" / "include" / "common_local_source_599.h").write_text('#include "common.h"\n')
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
    assert {p.name for p in (out / "include").glob("*.h")} == {"common.h", "common_local_source_599.h", "gap.h"}
    (repo / "port" / "wasm" / "src" / "func_8001E640.c").unlink()  # a file that went goes from the build too
    gather(repo, out)
    assert not (out / "src" / "func_8001E640.c").exists()


def test_a_save_written_before_saves_named_headers_takes_the_projects_own(tmp_path):
    """An N64 game's save from before saves named each function's header had per-part headers but no
    function_headers: every function got the shared header and 268 files stopped at `unknown type name`. The
    project's own mapping (play.function_headers) fills the gap."""
    repo, manifest = save(tmp_path)
    del manifest["function_headers"]
    (repo / "progress" / "decompdecomp.json").write_text(json.dumps(manifest))
    out = tmp_path / "build"
    gather(repo, out, function_headers={"race_update": "local_source_599"})
    assert (out / "src" / "race_update.c").read_text().startswith('#include "common_local_source_599.h"\n')


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


def test_a_void_function_ending_with_a_void_call_keeps_what_that_callee_left():
    """A racing game's start-yaw helper was declared void and ended with a void atan2 wrapper, which itself ended
    with the atan2 call: on the console the angle went through both in $v0 to the caller. The port stored 0 after the
    wrapper and every car started facing backwards."""
    from decompnet.port.wasm.adapt import V0, adapt
    from decompnet.port.wasm.beir import Types
    ir = ("declare i32 @atan2(i32, i32)\n"
          "define void @wrapper(i32 %x, i32 %z) {\n  %1 = call i32 @atan2(i32 noundef %x, i32 noundef %z) #2\n"
          "  ret void\n}\n"
          "define void @start_yaw(i32 %x, i32 %z) {\n  call void @wrapper(i32 noundef %x, i32 noundef %z) #2\n"
          "  ret void\n}\n"
          "define void @tidy(ptr %p) {\n  call void @llvm.memset.p0.i32(ptr %p, i8 0, i32 4, i1 false)\n"
          "  ret void\n}\n"
          "define void @caller() {\n  %y = call i32 @start_yaw(i32 noundef 1, i32 noundef 2)\n  ret void\n}\n")
    out = adapt(ir, [], Types(ir), {})
    body = lambda name: out.split(f"define void @{name}")[1].split("}")[0]  # noqa: E731
    assert "%1" in body("wrapper") and f"ptr {V0}" in body("wrapper")
    assert V0 not in body("start_yaw"), "the wrapper's angle stays in $v0"
    assert f"store i32 0, ptr {V0}" in body("tidy"), "an intrinsic runs no callee: 0 as before"
    assert f"load i32, ptr {V0}" in out.split("define internal i32 @start_yaw.port_thunk")[1]


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


def test_a_rebuild_compiles_a_file_again_only_when_it_or_a_header_it_includes_changed(tmp_path):
    """Play rebuilt a 2,000-file game in minutes after a Save that changed one function: the keys follow each file's
    own C and the headers it includes, not times a Save rewrites, and a header only some files include (a part's
    common_<part>.h) leaves the others alone."""
    from decompnet.port.wasm.build import _Files
    inc = tmp_path / "include"
    inc.mkdir()
    (inc / "common.h").write_text('#include "types.h"\n')
    (inc / "types.h").write_text("typedef int s32;\n")
    (inc / "common_part.h").write_text('#include "common.h"\n')
    a, b = tmp_path / "a.c", tmp_path / "b.c"
    a.write_text('#include "common.h"\n#include <stdarg.h>\ns32 a;\n')
    b.write_text('#include "common_part.h"\ns32 b;\n')
    keys = lambda: (_Files().key(a, [inc], "zig", "-O0"), _Files().key(b, [inc], "zig", "-O0"))  # noqa: E731
    before = keys()
    a.write_text(a.read_text())  # rewritten as it was: still the same
    assert keys() == before
    (inc / "common_part.h").write_text('#include "common.h"\nextern s32 c;\n')
    after = keys()
    assert after[0] == before[0] and after[1] != before[1]
    (inc / "types.h").write_text("typedef long s32;\n")  # included through another header
    assert all(x != y for x, y in zip(keys(), after))
    assert _Files().key(a, [inc], "zig", "-O1") != _Files().key(a, [inc], "zig", "-O0")  # the flags count


def test_placing_reads_only_the_table_rows_of_the_names_its_function_uses():
    """The signatures and addresses change with nearly every Save, so keying each function's placing on the whole
    tables placed all 2,000 again; it depends only on the rows of the names its IR uses, a call's new name among them."""
    from decompnet.port.wasm.build import table_rows
    ir = "define void @caller() {\n  call void @func_8007F798(i32 1)\n  %1 = load i32, ptr @gRecords\n  ret void\n}\n"
    sigs = {"caller": "void\t", "stream_poll": "void\ti32", "other": "i32\t"}
    fixed = {"caller": "0x80001000 func", "gRecords": "0x800C35A0 data", "other": "0x80002000 func"}
    renamed = {"func_8007F798": "stream_poll"}
    rows = table_rows(ir, sigs, fixed, {"caller", "stream_poll", "other"}, renamed)
    assert [r.split("|")[0] for r in rows] == ["caller", "func_8007F798", "gRecords", "stream_poll"]
    unrelated = table_rows(ir, {**sigs, "other": "void\ti32", "new_fn": "void\t"}, {**fixed, "new_fn": "0x80003000 func"},
                           {"caller", "stream_poll", "other", "new_fn"}, renamed)
    assert unrelated == rows
    assert table_rows(ir, {**sigs, "stream_poll": "void\ti32, i32"}, fixed, {"caller", "stream_poll"}, renamed) != rows


def test_wasm2c_output_that_didnt_change_keeps_its_file(tmp_path):
    """wasm2c writes every part again; only the parts that differ replace the build's, so the host compiles only those."""
    import os

    from decompnet.port.wasm.build import _sync
    old, new = tmp_path / "c", tmp_path / "c.new"
    old.mkdir()
    new.mkdir()
    for name, text in {"game_0.c": "same", "game_1.c": "old", "game_9.c": "gone"}.items():
        (old / name).write_text(text)
    os.utime(old / "game_0.c", (1, 1))
    (new / "game_0.c").write_text("same")
    (new / "game_1.c").write_text("new")
    (new / "game.h").write_text("h")
    _sync(new, old)
    assert sorted(p.name for p in old.iterdir()) == ["game.h", "game_0.c", "game_1.c"] and not new.exists()
    assert (old / "game_0.c").stat().st_mtime == 1 and (old / "game_1.c").read_text() == "new"


def test_the_build_reports_its_steps_and_each_steps_files(tmp_path):
    """Play's bar: which step of how many, and how many of a step's files are done; the log says how many a rebuild
    could keep."""
    from decompnet.port.wasm.build import STEPS, Progress, _parallel
    seen, lines = [], []

    def log(line):
        lines.append(line)
    log.progress = seen.append
    progress = Progress(log, (*STEPS, "Starting the game"))
    progress.start(STEPS[1])
    failed = _parallel(["a.c", "b.c", "c.c"], lambda job: False if job == "a.c" else ("broken" if job == "c.c" else None),
                       log, "files", progress.files)
    assert failed == ["c.c: broken"]
    assert seen[0] == {"phase": STEPS[1], "step": 2, "steps": len(STEPS) + 1}
    assert seen[-1] == {"phase": STEPS[1], "step": 2, "steps": len(STEPS) + 1, "done": 3, "total": 3}
    assert "2 files redone, 1 unchanged" in lines
    Progress(print).start(STEPS[0])  # a log without a bar (the command line) builds the same


def test_the_build_leaves_out_the_library_files_play_proves_besides_exclude_txt(tmp_path):
    repo, _ = save(tmp_path)
    chosen = gather(repo, tmp_path / "build", {"race_update.c"})
    assert sorted(chosen) == ["func_8001E640.c", "tokenize.c"]


def test_calls_that_disagree_with_their_callee_are_recorded_by_kind(tmp_path):
    """Matched but suspicious: the build bridges each call whose C disagrees with its callee, and records it. A
    result counts only when the caller uses it (an unprototyped call names its result either way)."""
    from decompnet.port.wasm import calls
    from decompnet.port.wasm.adapt import adapt
    from decompnet.port.wasm.beir import Types
    ir = ("declare void @wrapper(i32, i32)\n"
          "declare i32 @sprintf(ptr, ptr, ...)\n"
          "declare void @loader(ptr, i32, i32)\n"
          "declare void @set_uv(i32)\n"
          "declare void @flush()\n"
          "define void @start_yaw(i32 %x) {\n"
          "  %1 = call i32 @wrapper(i32 noundef %x, i32 noundef 2)\n"
          "  store i32 %1, ptr null, align 4\n"
          "  %2 = call i32 @wrapper(i32 noundef %x, i32 noundef 3)\n"
          "  %3 = call i32 @sprintf(ptr noundef null, ptr noundef null, i32 noundef 1)\n"
          "  call void @loader(ptr noundef null)\n"
          "  call void @set_uv(i8 noundef zeroext 255)\n"
          "  call void @flush(i32 noundef 7)\n"
          "  ret void\n}\n")
    report = []
    adapt(ir, report, Types(ir), {})
    kinds = sorted((r["kind"], r["callee"]) for r in report)
    assert kinds == [("argument-type", "set_uv"), ("extra-arguments", "flush"), ("missing-arguments", "loader"),
                     ("variadic-declared-fixed", "sprintf"), ("void-result", "wrapper")], kinds
    assert next(r for r in report if r["kind"] == "argument-type")["unsigned"]
    # gathered from the build's files, only the linked objects', worst kind first
    o = tmp_path / "o"
    o.mkdir()
    (o / "start_yaw.be.ll.calls").write_text("\n".join(json.dumps(r) for r in report) + "\n")
    (o / "gone.be.ll.calls").write_text(json.dumps({**report[0], "caller": "gone"}) + "\n")
    (o / "old.be.ll.calls").write_text("old calls new with 0 of its 1 arguments\n")  # an older build's sentence
    found = calls.write(tmp_path, [o / "start_yaw.o", o / "old.o"])
    assert list(found) == ["start_yaw"] and json.loads((tmp_path / "calls.json").read_text()) == found
    summary = calls.load(tmp_path)
    assert (summary["functions"], summary["calls"]) == (1, 5)
    assert [k["kind"] for k in summary["kinds"]] == ["void-result", "variadic-declared-fixed", "missing-arguments",
                                                     "argument-type", "extra-arguments"]
    assert summary["kinds"][0]["examples"] == ["start_yaw → wrapper"]
    assert "argument 1: unsigned i8 for i32" in summary["kinds"][3]["examples"][0]


def test_a_function_is_called_from_a_recorded_state_as_mips_passed_its_arguments():
    """compare_behaviour runs one call of the port's decompiled C from the registers the console had (the host's
    PORT_CALL): each function's invoker reads o32's argument words, leading floats from $f12/$f14, a 64-bit value
    from an even pair of words, and gives back $v0 (a void function's: the $v0 word), $v1 or $f0."""
    from decompnet.port.wasm.build import function_table, invoker
    assert invoker("w2c_game_f", "u32", ["u32", "u32"])[1].endswith("out[0] = w2c_game_f(g, w[0], w[1]); out[4] = 1; }")
    assert "w2c_game_f(g, port_f32(f[12]), port_f32(f[14]), w[2])" in invoker("w2c_game_f", "f32", ["f32", "f32", "u32"])[1]
    assert "out[2] = port_b32(" in invoker("w2c_game_f", "f32", ["f32"])[1]
    # a double first: $f12/$f13, low word first; an int then a 64-bit: words 2 and 3 (the even pair)
    assert "port_f64((u64)f[13] << 32 | f[12])" in invoker("w2c_game_f", "void", ["f64"])[1]
    assert "w2c_game_f(g, w[0], port_w64(w, 2))" in invoker("w2c_game_f", "u64", ["u32", "u64"])[1]
    assert "out[0] = port_v0(g); out[4] = 0;" in invoker("w2c_game_f", "void", [])[1], "says it returns nothing"
    header = ("/* export: 'start_yaw' */\nvoid w2c_game_start_yaw(w2c_game*, u32, u32);\n"
              "/* export: 'func_80001000' */\nu32 w2c_game_func_80001000(w2c_game*);\n")
    table = function_table(header, ["start_yaw 0x80002000 func"])
    assert "const PortCall port_calls[] = {\n    {0x80001000u, port_i_w2c_game_func_80001000},\n" \
           "    {0x80002000u, port_i_w2c_game_start_yaw}," in table


def test_a_part_header_the_save_never_wrote_falls_back_to_the_shared_one(tmp_path):
    """A racing game's save named alEvtqNextEvent's part (local_source_<id>) with no header for it: the app compiles
    such a function with the shared header, and the build stopped at the missing file."""
    repo, manifest = save(tmp_path)
    (repo / "progress" / "include" / "common_local_source_599.h").unlink()
    gather(repo, tmp_path / "build")
    assert (tmp_path / "build" / "src" / "race_update.c").read_text().startswith('#include "common.h"\n')


def test_an_unused_kr_varargs_stub_compiles_as_an_unprototyped_function():
    from decompnet.port.wasm.build import kr_varargs
    stub = ("#define va_alist __builtin_va_alist\n#define va_dcl int __builtin_va_alist; ...\n\n"
            "void func_8003DE60(va_alist)\nva_dcl\n{\n}\n")
    assert "void func_8003DE60()\n{" in kr_varargs(stub)
    reads = stub.replace("{\n}", "{ va_list ap; va_start(ap); va_end(ap); }")
    assert kr_varargs(reads) == reads  # one that reads its arguments is left for the port's own C


def test_a_file_defining_other_functions_keeps_only_its_own_when_they_have_theirs(tmp_path):
    """A racing game's three sound-player setters were each matched with C defining all three: the port compiles
    whole files, so the copies collided at link."""
    repo, _ = save(tmp_path)
    both = ("static int helper(void) { return 1; }\n"
            "void race_update(void) { helper(); }\n"
            "void tokenize(void) { /* not race_update's */ }\n"
            "void elsewhere(void) {}\n")
    (repo / "progress" / "src" / "race_update.c").write_text(both)
    gather(repo, tmp_path / "build")
    built = (tmp_path / "build" / "src" / "race_update.c").read_text()
    assert "void race_update(void)" in built and "static int helper" in built
    assert "void tokenize" not in built  # tokenize.c has its own
    assert "void elsewhere" in built  # no file of its own: kept


def test_callers_that_disagree_on_an_unprototyped_return_get_one_signature(tmp_path):
    """A racing game's callers declared a function outside its layout `s32 f();` in one file and `void f();` in
    another: neither defines it, sigs.py skipped declarations with no prototype, and the link refused the two. One
    declared only one way keeps its call sites as they are (the host's builtins take the argument the C passes)."""
    import subprocess
    import sys
    from pathlib import Path

    from decompnet.port.wasm import build
    one = tmp_path / "a.ll"
    one.write_text("declare i32 @func_8021FCA4(...)\ndeclare i32 @__builtin_mips_exchange_fcsr(...)\n")
    two = tmp_path / "b.ll"
    two.write_text("declare void @func_8021FCA4(...)\n")
    known = tmp_path / "known.sigs"
    known.write_text("")
    aliases = tmp_path / "renames.txt"
    aliases.write_text("")
    out = tmp_path / "sigs.txt"
    subprocess.run([sys.executable, str(Path(build.HERE) / "sigs.py"), str(out), str(known), str(aliases),
                    str(one), str(two)], check=True, capture_output=True, text=True)
    rows = dict(line.split("\t", 1) for line in out.read_text().splitlines())
    assert rows == {"func_8021FCA4": "i32\t"}  # a value over none, no parameters known


def test_renamed_library_calls_share_a_signature(tmp_path):
    """A game's two callers of a renamed library import used different argument counts and failed to link."""
    import subprocess
    import sys

    from decompnet.port.wasm.build import HERE

    typed = tmp_path / "typed.ll"
    typed.write_text("declare double @libraryLog(double)\n"
                     "define double @typed(double %x) {\n"
                     "  %r = call double @libraryLog(double %x)\n  ret double %r\n}\n")
    loose = tmp_path / "loose.ll"
    loose.write_text("declare double @oldLog(...)\n"
                     "define double @loose(double %x) {\n"
                     "  %r = call double @oldLog()\n  ret double %r\n}\n")
    aliases = tmp_path / "renames.txt"
    aliases.write_text("libraryLog log\noldLog log\n")
    known = tmp_path / "known.sigs"
    known.write_text("")
    sigs = tmp_path / "sigs.txt"
    subprocess.run([sys.executable, HERE / "sigs.py", sigs, known, aliases, typed, loose],
                   check=True, capture_output=True, text=True)

    for source in (typed, loose):
        placed = source.with_suffix(".be.ll")
        subprocess.run([sys.executable, HERE / "beir.py", source, placed, "--renames", aliases, "--signatures", sigs],
                       check=True, capture_output=True, text=True)
        ir = placed.read_text()
        assert "declare double @log(double)" in ir
        assert "call double @log(double " in ir
        assert "call double @log()" not in ir
        assert ("call double @log.port_thunk_" in ir) == (source == loose)


def test_renamed_strlen_uses_the_host_signature(tmp_path):
    """A legacy declaration of a renamed import must not change the ABI of the host's strlen."""
    import subprocess
    import sys

    from decompnet.port.wasm.build import HERE

    source = tmp_path / "caller.ll"
    source.write_text("declare i16 @string_length(ptr, i32, i32, i32)\n")
    aliases = tmp_path / "renames.txt"
    aliases.write_text("string_length strlen\n")
    sigs = tmp_path / "sigs.txt"
    known = get("n64").port_kit.wasm / "libultra.sigs"
    subprocess.run([sys.executable, HERE / "sigs.py", sigs, known, aliases, source],
                   check=True, capture_output=True, text=True)
    assert "strlen\ti32\tptr" in sigs.read_text().splitlines()


def test_library_code_with_no_c_is_called_by_its_library_name(tmp_path):
    """A racing game's bzero kept its automatic name (`func_8003DDC0`): the host stubbed it to nothing. Library
    detection's name goes to the host, which answers bzero by name in any game; a name another file defines wins."""
    from decompnet.port.wasm.build import renames
    ll = tmp_path / "a.ll"
    ll.write_text("define void @caller() {\n}\ndeclare void @func_8003DDC0(ptr, i32)\ndeclare void @func_80001000()\n"
                  "define void @mine() {\n}\n")
    out = renames([ll], ["mine 0x80001000 func"], {"func_8003DDC0": "bzero", "caller": "osOther"})
    assert out == {"func_8003DDC0": "bzero", "func_80001000": "mine"}


def test_stubs_read_every_host_file(tmp_path):
    """The host's functions are in host.c and in a game's own file (games/<sha1>.c): a stub for one defined in the
    second collided with it at link."""
    import subprocess
    import sys

    from decompnet.port.wasm import build
    h = tmp_path / "game.h"
    h.write_text("u32 w2c_env_osA(struct w2c_env*, u32);\nvoid w2c_env_func_80091920(struct w2c_env*, u32, u32);\n"
                 "void w2c_env_osB(struct w2c_env*);\n")
    (tmp_path / "host.c").write_text("u32 w2c_env_osA(struct w2c_env *e, u32 x) { return x; }\n")
    (tmp_path / "game.c").write_text("void w2c_env_func_80091920(struct w2c_env *e, u32 p, u32 n) {}\n")
    out = tmp_path / "stubs.c"
    subprocess.run([sys.executable, str(build.HERE / "stubs.py"), str(h), str(out), str(tmp_path / "host.c"),
                    str(tmp_path / "game.c")], check=True, capture_output=True)
    text = out.read_text()
    assert "w2c_env_osB(" in text and "w2c_env_osA(" not in text and "func_80091920" not in text


def test_an_old_name_for_a_function_with_no_c_goes_to_its_name_now(tmp_path):
    """A racing game's matched C called libultra's osCreateMesgQueue by its automatic name, func_80033A90. The build
    leaves libultra the host provides out, so no C defined it, and the call reached the host under the old name:
    stubbed, the game's threads never got their queue."""
    from decompnet.port.wasm.build import renames
    ll = tmp_path / "a.ll"
    ll.write_text("define void @caller() {\n}\ndeclare void @func_80033A90(ptr, ptr, i32)\n")
    assert renames([ll], ["osCreateMesgQueue 0x80033A90 func"]) == {"func_80033A90": "osCreateMesgQueue"}


def test_c_ido_takes_is_read_as_clang_reads_it():
    """An N64 game's matched C that IDO compiled: m2c's untyped declarations, a hex constant before a minus, and a
    part's header declaring a function it then defines static. 9 of its files stopped the WebAssembly build."""
    from decompnet.port.wasm.build import ido_c, static_prototypes
    text = ido_c("M2C_UNK Humvee_StopSound(arg0);  /* extern */\nvoid *f(arg0, arg1);\n"
                 "    return g(arg0);\nstruct B{char q[0x2be-0x174];};\ndouble d = 1e-5;\n")
    assert "M2C_UNK Humvee_StopSound();" in text and "void *f();" in text
    assert "return g(arg0);" in text  # a call stays a call
    assert "0x2be - 0x174" in text and "1e-5" in text
    header = static_prototypes("int Send (int Event);\nint other(void);\nstatic BOOL Send(int Event)\n{\n}\n")
    assert "static int Send (int Event);" in header and "\nint other(void);" in header


def test_the_build_leaves_debug_only_code_out():
    """zig cc defines _DEBUG at -O0, so libultra's debug heap code (`hp->count`) was compiled into the port."""
    from decompnet.port.wasm.build import IR_FLAGS
    assert "-U_DEBUG" in IR_FLAGS


def test_a_data_named_call_target_keeps_its_llvm_function_declaration():
    from decompnet.port.wasm.beir import pin
    text = """@callback = global ptr @D_80001200
@D_80002000 = external global i32

define i32 @update() {
  %v = call i32 @D_80001200(i32 1)
  ret i32 %v
}
declare i32 @D_80001200(i32)
"""
    result = pin(text, {'D_80001200': (0x80001200, 'data'), 'D_80002000': (0x80002000, 'data')}, set())
    assert 'declare i32 @D_80001200(i32)' in result
    assert 'call i32 @D_80001200(i32 1)' in result
    assert '@callback = global ptr inttoptr (i32 2147488256 to ptr)' in result
    assert '@D_80002000' not in result
    assert 'declare i32 inttoptr' not in result


def test_repeated_context_helpers_keep_each_callers_own_body_and_only_the_owner_is_public(tmp_path):
    from decompnet.port.wasm.build import context_functions
    first, second, owner = [tmp_path/f'{name}.ll' for name in ('first', 'second', 'helper')]
    first.write_text('define i32 @helper() {\n ret i32 1\n}\ndefine internal i32 @local() {\n ret i32 3\n}\n')
    second.write_text('define i32 @helper() {\n ret i32 2\n}\ndefine internal i32 @local() {\n ret i32 4\n}\n')
    private = context_functions([first, second])
    assert private[first]['helper'] != private[second]['helper']
    assert all('local' not in names for names in private.values())
    owner.write_text('define i32 @helper() {\n ret i32 5\n}\n')
    with_owner = context_functions([first, second, owner])
    assert owner not in with_owner and with_owner[first] == private[first]


def test_private_context_calls_keep_the_original_address_when_taken_as_a_pointer(tmp_path):
    import subprocess
    import sys

    from decompnet.port.wasm import build
    src, out, fixed = tmp_path/'in.ll', tmp_path/'out.ll', tmp_path/'fixed.txt'
    src.write_text('@callback = global ptr @helper\n'
                   'define i32 @helper() {\n ret i32 1\n}\n'
                   'define i32 @caller() {\n %v = call i32 @helper()\n ret i32 %v\n}\n')
    fixed.write_text('helper 0x80001200 func\n')
    subprocess.run([sys.executable,str(build.HERE/'beir.py'),str(src),str(out),'--fixed',str(fixed),
                    '--context-function','helper=port_context_first_helper'],check=True,capture_output=True,text=True)
    text=out.read_text()
    assert 'define i32 @port_context_first_helper(' in text
    assert '@port_context_first_helper(' in text and 'call i32 @helper(' not in text
    assert r'c"\80\00\12\00"' in text


def test_a_struct_returning_calls_argument_is_its_value():
    """`ptr dead_on_unwind writable sret(%struct.CQuatern_t) align 4 %24` (an N64 game's call through a pointer)
    was read with the attributes as its value, and its function couldn't be placed."""
    from decompnet.port.wasm.adapt import parse_arg
    assert parse_arg("ptr dead_on_unwind writable sret(%struct.CQuatern_t) align 4 %24")[1] == "%24"


def test_a_global_two_files_define_links_once():
    """A save can define one global in two files (each function matched with its own copy): the copies are weak, so
    wasm-ld keeps one instead of refusing the duplicate."""
    from decompnet.port.wasm.beir import Types, rewrite_global
    types = Types("")
    assert rewrite_global(types, "@seq = hidden global i32 0, align 4").startswith("@seq = weak hidden global")
    assert rewrite_global(types, "@s = internal global i32 0, align 4").startswith("@s = internal global")
    assert rewrite_global(types, "@e = external global i32, align 4") == "@e = external global i32, align 4"


def test_a_game_with_no_c_yet_builds_from_its_recompiled_c(tmp_path):
    """A racing game newly imported: none of its functions has C, so N64Recomp's C runs them all. The WebAssembly
    module still needs an object (wasm-ld: no input files) that keeps the stack pointer the host sets and imports
    from the host's environment, which wasm2c's instantiate then takes (host.c)."""
    from decompnet.port.wasm.build import EMPTY_UNIT
    repo = tmp_path / "game"
    (repo / "progress" / "src").mkdir(parents=True)
    (repo / "progress" / "decompdecomp.json").write_text(json.dumps({"layout": [], "symbols": []}))
    out = tmp_path / "build"
    chosen = gather(repo, out)
    assert list(chosen) == [EMPTY_UNIT]
    text = (out / "src" / EMPTY_UNIT).read_text()
    assert "char buffer[16];" in text and "osCreateThread(0, 0, 0, 0, 0, 0)" in text
    (repo / "progress" / "src" / "race_update.c").write_text("void race_update(void) {}\n")
    assert list(gather(repo, out)) == ["race_update.c"]  # the first matched function takes its place
    assert not (out / "src" / EMPTY_UNIT).exists()


def test_a_file_that_also_defines_a_left_out_function_is_left_out(tmp_path):
    """A packed shooter's _Printf was matched in its static helper's file too: leaving _Printf.c out still compiled
    _Printf there, with IDO's va_arg, and the build stopped."""
    repo, _ = save(tmp_path)
    src = repo / "progress" / "src"
    (src / "gl_func_0006E224.c").write_text("static void gl_func_0006E224(int x) {}\n"
                                            "int _Printf(int a) { gl_func_0006E224(a); return 0; }\n")
    (src / "_Printf.c").write_text("int _Printf(int a) { return 0; }\n")
    chosen = gather(repo, tmp_path / "build", {"_Printf.c"})
    assert "gl_func_0006E224.c" not in chosen and "_Printf.c" not in chosen
    assert "tokenize.c" in chosen


def test_every_automatic_data_name_says_its_address():
    """A packed shooter's sound bank was `RO_00628350 - D_00626DD0`: the build named only D_, B_ and func_ by their
    address, so the end was 0 and the DMA asked for -6 MB. The build's names are the app's (engine/common.py)."""
    from decompnet.app.engine.common import _AUTO_TOKEN
    from decompnet.port.wasm.beir import AUTO_DATA

    for name in ("RO_00628350", "D_00626DD0", "STR_80012340", "FLT_80012340", "DBL_80012340", "jtbl_80012340"):
        assert _AUTO_TOKEN.fullmatch(name) and re.fullmatch(rf"(?:{AUTO_DATA})_[0-9A-F]+", name), name


def test_an_endless_empty_loop_parks_its_thread():
    """A packed shooter's idle thread ended in `for (;;) {}` at priority 0: the host switches threads only at a
    call, so its scheduler, ready at 14, never ran and nothing was drawn."""
    from decompnet.port.wasm.build import ido_c

    for loop in ("for (;;) {}", "for(;;);", "while (1) { }", "while(1);"):
        out = ido_c(f"void idle(void) {{ start(); {loop} }}")
        assert "for (;;) port_idle_forever();" in out and out.startswith("void port_idle_forever(void);"), loop
    assert ido_c("void f(void) { for (;;) { work(); } }") == "void f(void) { for (;;) { work(); } }"


def test_a_bitfield_shares_its_unit_with_the_members_after_it():
    """libultra's Gsetcolor (`int cmd:8;` then three bytes and a word) is 8 bytes on the console: its bitfield closed
    in a word of its own made it 12, Gfx 16, and a packed shooter's text rectangles read padding as their texture
    coordinates. The struct keeps the console's size and the field its byte."""
    from decompnet.port.wasm.build import big_endian_bitfields

    text = ("typedef struct {\n  int cmd:8;\n  unsigned char pad;\n  unsigned char prim_min_level;\n"
            "  unsigned char prim_level;\n  unsigned long color;\n} Gsetcolor;")
    out = big_endian_bitfields(text)
    assert "  signed char cmd;" in out and ": 0;" not in out
    # a unit that closes the struct, or isn't whole bytes, is laid out in reverse as before
    assert "int : 0;" in big_endian_bitfields("struct s {\n  int a:8;\n};")
    assert "int : 0;" in big_endian_bitfields("struct s {\n  int a:3;\n  char b;\n};")
