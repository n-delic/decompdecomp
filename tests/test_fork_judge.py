"""What counts as a match in a fork, and what may never block one.

The original object here is what a split of the game gives (dtk, splat): its literals carry the original's names
and sit where the whole file put them (lbl_80012348 behind constants of functions nobody has decompiled yet),
while the compiler names the decompiled function's literals itself and starts them at 0. objdiff's progress report
counts such a function as matched; every judgement in decompdecomp has to agree, or a function reads 99.9% forever
and the guards reject every change next to it.
"""

import json
import os
import posixpath
import shutil
import subprocess
from pathlib import Path

import pytest

from decompnet import toolchains
from decompnet.app.engine import RomNetwork
from decompnet.compiler import run_in
from decompnet.project import compiler_config

from .handmade import write_compiler

pytestmark = pytest.mark.skipif(
    toolchains.installed("gcc-2.7.2-kmc") is None or toolchains.installed("objdiff-cli") is None
    or not shutil.which("git"), reason="needs gcc-2.7.2-kmc, objdiff-cli and git")

TARGET = """\
	.set noreorder
	.set noat
	.section .rodata
	.align 3
	.word 0x40000000, 0
	.globl lbl_80012348
lbl_80012348:
	.word 0x3FF19999, 0x9999999A
	.globl lbl_80012350
lbl_80012350:
	.word 0x40019999, 0x9999999A
	.text
	.align 2
	.globl offset
	.type offset,@function
	.ent offset
offset:
	lui $at, %hi(lbl_80012350)
	ldc1 $f0, %lo(lbl_80012350)($at)
	jr $ra
	add.d $f0, $f12, $f0
	.end offset
.Lfe1:
	.size offset,.Lfe1-offset
	.globl scale
	.type scale,@function
	.ent scale
scale:
	lui $at, %hi(lbl_80012348)
	ldc1 $f0, %lo(lbl_80012348)($at)
	jr $ra
	mul.d $f0, $f12, $f0
	.end scale
.Lfe2:
	.size scale,.Lfe2-scale
	.globl read_gain
	.type read_gain,@function
	.ent read_gain
read_gain:
	lui $at, %hi(gGain)
	jr $ra
	lwc1 $f0, %lo(gGain)($at)
	.end read_gain
.Lfe3:
	.size read_gain,.Lfe3-read_gain
"""

OFFSET = "double offset(double x) {\n    return x + 2.2;\n}\n"
SCALE = "double scale(double x) {\n    return x * 1.1;\n}\n"
GAIN = "float read_gain(void) {\n    return gVolume;\n}\n"


def _run(cwd: Path, *argv: str) -> None:
    subprocess.run(argv, cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "HOME": str(cwd)})


@pytest.fixture
def fork(tmp_path):
    repo = tmp_path / "upstream"
    (repo / "src").mkdir(parents=True)
    (repo / "include").mkdir()
    (repo / "expected").mkdir()
    (repo / "include" / "lit.h").write_text("extern float gVolume;\n")
    (repo / "src" / "lit.c").write_text('#include "lit.h"\n\n' + OFFSET)
    (repo / "expected" / "target.s").write_text(TARGET, newline="\n")
    cfg = compiler_config(repo, {"compiler": {"toolchain": "gcc-2.7.2-kmc"}})
    assembled = run_in(cfg, repo, [posixpath.dirname(cfg.cmd[0]) + "/as", "-mips3", "-EB", "-o", "expected/lit.o",
                                   "expected/target.s"], inputs=["expected/target.s"], outputs=["expected/lit.o"])
    assert assembled.returncode == 0, assembled.stderr
    command = write_compiler(repo, ["-G0", "-mips3", "-mgp32", "-mfp32", "-O2"])
    (repo / "build.ninja").write_text(
        f"rule cc\n  command = {command} $in $out\n"
        "build build/lit.o: cc src/lit.c | include/lit.h\n")
    (repo / "objdiff.json").write_text(json.dumps({"custom_make": "ninja", "units": [
        {"name": "lit", "target_path": "expected/lit.o", "base_path": "build/lit.o",
         "metadata": {"source_path": "src/lit.c", "complete": False}}]}))
    (repo / ".gitignore").write_text("build/\n")
    _run(repo, "git", "init", "-q", "-b", "main")
    _run(repo, "git", "add", "-A")
    _run(repo, "git", "commit", "-q", "-m", "initial")
    (tmp_path / "work").mkdir()
    net = RomNetwork(tmp_path / "app.db", tmp_path / "work")
    slug = net.forks.clone(str(repo), name="Literals", platform="n64", log=lambda *_: None)
    net.forks.get(slug)[1].build_all()
    net.forks.check(slug, log=lambda *_: None)
    agent, _ = net.local_agent("agent-1")
    return net, slug, agent


