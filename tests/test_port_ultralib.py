"""libultra's own C for a port, from the user's ultralib (port/ultralib.py): a homemade ultralib-shaped tree, never
ultralib's files (its C is SGI's and Nintendo's).

A racing game's `guTranslate` was found as ultralib's own build but still went to the agents, because the N64 port
compiles libultra's math as the game's C. With ultralib installed it's set aside and the port compiles ultralib's.
"""
import json
import threading
from pathlib import Path

import pytest

from decompnet import toolchains
from decompnet.app import sdk_assembly
from decompnet.network import NetworkError
from decompnet.port import pc_link, ultralib
from decompnet.port.wasm import build

from .test_sdk_c_set_aside import example  # noqa: F401  (the fixture: a homemade linked SDK object)

TRANSLATE = '''/* (C) a homemade test file { not a brace } */
#include "guint.h"

static float twice(float x) { return x + x; }

void guToyF(float mf[4][4], float x)
{
    mf[3][0] = twice(x);   /* "}" in a comment */
}

#if BUILD_VERSION >= VERSION_J
void guToy(Mtx *m, float x)
{
    Matrix mf;
    guToyF(mf, x);
}
#endif
'''


@pytest.fixture
def tree(tmp_path, monkeypatch):
    folder = tmp_path / "ultralib"
    (folder / "src" / "gu").mkdir(parents=True)
    (folder / "include" / "PR").mkdir(parents=True)
    (folder / "src" / "gu" / "translate.c").write_text(TRANSLATE)
    monkeypatch.setattr(ultralib, "root", lambda: folder)
    ultralib.index.cache_clear()
    return folder


def test_one_function_of_a_file_with_its_directives_and_static_helpers(tree):
    index = ultralib.index(str(tree))
    assert set(index) == {"guToyF", "guToy"}  # (the static helper is the file's own)
    text = ultralib.extract(index["guToy"], "guToy")
    assert '#include "guint.h"' in text and "static float twice" in text and "#if BUILD_VERSION" in text
    # (guToyF is guToy's callee: only its declaration stays, no body)
    assert "void guToy(Mtx" in text and "void guToyF(float mf[4][4], float x);" in text
    assert "twice(x);   /*" not in text and text.rstrip().endswith("#endif")


def test_the_flags_are_those_of_the_build_the_game_matched(tree):
    rom = ultralib.flags(tree, tree / "src/gu/translate.c", "2.0I/libgultra_rom")
    assert "-DBUILD_VERSION=VERSION_I" in rom and "-DNDEBUG" in rom and "-D_FINALROM" in rom
    debug = ultralib.flags(tree, tree / "src/gu/translate.c", "2.0J/libgultra_d")
    assert "-D_DEBUG" in debug and "-D_FINALROM" not in debug and str(tree / "src" / "gu") in debug


def test_source_needs_ultralibs_own_build_and_ultralib_installed(tree, monkeypatch):
    monkeypatch.setattr(ultralib, "_builds", lambda: {"f" * 40: ["2.0K/libultra", "2.0I/libgultra_d", "2.0I/libgultra"]})
    hint = {"names": ["guToy"], "corpora": ["built in / decompals/ultralib e24c836796df"], "fingerprint": "f" * 40}
    assert ultralib.source("guToy", hint) == (tree / "src/gu/translate.c", "2.0I/libgultra")
    assert ultralib.source("guToy", {**hint, "corpora": ["built in / n_audio 2.0J-2.0L"]}) is None
    assert ultralib.source("guMissing", hint) is None
    assert ultralib.source("guToy", {**hint, "fingerprint": "0" * 40}) is None
    monkeypatch.setattr(ultralib, "root", lambda: None)
    assert ultralib.source("guToy", hint) is None


