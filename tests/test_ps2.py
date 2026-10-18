"""PlayStation 2 ROM projects: split a homemade PS2 executable and prove the matcher on it.

Demo Quest PS2 (projects/demo-ps2) is the Demo Quest game built for the PlayStation 2 with EE-GCC 2.96 at -O2 -G8,
little-endian, small globals $gp-relative, like games of its time. The executable carries no symbols, so the test
does what an agent does: give addresses names, declare the game's types, then write each function. Every one of
them must match byte for byte.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest
from elftools.elf.elffile import ELFFile

from decompnet import toolchains
from decompnet.app import tools
from decompnet.app.engine import RomNetwork
from decompnet.forks import csource
from decompnet.network import NetworkError

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "projects" / "demo-ps2"
EXE = DEMO / "build" / "SLUS_999.99"
ELF = DEMO / "build" / "demoq_ps2.elf"
GAME = ROOT / "projects" / "demo-n64" / "original"
FUNCTIONS = ["clamp_s32", "abs_s32", "player_take_damage", "rng_next", "count_alive_entities", "entity_update_physics",
             "entity_default_hp", "entity_spawn", "add_score", "lerp", "checksum_save", "entity_collides", "game_tick"]

pytestmark = pytest.mark.skipif(toolchains.installed("ee-gcc-2.96") is None or toolchains.installed("binutils-mips") is None,
                                reason="needs ee-gcc-2.96 and binutils-mips to build the PS2 demo")


@pytest.fixture(scope="module")
def ps2(tmp_path_factory):
    if not EXE.exists():
        subprocess.run([sys.executable, str(DEMO / "build.py")], check=True)
    work = tmp_path_factory.mktemp("ps2")
    net = RomNetwork(work / "app.db", work)
    slug = net.import_rom(EXE, confirmed=True, log=lambda *_: None)
    return net, slug


def test_import(ps2):
    net, slug = ps2
    ov = net.overview(slug)
    assert ov["platform"] == "ps2" and ov["compiler"]["toolchain"] == "ee-gcc-2.96" and ov["functions"] > 60
    assert ov["compiler"]["flags"] == ["-O2", "-G8"]
    assert "WITHOUT" in net.get_context(slug)  # the gp-relative globals advice


def test_read_data_reads_a_ps2_games_pointers(ps2):
    """read_data took only N64 addresses (0x80...) for pointers, never a PS2 game's: zlib's table of error
    messages is ten pointers to its strings."""
    net, slug = ps2
    table = next(s["st_value"] for s in ELFFile(open(ELF, "rb")).get_section_by_name(".symtab").iter_symbols()
                 if s.name == "z_errmsg")
    said = net.read_data(slug, f"0x{table:08X}", 40)
    assert "words that look like addresses" in said, said


def test_a_removed_game_can_be_imported_again(tmp_path):
    """Issue #18: removing a project left its symbols behind, and importing the game again (the new project gets
    the same id) failed on them. A database that has such leftovers loses them when decompdecomp opens it."""
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    slug = net.import_rom(EXE, confirmed=True, log=lambda *_: None)
    assert net.store.one("SELECT COUNT(*) AS n FROM symbols")["n"] > 60
    net.remove_project(slug)
    assert net.store.one("SELECT COUNT(*) AS n FROM symbols")["n"] == 0
    slug = net.import_rom(EXE, confirmed=True, log=lambda *_: None)
    pid = net.project_row(slug)["id"]
    net.remove_project(slug)
    with net.store.tx() as db:  # what 0.10.2 left behind
        db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)", (pid, "main", 0x100000, 0, "func"))
    again = RomNetwork(tmp_path / "app.db", tmp_path)
    assert again.store.one("SELECT COUNT(*) AS n FROM symbols")["n"] == 0
    again.import_rom(EXE, confirmed=True, log=lambda *_: None)


def test_every_game_function_matches(ps2):
    net, slug = ps2
    _match_every_function(net, slug, ELF)


def _match_every_function(net, slug, elf):
    agent = _name_the_game(net, slug, elf)
    _attempt_every_function(net, slug, agent)


def _name_the_game(net, slug, elf):
    """What an agent does first: the game's types into the shared header, and the game's names on its addresses."""
    agent, _ = net.local_agent("ps2-agent")
    syms = {s.name: s["st_value"] for s in ELFFile(open(elf, "rb")).get_section_by_name(".symtab").iter_symbols()}
    header = (GAME / "game.h").read_text()
    header = "\n".join(l for l in header.splitlines()
                       if not l.startswith("#include") and not re.match(r"typedef \w+( \w+)? [su]\d+;|typedef \w+ f\d+;", l))
    header = re.sub(r"^extern (s32|u32) (g_\w+);", r"\1 \2;", header, flags=re.M)  # small globals: no extern
    assert net.edit_context(agent, slug, "", header, "the game's types").startswith("✅")
    table = net.symbols(net.project_row(slug)["id"])
    for name in FUNCTIONS + ["g_player", "g_entities", "g_score", "g_frame_count", "g_rng_state",
                             "__muldi3", "__udivdi3", "__umoddi3", "__divdi3", "__moddi3"]:
        if name in syms:
            current, offset = table.name_at(syms[name])
            if offset:  # inside a bigger data symbol (adjacent globals): name the address itself
                current = f"D_{syms[name]:08X}"
            if current != name:
                net.rename_symbol(agent, slug, current, name)
                table = net.symbols(net.project_row(slug)["id"])
    return agent


def _attempt_every_function(net, slug, agent):
    source = (GAME / "game.c").read_text()
    scores = {}
    for name in FUNCTIONS:
        res = net.attempt(agent, name, csource.get(source, name), slug)
        scores[name] = res["score"]
        if not res["matched"]:
            print(name, res["text"][:3000])
            net.release(agent, name, "", slug)
    assert all(score == 0 for score in scores.values()), scores


def test_wrong_code_does_not_match(ps2):
    net, slug = ps2
    agent, _ = net.local_agent("ps2-agent-2")
    fn = net.store.one("SELECT name FROM functions WHERE status='open' AND n_insns > 8 ORDER BY n_insns LIMIT 1")
    res = net.attempt(agent, fn["name"], f"int {fn['name']}(int a) {{ return a * 3; }}", slug)
    assert not res["matched"]
    net.release(agent, fn["name"], "", slug)


OTHER_COMPILERS = ["ee-gcc-2.9-991111", "ee-gcc-2.95.2", "ee-gcc-2.95.3", "ee-gcc-3.2", "mwcps2-2.3.3", "mwcps2-3.0.1"]


@pytest.mark.parametrize("toolchain", OTHER_COMPILERS)
def test_games_built_with_other_compilers_match(toolchain, tmp_path):
    """PS2 games were built with many compilers: Sony's EE-GCC releases, SN Systems' (ProDG) and Metrowerks'
    CodeWarrior. The demo built with each must import with it and match function by function."""
    if toolchains.installed(toolchain) is None or toolchains.installed("wibo") is None:
        pytest.skip(f"needs {toolchain} (and wibo)")
    build = DEMO / "build" / toolchain
    if not (build / "SLUS_999.99").exists():
        subprocess.run([sys.executable, str(DEMO / "build.py"), toolchain], check=True)
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    slug = net.import_rom(build / "SLUS_999.99", confirmed=True, log=lambda *_: None, toolchain=toolchain)
    ov = net.overview(slug)
    assert ov["compiler"]["toolchain"] == toolchain and ov["functions"] > 60
    if toolchain.startswith("mwcps2"):
        assert ov["compiler"]["flags"] == ["-O2", "-sdatathreshold", "8"]
        assert ov["rom"]["compiler_guess"].startswith("MWCPS2")  # CodeWarrior's .mwcats sections give it away
    _match_every_function(net, slug, build / "demoq_ps2.elf")


@pytest.mark.parametrize("toolchain", ["ee-gcc-2.96", *OTHER_COMPILERS])
def test_the_import_finds_the_compiler(toolchain, tmp_path):
    """Not told which compiler built it, the import works it out: CodeWarrior and its version, and SN's 2.95.2, from
    the code's style; the other GCCs by compiling drafts of small functions with each."""
    if toolchains.installed(toolchain) is None or toolchains.installed("m2c") is None:
        pytest.skip(f"needs {toolchain} and m2c")
    build = DEMO / "build" if toolchain == "ee-gcc-2.96" else DEMO / "build" / toolchain
    if not (build / "SLUS_999.99").exists():
        subprocess.run([sys.executable, str(DEMO / "build.py"), toolchain], check=True)
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    slug = net.import_rom(build / "SLUS_999.99", confirmed=True, log=lambda *_: None)
    assert net.overview(slug)["compiler"]["toolchain"] == toolchain