def _status(net, slug, name):
    return net._function(name, slug)["status"]


def test_a_forks_best_attempt_can_be_diffed(fork):
    """The GUI's diff switch asks for the best attempt, not the source now: it is built like a try, diffed and
    undone, so the file keeps the later attempt an agent left there."""
    net, slug, agent = fork
    near = SCALE.replace("* 1.1", "+ 1.1")
    out = net.compile_and_diff(agent, "scale", near, slug)
    assert not out.startswith("✅"), out
    latest = net.function_diff(slug, "scale")
    assert latest["kind"] == "latest" and latest["rows"]
    assert net.function_detail(slug, "scale")["best_differs"] is False  # the file holds the best attempt now
    # An edit leaves a worse version in the file; the first attempt stays the best one.
    worse = SCALE.replace("* 1.1", "- 1.1")
    out = net.forks.edit_file(agent, slug, "src/lit.c", near, worse, "a worse version")
    assert out.startswith("✅"), out
    root = Path(net.project_row(slug)["root"])
    before = (root / "src" / "lit.c").read_text()
    assert "- 1.1" in before
    assert net.function_detail(slug, "scale")["best_differs"] is True
    best = net.function_diff(slug, "scale", "best")
    assert best["kind"] == "best"
    assert best["rows"] == latest["rows"] and best["percent"] == latest["percent"]
    assert (root / "src" / "lit.c").read_text() == before  # the best attempt was built and undone
    assert net.function_diff(slug, "scale")["rows"] != latest["rows"]  # the source now is the other attempt


def test_a_best_diff_does_not_create_a_missing_source_file(fork):
    """A unit with no source file yet gets one for the try and must have none again after it, not an empty one."""
    net, slug, agent = fork
    out = net.compile_and_diff(agent, "scale", SCALE.replace("* 1.1", "+ 1.1"), slug)
    assert not out.startswith("✅"), out
    path = Path(net.project_row(slug)["root"]) / "src" / "lit.c"
    path.unlink()
    assert net.function_diff(slug, "scale", "best")["kind"] == "best"
    assert not path.exists()


def test_a_fork_without_a_best_attempt_says_so(fork):
    net, slug, _ = fork
    d = net.function_diff(slug, "scale", "best")
    assert d["kind"] == "best" and d["rows"] is None
    assert "no best attempt" in d["message"].lower()


def test_a_literal_named_and_placed_differently_matches(fork):
    net, slug, agent = fork
    assert _status(net, slug, "offset") == "matched"  # the progress report's verdict
    out = net.compile_and_diff(agent, "scale", SCALE, slug)
    assert out.startswith("✅ MATCH"), out
    assert _status(net, slug, "scale") == "matched" and _status(net, slug, "offset") == "matched"


def test_resubmitting_a_matched_function_is_never_rejected(fork):
    net, slug, agent = fork
    out = net.compile_and_diff(agent, "offset", OFFSET, slug)
    assert "still matches" in out, out


def test_a_wrong_constant_is_not_a_match(fork):
    net, slug, agent = fork
    out = net.compile_and_diff(agent, "scale", SCALE.replace("1.1", "1.2"), slug)
    assert not out.startswith("✅") and "constants" in out and "1.1" in out and "1.2" in out, out
    assert _status(net, slug, "scale") == "open"
    net.forks.sync(slug, log=lambda *_: None)  # objdiff's report counts it; decompdecomp still doesn't
    assert _status(net, slug, "scale") == "open"
    out = net.compile_and_diff(agent, "scale", SCALE, slug)
    assert out.startswith("✅ MATCH"), out
    net.forks.sync(slug, log=lambda *_: None)
    assert _status(net, slug, "scale") == "matched"


