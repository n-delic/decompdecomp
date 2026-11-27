"""Fork mode: continue a public decomp inside its own source tree, judged by objdiff.

projects/demo-fork is a small project in the shape community decomps use (objdiff.json, ninja build, partly
decompiled src/), for Demo Quest 64. The test clones it like a repository from decomp.dev, adds the game,
builds it and has an agent work through the guarded tools.
"""

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from decompnet import toolchains
from decompnet.app.engine import RomNetwork
from decompnet.forks import csource
from decompnet.forks.project import ForkProject
from decompnet.network import NetworkError

ROOT = Path(__file__).resolve().parent.parent


FORK_SRC = ROOT / "projects" / "demo-fork"


ROM = ROOT / "src" / "decompnet" / "app" / "static" / "demo" / "demo-quest-64.z64"


REFERENCE = (FORK_SRC / "tools" / "reference" / "game.c").read_text()


needs_tools = pytest.mark.skipif(
    toolchains.installed("gcc-2.7.2-kmc") is None or toolchains.installed("objdiff-cli") is None
    or not shutil.which("git"), reason="needs gcc-2.7.2-kmc, objdiff-cli and git")


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                               "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin", "HOME": str(cwd)}).stdout


def test_splat_setup_reports_the_expected_hash_or_its_absence(tmp_path):
    (tmp_path / "Makefile").write_text("all:\n\t@true\n")
    config = tmp_path / "game.yaml"
    expected = "A" * 40
    config.write_text(f"options:\n  target_path: baserom.z64\n  sha1: '{expected}'\nsegments: []\n")
    need = ForkProject(tmp_path).needs()
    assert need["sha1"] == expected.lower()
    assert need["path"] == "baserom.z64"
    config.write_text("options:\n  target_path: baserom.z64\nsegments: []\n")
    need = ForkProject(tmp_path).needs()
    assert need["sha1"] is None
    assert "does not specify a SHA-1" in need["help"]


def test_splat_generated_target_uses_original_rom_versions(tmp_path, monkeypatch):
    from decompnet.app.forkengine import ForkEngine

    us, pal = b"original us rom", b"original pal rom"
    hashes = {"us.v10": hashlib.sha1(us).hexdigest(), "pal": hashlib.sha1(pal).hexdigest()}
    (tmp_path / "Makefile").write_text("VERSION ?= us.v10\nBASEROM := baserom.$(VERSION).z64\n"
                                       "DECOMPRESSED_BASEROM := decompressed.$(VERSION).z64\n")
    (tmp_path / "README.md").write_text("\n".join(f"`baserom.{v}.z64`: `{sha1}`" for v, sha1 in hashes.items()))
    (tmp_path / "decompressed.pal.yaml").write_text("options:\n  platform: n64\n  target_path: decompressed.pal.z64\n"
                                                      f"  sha1: {'e' * 40}\nsegments: []\n")
    (tmp_path / "decompressed.us.v10.yaml").write_text("options:\n  platform: n64\n  target_path: decompressed.us.v10.z64\n"
                                                         f"  sha1: {'f' * 40}\nsegments: []\n")
    fp = ForkProject(tmp_path)
    assert fp.needs()["sha1"] == hashes["us.v10"]
    assert fp.needs("pal")["sha1"] == hashes["pal"]
    assert fp.needs("pal")["path"] == "baserom.pal.z64"
    assert fp.setup_commands("pal") == [["make", "VERSION=pal", "-j" + str(__import__("os").cpu_count() or 4)]]
    game = tmp_path / "my-copy.z64"
    game.write_bytes(pal)
    cfg = {}
    engine = object.__new__(ForkEngine)
    monkeypatch.setattr(ForkEngine, "_save_cfg", lambda _self, _proj, _cfg: None)
    engine._match_version(SimpleNamespace(), fp, cfg, game, log=lambda *_: None)
    assert cfg["version"] == "pal"
    assert fp.place_game(game, game.name, cfg["version"]) == tmp_path / "baserom.pal.z64"
    game.write_bytes(b"")
    with pytest.raises(NetworkError, match=r"empty \(0 bytes\)"):
        engine._match_version(SimpleNamespace(), fp, cfg, game, log=lambda *_: None)
    game.write_bytes(b"wrong edition")
    with pytest.raises(NetworkError, match="supported by this project") as error:
        engine._match_version(SimpleNamespace(), fp, cfg, game, log=lambda *_: None)
    assert hashlib.sha1(game.read_bytes()).hexdigest() in str(error.value)
    assert "Big-endian conversion: not attempted" in str(error.value)


@pytest.mark.parametrize("fmt", ["v64", "n64"])
def test_splat_accepts_other_n64_byte_orders(tmp_path, monkeypatch, fmt):
    from decompnet.app.forkengine import ForkEngine

    original = b"\x80\x37\x12\x40" + bytes(range(64))
    digest = hashlib.sha1(original).hexdigest()
    (tmp_path / "Makefile").write_text("VERSION ?= pal\nBASEROM := baserom.$(VERSION).z64\n"
                                       "DECOMPRESSED_BASEROM := decompressed.$(VERSION).z64\n")
    (tmp_path / "README.md").write_text(f"`baserom.pal.z64`: `{digest}`\n")
    (tmp_path / "decompressed.pal.yaml").write_text("options:\n  platform: n64\n  target_path: decompressed.pal.z64\n"
                                                      f"  sha1: {'e' * 40}\nsegments: []\n")
    width = 2 if fmt == "v64" else 4
    game = tmp_path / f"my-copy.{fmt}"
    game.write_bytes(b"".join(original[i:i + width][::-1] for i in range(0, len(original), width)))
    fp = ForkProject(tmp_path)
    cfg = {}
    engine = object.__new__(ForkEngine)
    monkeypatch.setattr(ForkEngine, "_save_cfg", lambda _self, _proj, _cfg: None)
    engine._match_version(SimpleNamespace(), fp, cfg, game, log=lambda *_: None)
    assert cfg["version"] == "pal"
    assert fp.place_game(game, game.name, cfg["version"]).read_bytes() == original
    assert game.read_bytes() != original