def test_native_library_code_is_set_aside_for_ultralibs_c(example, tree, monkeypatch):  # noqa: F811
    net, fid = example
    with net.project_store(1).tx() as db:  # the toy function, as library math the port compiles (gu*: "native")
        meta = json.loads(db.execute('SELECT meta FROM functions WHERE id=?', (fid,)).fetchone()['meta'])
        meta['naming'].update(name='guToy', source_name='guToy', fingerprint='f' * 40)
        meta['sdk_display']['name'] = 'guToy'
        db.execute('UPDATE functions SET meta=? WHERE id=?', (json.dumps(meta), fid))
    sdk_assembly.choose_c(net, 'game', True)
    monkeypatch.setattr(ultralib, "root", lambda: None)
    assert sdk_assembly.discover_c(net, net.project_row('game'), threading.Event()) == 0  # no ultralib: C work
    monkeypatch.setattr(ultralib, "root", lambda: tree)
    monkeypatch.setattr(ultralib, "source", lambda name, hint, fp=None: (tree / "src/gu/translate.c", "2.0I/libgultra"))
    assert sdk_assembly.discover_c(net, net.project_row('game'), threading.Event()) == 1
    assert sdk_assembly.ultralib_set_aside(net, net.project_row('game')) == {'osToyRead': ('src/gu/translate.c', '2.0I/libgultra')}


def test_units_are_ultralibs_c_and_need_it_installed(tree, monkeypatch):
    net = type("Net", (), {})()
    net.progress = type("P", (), {"view": lambda self: __import__("contextlib").nullcontext()})()
    net._project = lambda slug: {"id": 1, "slug": slug}
    monkeypatch.setattr(sdk_assembly, "ultralib_set_aside", lambda n, p: {"guToy": ("src/gu/translate.c", "2.0J/libgultra")})
    units = ultralib.units(net, "game")
    text, flags = units["guToy.c"]
    assert "void guToy(Mtx" in text and "guToyF(float mf[4][4], float x);" in text and "mf[3][0]" not in text
    assert "-DBUILD_VERSION=VERSION_J" in flags
    monkeypatch.setattr(ultralib, "root", lambda: None)
    with pytest.raises(NetworkError, match="install libultra source"):
        ultralib.units(net, "game")


def test_the_build_writes_units_beside_the_game_never_in_the_save(tmp_path):
    out = tmp_path / "port" / "wasm" / "build"
    (out / "ultralib").mkdir(parents=True)
    (out / "ultralib" / "gone.c").write_text("old")
    library = build._library(out, {"guToy.c": ("void guToy(void) {}", ["-DX"]), "guMine.c": ("x", [])},
                             {"guMine.c": Path("progress/src/guMine.c")}, lambda *a: None)
    assert library == {"guToy.c": ["-DX"]}  # the game's own matched C wins
    assert sorted(p.name for p in (out / "ultralib").iterdir()) == ["guToy.c"]


def test_ultralib_functions_a_unit_calls_are_built_with_it(tree):
    """A synthesizer function set aside as ultralib's own called alFilterNew and its neighbours, which nothing
    defined: the link stubbed them ("not implemented, skipped") and the audio thread never finished a frame, so it
    kept the CPU from the game's main thread. A callee nothing else provides is built with its caller's flags."""
    text = ultralib.extract(ultralib.index(str(tree))["guToy"], "guToy")
    units = {"guToy.c": (text, ["-DBUILD_VERSION=VERSION_J"])}
    out = ultralib.with_callees(units, tree, lambda name: False)
    assert set(out) == {"guToy.c", "guToyF.c"} and out["guToyF.c"][1] == ["-DBUILD_VERSION=VERSION_J"]
    assert "void guToyF(float" in out["guToyF.c"][0] and "void guToy(" not in out["guToyF.c"][0]
    # what the game, the host or the recompiler provides is left to them
    assert set(ultralib.with_callees(units, tree, lambda name: name == "guToyF")) == {"guToy.c"}


def test_a_callee_is_built_with_the_include_folder_of_its_own_file(tree):
    """72 callees of one audio-heavy game (`__freeParam`: `synthInternals.h` beside it) stopped at 'file not found':
    they were built with their caller's flags, whose last `-I` is the CALLER's folder. They were left to stubs
    ("not implemented, skipped") and the synthesizer's parameter free list never got a parameter back."""
    known = ultralib.index(str(tree))
    other = str(tree / "src" / "somewhere_else")
    caller_flags = ["-DBUILD_VERSION=VERSION_J", "-I", other]
    text = ultralib.extract(known["guToy"], "guToy")
    out = ultralib.with_callees({"guToy.c": (text, caller_flags)}, tree, lambda name: False)
    assert out["guToyF.c"][1] == ["-DBUILD_VERSION=VERSION_J", "-I", str(known["guToyF"].parent)]
    assert out["guToy.c"][1] == caller_flags  # the unit itself keeps its own
    # the game's own C naming it: the common flags of the units, with the callee's folder
    both = ultralib.game_callees({"guToy.c": (text, caller_flags)}, tree, lambda name: False, ["guToyF(1, 2);"])
    assert both["guToyF.c"][1] == ["-DBUILD_VERSION=VERSION_J", "-I", str(known["guToyF"].parent)]