def test_a_reference_by_another_name_matches_with_a_warning(fork):
    net, slug, agent = fork
    out = net.compile_and_diff(agent, "read_gain", GAIN, slug)
    assert out.startswith("✅ MATCH") and "gGain" in out and "gVolume" in out, out


def test_a_stale_match_in_the_database_blocks_nothing(fork):
    net, slug, agent = fork
    pid = net.project_row(slug)["id"]
    with net.store.tx() as db:  # e.g. measured matched by an older build, or before an edit outside decompdecomp
        db.execute("UPDATE functions SET status='matched' WHERE project_id=? AND name='scale'", (pid,))
    out = net.compile_and_diff(agent, "read_gain", GAIN, slug)
    assert out.startswith("✅ MATCH"), out
    assert _status(net, slug, "scale") == "open"  # the database follows the source tree again


def test_an_edit_that_breaks_the_build_is_undone(fork):
    net, slug, agent = fork
    path = Path(net.project_row(slug)["root"]) / "src" / "lit.c"
    before = path.read_text()
    out = net.forks.edit_file(agent, slug, "src/lit.c", "", "int broken = ;", "oops")
    assert out.startswith("❌") and "compile" in out, out
    assert path.read_text() == before
    out = net.forks.edit_file(agent, slug, "include/lit.h", "", "typedef int;\nint broken = ;", "oops")
    assert out.startswith("❌") and "compile" in out, out
    assert _status(net, slug, "offset") == "matched"
    out = net.forks.edit_file(agent, slug, "src/lit.c", "", "/* a note */", "a comment")
    assert out.startswith("✅"), out
    assert _status(net, slug, "offset") == "matched"


def test_an_edit_without_a_message_goes_in_as_a_source_event(fork):
    """Agents left edit_file's message out hundreds of times a night, each a refused turn: the commit says what
    changed where instead. One unit's file is a "source" event, which doesn't bring given-up functions back."""
    net, slug, agent = fork
    out = net.forks.edit_file(agent, slug, "src/lit.c", "", "/* a note */")
    assert out.startswith("✅"), out
    ev = net.store.one("SELECT kind, detail FROM events WHERE agent_id=? ORDER BY id DESC LIMIT 1", agent.id)
    assert ev["kind"] == "source" and ev["detail"] == "src/lit.c: Edit src/lit.c", dict(ev)


def test_a_match_made_with_edit_file_is_a_match_like_any_other(fork):
    net, slug, agent = fork
    out = net.forks.edit_file(agent, slug, "src/lit.c", "", SCALE, "write scale")
    assert out.startswith("✅"), out
    fn = net._function("scale", slug)
    assert fn["status"] == "matched" and fn["matched_agent_id"] == agent.id and fn["matched_at"]
    assert "1.1" in fn["matched_source"] and "offset" not in fn["matched_source"]
    assert net.store.one("SELECT 1 FROM events WHERE kind='match' AND detail='scale' AND agent_id=?", agent.id)
    assert net.stats(slug)["agents"][0]["matched"] >= 1


def test_edit_file_forgives_whitespace_and_shows_where_the_text_went(fork):
    net, slug, agent = fork
    path = Path(net.project_row(slug)["root"]) / "src" / "lit.c"
    out = net.forks.edit_file(agent, slug, "src/lit.c", "double offset(double x) {\n  return x + 2.2;",
                              "double offset(double x) {\n    /* tidied */\n    return x + 2.2;", "comment")
    assert out.startswith("✅") and "/* tidied */" in path.read_text(), out
    with pytest.raises(Exception) as e:
        net.forks.edit_file(agent, slug, "src/lit.c", "double ofset(double y) {\n    return y + 2.2;", "", "x")
    assert "closest" in str(e.value) and "double offset(double x)" in str(e.value)