def test_macos_docker_only_project_routes_make_through_container(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace

    from decompnet import compiler_processes
    from decompnet.forks import project as project_module

    (tmp_path / "Dockerfile").write_text("FROM example\n")
    (tmp_path / "README.md").write_text("- Docker only\n    - Linux\n    - macOS\n")
    (tmp_path / "Makefile").write_text("all:\n\t@true\n")
    monkeypatch.setattr(project_module.sys, "platform", "darwin")
    fp = ForkProject(tmp_path)
    assert fp._docker_only_macos()
    monkeypatch.setattr(ForkProject, "_prepare_docker", lambda *_: None)
    seen = []

    # Dispatch only: process ownership and watchdogs have dedicated real-process tests.
    monkeypatch.setattr(compiler_processes,"track",lambda *args:None)
    monkeypatch.setattr(compiler_processes,"finish",lambda *args,**kwargs:None)
    monkeypatch.setattr(project_module,"CompilerMemoryWatchdog",
                        lambda *args,**kw:nullcontext(SimpleNamespace(timed_out=False,exceeded=False)))

    class Process:
        pid = 0
        stdout = []

        def wait(self, timeout):
            return 0

    monkeypatch.setattr(project_module.subprocess, "Popen", lambda cmd, **kwargs: seen.append(cmd) or Process())
    assert fp.run(["make", "VERSION=pal", "-j4"])[0]
    docker = seen[0][next(i for i, part in enumerate(seen[0]) if Path(part).stem == "docker"):]
    assert docker[1:3] == ["run", "--rm"]
    assert docker[-2:] == ["-c", "make VERSION=pal -j4"]
    assert docker[docker.index("--entrypoint") + 1] == "/bin/sh"


@pytest.fixture(scope="module")
def fork(tmp_path_factory):
    base = tmp_path_factory.mktemp("fork")
    upstream = base / "upstream"
    shutil.copytree(FORK_SRC, upstream)
    _git(upstream, "init", "-q", "-b", "main")
    _git(upstream, "add", "-A")
    _git(upstream, "commit", "-q", "-m", "initial")
    net = RomNetwork(base / "app.db", base / "work")
    (base / "work").mkdir()
    slug = net.forks.clone(str(upstream), name="Demo Quest decomp", platform="n64", log=lambda *_: None)
    need = net.forks.needs(slug)
    assert need["kind"] == "manifest" and need["path"] == "baserom.z64"
    with pytest.raises(NetworkError, match="Expected SHA-1:"):
        net.forks.setup(slug, b"\0" * 1024, "nope.z64", log=lambda *_: None)
    m = net.forks.setup(slug, ROM.read_bytes(), "demo.z64", log=lambda *_: None)
    assert m["total_functions"] == 13
    agent, _ = net.local_agent("claude-1")
    return net, slug, agent


def _ref(name: str) -> str:
    return csource.get(REFERENCE, name).strip()


def test_the_comment_above_a_definition_is_its_own_up_to_a_blank_line_or_a_marker():
    """A polished function's comment (what it does) goes into its file with it: the lines right above it, never the
    file's comment past a blank line or a reccmp annotation (`// FUNCTION: 0x...`, the project's own marker)."""
    text = ("/* the file */\n\n// FUNCTION: 0x00401000\n// Adds them.\n/* Both\n   ints. */\nint add(int a, int b)\n"
            "{\n    return a + b;\n}\n\n/* not this one's */\n\nint sub(int a, int b) { return a - b; }\n")
    add, sub = csource.find(text, "add")[0], csource.find(text, "sub")[0]
    assert text[csource.comment_above(text, add):add] == "// Adds them.\n/* Both\n   ints. */\n"
    assert csource.comment_above(text, sub) == sub
    assert csource.comment_above("int f(void) { return 0; }\n", 0) == 0
    # only reccmp's words are markers: a comment with `// NOTE:` in it was dropped
    text = "// Sums them.\n// NOTE: wraps at 65521.\nint f(void) { return 0; }\n"
    assert csource.comment_above(text, text.index("int")) == 0
    # the annotation right above the comment (a parked definition's, carried with it: ForkEngine._attempt)
    text = "int g;\n// FUNCTION: 0x00401000 (NONMATCHING: frame size)\n// Adds.\nint add(int a, int b) { return a + b; }\n"
    lo = csource.comment_above(text, text.index("int add"))
    assert text[csource.marker_above(text, lo):lo].startswith("// FUNCTION: 0x00401000")
    assert csource.marker_above(text, text.index("// FUNCTION")) == text.index("// FUNCTION")  # none above it


def test_a_wrapper_is_taken_with_the_comment_inside_it():
    """A polish writes `#pragma push`, the comment, then the definition: the next rewrite's wrapper stopped at the
    comment and left the file's push behind, never popped."""
    from decompnet.app.forkengine import _pragmas_around

    text = "int g;\n#pragma push\n#pragma opt 0\n// Adds.\nint f(void) { return 1; }\n#pragma pop\nint h;\n"
    lo, hi = csource.find(text, "f")
    assert text[slice(*_pragmas_around(text, lo, hi))] == text[text.index("#pragma push"):text.index("int h")]
    plain = "int g;\n// Adds.\nint f(void) { return 1; }\n"
    lo, hi = csource.find(plain, "f")
    assert _pragmas_around(plain, lo, hi)[0] == lo  # no wrapper: the comment isn't taken


@needs_tools
def test_fork_state_after_setup(fork):
    net, slug, agent = fork
    ov = net.overview(slug)
    assert ov["kind"] == "fork" and ov["ready"] and ov["functions"] == 13
    assert ov["matched"] == 4  # clamp_s32, abs_s32, rng_next, add_score
    rows = {r["name"]: r for r in net.function_map(slug)}
    assert rows["clamp_s32"]["status"] == "matched" and rows["lerp"]["status"] == "open"
    assert "fork of" in net.forks.info(slug) and "functions" in net.forks.info(slug)


@needs_tools
def test_agent_matches_a_new_function_and_commits(fork):
    net, slug, agent = fork
    brief = net.claim(agent, slug, "lerp")
    assert "Target assembly" in brief and "src/game.c" in brief and "Not in src/game.c yet" in brief
    res = net.attempt(agent, "lerp", _ref("lerp"), slug)
    assert res["matched"], res["text"]
    text = (Path(net.project_row(slug)["root"]) / "src" / "game.c").read_text()
    # placed where it was in the original: after add_score, before the later player_take_damage
    assert text.index("void add_score(") < text.index("lerp(") < text.index("void player_take_damage(")
    log = _git(Path(net.project_row(slug)["root"]), "log", "--oneline", "decomp-wtf")
    assert "Match lerp" in log
    assert net.overview(slug)["matched"] == 5


@needs_tools
def test_decomp_wtfs_own_tries_leave_no_claim_behind(fork):
    """A twin's code tried on an unclaimed function used to leave it claimed by the autopilot for 30 minutes (agents
    were kept off it), and the expiry counted as giving up on it: Mario Party 6 had 3,132 such expiries in 5 hours."""
    net, slug, _ = fork
    autopilot, _ = net.local_agent("autopilot")
    name = "checksum_save"
    fn = net.store.one("SELECT id, status, abandons FROM functions WHERE name=?", name)
    if fn["status"] != "open" or net.store.one("SELECT 1 FROM claims WHERE function_id=?", fn["id"]):
        pytest.skip("checksum_save isn't open and free in this run")
    res = net.forks.attempt(autopilot, slug, name, _ref(name).replace("return", "return 1 +", 1), keep=False)
    assert not res["matched"]
    assert net.store.one("SELECT 1 FROM claims WHERE function_id=?", fn["id"]) is None
    with net.store.tx() as db:  # one cut short by a restart runs out: nobody gave up on the function
        db.execute("INSERT INTO claims VALUES(?,?,?,?)", (fn["id"], autopilot.id, 0, 1))
        net._expire_claims(db)
    assert net.store.one("SELECT abandons FROM functions WHERE id=?", fn["id"])["abandons"] == fn["abandons"]


@needs_tools
def test_near_miss_gets_a_diff_and_release_restores_the_best(fork):
    net, slug, agent = fork
    root = Path(net.project_row(slug)["root"])
    net.claim(agent, slug, "count_alive_entities")
    good = _ref("count_alive_entities")
    near, worse = good.replace("return n;", "return n + 1;"), good.replace("n++;", "n += 2;")
    assert near != good and worse != good
    first = net.attempt(agent, "count_alive_entities", near, slug)
    assert not first["matched"] and first["score"] and "TARGET" in first["text"]
    d = net.function_diff(slug, "count_alive_entities")
    assert "Mismatch categories (advisory)" in d["mismatch_summary"]
    assert d["kind"] == "latest" and 0 < d["percent"] < 100 and any(r[0] != " " for r in d["rows"])
    second = net.attempt(agent, "count_alive_entities", worse, slug)
    assert second["score"] is not None
    net.release(agent, "count_alive_entities", "testing release", slug)
    best = net.store.one("SELECT best_source FROM functions WHERE name='count_alive_entities'")["best_source"]
    assert best.strip() in (root / "src" / "game.c").read_text()


@needs_tools
def test_guards(fork):
    net, slug, agent = fork
    root = Path(net.project_row(slug)["root"])
    net.claim(agent, slug, "checksum_save")
    before = (root / "src" / "game.c").read_text()
    with pytest.raises(NetworkError, match="assembly"):
        net.attempt(agent, "checksum_save", 'u32 checksum_save(u8 *p, s32 n) { __asm__("nop"); return 0; }', slug)
    with pytest.raises(NetworkError, match="go in through edit_file"):
        net.attempt(agent, "checksum_save", "struct X { int a; };\n" + _ref("checksum_save"), slug)
    broken = net.attempt(agent, "checksum_save", "u32 checksum_save(void) { return undefined_thing; }", slug)
    assert broken["score"] is None and "BUILD ERROR" in broken["text"]
    assert (root / "src" / "game.c").read_text() == before
    for bad in ("configure.py", "build/expected/game.o", "../outside.c", "objdiff.json", "tools/reference/game.c"):
        with pytest.raises(NetworkError):
            net.forks.edit_file(agent, slug, bad, "", "int x;", "nope")
    with pytest.raises(NetworkError, match="ROM projects"):
        net.get_context(slug)
    # a header edit that would break matches is undone
    header = (root / "include" / "game.h").read_text()
    res = net.forks.edit_file(agent, slug, "include/game.h", "#define MAX_SCORE 999999", "#define MAX_SCORE 5000", "cap")
    assert res.startswith("❌") and "add_score" in res and (root / "include" / "game.h").read_text() == header
    # one that keeps every match is committed
    res = net.forks.edit_file(agent, slug, "include/game.h", "", "#define SAVE_MAGIC 0x5A5A", "save magic")
    assert res.startswith("✅") and "SAVE_MAGIC" in (root / "include" / "game.h").read_text()
    assert net.overview(slug)["matched"] >= 5
    net.release(agent, "checksum_save", "", slug)


@needs_tools
def test_read_and_search(fork):
    net, slug, agent = fork
    assert "clamp_s32" in net.forks.read_file(slug, "src/game.c")
    assert "game.h" in net.forks.search(slug, r"MAX_ENTITIES")
    with pytest.raises(NetworkError):
        net.forks.read_file(slug, ".git/config")
    with pytest.raises(NetworkError, match="source or text"):
        net.forks.read_file(slug, "baserom.z64")
    assert "../" not in net.forks.search(slug, "x", "../**/*.c")
    # shell-style globs pathlib refuses ("'**' can only be an entire path component") still search
    assert "game.h" in net.forks.search(slug, r"MAX_ENTITIES", "**.h")
    assert isinstance(net.forks.search(slug, r"MAX_ENTITIES", "src**.c"), str)  # no ValueError


@needs_tools
def test_function_detail(fork):
    net, slug, agent = fork
    d = net.function_detail(slug, "clamp_s32")
    assert d["id"] == net._function("clamp_s32", slug)["id"]
    assert d["status"] == "matched" and "slt" in d["asm"] and "clamp_s32" in d["source"]


@needs_tools
def test_function_detail_can_skip_optional_name_search(fork, monkeypatch):
    net, slug, _ = fork
    def unexpected(*_):
        raise AssertionError('Opening a function must not wait for optional name searches')
    monkeypatch.setattr(net.twins, 'suggestions', unexpected)
    d = net.function_detail(slug, "clamp_s32", include_hints=False)
    assert d['source'] and d['cross_game_hints'] == []


def test_search_globs_as_agents_write_them():
    from pathlib import Path

    from decompnet.app.forkengine import _path_glob

    for given, want in [("src/**.cpp", "src/**/*.cpp"), ("**.h", "**/*.h"), ("src/**/*.c", "src/**/*.c"),
                        ("/include/*.h", "include/*.h"), ("src**/x.h", "src*/**/x.h")]:
        assert _path_glob(given) == want
        list(Path(".").glob(want))  # pathlib takes it (it raised ValueError for the given form)


def test_csource_finds_definitions_and_stubs():
    src = ('s32 f(s32 a) {\n    return a; /* } */\n}\nINCLUDE_ASM("asm/nonmatchings/x", g);\n'
           'uLong h(a)\n    uLong a;\n{\n    return a;\n}\nvoid C::m(int x) {\n}\n')
    assert csource.get(src, "f").startswith("s32 f(") and csource.get(src, "f").endswith("}\n")
    assert "INCLUDE_ASM" in csource.get(src, "g")
    assert csource.get(src, "h").startswith("uLong h(a)")
    assert csource.get(src, "C::m").startswith("void C::m")
    assert csource.get(src, "missing") is None
    assert "return 1" in csource.replace(src, "g", "int g(void) { return 1; }")


def test_a_cpp_constructor_or_destructor_is_found_as_the_function_it_stands_for():
    """A PC game's function written as a class named after it: its constructor was found, its destructor wasn't (an
    agent's func_004D9DD0::~func_004D9DD0 was refused: "send the complete definition of _func_004D9DD0"). C files
    (every other console's) find what they found before."""
    from decompnet.app.forkengine import ForkEngine

    names = ForkEngine._names({"symbol": "_func_004D9DD0"}, {"name": "_func_004D9DD0"})
    dtor = "struct func_004D9DD0 { ~func_004D9DD0(); };\nfunc_004D9DD0::~func_004D9DD0() {\n    func_005F10E0();\n}\n"
    assert ForkEngine._find(None, dtor, names)[0] == "func_004D9DD0::~func_004D9DD0"
    ctor = "func_004D9DD0::func_004D9DD0(int x) {\n}\n"
    assert ForkEngine._find(None, ctor, names)[1] == (0, len(ctor))
    # a function with a C++ name (a fork's) is found by it, not by a destructor of the same plain name
    names = ForkEngine._names({"symbol": "??0Foo@@QAE@XZ", "demangled": "Foo::Foo(void)"}, {"name": "??0Foo@@QAE@XZ"})
    assert not any("~" in n for n in names)
    assert ForkEngine._find(None, "Foo::~Foo() {\n}\n", names) == (None, None)
    # C: a definition, not a call or a prototype, and nothing of C reads as a destructor
    c = "int g(void);\nint f(int a) {\n    return ~g() + a;\n}\n"
    names = ForkEngine._names({"symbol": "f"}, {"name": "f"})
    assert names == ["f", "f::~f"] and ForkEngine._find(None, c, names) == ("f", (13, len(c)))
    assert csource.find(c, "g") is None
    # the one constructor or destructor a source defines names an unnamed PC function (ForkEngine._attempt)
    assert csource.structor("struct CFoo { CFoo(); ~CFoo(); };\nCFoo::~CFoo() {\n}\n") == "CFoo::~CFoo"
    assert csource.structor("A::CFoo::CFoo(int x) : y(x) {\n}\n") == "A::CFoo::CFoo"
    assert csource.structor("CFoo::CFoo() {}\nCFoo::~CFoo() {}\n") is None  # two: which one is the function?
    assert csource.structor("void CFoo::Update() {}\nCFoo::CFoo();\n" + c) is None


def test_clone_rejects_odd_sources(tmp_path):
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    for bad in ("file:///etc", "https://evil.example/x/y", "--upload-pack=x", str(tmp_path)):
        with pytest.raises(NetworkError):
            net.forks.clone(bad, log=lambda *_: None)


@needs_tools
def test_find_similar(fork):
    net, slug, agent = fork
    out = net.find_similar(slug, "entity_spawn")
    assert "index covers" in out or "Functions that look like" in out


@needs_tools
def test_the_look_alike_index_is_written_only_when_something_new_is_indexed(fork):
    # every sync ends an indexing pass; a GameCube game's 11 MB index.json rewritten on each one wrote 50 MB/s
    from decompnet.forks.project import STATE
    net, slug, _ = fork
    proj, fp, _ = net.forks.get(slug)
    for unit in fp.units():
        net.forks._unit_index(proj, fp, unit, save=False)
    net.forks._save_index(proj, fp)
    path = fp.root / STATE / "index.json"
    assert json.loads(path.read_text())
    net.forks._index.pop(proj["id"])  # as after a restart: the index comes back from index.json
    net.forks._index_of(proj, fp)
    path.unlink()
    for unit in fp.units():  # all known already: nothing to write
        net.forks._unit_index(proj, fp, unit)
    net.forks._save_index(proj, fp)
    assert not path.exists()
    # a compile measures its unit again (the same target): nothing new either
    from decompnet.app.forkengine import objdiff_diff
    unit = next(u for u in fp.units() if net.forks._unit_index(proj, fp, u, save=False))
    symbol = net.forks._unit_index(proj, fp, unit, save=False)["order"][0]
    net.forks._remember(proj, fp, unit, objdiff_diff(fp, unit, symbol, target_only=True))
    net.forks._save_index(proj, fp)
    assert not path.exists()


def test_similarity_ranks_copies_first():
    from decompnet import similarity

    a = ["addiu", "sw", "lw", "addu", "slt", "bnez", "nop", "jr", "addiu"]
    cands = {"copy": list(a), "tweaked": a[:4] + ["sltu"] + a[5:], "other": ["lui", "ori", "jal", "nop"] * 3}
    ranked = similarity.rank(a, cands, top=3)
    assert [n for n, _ in ranked][:2] == ["copy", "tweaked"] and ranked[0][1] == 1.0


def test_a_source_is_rewritten_from_its_first_change_only(tmp_path, monkeypatch):
    # a compile writes the agent's version of a dtk unit (up to a megabyte) and an undo writes the original back
    import os

    from decompnet.app import forkengine
    head = "".join(f"void f{i}(void) {{ }}\n" for i in range(2000))  # ~40 KB the edit doesn't touch
    before, after = head + "int g(void) { return 1; }\n", head + "int g(void) { return 2; }\nint h;\n"
    written = []

    class Spy:
        def __init__(self, f):
            self.f = f

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.f.close()

        def __getattr__(self, name):
            return getattr(self.f, name)

        def write(self, data):
            written.append(len(data))
            return self.f.write(data)
    monkeypatch.setattr(forkengine, "open", lambda *a, **k: Spy(open(*a, **k)), raising=False)
    for linesep in ("\n", "\r\n"):  # what text mode writes on Linux and macOS, and on Windows
        monkeypatch.setattr(os, "linesep", linesep)
        p = tmp_path / f"unit{len(linesep)}.c"
        p.write_bytes(before.replace("\n", linesep).encode())
        for text in (after, before, before[:100], "", before):  # an edit, its undo, shorter, empty, longer again
            written.clear()
            forkengine._rewrite(p, text)
            assert p.read_bytes() == text.replace("\n", linesep).encode()
        written.clear()
        forkengine._rewrite(p, after)
        assert p.read_bytes() == after.replace("\n", linesep).encode()
        assert 0 < sum(written) < 8192 < len(head)  # only the block with the change, and what follows it
        os.utime(p, (1, 1))
        written.clear()
        forkengine._rewrite(p, after)  # the same text: nothing written, but a build still sees it as new
        assert not written and p.stat().st_mtime > 1
    forkengine._rewrite(tmp_path / "new.c", "int x;\n")
    assert (tmp_path / "new.c").read_text() == "int x;\n"


@pytest.mark.skipif(toolchains.installed("m2c") is None, reason="needs m2c")
def test_drafts_from_project_assembly(tmp_path):
    from decompnet.forks import drafts

    (tmp_path / "asm" / "nonmatchings" / "game").mkdir(parents=True)
    (tmp_path / "asm" / "nonmatchings" / "game" / "func_80001234.s").write_text(
        "glabel func_80001234\n"
        "/* 1234 80001234 27BDFFE8 */  addiu      $sp, $sp, -0x18\n"
        "/* 1238 80001238 AFBF0010 */  sw         $ra, 0x10($sp)\n"
        "/* 123C 8000123C 0C000000 */  jal        func_80000000\n"
        "/* 1240 80001240 00000000 */   nop\n"
        "/* 1244 80001244 8FBF0010 */  lw         $ra, 0x10($sp)\n"
        "/* 1248 80001248 03E00008 */  jr         $ra\n"
        "/* 124C 8000124C 27BD0018 */   addiu     $sp, $sp, 0x18\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "game.c").write_text('INCLUDE_ASM("asm/nonmatchings/game", func_80001234);\n')
    out = drafts.draft(tmp_path, "n64", "game", "func_80001234", "src/game.c")
    assert out and "func_80000000();" in out
    assert csource.is_stub('INCLUDE_ASM("asm/nonmatchings/game", func_80001234);')
    assert drafts.draft(tmp_path, "win32", "game", "func_80001234", "src/game.c") is None


@needs_tools
def test_ghidra_drafts_for_a_unit(fork):
    from decompnet.forks import ghidra

    if not ghidra.available():
        pytest.skip("needs Ghidra and Java 21+")
    net, slug, agent = fork
    root = Path(net.project_row(slug)["root"])
    out = ghidra.decompile_object(root / "build" / "expected" / "game.o", root / ".decomp-wtf" / "ghidra")
    assert "clamp_s32" in out and "return" in out["clamp_s32"]


@needs_tools
def test_rewriting_a_matched_function_must_keep_it_matching(fork):
    net, slug, agent = fork
    root = Path(net.project_row(slug)["root"])
    before = (root / "src" / "game.c").read_text()
    res = net.attempt(agent, "clamp_s32", "s32 clamp_s32(s32 v, s32 lo, s32 hi) { return v; }", slug)
    assert not res["matched"] and "already matches" in res["text"]
    assert (root / "src" / "game.c").read_text() == before
    row = net.store.one("SELECT id, status FROM functions WHERE name='clamp_s32'")
    assert row["status"] == "matched"
    # the try counts as a compile: a polishing helper's showed none
    assert net.store.one("SELECT COUNT(*) AS n FROM attempts WHERE function_id=? AND score > 0", row["id"])["n"] == 1


def _dtk_project(root: Path, version: str, config: str) -> None:
    (root / "config" / version).mkdir(parents=True)
    (root / "config" / version / "config.yml").write_text(config)
    (root / "configure.py").write_text("# dtk-template\n")
    (root / "orig" / version).mkdir(parents=True)


@pytest.mark.parametrize(("obj", "expected", "got"), [
    ("main.dol", "9619ba57...", "8fe64b0a..."),
    ("files/dll/a.rel", "0123456789abcdef0123456789abcdef01234567", "89abcdef0123456789abcdef0123456789abcdef"),
])
def test_a_dtk_hash_mismatch_says_the_project_was_built_from_another_copy(tmp_path, monkeypatch, obj, expected, got):
    """#97: dtk's hash mismatch is correct, but the user's dump is likely fine: some public projects build a main.dol
    from an ELF or use a .rel module the retail disc doesn't contain. Say that instead of only the ninja output."""
    from decompnet.forks.project import ForkError, ForkProject

    _dtk_project(tmp_path, "GOWE69", "object: sys/main.dol\nmodules:\n- object: files/dll/a.rel\n")
    fp = ForkProject(tmp_path)
    tail = ("[1/2] SPLIT config\\GOWE69\\config.yml\n"
            "FAILED: [code=1] build/GOWE69/config.json\n"
            "build\\tools\\dtk.exe dol split config\\GOWE69\\config.yml build\\GOWE69\n"
            " INFO Loading config\\GOWE69\\config.yml\n"
            " INFO Loading and analyzing 1 module (using 1 thread)\n"
            f"Failed: While loading object '{obj}'\n\nCaused by:\n"
            f"    Hash mismatch: expected {expected}, but was {got}\n"
            "ninja: error: rebuilding 'build.ninja': subcommand failed")
    monkeypatch.setattr(fp, "unpack_disc", lambda version, log=print: 0)
    monkeypatch.setattr(fp, "setup_commands", lambda version=None: [["configure.py"], ["ninja"]])
    monkeypatch.setattr(fp, "run", lambda cmd, log=print: (False, tail))
    with pytest.raises(ForkError) as err:
        fp.setup("GOWE69", log=lambda *_: None)
    text = str(err.value)
    assert f"your copy's {obj} isn't the one this project was built against" in text
    assert f"expects hash {expected[:8]}..." in text and f"yours is {got[:8]}..." in text
    assert "dump is likely fine" in text and "README" in text
    assert "your own copy" in text and "New project → Open a game file" in text
    assert tail in text


def test_a_setup_failure_that_isnt_a_dtk_hash_mismatch_keeps_the_plain_message(tmp_path, monkeypatch):
    from decompnet.forks.project import ForkError, ForkProject

    _dtk_project(tmp_path, "GOWE69", "object: sys/main.dol\n")
    fp = ForkProject(tmp_path)
    tail = "configure.py: error: unrecognized arguments: --version GOWE69"
    monkeypatch.setattr(fp, "unpack_disc", lambda version, log=print: 0)
    monkeypatch.setattr(fp, "setup_commands", lambda version=None: [["configure.py"], ["ninja"]])
    monkeypatch.setattr(fp, "run", lambda cmd, log=print: (False, tail))
    with pytest.raises(ForkError) as err:
        fp.setup("GOWE69", log=lambda *_: None)
    assert str(err.value) == f"setup step failed: configure.py\n{tail}"


def test_a_project_that_reads_an_extracted_disc_gets_its_files_from_the_disc_image(tmp_path, monkeypatch):
    """Older dtk projects read orig/<version>/sys/main.dol and files/... (the README says "extract the disc with
    Dolphin"); decompdecomp copies exactly those files out of the user's disc image, picking the disc of that version."""
    from decompnet.consoles.gc_wii import scratch as scratch_dtk
    from decompnet.forks.project import ForkProject

    _dtk_project(tmp_path, "GP6E01", "object: orig/GP6E01/sys/main.dol\nmodules:\n- object: orig/GP6E01/files/dll/a.rel\n"
                                     "- object: 'orig/GP6E01/files/dll/b.rel'\n")
    for name in ("eu.rvz", "us.rvz"):
        (tmp_path / "orig" / "GP6E01" / name).write_bytes(b"RVZ\x01" + bytes(2 << 20))
    monkeypatch.setattr(scratch_dtk, "disc_id", lambda p: {"eu.rvz": "GP6P01", "us.rvz": "GP6E01"}.get(p.name))
    copied = []

    def fake_copy(disc, inner, dest):
        copied.append((disc.name, list(inner), dest.relative_to(tmp_path).as_posix()))
        for x in inner:
            (dest / Path(x).name).parent.mkdir(parents=True, exist_ok=True)
            (dest / Path(x).name).write_bytes(b"x")
    monkeypatch.setattr(scratch_dtk, "vfs_copy", fake_copy)
    fp = ForkProject(tmp_path)
    assert fp.unpack_disc("GP6E01", log=lambda *_: None) == 3
    assert sorted(copied) == [("us.rvz", ["files/dll/a.rel", "files/dll/b.rel"], "orig/GP6E01/files/dll"),
                              ("us.rvz", ["sys/main.dol"], "orig/GP6E01/sys")]
    assert fp.unpack_disc("GP6E01", log=lambda *_: None) == 0  # everything is there now
    # a project whose dtk reads the disc image itself is left alone
    other = tmp_path / "new"
    _dtk_project(other, "GALE01", "object_base: orig/GALE01\nobject: sys/main.dol\n")
    assert ForkProject(other).unpack_disc("GALE01", log=lambda *_: None) == 0


def test_a_disc_of_another_version_switches_the_project_or_is_refused(tmp_path, monkeypatch):
    from decompnet.consoles.gc_wii import scratch as scratch_dtk

    net = RomNetwork(tmp_path / "decomp.db", tmp_path / "ws")
    root = tmp_path / "ws" / "mp6"
    _dtk_project(root, "GP6E01", "object: orig/GP6E01/sys/main.dol\n")
    (root / "config" / "GP6P01").mkdir(parents=True)
    (root / "config" / "GP6P01" / "config.yml").write_text("object: orig/GP6P01/sys/main.dol\n")
    net.forks._register("mp6", "Mario Party 6", "fork", root, {"kind": "fork", "version": "GP6E01", "fork_kind": "dtk"}, "x")
    proj, fp, cfg = net.forks.get("mp6")
    disc = tmp_path / "game.rvz"
    disc.write_bytes(b"RVZ\x01")
    monkeypatch.setattr(scratch_dtk, "disc_id", lambda p: "GP6P01")
    net.forks._match_version(proj, fp, cfg, disc, log=lambda *_: None)
    assert net.forks.get("mp6")[2]["version"] == "GP6P01"
    monkeypatch.setattr(scratch_dtk, "disc_id", lambda p: "GP6J01")
    with pytest.raises(NetworkError, match="GP6J01.*GP6E01, GP6P01"):
        net.forks._match_version(*net.forks.get("mp6"), disc, log=lambda *_: None)


def test_a_project_made_from_scratch_builds_with_this_computers_tools(tmp_path, monkeypatch):
    """The manifest of a generated project records the tools of the computer that made it; setup uses this one's."""
    from decompnet.consoles.pc import scratch as scratch_pe
    from decompnet.forks.project import ForkProject

    (tmp_path / ".decomp.wtf.json").write_text(json.dumps({
        "created": "from scratch", "platform": "win32", "version": "game",
        "setup": [["python", "tools/configure.py", "game", "--msvc", "/elsewhere/msvc"], ["ninja", "prepare"]]}))
    monkeypatch.setattr(scratch_pe, "tool_args", lambda kit_ids=(): ["--msvc", "/here/msvc", "--wibo", "/here/wibo"])
    cmds = ForkProject(tmp_path).setup_commands("game")
    assert cmds[0][1:] == ["tools/configure.py", "game", "--msvc", "/here/msvc", "--wibo", "/here/wibo"]
    assert cmds[1] == ["ninja", "prepare"]


@needs_tools
def test_a_unit_without_a_compiled_object_is_measured_as_not_matched(fork, tmp_path):
    """Work in progress that doesn't compile, or units the default build never compiles: the report still works,
    and those units count as not matched yet instead of stopping the whole measurement."""
    from decompnet.forks import objdiff

    net, slug, _ = fork
    root = Path(net.project_row(slug)["root"])
    copy = tmp_path / "copy"
    shutil.copytree(root, copy, symlinks=True)
    cfg = json.loads((copy / "objdiff.json").read_text())
    unit = next(u for u in cfg["units"] if u.get("base_path") and (copy / u["base_path"]).is_file())
    (copy / unit["base_path"]).unlink()
    rep = objdiff.report(copy, tmp_path / "report.json")
    got = next(u for u in rep["units"] if u["name"] == unit["name"])
    assert all((f.get("fuzzy_match_percent") or 0) < 100 for f in got.get("functions", []))
    assert len(rep["units"]) == len(cfg["units"])


def test_two_functions_of_the_same_name_in_one_unit_both_get_a_row(tmp_path, monkeypatch):
    """Mario Party 6 has two static `_langGetNbrSpeechUnit` in one unit: measuring must not trip over that."""
    from decompnet.forks.project import ForkProject

    net = RomNetwork(tmp_path / "decomp.db", tmp_path / "ws")
    root = tmp_path / "ws" / "p"
    root.mkdir(parents=True)
    (root / "objdiff.json").write_text(json.dumps({"units": []}))
    net.forks._register("p", "P", "fork", root, {"kind": "fork", "version": "X", "fork_kind": "objdiff", "ready": False}, "x")
    fn = lambda name, addr, pct=None: {"name": name, "size": "12", "metadata": {"virtual_address": str(addr)},
                                       **({"fuzzy_match_percent": pct} if pct is not None else {})}
    report = {"measures": {}, "units": [
        {"name": "main/lang", "functions": [fn("_lang", 1, 100.0), fn("_lang", 2), fn("init", 3)]},
        {"name": "main/other", "functions": [fn("init", 4)]}]}
    monkeypatch.setattr(ForkProject, "report", lambda self: report)
    monkeypatch.setattr(ForkProject, "units", lambda self: [])
    for _ in range(2):  # measuring again updates the same rows
        net.forks.sync("p", log=lambda *_: None)
    rows = {r["name"]: r for r in net.store.all("SELECT name, vram, status FROM functions")}
    assert set(rows) == {"_lang@main/lang", "_lang@main/lang#2", "init@main/lang", "init@main/other"}
    assert rows["_lang@main/lang"]["vram"] == 1 and rows["_lang@main/lang#2"]["vram"] == 2


def test_measuring_again_keeps_what_set_a_function_aside_and_what_gave_it_back(tmp_path, monkeypatch):
    """Sync rebuilt each function's meta from the report: a function agents agreed no C makes lost its "not C" (red in
    the map) at the next match anywhere, and one the user gave back lost that, so the next check set it aside again.
    One agent's verdict alone sets nothing aside."""
    from types import SimpleNamespace

    from decompnet.forks.project import ForkProject

    net = RomNetwork(tmp_path / "decomp.db", tmp_path / "ws")
    root = tmp_path / "ws" / "p"
    root.mkdir(parents=True)
    (root / "objdiff.json").write_text(json.dumps({"units": []}))
    net.forks._register("p", "P", "fork", root, {"kind": "fork", "version": "X", "fork_kind": "objdiff", "ready": False}, "x")
    report = {"measures": {}, "units": [{"name": "main/a", "functions": [
        {"name": n, "size": "12", "metadata": {"virtual_address": str(k)}} for k, n in enumerate(("stub", "back", "one"))]}]}
    monkeypatch.setattr(ForkProject, "report", lambda self: report)
    monkeypatch.setattr(ForkProject, "units", lambda self: [SimpleNamespace(name="main/a", source="src/a.c", target=None)])
    net.forks.sync("p", log=lambda *_: None)
    metas = {"stub": {"asm_code": "3 agents found no C makes it", "not_c": {"a-1": {}, "b-1": {}, "c-1": {}}},
             "back": {"reopened": True}, "one": {"not_c": {"a-1": {}}}}
    with net.store.tx() as db:
        for name, extra in metas.items():
            meta = json.loads(db.execute("SELECT meta FROM functions WHERE name=?", (name,)).fetchone()[0])
            db.execute("UPDATE functions SET status=?, meta=? WHERE name=?",
                       ("asm" if "asm_code" in extra else "open", json.dumps({**meta, **extra}), name))
    net.forks.sync("p", log=lambda *_: None)
    rows = {r["name"]: (r["status"], json.loads(r["meta"])) for r in net.store.all("SELECT name, status, meta FROM functions")}
    assert rows["stub"][0] == "asm" and rows["stub"][1]["not_c"] and net.function_map("p")[0]["not_c"]
    assert rows["back"][0] == "open" and rows["back"][1]["reopened"]
    assert rows["one"][0] == "open" and rows["one"][1]["not_c"] == {"a-1": {}}
    # pieces joined to it: its code grew, so what set it aside (about the piece) goes, and agents get it again
    report["units"][0]["functions"][0]["size"] = "40"
    net.forks.sync("p", log=lambda *_: None)
    stub = net.store.one("SELECT status, meta FROM functions WHERE name='stub'")
    assert stub["status"] == "open" and "asm_code" not in json.loads(stub["meta"]) and "not_c" not in json.loads(stub["meta"])
    # a function gone from the build (joined to another), with attempts: kept for them, marked gone (IN_BUILD)
    with net.store.tx() as db:
        db.execute("INSERT INTO attempts(function_id, agent_id, score, created_at) SELECT id, NULL, 5, 0 FROM functions "
                   "WHERE name='one'")
    report["units"][0]["functions"] = report["units"][0]["functions"][:2]
    net.forks.sync("p", log=lambda *_: None)
    one = net.store.one("SELECT status, meta FROM functions WHERE name='one'")
    assert one["status"] == "nosource" and json.loads(one["meta"])["gone"] == 1
    assert [r["name"] for r in net.function_map("p")] == ["stub", "back"]


@needs_tools
def test_a_public_decomp_together(tmp_path):
    """A room on a fork of a public decomp: the member gets the public project from its own host (here a local
    'upstream'), at the commit the room's host started from, plus the room's commits as a bundle; then builds it with
    their own copy of the game and works with the room."""
    from starlette.testclient import TestClient

    from decompnet.app import collab

    upstream = tmp_path / "upstream"
    shutil.copytree(FORK_SRC, upstream)
    _git(upstream, "init", "-q", "-b", "main")
    _git(upstream, "add", "-A")
    _git(upstream, "commit", "-q", "-m", "initial")
    (tmp_path / "host").mkdir()
    host = RomNetwork(tmp_path / "host.db", tmp_path / "host")
    slug = host.forks.clone(str(upstream), name="Demo Quest decomp", platform="n64", log=lambda *_: None)
    host.forks.setup(slug, ROM.read_bytes(), "demo.z64", log=lambda *_: None)
    hagent, _ = host.local_agent("claude-host")
    assert host.attempt(hagent, "count_alive_entities", _ref("count_alive_entities"), slug)["matched"]
    _git(upstream, "commit", "-q", "--allow-empty", "-m", "upstream moved on")  # members still start where the host did

    host.rooms[slug] = collab.Room(host, slug)
    code = host.rooms[slug].open_invite()
    client = TestClient(collab.host_app(host, host.rooms), base_url="https://abc.trycloudflare.com")
    (tmp_path / "guest").mkdir()
    guest = RomNetwork(tmp_path / "guest.db", tmp_path / "guest")
    gslug = collab.join_room(guest, f"https://abc.trycloudflare.com/join/{code}#room={slug}", "alex", ROM,
                             log=lambda *_: None, client=client, start=False)
    hroot, groot = Path(host.project_row(slug)["root"]), Path(guest.project_row(gslug)["root"])
    head = lambda root: subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True).stdout  # noqa: E731
    assert head(groot) == head(hroot)
    assert guest.overview(gslug)["functions"] == host.overview(slug)["functions"]
    assert guest._function("count_alive_entities", gslug)["status"] == "matched"  # the host's match came along

    gagent, _ = guest.local_agent("claude-1")
    assert "CLAIMED" in guest.forks.claim(gagent, gslug, "lerp")
    res = guest.attempt(gagent, "lerp", _ref("lerp"), gslug)
    assert res["matched"] and "host confirmed" in res["text"], res["text"]
    assert host._function("lerp", slug)["status"] == "matched"
    guest.links[gslug].sync()
    assert head(groot) == head(hroot)


def test_a_projects_own_words_on_ai_come_before_forking(tmp_path):
    """#21: every project decides about AI for itself. What its README, CONTRIBUTING and agent files say is read
    (a partial clone, any git host) and shown before anything is forked, with which way it leans."""
    from decompnet.forks import policy

    assert policy.lean(policy.lines_about_ai("We do **not** accept AI-generated code (ChatGPT, Copilot, ...).")) == "no"
    assert policy.lean(policy.lines_about_ai("Contributions made with LLMs are welcome if you review them.")) == "yes"
    assert policy.lean(policy.lines_about_ai("Said the mail: it is plain.")) == "silent"  # "AI" in capitals only
    melee = ("Introduction - Coding Style - Compiler Notes - Pull Requests - AI Assistance\n\nUsing AI to match functions "
             "is acceptable and frequently practiced. Do not let AI name fields in structs.")
    assert policy.lines_about_ai(melee)[0].startswith("Using AI") and policy.lean(policy.lines_about_ai(melee)) == "rules"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# Some decomp\n\nBuild with ninja.\n")
    (repo / ".github").mkdir()
    (repo / ".github" / "CONTRIBUTING.md").write_text("Rules.\n\nPull requests written by AI tools will be closed.\n")
    (repo / "src.c").write_text("/* not read: AI */\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial")
    got = policy.check(f"file://{repo}")
    assert got["error"] is None and set(got["files"]) == {"README.md", ".github/CONTRIBUTING.md"}
    assert got["lines"] == [{"file": ".github/CONTRIBUTING.md", "text": "Pull requests written by AI tools will be closed."}]
    assert got["lean"] == "no"
    gone = policy.check(f"file://{tmp_path}/nothing")
    assert gone["error"] == policy.MISSING_REPO and gone["missing"]


def test_git_trouble_reading_a_repository_is_said_in_a_sentence():
    """A fork of a repository that doesn't exist showed git's "could not read Username for 'https://github.com':
    terminal prompts disabled": GitHub's answer for a missing repository, which says nothing to most people."""
    from decompnet.forks import policy
    github = "Cloning into 'x'...\nfatal: could not read Username for 'https://github.com': terminal prompts disabled\n"
    assert policy.git_trouble(github) == policy.MISSING_REPO
    assert "internet connection" in policy.git_trouble("fatal: unable to access 'https://x/': Could not resolve host: x")
    assert policy.git_trouble("fatal: something odd\n") == "git couldn't read the repository (something odd)."


def test_c_code_going_into_a_cpp_file_gets_c_linkage(tmp_path, monkeypatch):
    """A twin's code from a C file tried in one of decompdecomp's C++ files built, but had no fn_1_BB34 in it (the
    name was mangled): 471 of Mario Party 6's twin misses. With extern "C" on it and its prototypes, it matches."""
    from types import SimpleNamespace

    from decompnet.app import forkengine

    net = RomNetwork(tmp_path / "app.db", tmp_path)
    units = {"a": SimpleNamespace(source="src/a.c"), "b": SimpleNamespace(source="src/b.cpp"),
             "c": SimpleNamespace(source="src/c.cpp")}
    monkeypatch.setattr(forkengine, "ForkProject", lambda root: SimpleNamespace(unit=lambda name: units[name]))
    row = lambda name, unit: {"name": name, "unit": unit, "meta": json.dumps({"symbol": name})}
    source = "extern void __dl__FPv(void *ptr);\nint fn_1_BB34(int p) {\n    __dl__FPv((void *)p);\n    return p;\n}\n"
    got = net.forks.c_linkage({"root": str(tmp_path)}, row("fn_1_1EE74", "a"), row("fn_1_BB34", "b"), source)
    assert got == 'extern "C" void __dl__FPv(void *ptr);\nextern "C" int fn_1_BB34(int p) {\n    __dl__FPv((void *)p);\n    return p;\n}\n'
    assert net.forks.c_linkage({"root": str(tmp_path)}, row("fn_1_1", "c"), row("fn_1_BB34", "b"), source) is None  # C++ to C++
    static = source.replace("int fn_1_BB34", "static int fn_1_BB34")
    assert net.forks.c_linkage({"root": str(tmp_path)}, row("fn_1_1EE74", "a"), row("fn_1_BB34", "b"), static) is None


def test_arms_pc_bias_isnt_shown_as_an_offset():
    """objdiff showed "bl func_0203aca8-0x8": the -8 is the pipeline's, not an offset into the function."""
    from decompnet.forks import objdiff

    def row(text, kind, addend):
        return {"instruction": {"formatted": text, "relocation": {"type_name": kind, "addend": addend}}}
    assert objdiff._fmt(row("bl func_0203aca8-0x8", "R_ARM_PC24", "-8")) == "bl func_0203aca8"
    assert objdiff._fmt(row("bl func_02001000-0x4", "R_ARM_THM_CALL", "-4")) == "bl func_02001000"
    assert objdiff._fmt(row("bl func_0203aca8-0x10", "R_ARM_PC24", "-16")) == "bl func_0203aca8-0x10"  # a real one
    assert objdiff._fmt(row("lui at, %hi(x-0x8)", "R_MIPS_HI16", "-8")) == "lui at, %hi(x-0x8)"


@needs_tools
def test_a_from_scratch_brief_says_what_the_data_it_uses_is(fork, monkeypatch):
    """Its headers start empty: on a DS game a model looked in config/symbols.txt for minutes for what a global
    was. The brief says where each one is and how big (read_data's answer on that game), and leaves functions out."""
    net, slug, agent = fork
    proj = net.project_row(slug)

    def read_data(agent, slug, what, length=64):
        if what == "data_02225058":
            return "data_02225058 (_dsd_gap@main_2, .bss+0x107CF8, 2 bytes): uninitialized data (.bss), zeros at boot."
        raise NetworkError(f"no data symbol `{what}`")
    monkeypatch.setattr(net.forks, "read_data", read_data)
    lines = net.forks._data_used(proj, ["add_score", "data_02225058", "g_score"])
    assert lines[1:4] == ["### Data it uses", "- data_02225058: .bss, 2 bytes", "- g_score"]
    assert not any("add_score" in line for line in lines) and "extern" in lines[-1]
    assert net.forks._data_used(proj, ["add_score"]) == []


def test_progress_counts_the_games_own_code_with_middleware_set_aside_by_range(tmp_path):
    from decompnet.app.forkengine import _set_aside
    from decompnet.forks import objdiff
    from decompnet.forks.project import ForkProject

    conf = tmp_path / "config" / "v"
    conf.mkdir(parents=True)
    fns = [{"name": f"_func_{a:08X}", "addr": a, "size": 0x10} for a in (0x11000, 0x11010, 0x200000, 0x200010)]
    (conf / "functions.json").write_text(json.dumps({"units": {"game": fns[:2], "engine": fns[2:3], "lib": fns[3:]}}))
    (conf / "set_aside.json").write_text(json.dumps({
        "_func_00200010": "library code (libc.lib)",  # what an import writes: symbol -> why
        "ranges": [{"from": "0x200000", "to": "0x200010", "why": "middleware engine 3.1"}]}))
    (tmp_path / ".decomp.wtf.json").write_text(json.dumps({"version": "v", "platform": "pc"}))
    aside = _set_aside(ForkProject(tmp_path))  # found beside functions.json, even without the manifest naming it
    assert aside == {"_func_00200010": "library code (libc.lib)", "_func_00200000": "middleware engine 3.1"}
    rep = {"measures": {"total_functions": 4, "matched_functions": 2, "total_code": "64", "matched_code": "32",
                        "matched_code_percent": 50.0},
           "units": [{"name": "game", "functions": [{"name": "_func_00011000", "size": "16", "fuzzy_match_percent": 100},
                                                    {"name": "_func_00011010", "size": "48", "fuzzy_match_percent": 50}]},
                     {"name": "engine", "functions": [{"name": "_func_00200000", "size": "16", "fuzzy_match_percent": 100}]},
                     {"name": "lib", "functions": [{"name": "_func_00200010", "size": "16"}]}]}
    m = objdiff.game_measures(rep, set(aside))
    assert (m["total_functions"], m["matched_functions"], m["total_code"], m["matched_code"]) == (2, 1, "64", "16")
    assert m["matched_code_percent"] == 25.0 and m["all_functions"] == 4 and m["set_aside_functions"] == 2
    assert objdiff.game_measures(rep, set()) == rep["measures"]
    assert objdiff.expand_set_aside({"functions": {"0x11010": "CRT"}}, {"units": {"game": fns}}) == \
        {"_func_00011010": "CRT"}


def test_the_progress_the_app_shows_leaves_set_aside_code_out(tmp_path):
    """The measures left set-aside code out, but the project list and header add up the function rows: one project
    still showed 3115/21237 (middleware, the CRT and an SDK counted). Measuring marks those rows set_aside, and set
    aside is one count with what no C makes (network.SET_ASIDE), which the pages take off the total."""
    from decompnet.network import PROGRESS_COLUMNS
    from decompnet.store import Store

    store = Store(tmp_path / "t.db")
    with store.tx() as db:
        for i, (status, size, meta) in enumerate([("matched", 16, {}), ("open", 48, {}),
                                                  ("matched", 16, {"asm_code": "middleware engine 3.1", "set_aside": True}),
                                                  ("asm", 16, {"asm_code": "library code (libc.lib)", "set_aside": True}),
                                                  ("asm", 8, {"asm_code": "hand-written assembly"})]):
            db.execute("INSERT INTO functions(id,project_id,name,size,status,meta) VALUES(?,7,?,?,?,?)",
                       (i + 1, f"f{i}", size, status, json.dumps(meta)))
    row = store.one(f"SELECT {PROGRESS_COLUMNS} FROM functions WHERE project_id=7")
    # five in all, three set aside (two not the game's own, one no C makes): two shown, one matched (the matched
    # middleware function doesn't count)
    assert (row["total"], row["set_aside"], row["matched"]) == (5, 3, 1)
    assert (row["bytes"] - row["set_aside_bytes"], row["matched_bytes"]) == (64, 16)


def test_a_match_that_is_lost_gets_its_score_back_so_the_queue_doesnt_say_it_is_done(tmp_path):
    # A reopened match kept best_score 0 (identical): briefs said "score 0" and agents released it as done.
    from types import SimpleNamespace

    from decompnet.app.forkengine import ForkEngine
    from decompnet.store import Store

    store = Store(tmp_path / "t.db")
    with store.tx() as db:
        db.execute("INSERT INTO functions(id,project_id,name,size,n_insns,status,best_score,best_source,unit,meta,asm) "
                   "VALUES(1,7,'F',40,10,'matched',0,'int F() {}','u','{\"symbol\": \"_F\"}','')")
        db.execute("INSERT INTO functions(id,project_id,name,size,n_insns,status,best_score,best_source,unit,meta,asm) "
                   "VALUES(2,7,'G',40,10,'open',0,'int G() {}','u','{\"symbol\": \"_G\"}','')")
    eng = ForkEngine.__new__(ForkEngine)
    eng.net = SimpleNamespace(store=store, project_store=lambda _:store, _event=lambda db, pid, aid, kind, detail: db.execute(
        "INSERT INTO events(project_id,agent_id,kind,detail,created_at) VALUES(?,?,?,?,0)", (pid, aid, kind, detail)))
    eng._apply_scores({"id": 7, "root": str(tmp_path)}, "u", {"_F": 52.7, "_G": 90.0}, via="unit diff before a compile")
    rows = {r["name"]: r for r in store.all("SELECT name, status, best_score FROM functions")}
    assert rows["F"]["status"] == "open" and rows["F"]["best_score"] > 0  # reopened now
    assert rows["G"]["best_score"] > 0  # reopened before the fix: mended at the next measurement
    # and the reopen is in the events, with what measured it and how much it scored
    events = [(r["kind"], r["detail"]) for r in store.all("SELECT kind, detail FROM events")]
    assert events == [("reopen", "F (unit diff before a compile: 52.70%)")]


def test_unit_diff_updates_improvement_date_without_a_local_check(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from decompnet.app.forkengine import ForkEngine
    from decompnet.store import Store

    store = Store(tmp_path / "t.db")
    with store.tx() as db:
        db.execute("INSERT INTO functions(id,project_id,name,size,n_insns,status,best_score,unit,meta,asm) "
                   "VALUES(1,7,'F',40,10,'open',900,'u','{\"symbol\": \"_F\"}','')")
    eng = ForkEngine.__new__(ForkEngine)
    def project_store(pid):
        assert pid == 7
        return store
    eng.net = SimpleNamespace(store=store, project_store=project_store)
    monkeypatch.setattr("decompnet.app.forkengine.time.time", lambda: 42)
    eng._apply_scores({"id": 7, "root": str(tmp_path)}, "u", {"_F": 20.0})
    row = store.one("SELECT best_score,last_checked,last_improved FROM functions WHERE id=1")
    assert row["best_score"] < 900
    assert (row["last_checked"], row["last_improved"]) == (None, 42)
    eng._apply_scores({"id": 7, "root": str(tmp_path)}, "u", {"_F": 10.0})
    assert store.one("SELECT last_improved FROM functions WHERE id=1")["last_improved"] == 42


@needs_tools
def test_a_sync_with_nothing_new_leaves_the_functions_alone(fork):
    # SQLite writes a row's pages even when an UPDATE sets the same values: 34,000 functions on every sync
    net, slug, _ = fork
    net.forks.sync(slug, log=lambda *_: None)
    before = net.store.conn.total_changes
    net.forks.sync(slug, log=lambda *_: None)
    functions = net.store.one("SELECT COUNT(*) n FROM functions")["n"]
    assert net.store.conn.total_changes - before < functions


def test_splat_runs_tool_setup_and_extraction_before_build(tmp_path):
    if shutil.which('make') is None:
        pytest.skip('needs make')
    (tmp_path / 'game.yaml').write_text('options:\n  target_path: game.z64\nsegments: []\n')
    (tmp_path / 'Makefile').write_text(
        '.DEFAULT_GOAL := all\n'
        'setup:\n\t@echo setup >> order.txt\n'
        'extract:\n\t@test -f order.txt\n\t@touch game.ld\n\t@echo extract >> order.txt\n'
        'all:\n\t@test -f game.ld\n\t@echo build >> order.txt\n')
    for command in ForkProject(tmp_path).setup_commands():
        subprocess.run(command, cwd=tmp_path, check=True, capture_output=True, timeout=15)
    assert (tmp_path / 'order.txt').read_text().splitlines() == ['setup', 'extract', 'build']


def test_direct_splat_version_follows_rom_on_import_and_retry(tmp_path, monkeypatch):
    from decompnet.app.forkengine import ForkEngine

    (tmp_path / "Makefile").write_text('BASENAME := game\nVERSION := us\nextract:\n\tpython -m splat split $(BASENAME).$(VERSION).yaml\n')
    data = {v: b'\x80\x37\x12\x40' + v.encode() * 32 for v in ('eu', 'us')}
    for v, rom in data.items():
        (tmp_path / f'game.{v}.yaml').write_text(
            f'sha1: {hashlib.sha1(rom).hexdigest()}\noptions:\n  platform: n64\n  base_path: .\n'
            f'  target_path: baserom.{v}.z64\nsegments: []\n')
    fp = ForkProject(tmp_path)
    assert fp.splat_default_version() == 'us'  # Alphabetically first YAML is EU.
    assert fp.needs()['path'] == 'baserom.us.z64'
    cfg = {}
    engine = object.__new__(ForkEngine)
    monkeypatch.setattr(ForkEngine, '_save_cfg', lambda *_: None)
    game = tmp_path / 'upload.z64'
    game.write_bytes(data['eu'])
    engine._match_version(SimpleNamespace(), fp, cfg, game, log=lambda *_: None)
    assert cfg['version'] == 'eu'
    assert fp.place_game(game, game.name, cfg['version']).name == 'baserom.eu.z64'
    assert fp.setup_commands('eu')[0] == ['make', 'VERSION=eu', 'extract']
    assert fp.setup_commands('eu')[1][1] == 'VERSION=eu'

    # An existing import from the old app has the right ROM, but no saved version.
    cfg.clear()
    monkeypatch.setattr(ForkEngine, 'get', lambda *_: (SimpleNamespace(), fp, cfg))
    calls = []
    monkeypatch.setattr(ForkProject, 'setup', lambda _self, v, **_: calls.append(v))
    monkeypatch.setattr(ForkEngine, 'check', lambda *_ , **__: {'ready': True})
    assert engine.setup('example', None, log=lambda *_: None)['ready']
    assert calls == ['eu']
    assert cfg['version'] == 'eu'

    # A template that points outside the checkout is not inferred as a supported ROM edition.
    p = tmp_path / 'game.eu.yaml'
    p.write_text(p.read_text().replace('base_path: .', 'base_path: ..'))
    assert 'eu' not in fp.splat_baseroms()


def test_nested_splat_editions_use_rom_hash_for_build_and_reference_config(tmp_path, monkeypatch):
    import yaml

    from decompnet import consoles
    from decompnet.app.forkengine import ForkEngine

    (tmp_path/'Makefile').write_text('BASENAME := demo\nVERSION ?= us\n'
        'SPLAT_YAML := config/$(VERSION)/$(BASENAME).$(VERSION).yaml\nextract:\n\tpython -m splat split $(SPLAT_YAML)\n')
    data = {v: b'\x80\x37\x12\x40'+v.encode()*32 for v in ('jp','us')}
    for version, rom in data.items():
        path = tmp_path/f'config/{version}/demo.{version}.yaml'
        path.parent.mkdir(parents=True)
        path.write_text(yaml.safe_dump(dict(sha1=hashlib.sha1(rom).hexdigest(),
            options=dict(base_path='../..', target_path=f'baserom.{version}.z64', platform='n64'), segments=[])))
    fp = ForkProject(tmp_path)
    assert fp.needs()['path'] == 'baserom.us.z64'
    engine = object.__new__(ForkEngine)
    cfg = {}
    monkeypatch.setattr(ForkEngine, '_save_cfg', lambda *_: None)
    game = tmp_path/'upload.z64'
    game.write_bytes(data['jp'])
    engine._match_version(SimpleNamespace(), fp, cfg, game, log=lambda *_: None)
    assert cfg['version'] == 'jp'
    assert fp.place_game(game, game.name, 'jp').name == 'baserom.jp.z64'
    assert fp.setup_commands('jp')[0] == ['make','VERSION=jp','extract']
    seen = []
    monkeypatch.setattr(type(consoles.get('n64')), 'generate_objdiff',
                        lambda self, project, path, config, **kwargs: seen.append(path) or True)
    assert fp.generate_objdiff('us', log=lambda *_: None)
    assert seen == [tmp_path/'config/us/demo.us.yaml']
    bad = tmp_path/'config/jp/demo.jp.yaml'
    bad.write_text(bad.read_text().replace('../..','../../..'))
    assert 'jp' not in fp.splat_baseroms()


@needs_tools
def test_whole_image_rejects_a_function_match_that_changes_other_data(fork):
    """A function can match while its new globals change the linked image."""
    import sys

    net, slug, agent = fork
    _, fp, _ = net.forks.get(slug)
    unit = next(u for u in fp.units() if u.source == "src/game.c")
    assert not unit.complete
    source = fp.root / unit.source
    before = source.read_text()
    manifest_path = fp.root / ".decomp.wtf.json"
    manifest = manifest_path.read_text()
    from elftools.elf.elffile import ELFFile

    with unit.base.open("rb") as stream:
        elf = ELFFile(stream)
        material = b"".join(elf.get_section_by_name(n).data() for n in (".text", ".data", ".rodata")
                            if elf.get_section_by_name(n))
        bss = elf.get_section_by_name(".bss")
        common = sum(s["st_size"] for s in elf.get_section_by_name(".symtab").iter_symbols()
                     if s["st_shndx"] == "SHN_COMMON")
        material += (int(bss["sh_size"] if bss else 0) + common).to_bytes(4, "big")
    expected = hashlib.sha1(material).hexdigest()
    command = [sys.executable, "-c", "from pathlib import Path; from elftools.elf.elffile import ELFFile; "
               f"f=open({str(unit.base)!r}, 'rb'); e=ELFFile(f); "
               "data=b''.join(e.get_section_by_name(n).data() for n in ('.text','.data','.rodata') "
               "if e.get_section_by_name(n)); b=e.get_section_by_name('.bss'); "
               "common=sum(s['st_size'] for s in e.get_section_by_name('.symtab').iter_symbols() "
               "if s['st_shndx']=='SHN_COMMON'); "
               "Path('build/image.bin').write_bytes(data+(int(b['sh_size'] if b else 0)+common).to_bytes(4,'big'))"]
    fp.update_manifest(game={"path": "baserom.z64", "sha1": expected},
                       verify_image={"path": "build/image.bin", "commands": [command]})
    try:
        assert fp.verify()[0]
        result = net.forks.attempt(agent, slug, "clamp_s32", _ref("clamp_s32"),
                                   prelude="int extra_image_data;\n")
        assert not result["matched"] and "no longer builds byte for byte" in result["text"]
        assert source.read_text() == before
        assert fp.verify()[0]
        result = net.forks.attempt(agent, slug, "clamp_s32",
                                   "s32 clamp_s32(s32 v, s32 lo, s32 hi) { return v; }")
        assert not result["matched"] and "no longer builds byte for byte" in result["text"]
        assert source.read_text() == before and fp.verify()[0]
    finally:
        manifest_path.write_text(manifest)


@pytest.mark.parametrize("platform", ["n64", "psx", "ps2"])
def test_nested_splat_rom_uses_effective_yaml_base(tmp_path, platform):
    (tmp_path / 'Makefile').write_text('all:\n\t@true\n')
    folder = tmp_path / 'config/us'
    folder.mkdir(parents=True)
    config = folder / 'game.yaml'
    config.write_text(f"sha1: {'a' * 40}\noptions:\n  platform: {platform}\n"
                      "  base_path: .\n  target_path: baserom.z64\n  base_path: ../..\nsegments: []\n")
    project = ForkProject(tmp_path)
    assert project.needs()['path'] == 'baserom.z64'
    assert project.needs()['sha1'] == 'a' * 40
    config.write_text(config.read_text().replace('base_path: ../..', 'base_path: ../../..'))
    assert not project.needs()['auto']


def test_bundled_progress_detection_rejects_invalid_or_linked_manifests(tmp_path):
    progress = tmp_path / 'progress'
    progress.mkdir()
    manifest = progress / 'decompdecomp.json'
    manifest.write_text(json.dumps({'format': 1, 'game': {'sha1': 'a' * 40}}))
    needs = ForkProject(tmp_path).needs()
    assert needs['auto'] and needs['saved_progress'] and needs['sha1'] == 'a' * 40
    manifest.write_text('{bad')
    with pytest.raises(NetworkError, match='saved progress manifest'):
        ForkProject(tmp_path).needs()
    manifest.unlink()
    outside = tmp_path / 'outside.json'
    outside.write_text('{}')
    manifest.symlink_to(outside)
    with pytest.raises(NetworkError, match='symbolic links'):
        ForkProject(tmp_path).needs()


@needs_tools
def test_background_trial_preserves_another_agents_claim_and_unfinished_file(tmp_path_factory):
    net, slug, owner = fork.__wrapped__(tmp_path_factory)
    search_agent, _ = net.local_agent('deep-side-search')
    name = 'checksum_save'
    net.claim(owner, slug, name)
    fn = net._function(name, slug)
    claim = dict(net.store.one('SELECT * FROM claims WHERE function_id=?', fn['id']))
    _, fp, _ = net.forks.get(slug)
    path = fp.root / fp.unit(fn['unit']).source
    before = path.read_bytes()
    candidate = _ref(name).replace('return', 'return 1 +', 1)
    with pytest.raises(NetworkError, match='claimed by'):
        net.forks.attempt(search_agent, slug, name, candidate, keep=False)
    result = net.forks.attempt(search_agent, slug, name, candidate, keep=False, background=True)
    assert not result['matched']
    assert path.read_bytes() == before
    assert dict(net.store.one('SELECT * FROM claims WHERE function_id=?', fn['id'])) == claim
    with pytest.raises(NetworkError, match='restore unfinished source'):
        net.forks.attempt(search_agent, slug, name, candidate, background=True)


@needs_tools
def test_deep_imported_source_baseline_is_rebuilt_by_project_judge(fork):
    from decompnet.app import deep_permuter
    from decompnet.app.permute import Permuter, State
    net, slug, agent = fork
    proj, fp, _ = net.forks.get(slug)
    candidates = net.project_store(proj['id']).all("SELECT * FROM functions WHERE project_id=? AND status='open' AND best_score IS NOT NULL AND best_source IS NULL",proj['id'])
    fn = next(fn for fn in candidates if net.forks._current_source(fp, fn))
    before = {p:p.read_bytes() for p in fp.root.glob('src/*') if p.is_file()}
    prepared = deep_permuter.prepare_source(net, proj, fn)
    assert prepared['best_source'] and prepared['status'] == 'open'
    assert prepared['best_score'] == fn['best_score']
    assert all(p.read_bytes()==content for p,content in before.items())
    score, _ = Permuter(net)._baseline(agent, proj, prepared, State(), slug)
    assert score is not None


def test_file_scope_is_outside_every_block_but_a_namespace_or_extern_c():
    """A definition past a parked one is taken only at file scope: find() reads a call followed by a block as one."""
    text = 'namespace a {\nextern "C" {\nint f(void) {\n    if (x) {\n        g();\n    }\n}\n}\n}\nint h;\n'
    assert csource.at_file_scope(text, text.index("int f"))
    assert not csource.at_file_scope(text, text.index("g();"))
    assert not csource.at_file_scope(text, text.index("    }\n}"))
    assert csource.at_file_scope(text, text.index("int h"))
    call = "int t(int x) {\n    y = scale(x) * 2.0;\n    if (y) {\n        y = 1;\n    }\n}\n"
    assert csource.find(call, "scale") is None  # (decomp-repair's find rejects a call in an expression)
    assert not csource.at_file_scope(call, call.index("scale"))


def test_the_definition_the_build_sees_beside_a_parked_one_is_at_file_scope():
    from types import SimpleNamespace

    from decompnet.app.forkengine import ForkEngine

    fp = SimpleNamespace(never_defined=lambda names: set(names))  # (no build defines PARKED)
    text = "#ifdef PARKED\nint f(void) {\n    return 2;\n}\n#else\nint f(void) {\n    return 1;\n}\n#endif\n"
    seen = ForkEngine._seen_beside(fp, text, csource.find(text, "f"), "f", ["f"])
    assert seen is not None and text[seen[0]:seen[1]] == "int f(void) {\n    return 1;\n}\n"
    # past the parked copy, a call in g followed by a block isn't the build's definition
    text = "#ifdef PARKED\nint f(void) {\n    return 2;\n}\n#endif\nint g(void) {\n    x = f() * 2;\n    if (x) {\n    }\n}\n"
    assert ForkEngine._seen_beside(fp, text, csource.find(text, "f"), "f", ["f"]) is None
    # nor one at file scope, a C++ initialiser's (find() read `* 2.0;` as K&R declarations, up to twice's `{`): a
    # try on scale matched, and the file lost both bias and twice
    text = ('#ifdef PARKED_DRAFTS\nextern "C" double scale(double x) { return x * 1.2; }\n#endif\n'
            'extern "C" double scale(double x);\nstatic double bias = scale(2.0) * 2.0;\n'
            'extern "C" double twice(double x) { double y = 0.0; y = scale(x) * 2.0; if (y > 1.0) { y = 1.0; } '
            'return y; }\n')
    assert ForkEngine._seen_beside(fp, text, csource.find(text, "scale"), "scale", ["scale"]) is None
    # and of an #ifdef _WIN32 / #else pair (a macro the app can't tell), neither is surely the build's: the try went
    # into the _WIN32 copy, maybe the one the build leaves out, and its #else kept being judged
    text = ("#ifdef PARKED\nint f(void) {\n    return 2;\n}\n#endif\n#ifdef _WIN32\nint f(void) {\n    return 3;\n}\n"
            "#else\nint f(void) {\n    return 1;\n}\n#endif\n")
    assert ForkEngine._seen_beside(fp, text, csource.find(text, "f"), "f", ["f"]) is None
    # a K&R definition isn't taken either (what's between its parameters and its body reads like a call's)
    assert csource.opens_body("int f(a)\nint a;\n{\n}\n", (0, 20), "f") is False
    assert csource.opens_body("int Car::f(int a) const throw() {\n}\n", (0, 35), "Car::f")


def test_not_in_your_build_names_the_symbol_not_the_rows_unique_name():
    """A function whose row name was made unique with a trailing "'" (a re-sync after a split) was reported as "its
    object doesn't define _f@4'": agents spent days saying no C makes a name with a quote, and never looked further."""
    from types import SimpleNamespace

    from decompnet.app.forkengine import ForkEngine

    fn = {"name": "_f@4'", "meta": json.dumps({"symbol": "_f@4"}), "vram": None}
    note = ForkEngine._missing_note(SimpleNamespace(name="u", base=None), fn)
    assert "doesn't define _f@4," in note and "_f@4'" not in note


def test_a_projects_overview_claims_no_compiler_facts_where_its_build_decides(tmp_path):
    """A public decomp's own build sets its compilers: the overview adds no compiler facts at all, so the Settings
    panel stays hidden and a page reading a missing `compiler_map` as false says there's no map (steering.js). The
    Settings answer refuses rather than calling every fork's map MIPS; the map fact the GUI's wording follows is its
    own (`compiler_answer`), not the overview's."""
    from types import SimpleNamespace

    net = RomNetwork(tmp_path / "app.db", tmp_path / "ws")
    root = tmp_path / "ws" / "p"
    root.mkdir(parents=True)
    fp = SimpleNamespace(root=root, manifest={}, units=lambda: [])
    assert net.forks._compiler_facts(fp, {"platform": "gc"}) == {}
    net.forks._register("p", "P", "fork", root,
                        {"kind": "fork", "platform": "gc", "origin": "fork", "ready": True}, "x")
    with pytest.raises(NetworkError, match="this project's own build sets its compilers"):
        net.compiler_answer("p")


def test_a_join_a_console_cant_make_refuses_before_any_vote_is_counted(tmp_path):
    """A GameCube project has no join_functions yet: the refusal comes before the votes, so no agent's agreement is
    kept for a later version and joins_refused gets nothing either (only a real attempted join that fails does)."""
    net = RomNetwork(tmp_path / "app.db", tmp_path / "ws")
    root = tmp_path / "ws" / "p"
    root.mkdir(parents=True)
    (root / "objdiff.json").write_text(json.dumps({"units": []}))
    net.forks._register("p", "P", "fork", root,
                        {"kind": "fork", "platform": "gc", "origin": "scratch", "ready": True}, "x")
    with net.store.tx() as db:
        for k, name in enumerate(("f", "g")):
            db.execute("INSERT INTO functions(project_id,name,size,n_insns,status,unit,vram) "
                       "VALUES(1,?,8,2,'open','a',?)", (name, 0x80000000 + 8 * k))
    for who in ("opencode-beam-free-1", "opencode-beam-free-2", "claude-sonnet-5-5-1"):
        with pytest.raises(NetworkError, match="GameCube projects can't join functions yet"):
            net.forks.propose_join(net.local_agent(who)[0], "p", "g", "f", "f has no epilogue; g pops it")
    for row in net.store.all("SELECT meta FROM functions WHERE project_id=1"):
        assert not json.loads(row["meta"] or "{}").get("joins")
    cfg = json.loads(net.store.one("SELECT config FROM projects WHERE slug='p'")["config"])
    assert "joins_refused" not in cfg


def test_a_boundary_report_names_the_console_that_cant_cut_functions(tmp_path):
    """The old refusal said report_boundary works in "a PC game started from scratch" even in a GameCube or DS
    project: it names the project's own console now."""
    net = RomNetwork(tmp_path / "app.db", tmp_path / "ws")
    root = tmp_path / "ws" / "p"
    root.mkdir(parents=True)
    (root / "objdiff.json").write_text(json.dumps({"units": []}))
    net.forks._register("p", "P", "fork", root,
                        {"kind": "fork", "platform": "gc", "origin": "scratch", "ready": True}, "x")
    with net.store.tx() as db:
        db.execute("INSERT INTO functions(project_id,name,size,n_insns,status,unit,vram) "
                   "VALUES(1,'f',16,4,'open','a',0x80000000)")
    agent, _ = net.local_agent("claude-sonnet-5-5-1")
    with pytest.raises(NetworkError, match="GameCube projects can't report function ends yet"):
        net.forks.report_boundary(agent, "p", "f", "0x80000008", "padding")


def test_a_unit_whose_build_step_fails_counts_as_not_building(tmp_path):
    """A unit built in steps (here a part compiled, then linked into the unit's object) reports only the step that
    failed: ninja never tries the object. Counted by the object alone, an x86 game's header edits that broke a step
    were kept as breaking nothing, and the next compile there was told the file had been broken before it came."""
    import sys

    from decompnet.forks.project import ForkProject, _tool

    if _tool("ninja") is None:
        pytest.skip("needs ninja")
    (tmp_path / "src").mkdir()
    (tmp_path / "step.py").write_text(
        "import shutil, sys\n"
        "if 'BROKEN' in open(sys.argv[1]).read():\n    sys.exit('error: a broken step')\n"
        "shutil.copy(sys.argv[1], sys.argv[2])\n")
    for name, text in (("a", "fine\n"), ("b", "BROKEN\n"), ("c", "fine\n")):
        (tmp_path / "src" / f"{name}.c").write_text(text)
    py = sys.executable.replace("\\", "/")
    rules = f'rule step\n  command = "{py}" step.py $in $out\n'
    for name in "abc":
        rules += f"build build/{name}/part.o: step src/{name}.c\nbuild build/{name}.o: step build/{name}/part.o\n"
    rules += "build all_source: phony build/a.o build/b.o build/c.o\n"
    (tmp_path / "build.ninja").write_text(rules)
    (tmp_path / "objdiff.json").write_text(json.dumps({"custom_make": "ninja", "units": [
        {"name": n, "target_path": f"expected/{n}.o", "base_path": f"build/{n}.o",
         "metadata": {"source_path": f"src/{n}.c"}} for n in "abc"]}))
    fp = ForkProject(tmp_path)
    failed: list[str] = []
    ok, _ = fp.build_all(failed=failed)
    assert not ok and [Path(t).as_posix() for t in failed] == ["build/b/part.o"]  # the step, not the object
    assert fp.failed_units(failed) == {"b"}
    assert fp.failed_units([]) == set()
    # the build file custom_args names is the one queried (the query ran on build.ninja: no unit failed)
    (tmp_path / "build.ninja").rename(tmp_path / "other.ninja")
    cfg = json.loads((tmp_path / "objdiff.json").read_text())
    (tmp_path / "objdiff.json").write_text(json.dumps({**cfg, "custom_args": ["-f", "other.ninja"]}))
    shutil.rmtree(tmp_path / "build", ignore_errors=True)
    fp = ForkProject(tmp_path)
    failed = []
    ok, _ = fp.build_all(failed=failed)
    assert not ok and fp.failed_units(failed) == {"b"}
    # a build run in a folder of its own (`-C sub`) names its targets from there: b/part.o for sub/b/part.o, which
    # matched no unit's object; and `ninja -C sub sub/a.o` was an unknown target
    (tmp_path / "other.ninja").unlink()
    (tmp_path / "sub").mkdir()
    rules = f'rule step\n  command = "{py}" ../step.py $in $out\n'
    for name in "abc":
        rules += f"build {name}/part.o: step ../src/{name}.c\nbuild {name}.o: step {name}/part.o\n"
    (tmp_path / "sub" / "build.ninja").write_text(rules + "build all_source: phony a.o b.o c.o\n")
    (tmp_path / "objdiff.json").write_text(json.dumps({**cfg, "custom_args": ["-C", "sub"], "units": [
        {**u, "base_path": f"sub/{u['name']}.o"} for u in cfg["units"]]}))
    fp = ForkProject(tmp_path)
    failed = []
    ok, _ = fp.build_all(failed=failed)
    assert not ok and [Path(t).as_posix() for t in failed] == ["b/part.o"] and fp.failed_units(failed) == {"b"}
    assert fp.build_unit(fp.unit("a"))[0] and (tmp_path / "sub" / "a.o").exists()


def _steps_project(tmp_path: Path, broken: str = "b"):
    """Units a, b, c, d, each built in two steps (a part, then its object); the step of the units in `broken` fails
    with an error naming its file."""
    import sys

    from decompnet.forks.project import ForkProject

    (tmp_path / "src").mkdir()
    (tmp_path / "step.py").write_text(
        "import shutil, sys\n"
        "if 'BROKEN' in open(sys.argv[1]).read():\n    print(sys.argv[1] + ':1: error: a broken step')\n"
        "    sys.exit(1)\n"
        "shutil.copy(sys.argv[1], sys.argv[2])\n")
    for name in "abcd":
        (tmp_path / "src" / f"{name}.c").write_text("BROKEN\n" if name in broken else "fine\n")
    py = sys.executable.replace("\\", "/")
    rules = f'rule step\n  command = "{py}" step.py $in $out\n'
    for name in "abcd":
        rules += f"build build/{name}/part.o: step src/{name}.c\nbuild build/{name}.o: step build/{name}/part.o\n"
    (tmp_path / "build.ninja").write_text(rules)
    (tmp_path / "objdiff.json").write_text(json.dumps({"custom_make": "ninja", "units": [
        {"name": n, "target_path": f"expected/{n}.o", "base_path": f"build/{n}.o",
         "metadata": {"source_path": f"src/{n}.c"}} for n in "abcd"]}))
    return ForkProject(tmp_path)


def test_units_built_in_one_run_each_hear_only_their_own_failure(tmp_path, monkeypatch):
    """A project's unit builds asked for together go in one run of ninja (half a unit's build is the run's own
    cost), and each unit gets what building it alone would say: a unit ninja says is built is built, and one that
    isn't is built alone, so a unit whose step failed sees that failure (and buildhelp's hints), never another
    unit's."""
    fp = _steps_project(tmp_path, broken="bd")
    runs = []
    execute = fp._execute
    monkeypatch.setattr(fp, "_execute", lambda cmd, *a, **k: runs.append(cmd) or execute(cmd, *a, **k))
    results = fp.build_units([fp.unit(n) for n in "abcd"])
    assert len([c for c in runs if "-k" in c]) == 1  # one run, going on past a failure
    assert len([c for c in runs if "-n" in c]) == 4  # (ninja asked which units are built)
    assert results == [(True, ""), results[1], (True, ""), results[3]] and not results[1][0] and not results[3][0]
    assert results[1] == fp.build_unit(fp.unit("b")) and results[3] == fp.build_unit(fp.unit("d"))
    assert "src/b.c:1: error: a broken step" in results[1][1] and "d.c" not in results[1][1]
    assert "src/d.c:1: error: a broken step" in results[3][1] and "b.c" not in results[3][1]
    assert results[1][1].count("error: a broken step") == 1
    assert (tmp_path / "build/a.o").is_file() and (tmp_path / "build/c.o").is_file()
    # one unit builds alone, as it always did
    runs.clear()
    assert fp.build_units([fp.unit("b")]) == [fp.build_unit(fp.unit("b"))] and len(runs) == 2


def test_a_unit_without_a_failure_of_its_own_is_built_only_when_ninja_says_so(tmp_path, monkeypatch):
    """A run can stop short of a unit without a failure that names it (the memory cap ends the whole run, a
    dependency walk cut short): reported built, its old object was judged as its new file's. Ninja is asked; a
    unit it would still build is built alone, and says what it says."""
    import sys

    fp = _steps_project(tmp_path, broken="b")
    py = sys.executable.replace("\\", "/")
    rules = (f'rule step\n  command = "{py}" step.py $in $out\n'
             "build build/a/part.o: step src/a.c\nbuild build/a.o: step build/a/part.o\n"
             "build build/b.o: step src/b.c\n"
             "build build/c/part.o: step src/c.c\nbuild build/c.o: step build/c/part.o | build/b.o\n")
    (tmp_path / "build.ninja").write_text(rules)
    monkeypatch.setattr(fp, "_made_from", lambda targets: set())  # (a walk that found nothing above the step)
    alone = []
    build_unit = fp.build_unit
    monkeypatch.setattr(fp, "build_unit", lambda u, **k: alone.append(u.name) or build_unit(u, **k))
    results = fp.build_units([fp.unit(n) for n in "abc"])
    assert results[0] == (True, "")  # a is built, ninja says: it isn't built again
    assert not results[1][0] and "src/b.c:1: error: a broken step" in results[1][1]
    assert not results[2][0] and alone == ["b", "c"]  # c needs b's object: ninja didn't build it, so it isn't built
    # ninja's progress lines as a user set them, or none (a project's --quiet), don't make a unit built
    from decompnet.forks.project import _tool

    ninja = [_tool("ninja") or "ninja"]
    monkeypatch.setenv("NINJA_STATUS", "")
    assert not fp._no_work([*ninja, "--quiet"], ["build/c.o"])
    assert fp._no_work(ninja, ["build/a.o"])


def test_a_run_ended_by_the_memory_cap_says_nothing_of_its_units(tmp_path, monkeypatch):
    """The memory cap covers the whole run of several units and kills whichever step is biggest: that step's
    failure isn't its unit's own. A unit ninja says is built is; the one it killed is built alone (and gets its own
    verdict there); and a unit whose own build then raises gets the exception alone."""
    from decompnet.forks import project

    fp = _steps_project(tmp_path, broken="bc")
    monkeypatch.setattr(project, "killed_for_memory", lambda code: code not in (0, None))
    build_unit = fp.build_unit

    def alone(u, **k):
        if u.name == "c":
            raise PermissionError("ninja.exe is in use")
        return build_unit(u, **k)

    monkeypatch.setattr(fp, "build_unit", alone)
    results = fp.build_units([fp.unit(n) for n in "abc"])
    assert results[0] == (True, "") and not results[1][0] and isinstance(results[2], PermissionError)
    assert "more memory than decompdecomp allows" in results[1][1]


def _running(pid: int) -> bool:
    import os

    if os.name == "nt":
        listed = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return f" {pid} " in listed
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def test_a_unit_build_that_hangs_with_its_output_open_is_stopped_with_its_compiler(tmp_path, monkeypatch):
    """A compiler stuck under wibo kept a unit's build, and the project's build lock, forever: its output was read
    to the end before the time limit was looked at. Stopped, ninja alone went: the compiler it started ran on."""
    import sys
    import time

    from decompnet.forks import project

    fp = _steps_project(tmp_path, broken="")
    py = sys.executable.replace("\\", "/")
    pid_file = tmp_path / "compiler.pid"
    (tmp_path / "hang.py").write_text(f"import os, time\nopen({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
                                      "print('compiling', flush=True)\ntime.sleep(60)\n")
    (tmp_path / "build.ninja").write_text(f'rule hang\n  command = "{py}" hang.py\nbuild build/a.o: hang src/a.c\n'
                                          "build build/b.o: hang src/b.c\n")
    monkeypatch.setattr(project, "UNIT_SECONDS", 2)
    started = time.monotonic()
    ok, said = fp.build_unit(fp.unit("a"))
    assert not ok and "didn't finish in 2 seconds" in said  # (ninja holds a step's output until it ends)
    assert time.monotonic() - started < 20
    assert not _running(int(pid_file.read_text()))
    # a run of builds out of time: which unit hung isn't known, so each is built alone, with the whole limit
    alone = []
    build_unit = fp.build_unit
    monkeypatch.setattr(fp, "build_unit", lambda u, **k: alone.append(u.name) or build_unit(u, **k))
    results = fp.build_units([fp.unit("a"), fp.unit("b")])
    assert alone == ["a", "b"] and all(not ok and "didn't finish" in said for ok, said in results)
    assert not _running(int(pid_file.read_text()))


@pytest.mark.skipif(sys.platform == "win32", reason="Windows stops the whole tree by force (taskkill /T /F)")
def test_a_compiler_that_ignores_being_asked_to_stop_is_killed_at_the_time_limit(tmp_path, monkeypatch):
    """ninja runs each command in a process group of its own: the step's group was killed, and a compiler that
    ignored SIGTERM outlived its build."""
    import sys
    import time

    from decompnet.forks import project

    fp = _steps_project(tmp_path, broken="")
    pid_file = tmp_path / "compiler.pid"
    (tmp_path / "stubborn.py").write_text(f"import os, signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                                          f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))\ntime.sleep(60)\n")
    (tmp_path / "build.ninja").write_text(f'rule hang\n  command = "{sys.executable}" stubborn.py\n'
                                          "build build/a.o: hang src/a.c\n")
    monkeypatch.setattr(project, "UNIT_SECONDS", 2)
    started = time.monotonic()
    ok, said = fp.build_unit(fp.unit("a"))
    assert not ok and "didn't finish in 2 seconds" in said and time.monotonic() - started < 20
    pid = int(pid_file.read_text())
    for _ in range(20):  # (killed, it's gone once its new parent reaps it)
        if not _running(pid):
            break
        time.sleep(0.1)
    assert not _running(pid)


def test_a_step_that_prints_another_units_failure_doesnt_fail_that_unit(tmp_path):
    """Each unit's verdict was read from what the run printed: a step's own output saying `FAILED: build/a.o`
    failed unit a, which builds alone."""
    import sys

    fp = _steps_project(tmp_path, broken="b")
    py = sys.executable.replace("\\", "/")
    (tmp_path / "liar.py").write_text("import shutil, sys\nif 'BROKEN' in open(sys.argv[1]).read():\n"
                                      "    print('FAILED: build/a.o')\n    sys.exit(1)\nshutil.copy(sys.argv[1], sys.argv[2])\n")
    (tmp_path / "build.ninja").write_text(f'rule step\n  command = "{py}" liar.py $in $out\n'
                                          "build build/a.o: step src/a.c\nbuild build/b.o: step src/b.c\n")
    assert fp.build_unit(fp.unit("a"))[0]
    results = fp.build_units([fp.unit("a"), fp.unit("b")])
    assert results[0] == (True, "") and not results[1][0]


def test_a_failed_step_that_makes_two_files_is_one_failure_in_the_log(tmp_path):
    import sys

    fp = _steps_project(tmp_path, broken="b")
    py = sys.executable.replace("\\", "/")
    rules = (f'rule step\n  command = "{py}" step.py $in $out\n'
             "build build/a/part.o: step src/a.c\nbuild build/a.o: step build/a/part.o\n"
             "build build/b/part.o build/b/part.map: step src/b.c\n"
             "build build/b.o: step build/b/part.o | build/b/part.map\n")
    (tmp_path / "build.ninja").write_text(rules)
    results = fp.build_units([fp.unit(n) for n in "ab"])
    assert results[0] == (True, "") and not results[1][0]
    assert results[1][1].count("error: a broken step") == 1


def test_a_run_that_stops_before_building_builds_each_unit_alone(tmp_path):
    """ninja stops before it builds anything when the build file can't be brought up to date (configure failing)
    or a target has no rule: no failure says whose it is, so each unit is built alone and says what it says."""
    import os
    import sys
    import time

    fp = _steps_project(tmp_path, broken="")
    py = sys.executable.replace("\\", "/")
    (tmp_path / "regen.py").write_text("import sys\nprint('configure: a driver it cannot read')\nsys.exit(1)\n")
    text = (tmp_path / "build.ninja").read_text()
    (tmp_path / "build.ninja").write_text(
        f'rule regen\n  command = "{py}" regen.py\n  generator = 1\nbuild build.ninja: regen facts.txt\n{text}')
    (tmp_path / "facts.txt").write_text("changed\n")
    os.utime(tmp_path / "facts.txt", (time.time() + 5, time.time() + 5))  # newer than build.ninja
    results = fp.build_units([fp.unit("a"), fp.unit("b")])
    assert [ok for ok, _ in results] == [False, False]
    assert all("configure: a driver it cannot read" in log for _, log in results)


def test_builds_asked_for_during_a_run_go_together_in_the_next():
    """The permuter's four workers and the agents took turns one unit at a time. A build asked for while a run
    goes waits for the next run (this one may be building an older copy of its file), together with the others
    asked for meanwhile; an error reaches each of its run, and the next run goes on."""
    import threading
    import time

    from decompnet.app.forkengine import _UnitBuilds

    # a unit whose own build raised (build_units puts the exception in its place): its caller alone hears it
    own = _UnitBuilds()
    assert own.build("a", lambda units: [(True, "")] if units == ["a"] else None) == (True, "")
    try:
        _UnitBuilds().build("b", lambda units: [OSError("b's own")])
        raise AssertionError("b's error was lost")
    except OSError as e:
        assert str(e) == "b's own" and e.__cause__ is None

    builds, runs = _UnitBuilds(), []
    first, release = threading.Event(), threading.Event()

    def run(units):
        runs.append(list(units))
        if units == ["a"]:
            first.set()
            assert release.wait(10)
        if "boom" in units:
            raise OSError("the build stopped")
        return [(True, f"built {u}") for u in units]

    got = {}

    def ask(u):
        try:
            got[u] = builds.build(u, run)
        except OSError as e:
            got[u] = e

    threads = [threading.Thread(target=ask, args=("a",))]
    threads[0].start()
    assert first.wait(10)
    for u in "bcd":  # while a's run goes
        threads.append(threading.Thread(target=ask, args=(u,)))
        threads[-1].start()
    deadline = time.monotonic() + 10
    while len(builds._queue) < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    release.set()
    for t in threads:
        t.join(10)
    assert runs[0] == ["a"] and sorted(runs[1]) == ["b", "c", "d"] and len(runs) == 2
    assert got == {u: (True, f"built {u}") for u in "abcd"}
    t = threading.Thread(target=ask, args=("boom",))
    t.start()
    t.join(10)
    assert isinstance(got["boom"], OSError) and builds.build("e", run) == (True, "built e")
    # every unit of a run that raised hears it, each thread with an exception of its own
    first.clear()
    release.clear()

    def slow_boom(units):
        if not first.is_set():
            first.set()
            assert release.wait(10)
            return [(True, "")] * len(units)
        raise OSError("the build stopped")

    errors = {}

    def ask_boom(u):
        try:
            builds.build(u, slow_boom)
        except OSError as e:
            errors[u] = e

    threads = [threading.Thread(target=ask_boom, args=(u,)) for u in ("x", "y", "z")]
    threads[0].start()
    assert first.wait(10)
    for t in threads[1:]:
        t.start()
    deadline = time.monotonic() + 10
    while len(builds._queue) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    release.set()
    for t in threads:
        t.join(10)
    assert set(errors) == {"y", "z"} and errors["y"] is not errors["z"]
    assert errors["y"].__cause__ is errors["z"].__cause__ and str(errors["y"]) == "the build stopped"


def test_which_if_branch_leaves_code_out_is_told_only_when_certain():
    """An x86 game's agents parked drafts in `#if defined(SOME_FLAG)` blocks no build defines; the brief showed them as
    current and every compile wrote into them, judged on other code. A test of a macro that may be defined (the
    compiler's own, one the build passes) hides nothing: only what is certain is said."""
    text = ("int a(void) { return 0; }\n"
            "#if defined(DRAFTS) || defined(MORE_DRAFTS) // parked\n"
            "int b(void) { return 1; }\n"
            "#elif defined(_M_IX86)\n"
            "int c(void) { return 2; }\n"
            "#else\n"
            "int d(void) { return 3; }\n"
            "#endif\n"
            "#ifndef DRAFTS\n"
            "int e(void) { return 4; }\n"
            "#endif\n"
            "#if 0\n"
            "/* #endif */ int f(void) { return 5; }\n"
            "#endif\n"
            "#if VERSION >= 2\n"
            "int g(void) { return 6; }\n"
            "#endif\n")
    at = {n: text.index(f"int {n}(") for n in "abcdefg"}
    undefined = {"DRAFTS", "MORE_DRAFTS"}
    assert csource.condition_names(text, at["d"]) == {"DRAFTS", "MORE_DRAFTS", "_M_IX86"}
    hidden = {n: csource.hidden_by(text, at[n], undefined) for n in at}
    assert hidden["b"] == (2, "#if defined(DRAFTS) || defined(MORE_DRAFTS) // parked")
    assert hidden["a"] is None and hidden["e"] is None
    assert hidden["c"] is None and hidden["d"] is None  # _M_IX86 may be defined: either branch may be built
    assert hidden["f"] == (12, "#if 0")  # an #endif in a comment ends nothing
    assert hidden["g"] is None  # a comparison can't be told
    assert csource.hidden_by(text, at["b"], {"DRAFTS"}) is None  # MORE_DRAFTS may be defined
    # with every macro known, each branch is told: the #elif's is out, the #else's is built
    assert csource.hidden_by(text, at["c"], undefined | {"_M_IX86"}) == (4, "#elif defined(_M_IX86)")
    assert csource.hidden_by(text, at["d"], undefined | {"_M_IX86"}) is None


def test_a_macro_counts_as_never_defined_only_when_nothing_could_define_it(tmp_path):
    from decompnet.app.forkengine import ForkEngine
    from decompnet.forks.project import ForkProject

    (tmp_path / "src").mkdir()
    (tmp_path / "include").mkdir()
    (tmp_path / "build").mkdir()
    (tmp_path / "src" / "a.c").write_text("#ifdef PARKED\nint f(void) { return 1; }\n#endif\n#if HAS_SOUND\n#endif\n")
    (tmp_path / "include" / "cfg.h").write_text("#define HAS_SOUND 1\n")
    (tmp_path / "build.ninja").write_text("cflags = -DFROM_BUILD /DFROM_MSVC\n")
    (tmp_path / "build" / "flags.txt").write_text("#define PARKED\n")  # build output isn't the project's
    fp = ForkProject(tmp_path)
    names = {"PARKED", "HAS_SOUND", "FROM_BUILD", "FROM_MSVC", "_WIN32", "mips", "WIN32"}
    assert fp.never_defined(names) == {"PARKED"}
    text = (tmp_path / "src" / "a.c").read_text()
    assert ForkEngine._hidden(fp, text, text.index("int f")) == (1, "#ifdef PARKED")
    (tmp_path / "include" / "cfg.h").write_text("#define PARKED\n")
    assert ForkEngine._hidden(fp, text, text.index("int f")) is None
    # a header the build generates (build/config.h, which a source includes) can define one: it was taken for never
    # defined, and code the build compiles for hidden
    (tmp_path / "build" / "gen").mkdir()
    (tmp_path / "build" / "gen" / "config.h").write_text("#define HAS_FEATURE 1\n")
    assert fp.never_defined({"HAS_FEATURE", "NOWHERE"}) == {"NOWHERE"}


@pytest.mark.parametrize("macro", ["MACH", "R5900"])
def test_code_under_a_macro_the_compiler_defines_isnt_hidden(tmp_path, macro):
    """The pinned KMC GCC 2.7.2 defines MACH and EE-GCC 2.96 R5900 (their `-dM -E`): code under `#ifdef MACH` was
    said to be left out of the build, though it's compiled."""
    from decompnet.app.forkengine import ForkEngine
    from decompnet.forks.project import ForkProject

    (tmp_path / "src").mkdir()
    text = f"#ifdef {macro}\nint f(void) {{ return 1; }}\n#endif\n"
    (tmp_path / "src" / "a.c").write_text(text)
    assert ForkEngine._hidden(ForkProject(tmp_path), text, text.index("int f")) is None


def test_splat_project_without_a_split_target_runs_its_bundled_splat_for_the_assets(tmp_path):
    (tmp_path / "Makefile").write_text("B_FILES := $(wildcard assets/*.bin)\nall:\n\ttrue\n")
    (tmp_path / "game.yaml").write_text("options:\n  platform: n64\n  target_path: baserom.z64\nsegments: []\n")
    (tmp_path / "tools" / "splat").mkdir(parents=True)
    (tmp_path / "tools" / "splat" / "split.py").write_text("")
    first = ForkProject(tmp_path).setup_commands()[0]
    assert first[1:] == ["tools/splat/split.py", "game.yaml"]
    (tmp_path / "assets").mkdir()
    assert ForkProject(tmp_path).setup_commands()[0][0] == "make"


def test_splat_project_links_bin_pieces_from_a_bundled_splat_and_forgives_its_closing_statistics(tmp_path):
    (tmp_path / "Makefile").write_text("BIN_DIRS = bin\nall:\n\ttrue\n")
    (tmp_path / "game.yaml").write_text("options:\n  platform: n64\n  target_path: baserom.z64\nsegments: []\n")
    (tmp_path / "splat").mkdir()
    (tmp_path / "splat" / "split.py").write_text("")
    assert ForkProject(tmp_path).setup_commands()[0] == ["python", "splat/split.py", "game.yaml"]


def test_a_project_that_names_its_originals_by_md5_in_baseroms_folders(tmp_path):
    for version, digest in (("ntsc-1.2", "a" * 32), ("gc-us-mq", "b" * 32)):
        folder = tmp_path / "baseroms" / version
        folder.mkdir(parents=True)
        (folder / "checksum-compressed.md5").write_text(f"{digest}  build/{version}/x-compressed.z64\n")
    (tmp_path / "Makefile").write_text("VERSION ?= gc-us-mq\nREGION ?= US\nall:\n\t@true\nsetup:\n\t@true\n"
                                       "ROM := baseroms/$(VERSION)/baserom.z64\n")
    project = ForkProject(tmp_path, create_state=False)
    found = project.md5_roms()
    assert found["ntsc-1.2"] == ("baseroms/ntsc-1.2/baserom.z64", {"a" * 32})
    need = project.needs("ntsc-1.2")
    assert need["path"] == "baseroms/ntsc-1.2/baserom.z64" and need["sha1"] is None
    assert {v["version"] for v in need["md5_versions"]} == {"ntsc-1.2", "gc-us-mq"}
    assert project.setup_commands("ntsc-1.2")[-1][:2] == ["make", "VERSION=ntsc-1.2"]


def test_region_and_revision_versions_still_split_for_make(tmp_path):
    (tmp_path / "Makefile").write_text("REGION ?= us\nVERSION ?= v80\nall:\n\t@true\n")
    project = ForkProject(tmp_path, create_state=False)
    assert project.setup_commands("us.v80")[-1][:3] == ["make", "REGION=us", "VERSION=v80"]


def test_md5_table_in_the_readme_names_versions_and_the_roms_file(tmp_path):
    (tmp_path / "Makefile").write_text("ROMID ?= ntsc-final\nextract:\n\t@true\nrom:\n\t@true\n")
    (tmp_path / "README.md").write_text(
        "| ROM ID | Description | MD5 Checksum |\n|---|---|---|\n"
        f"| ntsc-final | NTSC | {'c' * 32} |\n| pal-final | PAL | {'d' * 32} |\n\n"
        "Save your ROM as `pd.ntsc-final.z64`.\n")
    project = ForkProject(tmp_path, create_state=False)
    assert project.md5_roms()["pal-final"] == ("pd.pal-final.z64", {"d" * 32})
    assert project.needs("pal-final")["path"] == "pd.pal-final.z64"
    assert ["make", "ROMID=pal-final", "extract"] in project.setup_commands("pal-final")


def test_one_sha1_file_per_version_with_a_baserom_per_version(tmp_path):
    (tmp_path / "Makefile").write_text("VERSION ?= us\nall:\n\t@true\n")
    (tmp_path / "README.md").write_text("Place a ROM called `baserom.<VERSION>.z64` in the project folder.\n")
    (tmp_path / "game.us.sha1").write_text(f"{'1' * 40}  build/us/game.us.z64\n")
    (tmp_path / "game.jp.sha1").write_text(f"{'2' * 40}  build/jp/game.jp.z64\n")
    project = ForkProject(tmp_path, create_state=False)
    assert project.rom_originals() == {"us": "1" * 40, "jp": "2" * 40}
    need = project.needs("jp")
    assert need["path"] == "baserom.jp.z64" and need["sha1"] == "2" * 40


def test_decomp_yaml_hash_comes_from_the_versions_own_checksum_file(tmp_path):
    (tmp_path / "decomp.yaml").write_text(
        "name: g\nversions:\n- name: us\n  paths:\n    baserom: ver/us/baserom.z64\n"
        "- name: jp\n  paths:\n    baserom: ver/jp/baserom.z64\n")
    (tmp_path / "ver" / "us").mkdir(parents=True)
    (tmp_path / "ver" / "us" / "checksum.sha1").write_text(f"{'3' * 40}  ver/us/build/g.z64\n")
    roms = ForkProject(tmp_path, create_state=False).decomp_yaml_roms()
    assert roms["us"] == ("ver/us/baserom.z64", "3" * 40) and roms["jp"][1] is None


def test_a_root_configure_script_with_ninja_is_the_build_of_a_makefile_less_project(tmp_path):
    (tmp_path / "configure").write_text("#!/bin/sh\n")
    steps = ForkProject(tmp_path, create_state=False).setup_commands()
    assert steps[0] == [str(tmp_path / "configure")] and Path(steps[1][0]).name == "ninja"


def test_make_is_told_the_iconv_decompdecomp_has_when_the_makefile_wants_homebrews(tmp_path, monkeypatch):
    from decompnet.forks import project as module

    (tmp_path / "Makefile").write_text("ICONV := $(shell brew --prefix)/opt/libiconv/bin/iconv\n")
    shim = tmp_path / "tools"
    shim.mkdir()
    (shim / "iconv").write_text("#!/bin/sh\n")
    original = module.Path.is_file
    monkeypatch.setattr(module.Path, "is_file",
                        lambda self: False if str(self).endswith("opt/libiconv/bin/iconv") else original(self))
    assert module._iconv_override(tmp_path, [shim], "") == str(shim / "iconv")
    (tmp_path / "Makefile").write_text("ICONV := iconv\n")
    assert module._iconv_override(tmp_path, [shim], "") is None


def test_a_mac_gets_a_stand_in_for_a_linux_only_header_a_project_tool_includes(tmp_path):
    """Perfect Dark's tools/mkrom includes <crypt.h> (glibc's) from its gzip code and uses nothing in it."""
    from decompnet.forks import project as module

    (tmp_path / "tools/mkrom").mkdir(parents=True)
    (tmp_path / "tools/mkrom/gzip_bits.c").write_text('#include "gzip.h"\n#include "crypt.h"\n')
    folder = module._mac_host_headers(tmp_path)
    assert folder and (folder / "crypt.h").is_file()
    (tmp_path / "tools/mkrom/crypt.h").write_text("")  # (a project that ships its own needs no stand-in)
    (tmp_path / ".decomp-wtf").rename(tmp_path / "gone")
    assert module._mac_host_headers(tmp_path) is None


def test_a_gmake_failure_line_names_its_target():
    from decompnet.forks.project import _FAILED

    match = _FAILED.match("gmake: *** [Makefile:582: build/us/levels/bbh/leveldata.elf] Error 1")
    assert match and match.group(2) == "build/us/levels/bbh/leveldata.elf"


def test_a_splat_config_per_version_in_ver_is_found(tmp_path):
    """Paper Mario keeps ver/<version>/splat.yaml (beside splat-debug.yaml and splat-shift.yaml, which are not it)."""
    from decompnet.forks.project import ForkProject

    (tmp_path / "ver/us").mkdir(parents=True)
    (tmp_path / "ver/us/splat.yaml").write_text("options:\n  platform: n64\nsegments: []\n")
    (tmp_path / "ver/us/splat-debug.yaml").write_text("options:\n  platform: n64\nsegments: []\n")
    assert ForkProject(tmp_path)._splat_yaml() == tmp_path / "ver/us/splat.yaml"