def test_a_helper_declared_static_in_the_files_own_prototype_or_header_is_exported(tmp_path):
    """`_bnkfPatchBank` and `_sndpVoiceHandler` are declared static (in the file, or in a header beside it) and
    defined without it: the unit built for the function must export it, else its callers stay unresolved."""
    folder = tmp_path / "ultralib" / "src" / "audio"
    folder.mkdir(parents=True)
    (folder / "priv.h").write_text("static int _handler(void *node);\nstatic int _other(void);\n")
    path = folder / "player.c"
    path.write_text('#include "priv.h"\n#include <ultra64.h>\nstatic void _patch(int x);\n\n'
                    "int _handler(void *node)\n{\n    return 1;\n}\n\nvoid _patch(int x)\n{\n}\n")
    handler = ultralib.extract(path, "_handler")
    assert "\nint _handler(void *node);" in "\n" + handler and "static int _other(void);" in handler  # (only its own)
    assert '#include "priv.h"' not in handler and "#include <ultra64.h>" in handler
    assert "static void _patch" not in ultralib.extract(path, "_patch")  # the file's own prototype
    assert "static void _patch" in handler  # (another function's prototype stays)


def test_a_function_ultralib_defines_static_is_found_and_exported_when_one_file_has_it(tmp_path, monkeypatch):
    """`__handleMIDIMsg`, `read8` and `_handleEvent` are `static` in ultralib's sequence player: library detection named
    them, but only exported functions had a source, so they stayed open for agents and the deep permuter."""
    folder = tmp_path / "ultralib"
    audio = folder / "src" / "audio"
    audio.mkdir(parents=True)
    (folder / "include").mkdir()
    (audio / "seq.c").write_text('#include <ultra64.h>\nstatic int read8(int *p);\n\n'
                                 'static int helper(int x) { return x; }\n\n'
                                 'static int read8(int *p)\n{\n    return helper(*p);\n}\n\n'
                                 'int alSeqOther(int *p) { return read8(p); }\n')
    (audio / "a.c").write_text("static int shared(void) { return 1; }\n")
    (audio / "b.c").write_text("static int shared(void) { return 2; }\n")
    monkeypatch.setattr(ultralib, "root", lambda: folder)
    monkeypatch.setattr(ultralib, "_builds", lambda: {"f" * 40: ["2.0I/libgultra"]})
    ultralib.index.cache_clear()
    ultralib.static_index.cache_clear()
    assert "read8" not in ultralib.index(str(folder))  # (a callee is never built apart from its file's own copy)
    hint = {"corpora": ["built in / decompals/ultralib e24c836796df"], "fingerprint": "f" * 40}
    assert ultralib.source("read8", hint) == (audio / "seq.c", "2.0I/libgultra")
    assert ultralib.source("shared", hint) is None  # two files define it: ambiguous
    text = ultralib.extract(audio / "seq.c", "read8")
    assert "\nint read8(int *p)\n{" in text and "static int read8" not in text  # exported, prototype and definition
    assert "static int helper" in text and "alSeqOther" not in text.replace("int alSeqOther(int *p);", "")


def test_static_twins_with_one_body_resolve_and_different_ones_do_not(tmp_path, monkeypatch):
    """env.c and n_env.c both define `_getRate`: the same helper, whichever audio library a game links. csplayer.c's
    `_getVol` is another function with the same name: a unit can't tell which a game's copy is."""
    folder = tmp_path / "ultralib"
    audio = folder / "src" / "audio"
    audio.mkdir(parents=True)
    (folder / "include").mkdir()
    (audio / "env.c").write_text("static int _getRate(int x) { return x * 2; }\nstatic int _getVol(int x) { return x; }\n")
    (audio / "n_env.c").write_text("/* n_ copy */\nstatic int _getRate(int x)\n{\n    return x * 2; /* same */\n}\n")
    (audio / "csplayer.c").write_text("static int _getVol(int x) { return x + 1; }\n")
    ultralib.static_index.cache_clear()
    index = ultralib.static_index(str(folder))
    assert index["_getRate"] in (audio / "env.c", audio / "n_env.c") and "_getVol" not in index