def test_lines_the_file_already_has_may_come_along(fork):
    """Includes and declarations sent with a function are placed in its file (agents' prototypes and pragma
    wrappers came back as errors before, a wasted turn each); a struct or a global still goes in with edit_file."""
    net, slug, agent = fork
    out = net.compile_and_diff(agent, "scale", '#include "lit.h"\n\n' + SCALE, slug)
    assert out.startswith("✅ MATCH"), out
    root = Path(net.project_row(slug)["root"])
    net.compile_and_diff(agent, "read_gain", "extern float gGain;\n" + GAIN, slug)
    text = next(p.read_text() for p in (root / "src").rglob("*.c") if "read_gain" in p.read_text())
    assert text.index("extern float gGain;") < text.index("float read_gain(")
    with pytest.raises(Exception) as e:
        net.compile_and_diff(agent, "read_gain", "struct Gain { float v; };\n" + GAIN, slug)
    assert "struct Gain" in str(e.value) and "edit_file" in str(e.value)


def test_a_definition_the_build_leaves_out_is_said_and_compiled_where_the_build_sees_it(fork):
    """An x86 game's agents parked drafts in `#if defined(SOME_FLAG)` blocks no build defines: the brief showed one as
    the function's source, and each compile replaced it there, judged on other code (13 functions, about 18 KB).
    The brief says so, and a compile puts the agent's version where the build sees it, without the parked copy (its
    annotation comes along); kept, the function is visible from then on."""
    net, slug, agent = fork
    path = Path(net.project_row(slug)["root"]) / "src" / "lit.c"
    parked = "#ifdef PARKED_DRAFTS\n// FUNCTION: 0x80000010\n" + SCALE.replace("1.1", "1.2") + "#endif\n"
    path.write_text(path.read_text() + "\n" + parked)
    brief = net.claim(agent, slug, "scale")
    assert "inside `#ifdef PARKED_DRAFTS`" in brief and "leaves out" in brief, brief
    assert net.compile_and_diff(agent, "scale", SCALE, slug).startswith("✅ MATCH")
    text = path.read_text()
    assert "1.2" not in text and "#ifdef PARKED_DRAFTS\n#endif\n" in text  # the parked copy is gone, its block left
    assert "// FUNCTION: 0x80000010\n" + SCALE in text  # the annotation went with it, outside the block


def test_a_parked_definition_brought_out_leaves_out_its_harness_stand_in_too(fork):
    """An LTCG unit parked a draft under `#ifdef FLAG` while its harness defined a stand-in under `#ifndef FLAG`, so
    the matched callers linked. Moved out alone, the draft was defined twice: every compile of it a link error
    (LNK2005), where before every compile had measured the stand-in. The harness goes as if FLAG were on, with the
    draft: undone with it, kept with it."""
    net, slug, agent = fork
    root = Path(net.project_row(slug)["root"])
    path = root / "src" / "lit.c"
    path.write_text(path.read_text() + "\n#ifdef REAL_SCALE\n// FUNCTION: 0x80000010\n" + SCALE.replace("1.1", "1.2")
                    + "#endif\n")
    (root / "harness").mkdir()
    harness = root / "harness" / "lit.standins.cpp"
    standin = "int g_other;\n#ifndef REAL_SCALE\ndouble scale(double x) { return x; }\n#endif\n"
    harness.write_text(standin)
    other = root / "harness" / "lit2.standins.cpp"  # another unit's: not this draft's business
    other.write_text(standin)
    text = net.compile_and_diff(agent, "scale", SCALE.replace("{", "{ syntax error"), slug)
    assert "BUILD ERROR" in text and harness.read_text() == standin  # undone with the draft
    assert net.compile_and_diff(agent, "scale", SCALE, slug).startswith("✅ MATCH")
    assert harness.read_text() == "int g_other;\n" and other.read_text() == standin
    log = subprocess.run(["git", "-C", str(root), "show", "--stat", "HEAD"], capture_output=True, text=True).stdout
    assert "lit.standins.cpp" in log  # committed with the match