def test_a_librarys_note_does_not_name_the_compiler(monkeypatch):
    """GTA San Andreas keeps one "MW MIPS C Compiler" note, from a library: its own code is EE-GCC 2.96's, and
    agents could match nothing until the compiler was switched. The code's style outweighs the note."""
    from decompnet.consoles.ps2 import elf as ps2

    if not EXE.exists():
        pytest.skip("needs the PS2 demo built")
    monkeypatch.setattr(ps2, "_sections", lambda data: {".comment": b"MW MIPS C Compiler (2.4.1.01)\0PlayStation2\0"})
    assert ps2.guess_compiler(EXE.read_bytes()) == "EEGCC"


def test_agents_suggest_a_compiler_and_the_user_applies_it(tmp_path):
    """GTA San Andreas: agents worked out the compiler was wrong (one said EE-GCC 2.96, another 2.95.3). They suggest
    it; each suggestion naming a function is checked with that function's C before the user sees it, agreement is
    counted, and applying it switches the project."""
    tc = "ee-gcc-2.95.3"
    if any(toolchains.installed(t) is None for t in (tc, "ee-gcc-2.96", "wibo")):
        pytest.skip(f"needs {tc}, ee-gcc-2.96 and wibo")
    build = DEMO / "build" / tc
    if not (build / "SLUS_999.99").exists():
        subprocess.run([sys.executable, str(DEMO / "build.py"), tc], check=True)
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    slug = net.import_rom(build / "SLUS_999.99", confirmed=True, log=lambda *_: None, toolchain="ee-gcc-3.2")
    addr = {s.name: s["st_value"] for s in ELFFile(open(build / "demoq_ps2.elf", "rb")).get_section_by_name(".symtab")
            .iter_symbols()}["entity_default_hp"]
    name = net.symbols(net.project_row(slug)["id"]).name_at(addr)[0]
    header = (GAME / "game.h").read_text()
    enums = "\n".join(l for l in header.splitlines() if l.startswith(("enum ", "#define ENTITY")))
    source = enums + "\n" + csource.get((GAME / "game.c").read_text(), "entity_default_hp").replace(
        "entity_default_hp(", f"{name}(", 1)
    a, _ = net.local_agent("agent-a")
    assert not net.attempt(a, name, source, slug)["matched"]  # right C, wrong compiler
    net.release(a, name, "", slug)

    assert "not a match" in net.try_compiler(a, slug, name, "ee-gcc-2.96")  # testing records nothing
    assert "MATCHES" in net.try_compiler(a, slug, name, tc)
    assert net.overview(slug)["suggestions"] == []
    with pytest.raises(NetworkError):  # a suggestion its own check disproves isn't filed
        net.suggest(a, slug, "daddu moves: some EE-GCC", "ee-gcc-2.96", function=name)
    b, _ = net.local_agent("agent-b")
    assert "matches the game" in net.suggest(b, slug, "sd $ra, filled delay slots: SN's 2.95.3", tc, function=name)
    c, _ = net.local_agent("agent-c")
    net.suggest(c, slug, "agreed", tc)
    with pytest.raises(NetworkError):
        net.suggest(c, slug, "?", "gcc-9000")
    shown = net.overview(slug)["suggestions"]
    right = next(s for s in shown if s["toolchain"] == tc)
    assert right["agents"] == ["agent-b", "agent-c"] and "matches the game" in right["check"] and len(shown) == 1

    net.resolve_suggestion(slug, right["id"], apply=True)
    ov = net.overview(slug)
    assert ov["compiler"]["toolchain"] == tc and ov["compiler"]["flags"] == ["-O2", "-G8"]
    assert ov["suggestions"] == []
    assert net.attempt(a, name, source, slug)["matched"]