def test_a_symbol_a_define_gives_a_function_has_its_source_and_f3dex2_flags(tmp_path, monkeypatch):
    """`fsin` is `__sinf` (a #define in sinf.c), `spInit` is `spX2Init` for F3DEX2 (sp.h): the game's symbol is not the
    name the C defines, so the lookup follows the object-like macros, and the F3DEX2 names are built with
    F3DEX_GBI_2 as spriteex2.c does."""
    folder = tmp_path / "ultralib"
    (folder / "src" / "gu").mkdir(parents=True)
    (folder / "src" / "sp").mkdir()
    (folder / "include" / "PR").mkdir(parents=True)
    (folder / "include" / "PR" / "sp.h").write_text("#if defined(F3DEX_GBI_2)\n#define\tspInit\t\tspX2Init\n#endif\n")
    (folder / "src" / "sp" / "sprite.c").write_text('#include <PR/sp.h>\nvoid spInit(int *g) { *g = 1; }\nvoid spOther(void) { }\n')
    (folder / "src" / "gu" / "sinf.c").write_text("#define fsin __sinf\nstatic const int P[] = {1};\nfloat fsin(float x) { return x; }\n")
    for cached in (ultralib.index, ultralib.static_index, ultralib.macros, ultralib.alias_index):
        cached.cache_clear()
    monkeypatch.setattr(ultralib, "root", lambda: folder)
    monkeypatch.setattr(ultralib, "_builds", lambda: {"f" * 40: ["2.0I/libgultra"]})
    hint = {"corpora": ["built in / decompals/ultralib e24c836796df"], "fingerprint": "f" * 40}
    assert ultralib.source("__sinf", hint) == (folder / "src/gu/sinf.c", "2.0I/libgultra")
    assert ultralib.source("spX2Init", hint)[0] == folder / "src/sp/sprite.c"
    text = ultralib.extract(folder / "src/sp/sprite.c", "spX2Init")
    assert "void spInit(int *g)" in text and "spOther(void) { }" not in text  # (the other function stays out)
    assert "-DF3DEX_GBI_2" in ultralib.flags(folder, folder / "src/sp/sprite.c", "2.0I/libgultra", "spX2Init")
    assert "-DF3DEX_GBI_2" not in ultralib.flags(folder, folder / "src/sp/sprite.c", "2.0I/libgultra", "spXInit")


def test_the_build_adds_callees_nothing_else_provides(tree, tmp_path):
    text = ultralib.extract(ultralib.index(str(tree))["guToy"], "guToy")
    units = {"guToy.c": (text, ["-DX"])}
    out = tmp_path / "port" / "wasm" / "build"
    library = build._library(out, units, {}, lambda *a: None, lambda name: False)
    assert sorted(library) == ["guToy.c", "guToyF.c"] and library["guToyF.c"] == ["-DX"]
    # the game's own C for the callee wins, as it does for a unit
    library = build._library(out, units, {"guToyF.c": Path("progress/src/guToyF.c")}, lambda *a: None, lambda name: True)
    assert sorted(library) == ["guToy.c"]


def test_the_link_report_names_ultralib_apart_from_the_games_assembly(monkeypatch):
    class Kit:
        @staticmethod
        def sdk_group(name):
            return "native" if name.startswith("gu") else None
    facts = {"replaced": set(), "glue": set(), "ultralib": {"guToy"}, "kit": Kit(),
             "status": {"guToy": "asm", "func_80001000": "asm"}}
    monkeypatch.setattr(pc_link, "_facts", lambda net, proj: facts)
    assert pc_link._group(None, None, "guToy") == "ultralib"
    assert pc_link._group(None, None, "func_80001000") == "assembly"