def test_a_parked_functions_best_attempt_is_previewed_without_its_harness_stand_in(fork, monkeypatch):
    """The best attempt's preview places code as a try does (ForkEngine._place): of a draft parked under `#ifdef
    FLAG`, with its harness's stand-in under `#ifndef FLAG`, it's built with the stand-in left out (or defined twice,
    LNK2005), and the harness is as it was after."""
    net, slug, _ = fork
    root = Path(net.project_row(slug)["root"])
    path = root / "src" / "lit.c"
    path.write_text(path.read_text() + "\n#ifdef REAL_SCALE\n// FUNCTION: 0x80000010\n" + SCALE.replace("1.1", "1.2")
                    + "#endif\n")
    (root / "harness").mkdir()
    harness = root / "harness" / "lit.standins.cpp"
    standin = "int g_other;\n#ifndef REAL_SCALE\ndouble scale(double x) { return x; }\n#endif\n"
    harness.write_text(standin)
    before = path.read_text()
    fid = net._function("scale", slug)["id"]
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_score=50, best_source=? WHERE id=?", (SCALE, fid))
    seen = []
    build = net.forks._build_unit
    monkeypatch.setattr(net.forks, "_build_unit", lambda proj, fp, unit: (seen.append(harness.read_text()),
                                                                          build(proj, fp, unit))[1])
    assert net.function_diff(slug, "scale", "best")["kind"] == "best"
    assert seen[0] == "int g_other;\n"  # built with the stand-in left out
    assert harness.read_text() == standin and path.read_text() == before  # and both files as they were


def test_a_function_only_declared_in_the_build_is_not_built_yet(tmp_path, monkeypatch):
    """objdiff lists an extern the source calls on the build's side, with no size and no code: read as built, that
    was a "100% match", and a DS game's agents were told so of a function no file defined, again and again."""
    import json

    from decompnet.forks import objdiff

    rows = [{"instruction": {"formatted": "mov r0, #0", "size": 4}}] * 3
    answer = {"left": {"symbols": [{"name": "f", "size": "12", "kind": "SYMBOL_FUNCTION", "instructions": rows}]},
              "right": {"symbols": [{"name": "f", "flags": {"global": True}}]}}
    monkeypatch.setattr(objdiff, "_run", lambda args, cwd, timeout: json.dumps(answer))
    d = objdiff.diff(tmp_path / "t.o", tmp_path / "b.o", "f", tmp_path)
    assert d.match_percent is None and not d.matched


def test_a_change_that_breaks_a_neighbour_still_says_how_close_the_function_came(tmp_path):
    """Where a function's code changes how a neighbour compiles (inlined into it, or whole-program optimisation
    choosing registers across functions), a draft short of exact breaks the matched neighbour and is undone. The
    function's own result used to go with it, so an x86 game's agents parked drafts behind #if flags and rebuilt by
    hand to measure them. Its score and diff now come back, recorded as an attempt, never as the best (a release
    writes the best into the file)."""
    import sys

    repo = tmp_path / "upstream"
    (repo / "src").mkdir(parents=True)
    (repo / "expected").mkdir()
    base = "inline int base(int x) {\n    return x * 3 + 1;\n}\n"  # GNU89 inline: emitted, and inlined into twice
    twice = "int twice(int x) {\n    return base(x) * 2;\n}\n"
    command = write_compiler(repo, ["-G0", "-mips3", "-mgp32", "-mfp32", "-O2"])
    (repo / "ref.c").write_text(base + "\n" + twice)
    subprocess.run([sys.executable, "compile.py", "ref.c", "expected/lit.o"], cwd=repo, check=True)
    (repo / "ref.c").unlink()
    (repo / "src" / "lit.c").write_text(base + "\n" + twice)
    (repo / "build.ninja").write_text(f"rule cc\n  command = {command} $in $out\nbuild build/lit.o: cc src/lit.c\n")
    (repo / "objdiff.json").write_text(json.dumps({"custom_make": "ninja", "units": [
        {"name": "lit", "target_path": "expected/lit.o", "base_path": "build/lit.o",
         "metadata": {"source_path": "src/lit.c", "complete": False}}]}))
    (repo / ".gitignore").write_text("build/\n")
    _run(repo, "git", "init", "-q", "-b", "main")
    _run(repo, "git", "add", "-A")
    _run(repo, "git", "commit", "-q", "-m", "initial")
    (tmp_path / "work").mkdir()
    net = RomNetwork(tmp_path / "app.db", tmp_path / "work")
    slug = net.forks.clone(str(repo), name="Inlined", platform="n64", log=lambda *_: None)
    net.forks.get(slug)[1].build_all()
    net.forks.check(slug, log=lambda *_: None)
    agent, _ = net.local_agent("agent-1")
    assert _status(net, slug, "twice") == "matched" and _status(net, slug, "base") == "matched"
    path = Path(net.project_row(slug)["root"]) / "src" / "lit.c"
    before = path.read_text()
    out = net.compile_and_diff(agent, "base", base.replace("x * 3 + 1", "x * 5 + 1"), slug)
    assert out.startswith("❌ REJECTED") and "twice" in out, out
    assert "base itself came out at" in out and "score" in out and "TARGET" in out, out  # its own result and diff
    assert path.read_text() == before and _status(net, slug, "twice") == "matched"
    fn = net._function("base", slug)
    last = net.store.one("SELECT score, compiled FROM attempts WHERE function_id=? ORDER BY id DESC LIMIT 1", fn["id"])
    assert last["compiled"] and last["score"] and last["score"] > 0
    assert fn["best_source"] is None or "x * 5" not in fn["best_source"]


