"""Fork mode: continue a public decomp inside its own source tree, judged by objdiff.

projects/demo-fork is a small project in the shape community decomps use (objdiff.json, ninja build, partly
decompiled src/), for Demo Quest 64. The test clones it like a repository from decomp.dev, adds the game,
builds it and has an agent work through the guarded tools.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from decompnet import toolchains
from decompnet.app.engine import RomNetwork
from decompnet.forks import csource
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
    with pytest.raises(NetworkError, match="sha1"):
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
    assert d["status"] == "matched" and "slt" in d["asm"] and "clamp_s32" in d["source"]


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
    monkeypatch.setattr(scratch_pe, "tool_args", lambda: ["--msvc", "/here/msvc", "--wibo", "/here/wibo"])
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
    eng.net = SimpleNamespace(store=store, _event=lambda db, pid, aid, kind, detail: db.execute(
        "INSERT INTO events(project_id,agent_id,kind,detail,created_at) VALUES(?,?,?,?,0)", (pid, aid, kind, detail)))
    eng._apply_scores({"id": 7, "root": str(tmp_path)}, "u", {"_F": 52.7, "_G": 90.0}, via="unit diff before a compile")
    rows = {r["name"]: r for r in store.all("SELECT name, status, best_score FROM functions")}
    assert rows["F"]["status"] == "open" and rows["F"]["best_score"] > 0  # reopened now
    assert rows["G"]["best_score"] > 0  # reopened before the fix: mended at the next measurement
    # and the reopen is in the events, with what measured it and how much it scored
    events = [(r["kind"], r["detail"]) for r in store.all("SELECT kind, detail FROM events")]
    assert events == [("reopen", "F (unit diff before a compile: 52.70%)")]


@needs_tools
def test_a_sync_with_nothing_new_leaves_the_functions_alone(fork):
    # SQLite writes a row's pages even when an UPDATE sets the same values: 34,000 functions on every sync
    net, slug, _ = fork
    net.forks.sync(slug, log=lambda *_: None)
    before = net.store.conn.total_changes
    net.forks.sync(slug, log=lambda *_: None)
    functions = net.store.one("SELECT COUNT(*) n FROM functions")["n"]
    assert net.store.conn.total_changes - before < functions