def test_auto_adjust_applies_a_proven_compiler(tmp_path):
    """Settings: auto-adjust the compiler. A suggestion proven by a function's C applies at once; others wait for
    agreement (three agents) or the user."""
    tc = "ee-gcc-2.95.3"
    if any(toolchains.installed(t) is None for t in (tc, "ee-gcc-3.2", "wibo")):
        pytest.skip(f"needs {tc}, ee-gcc-3.2 and wibo")
    build = DEMO / "build" / tc
    if not (build / "SLUS_999.99").exists():
        subprocess.run([sys.executable, str(DEMO / "build.py"), tc], check=True)
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    net.auto_compiler = True
    slug = net.import_rom(build / "SLUS_999.99", confirmed=True, log=lambda *_: None, toolchain="ee-gcc-3.2")
    agents_ = [net.local_agent(f"agent-{k}")[0] for k in range(3)]
    for k, a in enumerate(agents_[:2]):  # agreement without proof: two isn't enough
        assert net.suggest(a, slug, "looks like 2.9", "ee-gcc-2.9-991111").startswith("Suggested")
    assert net.overview(slug)["compiler"]["toolchain"] == "ee-gcc-3.2"
    assert "Open suggestions" in tools.project_info(net, slug)
    assert net.suggest(agents_[2], slug, "me too", "ee-gcc-2.9-991111").startswith("Applied")
    assert net.overview(slug)["compiler"]["toolchain"] == "ee-gcc-2.9-991111"
    with pytest.raises(NetworkError):
        net.suggest(agents_[0], slug, "again", "ee-gcc-2.9-991111")  # already the project's