def test_a_game_with_no_set_aside_function_still_gets_the_callees_its_code_names_through_a_header_macro(tree):
    """A game's C says `alHeapAlloc(...)`, a macro in libaudio.h for `alHeapDBAlloc(...)`: the compiled code imports
    `alHeapDBAlloc`, a name no source text held, and with no function set aside there was no known build either, so
    nothing was built for it ("alHeapDBAlloc: not implemented, skipped" and the game went on with a null pointer).
    Header macros are followed, and the library's default build is used when there is no unit to take one from."""
    (tree / "include" / "PR" / "alias.h").write_text("#define guToyAlias(m, a) guToyF(m, a)\n#define NOT_A_CALL 3\n")
    ultralib.macro_targets.cache_clear()
    assert ultralib.macro_targets(str(tree)) == {"guToyAlias": "guToyF"}
    out = ultralib.game_callees({}, tree, lambda name: False, ["void g(void) { guToyAlias(m, 1); }"])
    assert set(out) == {"guToyF.c"} and "-DBUILD_VERSION=VERSION_L" in out["guToyF.c"][1]
    # a unit's own macro use is followed too
    units = {"x.c": ("void x(void) { guToyAlias(m, 2); }", ["-DX"])}
    assert set(ultralib.with_callees(units, tree, lambda name: False)) == {"x.c", "guToyF.c"}
    # the game's own C or the host providing it leaves it to them
    assert ultralib.game_callees({}, tree, lambda name: True, ["guToyAlias(m, 1);"]) == {}


def test_ultralib_is_a_pinned_proprietary_pack_at_the_tables_commit():
    pack = toolchains.get("ultralib-source")
    assert pack.proprietary  # installed only when the user accepts it in Compilers & tools
    import importlib.util
    spec = importlib.util.spec_from_file_location("ultralib_signatures", Path(__file__).resolve().parent.parent
                                                  / "tools" / "ultralib_signatures.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    assert tool.COMMIT in pack.parts[0]["url"] and pack.parts[0]["sha256"]  # the C of the build the table hashes


def test_ultralib_functions_the_games_own_c_calls_are_built_too(tree):
    """A game's own alInit called alSynNew, which no unit calls and the project had not set aside: nothing defined it,
    the link stubbed it ("not implemented, skipped") and the audio never started. A name the game's C uses that
    ultralib defines and nothing provides is built with the flags most units use; with no unit there is no known
    build, so the library's default one is used (a game that set nothing aside still got no `alHeapDBAlloc`)."""
    text = ultralib.extract(ultralib.index(str(tree))["guToy"], "guToy")
    units = {"guToy.c": (text, ["-DBUILD_VERSION=VERSION_J"])}
    game = ["void game_init(void) { guToyF(0, 0); }", "int unrelated(void) { return guNotInUltralib(1); }"]
    out = ultralib.game_callees(units, tree, lambda name: False, game)
    assert set(out) == {"guToy.c", "guToyF.c"} and out["guToyF.c"][1] == ["-DBUILD_VERSION=VERSION_J"]
    assert ultralib.game_callees(units, tree, lambda name: name == "guToyF", game) == units  # provided elsewhere
    none = ultralib.game_callees({}, tree, lambda name: False, game)  # no unit: the default build
    assert set(none) == {"guToyF.c"} and "-DBUILD_VERSION=VERSION_L" in none["guToyF.c"][1]


def test_a_function_taken_out_of_a_unit_leaves_its_prototype_for_the_calls_that_stay(tmp_path):
    """env.c defines `_ldexpf` above `_getRate` and no header declares it: with the definition gone from the unit the
    call was an implicit `int` one, and `_getRate`'s loop (`while (i_invn) i_invn >>= 1`) never ended."""
    path = tmp_path / "toy.c"
    path.write_text("""#include "toy.h"
f64
_ldexpf(f64 in, s32 ex)
{
    return in * (1 << ex);
}

s32 kandr(a, b)
s32 a; s32 b;
{
    return a + b;
}

void alToyPull(void)
{
    f64 x = _ldexpf(0.5, 3);
}
""")
    text = ultralib.extract(path, "alToyPull")
    assert "_ldexpf(f64 in, s32 ex);" in text and "in * (1 << ex)" not in text
    assert "alToyPull" in text and "_ldexpf(0.5, 3)" in text
    assert ultralib.prototype("f64\n_ldexpf(f64 in, s32 ex)\n{ return in; }") == "f64\n_ldexpf(f64 in, s32 ex);\n"
    assert ultralib.prototype("s32 kandr(a, b)\ns32 a; s32 b;\n{ return a; }") == "s32 kandr();\n"
