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
    assert "void guToy(Mtx" in text and "void guToyF" not in text and text.rstrip().endswith("#endif")


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
    assert "void guToy(Mtx" in text and "guToyF(float" not in text and "-DBUILD_VERSION=VERSION_J" in flags
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
    build and nothing is added."""
    text = ultralib.extract(ultralib.index(str(tree))["guToy"], "guToy")
    units = {"guToy.c": (text, ["-DBUILD_VERSION=VERSION_J"])}
    game = ["void game_init(void) { guToyF(0, 0); }", "int unrelated(void) { return guNotInUltralib(1); }"]
    out = ultralib.game_callees(units, tree, lambda name: False, game)
    assert set(out) == {"guToy.c", "guToyF.c"} and out["guToyF.c"][1] == ["-DBUILD_VERSION=VERSION_J"]
    assert ultralib.game_callees(units, tree, lambda name: name == "guToyF", game) == units  # provided elsewhere
    assert ultralib.game_callees({}, tree, lambda name: False, game) == {}  # no unit: no known build