def test_auto_adjust_never_breaks_matched_code(tmp_path):
    """Unattended, three agents agreeing on the wrong compiler for the whole game: matched functions would stop
    matching, so it isn't applied; the suggestion waits for the user."""
    tc = "ee-gcc-2.95.3"
    if any(toolchains.installed(t) is None for t in (tc, "ee-gcc-3.2", "wibo")):
        pytest.skip(f"needs {tc}, ee-gcc-3.2 and wibo")
    build = DEMO / "build" / tc
    if not (build / "SLUS_999.99").exists():
        subprocess.run([sys.executable, str(DEMO / "build.py"), tc], check=True)
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    net.auto_compiler = True
    slug = net.import_rom(build / "SLUS_999.99", confirmed=True, log=lambda *_: None, toolchain=tc)
    agent = _name_the_game(net, slug, build / "demoq_ps2.elf")
    _attempt_every_function(net, slug, agent)
    said = [net.suggest(net.local_agent(f"voter-{k}")[0], slug, "looks like 3.2", "ee-gcc-3.2") for k in range(3)]
    assert said[-1].startswith("Suggested, not applied by itself") and "wouldn't match" in said[-1]
    ov = net.overview(slug)
    assert ov["compiler"]["toolchain"] == tc and ov["suggestions"][0]["toolchain"] == "ee-gcc-3.2"