def test_a_call_after_a_parked_draft_isnt_taken_for_the_builds_own_definition(fork):
    """Past a parked draft, the next match was a call followed by a block (`y = scale(x) * 2.0;`, then the `if`'s
    `{`): the try replaced `twice` from the call on, and sat inside it."""
    net, slug, agent = fork
    path = Path(net.project_row(slug)["root"]) / "src" / "lit.c"
    twice = ("double twice(double x) {\n    double y = 0.0;\n    y = scale(x) * 2.0;\n    if (y > 1.0) {\n"
             "        y = 1.0;\n    }\n    return y;\n}\n")
    path.write_text(path.read_text() + "\n#ifdef PARKED_DRAFTS\n" + SCALE.replace("1.1", "1.2") + "#endif\n\n" + twice)
    net.compile_and_diff(agent, "scale", SCALE, slug)
    text = path.read_text()
    assert twice in text, text


def test_a_parked_draft_beside_the_builds_own_definition_leaves_the_draft_and_replaces_the_definition(fork):
    """A draft in `#ifdef PARKED_DRAFTS` and the build's own definition in its `#else`: the parked one was taken out
    and a new one put in, beside the visible one (redefinition of 'scale', every try a build error)."""
    net, slug, agent = fork
    path = Path(net.project_row(slug)["root"]) / "src" / "lit.c"
    # (the build's own one divides: another constant alone would read 100% by objdiff, already matched)
    both = ("#ifdef PARKED_DRAFTS\n" + SCALE.replace("1.1", "1.2") + "#else\n" + SCALE.replace("x * 1.1", "x / 1.1")
            + "#endif\n")
    path.write_text(path.read_text() + "\n" + both)
    assert net.compile_and_diff(agent, "scale", SCALE, slug).startswith("✅ MATCH")
    text = path.read_text()
    assert ("#ifdef PARKED_DRAFTS\n" + SCALE.replace("1.1", "1.2") + "#else\n" + SCALE + "#endif\n") in text, text
    assert "x / 1.1" not in text


def test_a_run_of_tries_builds_each_once_and_the_file_once_at_its_end(fork, monkeypatch):
    """The permuter's tries aren't kept: each built its variant and then the file it put back. On an x86 game's LTCG
    units a build is a ~5 s link, so half the permuter's time went to the rebuild. In a run (trial_run), a try that
    isn't kept only puts its file back; the unit is built once at the end, as its file is."""
    net, slug, agent = fork
    builds = []
    build = net.forks._build_unit
    monkeypatch.setattr(net.forks, "_build_unit", lambda *a, **k: builds.append(1) or build(*a, **k))
    path = Path(net.project_row(slug)["root"]) / "src" / "lit.c"
    before = path.read_text()
    with net.forks.trial_run(net.project_row(slug), "lit"):
        for constant in ("1.2", "1.3", "1.4"):
            res = net.forks.attempt(agent, slug, "scale", SCALE.replace("1.1", constant), keep=False)
            assert res["score"] and not res["matched"], res["text"]
        inside = len(builds)
    assert inside <= 4 and len(builds) == inside + 1  # (a baseline at most, then one build a try; one at the end)
    assert path.read_text() == before
    proj, fp, _ = net.forks.get(slug)
    assert fp.diff(fp.unit("lit"), None).unit_scores.get("offset") == 100  # the object is the file's again
    assert net.compile_and_diff(agent, "scale", SCALE, slug).startswith("✅ MATCH")