def test_a_game_built_with_two_compilers(tmp_path):
    """GTA San Andreas: EE-GCC 2.95.3 for most of it, CodeWarrior 3.0.1 -O3 for some address ranges. The demo built the
    same way (its game code with CodeWarrior): an agent suggests CodeWarrior for that range, naming a function whose C
    proves it; with auto-adjust on, the range is built with it from then on, and both compilers' code matches."""
    if any(toolchains.installed(t) is None for t in ("mwcps2-3.0.1", "ee-gcc-2.96", "wibo")):
        pytest.skip("needs CodeWarrior for PS2 3.0.1 (proprietary), EE-GCC 2.96 and wibo")
    build = DEMO / "build" / "mixed"
    if not (build / "SLUS_999.99").exists():
        subprocess.run([sys.executable, str(DEMO / "build.py"), "mixed"], check=True)
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    net.auto_compiler = True
    slug = net.import_rom(build / "SLUS_999.99", confirmed=True, log=lambda *_: None, toolchain="ee-gcc-2.96")
    agent = _name_the_game(net, slug, build / "demoq_ps2.elf")
    syms = {s.name: (s["st_value"], s["st_size"]) for s in ELFFile(open(build / "demoq_ps2.elf", "rb"))
            .get_section_by_name(".symtab").iter_symbols() if s.name in FUNCTIONS}
    start, end = min(a for a, _ in syms.values()), max(a + n for a, n in syms.values())
    source = (GAME / "game.c").read_text()
    for name in FUNCTIONS:  # agents' first tries, with EE-GCC: none of the game's code matches
        assert not net.attempt(agent, name, csource.get(source, name), slug)["matched"]
        net.release(agent, name, "", slug)

    # the suggestion is a hypothesis: its neighbours' C (agents' tries) matches with CodeWarrior and not with EE-GCC,
    # which confirms it, and with auto-adjust on the range applies
    net.rangechecks.background = False
    said = net.suggest(agent, slug, "game.c's code: frame like CodeWarrior's, delay slots filled", "mwcps2-3.0.1",
                       "-O3 -sdatathreshold 8", "entity_default_hp", f"0x{start:X}-0x{end:X}")
    assert "matches the game" in said and "Range check #1" in said
    check = net.cfg(net.project_row(slug))["range_checks"][0]
    assert check["status"] == "confirmed" and "(applied)" in check["said"]
    ov = net.overview(slug)
    assert ov["compiler"]["toolchain"] == "ee-gcc-2.96"  # the rest of the game keeps its compiler
    assert [(r["start"], r["end"], r["toolchain"]) for r in ov["compiler_ranges"]] == [(start, end, "mwcps2-3.0.1")]
    assert "this address range's compiler" in net.claim(agent, slug, "abs_s32")
    net.release(agent, "abs_s32", "", slug)
    _attempt_every_function(net, slug, agent)  # every game function, now built with CodeWarrior
    zlib = net.store.one("SELECT name FROM functions WHERE project_id=? AND vram>=? ORDER BY vram LIMIT 1",
                         net.project_row(slug)["id"], end)["name"]
    assert "this address range" not in net.claim(agent, slug, zlib)  # past the range: EE-GCC as before

    if toolchains.installed("binutils-mips") is not None:
        out = net.export_rom(slug, log=lambda *_: None)  # each file built by its own range's compiler
        assert out["identical"] and out["matched"] == len(FUNCTIONS), out["log"]

    with pytest.raises(NetworkError):  # matched code in a range never stops matching: that compiler is refused
        net.set_compiler_ranges(slug, [[start, end]], "ee-gcc-3.2")
    assert net.overview(slug)["compiler_ranges"][0]["toolchain"] == "mwcps2-3.0.1"

    # removing the range would leave its matched functions unmatched: refused, unless the user reopens them (a range
    # judged a quirk): then they're open again, with their C as their best attempt for the next agent
    with pytest.raises(NetworkError, match="reopen those functions"):
        net.set_compiler_ranges(slug, [[start, end]], None)
    net.set_compiler_ranges(slug, [[start, end]], None, reopen=True)
    ov = net.overview(slug)
    assert ov["compiler_ranges"] == []
    row = net.store.one("SELECT status, best_source, best_score FROM functions WHERE name='entity_default_hp'")
    assert row["status"] == "open" and "entity_default_hp" in row["best_source"] and row["best_score"]


@pytest.mark.parametrize("toolchain", ["ee-gcc-2.96", "ee-gcc-3.2"])
def test_a_function_written_as_a_cpp_method_matches(toolchain, tmp_path):
    """GTA San Andreas is C++ (2,493 of its indirect calls are virtual calls): a function can be written as a class's
    method. C that compiles stays C; the rest compiles as C++, and GCC's names (2.9x: clamp_s32__4Mathiii, 3.2:
    _ZN4Math9clamp_s32Eiii) are given back to the game's before it's judged."""
    if toolchains.installed(toolchain) is None:
        pytest.skip(f"needs {toolchain}")
    build = DEMO / "build" if toolchain == "ee-gcc-2.96" else DEMO / "build" / toolchain
    if not (build / "SLUS_999.99").exists():
        subprocess.run([sys.executable, str(DEMO / "build.py"), toolchain], check=True)
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    slug = net.import_rom(build / "SLUS_999.99", confirmed=True, log=lambda *_: None, toolchain=toolchain)
    agent = _name_the_game(net, slug, build / "demoq_ps2.elf")
    method = ("struct Math { static s32 clamp_s32(s32 v, s32 lo, s32 hi); };\n"
              "s32 Math::clamp_s32(s32 v, s32 lo, s32 hi) {\n    if (v < lo) {\n        return lo;\n    }\n"
              "    if (v > hi) {\n        return hi;\n    }\n    return v;\n}\n")
    res = net.attempt(agent, "clamp_s32", method, slug)
    assert res["matched"], res["text"]
    # a call to another of the game's functions, from C++, resolves to it too
    caller = ("struct Score { static void add_score(s32 points); };\n"
              + csource.get((GAME / "game.c").read_text(), "add_score").replace("void add_score(", "void Score::add_score(", 1))
    res = net.attempt(agent, "add_score", caller, slug)
    assert res["matched"], res["text"]
    if toolchains.installed("binutils-mips") is not None:
        out = net.export_rom(slug, log=lambda *_: None)  # the export builds them as .cpp, to the same bytes
        assert out["identical"], out["log"]
        assert (Path(out["path"]) / "src" / "cod" / "clamp_s32.cpp").exists(), out.get("fallback")
