"""A CodeWarrior version for each source file of a GameCube or Wii project started from scratch (issue #26: an agent
found part of a file matched 100% with GC/1.2.5 while the project built everything with GC/1.3.2).

dtk-template reads `Object(NonMatching, "game/x.c", mw_version="GC/1.2.5")` in configure.py, so these helpers only
edit that file and the manifest: no CodeWarrior is needed here. The project is shaped like consoles/gc_wii/scratch.py
leaves one (its real helpers write configure.py), with one file whose entry a contributor gave options of its own over
several lines.
"""

import json
from pathlib import Path

import pytest

from decompnet.app.forkengine import ForkEngine
from decompnet.consoles.gc_wii import GAMECUBE, WII, scratch
from decompnet.forks import dtksplits
from decompnet.forks.project import ForkError, ForkProject

# dtk-template's configure.py, cut to what scratch.py edits: VERSIONS, config.linker_version, config.src_dir and the
# markers the libraries are written between.
TEMPLATE = '''\
VERSIONS = [
    "GAMEID",  # 0
]

config = {}
config.linker_version = "GC/1.3.2"
config.src_dir = Path("src")

config.libs = [
]


# Optional callback to adjust link order
'''
SOURCES = ["auto/text_80004000.c", "auto/text_80005000.c", "auto/module/text_80006000.c"]


def _project(tmp_path: Path, platform: str = "gc", game_id: str = "GZ2E01",
             compiler: str = "GC/1.3.2") -> ForkProject:
    """A project as scratch.py leaves one: its manifest, configure.py from the real helpers (a library for the program
    and one for the modules), the splits of both, and a file whose Object(...) has options of its own over several
    lines, as a contributor leaves configure.py."""
    (tmp_path / ".decomp.wtf.json").write_text(json.dumps({
        "created": "from scratch", "platform": platform, "version": game_id, "compiler": compiler}), encoding="utf-8")
    (tmp_path / "configure.py").write_text(TEMPLATE, encoding="utf-8")
    scratch._write_configure(tmp_path, game_id, compiler, SOURCES, rels=True)  # the real writer
    conf = tmp_path / "configure.py"
    text = conf.read_text(encoding="utf-8")
    text = text.replace('            Object(NonMatching, "auto/text_80004000.c"),',
                        '            Object(NonMatching, "auto/text_80004000.c", extra_cflags=["-O4,s"]),')
    text = text.replace('            Object(NonMatching, "auto/text_80005000.c"),',
                        '            Object(\n                NonMatching,\n                "auto/text_80005000.c",\n'
                        '                extra_cflags=["-O4,s"],\n            ),')
    conf.write_text(text, encoding="utf-8")
    config = tmp_path / "config" / game_id
    (config / "rels" / "module").mkdir(parents=True)
    (config / "splits.txt").write_text(
        "Sections:\n\t.text       type:code align:4\n\n"
        "auto/text_80004000.c:\n\t.text       start:0x80004000 end:0x80004100\n\n"
        "auto/text_80005000.c:\n\t.text       start:0x80005000 end:0x80005100\n", encoding="utf-8")
    (config / "rels" / "module" / "splits.txt").write_text(
        "Sections:\n\t.text       type:code align:4\n\n"
        "auto/module/text_80006000.c:\n\t.text       start:0x00000000 end:0x00000040\n", encoding="utf-8")
    return ForkProject(tmp_path)


def _entry(root: Path, name: str) -> str:
    """The Object(...) entry configure.py has for a source file, exactly as it stands."""
    text = (root / "configure.py").read_text(encoding="utf-8")
    for lib in dtksplits.libs(text):
        for path, start, end in lib.objects:
            if path == name:
                return text[start:end]
    raise AssertionError(f"configure.py has no Object for {name}")


def test_each_source_file_names_its_codewarrior_version(tmp_path):
    fp = _project(tmp_path)
    assert GAMECUBE.file_compiler(fp, "") == "GC/1.3.2"  # the project's own: configure.py's config.linker_version
    assert GAMECUBE.file_compiler(fp, "src/auto/text_80004000.c") == "GC/1.3.2"  # its options aren't versions
    assert GAMECUBE.file_compiler(fp, "src/auto/module/text_80006000.c") == "GC/1.3.2"
    assert GAMECUBE.set_file_compiler(fp, "src/auto/text_80005000.c", "GC/1.2.5") == "configure.py"
    assert GAMECUBE.file_compiler(fp, "src/auto/text_80005000.c") == "GC/1.2.5"
    assert GAMECUBE.file_compiler(fp, "") == "GC/1.3.2"  # the project's own is the other files'
    assert GAMECUBE.file_compiler(fp, "src/auto/text_DEAD0000.c") == "GC/1.3.2"  # listed nowhere: no version yet


def test_a_version_is_added_replaced_and_removed_on_one_line(tmp_path):
    fp = _project(tmp_path)
    conf = tmp_path / "configure.py"
    GAMECUBE.set_file_compiler(fp, "src/auto/text_80004000.c", "GC/1.1")
    assert _entry(tmp_path, "auto/text_80004000.c") == \
        'Object(NonMatching, "auto/text_80004000.c", extra_cflags=["-O4,s"], mw_version="GC/1.1")'
    assert GAMECUBE.file_compiler(fp, "src/auto/text_80004000.c") == "GC/1.1"
    GAMECUBE.set_file_compiler(fp, "src/auto/text_80004000.c", "GC/1.2.5")  # replaced, never a second keyword
    assert _entry(tmp_path, "auto/text_80004000.c") == \
        'Object(NonMatching, "auto/text_80004000.c", extra_cflags=["-O4,s"], mw_version="GC/1.2.5")'
    assert _entry(tmp_path, "auto/text_80004000.c").count("mw_version") == 1
    GAMECUBE.set_file_compiler(fp, "src/auto/text_80004000.c", "GC/1.3.2")  # the project's own: no keyword at all
    assert _entry(tmp_path, "auto/text_80004000.c") == \
        'Object(NonMatching, "auto/text_80004000.c", extra_cflags=["-O4,s"])'
    # a version written first among the options goes without leaving a comma of its own behind
    conf.write_text(conf.read_text(encoding="utf-8").replace(
        'Object(NonMatching, "auto/text_80004000.c", extra_cflags=["-O4,s"])',
        'Object(NonMatching, "auto/text_80004000.c", mw_version="GC/1.2.5", extra_cflags=["-O4,s"])'), encoding="utf-8")
    assert GAMECUBE.file_compiler(fp, "src/auto/text_80004000.c") == "GC/1.2.5"
    GAMECUBE.set_file_compiler(fp, "src/auto/text_80004000.c", "GC/1.3.2")
    assert _entry(tmp_path, "auto/text_80004000.c") == \
        'Object(NonMatching, "auto/text_80004000.c", extra_cflags=["-O4,s"])'


def test_a_version_goes_on_a_line_of_its_own_in_a_multiline_entry(tmp_path):
    fp = _project(tmp_path)
    GAMECUBE.set_file_compiler(fp, "src/auto/text_80005000.c", "GC/1.1")
    assert _entry(tmp_path, "auto/text_80005000.c") == (
        'Object(\n'
        '                NonMatching,\n'
        '                "auto/text_80005000.c",\n'
        '                extra_cflags=["-O4,s"],\n'
        '                mw_version="GC/1.1",\n'
        '            )')
    GAMECUBE.set_file_compiler(fp, "src/auto/text_80005000.c", "GC/1.2.5")  # another version in the same place
    assert _entry(tmp_path, "auto/text_80005000.c") == (
        'Object(\n'
        '                NonMatching,\n'
        '                "auto/text_80005000.c",\n'
        '                extra_cflags=["-O4,s"],\n'
        '                mw_version="GC/1.2.5",\n'
        '            )')
    GAMECUBE.set_file_compiler(fp, "src/auto/text_80005000.c", "GC/1.3.2")  # the project's own: its line goes
    assert _entry(tmp_path, "auto/text_80005000.c") == (
        'Object(\n'
        '                NonMatching,\n'
        '                "auto/text_80005000.c",\n'
        '                extra_cflags=["-O4,s"],\n'
        '            )')
    GAMECUBE.set_file_compiler(fp, "src/auto/text_80005000.c", "GC/1.2.5")  # and comes back after the options
    assert _entry(tmp_path, "auto/text_80005000.c") == (
        'Object(\n'
        '                NonMatching,\n'
        '                "auto/text_80005000.c",\n'
        '                extra_cflags=["-O4,s"],\n'
        '                mw_version="GC/1.2.5",\n'
        '            )')


def test_an_entry_with_a_condition_and_one_without_options(tmp_path):
    """MatchingFor("GZ2E01") is a first argument like NonMatching, and a plain entry gains the keyword cleanly."""
    fp = _project(tmp_path)
    conf = tmp_path / "configure.py"
    conf.write_text(conf.read_text(encoding="utf-8").replace(
        'Object(NonMatching, "auto/module/text_80006000.c")',
        'Object(MatchingFor("GZ2E01"), "auto/module/text_80006000.c")'), encoding="utf-8")
    GAMECUBE.set_file_compiler(fp, "src/auto/module/text_80006000.c", "GC/2.0")
    assert _entry(tmp_path, "auto/module/text_80006000.c") == \
        'Object(MatchingFor("GZ2E01"), "auto/module/text_80006000.c", mw_version="GC/2.0")'
    GAMECUBE.set_file_compiler(fp, "src/auto/module/text_80006000.c", "GC/1.3.2")
    assert _entry(tmp_path, "auto/module/text_80006000.c") == \
        'Object(MatchingFor("GZ2E01"), "auto/module/text_80006000.c")'


def test_a_files_code_ranges_come_from_the_program_and_its_modules_splits(tmp_path):
    fp = _project(tmp_path)
    assert GAMECUBE.file_ranges(fp) == {
        "src/auto/module/text_80006000.c": [(0x00000000, 0x00000040)],  # a module's splits are offsets in it
        "src/auto/text_80004000.c": [(0x80004000, 0x80004100)],
        "src/auto/text_80005000.c": [(0x80005000, 0x80005100)],
    }


def test_the_whole_game_switches_and_each_file_keeps_its_version(tmp_path):
    fp = _project(tmp_path)
    conf = tmp_path / "configure.py"
    sources = ["src/auto/text_80004000.c", "src/auto/text_80005000.c", "src/auto/module/text_80006000.c"]
    GAMECUBE.set_file_compiler(fp, "src/auto/text_80005000.c", "GC/1.2.5")
    assert GAMECUBE.set_own_compiler(fp, "GC/2.0", sources) == ["configure.py", ".decomp.wtf.json"]
    text = conf.read_text(encoding="utf-8")
    assert 'config.linker_version = "GC/2.0"' in text
    assert text.count('"mw_version": config.linker_version') == 2  # every library follows the project's own
    assert fp.manifest["compiler"] == "GC/2.0"
    assert _entry(tmp_path, "auto/text_80004000.c") == \
        'Object(NonMatching, "auto/text_80004000.c", extra_cflags=["-O4,s"], mw_version="GC/1.3.2")'
    assert 'mw_version="GC/1.2.5"' in _entry(tmp_path, "auto/text_80005000.c")  # kept, though it isn't the own
    assert _entry(tmp_path, "REL/empty.c") == 'Object(Matching, "REL/empty.c")'  # the empty module file
    assert 'Object(NonMatching, "auto/module/text_80006000.c", mw_version="GC/1.3.2")' in text
    assert GAMECUBE.file_compiler(fp, "") == "GC/2.0"  # the panel's own compiler is the new one
    assert GAMECUBE.file_compiler(fp, "src/auto/text_80004000.c") == "GC/1.3.2"
    assert GAMECUBE.file_compiler(fp, "src/auto/text_80005000.c") == "GC/1.2.5"
    # a version that becomes the project's own needs no keyword: that file loses it, the others keep theirs
    assert GAMECUBE.set_own_compiler(fp, "GC/1.2.5", sources) == ["configure.py", ".decomp.wtf.json"]
    assert fp.manifest["compiler"] == "GC/1.2.5" and 'config.linker_version = "GC/1.2.5"' in conf.read_text()
    assert _entry(tmp_path, "auto/text_80005000.c") == (
        'Object(\n'
        '                NonMatching,\n'
        '                "auto/text_80005000.c",\n'
        '                extra_cflags=["-O4,s"],\n'
        '            )')
    assert 'mw_version="GC/1.3.2"' in _entry(tmp_path, "auto/text_80004000.c")
    assert GAMECUBE.file_compiler(fp, "src/auto/text_80005000.c") == "GC/1.2.5"


def test_a_wii_project_switches_its_files_too(tmp_path):
    fp = _project(tmp_path, platform="wii", compiler="Wii/1.0")
    sources = ["src/auto/text_80004000.c", "src/auto/text_80005000.c", "src/auto/module/text_80006000.c"]
    assert WII.file_compiler(fp, "") == "Wii/1.0"
    assert WII.set_file_compiler(fp, "src/auto/text_80004000.c", "Wii/1.6") == "configure.py"
    assert WII.file_compiler(fp, "src/auto/text_80004000.c") == "Wii/1.6"
    assert WII.set_own_compiler(fp, "Wii/1.6", sources) == ["configure.py", ".decomp.wtf.json"]
    assert WII.file_compiler(fp, "") == "Wii/1.6" and fp.manifest["compiler"] == "Wii/1.6"
    assert "mw_version" not in _entry(tmp_path, "auto/text_80004000.c")  # its own now: no keyword needed
    assert 'Object(NonMatching, "auto/module/text_80006000.c", mw_version="Wii/1.0")' in (tmp_path / "configure.py").read_text()


def test_a_public_decomp_sets_its_own_compilers(tmp_path):
    """A dtk project forked by address has no manifest decompdecomp wrote (and one that isn't "made from scratch" is
    the same): its build decides, so try_compiler must not offer a switch and Settings must not claim one."""
    public = tmp_path / "public"
    public.mkdir()
    (public / "configure.py").write_text(TEMPLATE.replace('"GC/1.3.2"', '"GC/1.2.5"'), encoding="utf-8")
    before = (public / "configure.py").read_text(encoding="utf-8")
    fp = ForkProject(public)
    assert GAMECUBE.file_compiler(fp, "") is None and GAMECUBE.file_compiler(fp, "src/a.c") is None
    assert GAMECUBE.file_ranges(fp) == {}
    with pytest.raises(ForkError, match="this project's own build sets its compilers"):
        GAMECUBE.set_file_compiler(fp, "src/a.c", "GC/1.1")
    with pytest.raises(ForkError, match="this project's own build sets its compilers"):
        GAMECUBE.set_own_compiler(fp, "GC/1.1", ["src/a.c"])
    assert (public / "configure.py").read_text(encoding="utf-8") == before  # nothing was written
    (public / ".decomp.wtf.json").write_text(json.dumps({"created": "a fork", "version": "GZ2E01"}), encoding="utf-8")
    assert GAMECUBE.file_compiler(fp, "") is None
    assert (public / "configure.py").read_text(encoding="utf-8") == before


def test_a_file_configure_py_doesnt_build_says_so_and_a_version_it_doesnt_know_is_written(tmp_path):
    fp = _project(tmp_path)
    with pytest.raises(ForkError, match="configure.py doesn't build it"):
        GAMECUBE.set_file_compiler(fp, "src/auto/text_DEAD0000.c", "GC/1.1")
    GAMECUBE.set_file_compiler(fp, "src/auto/text_80004000.c", "GC/9.9")  # try_compiler checks the id, not this
    assert GAMECUBE.file_compiler(fp, "src/auto/text_80004000.c") == "GC/9.9"


def test_a_project_made_on_windows_keeps_its_line_endings(tmp_path):
    fp = _project(tmp_path)
    conf = tmp_path / "configure.py"
    crlf = conf.read_text(encoding="utf-8").replace("\n", "\r\n")
    with open(conf, "w", encoding="utf-8", newline="") as f:
        f.write(crlf)
    GAMECUBE.set_file_compiler(fp, "src/auto/text_80005000.c", "GC/1.1")
    data = conf.read_bytes()
    assert b"\r\n" in data and b"\n" not in data.replace(b"\r\n", b"")
    assert b'mw_version="GC/1.1",\r\n' in data
    GAMECUBE.set_file_compiler(fp, "src/auto/text_80005000.c", "GC/1.3.2")
    assert conf.read_bytes() == crlf.encode()  # taken back, byte for byte


def test_the_settings_compiler_panel_gets_a_scratch_projects_files(tmp_path):
    """The GUI shows the panel when the overview's `compilers` isn't empty (steering.js). The overview fills it from
    _compiler_facts, which asks the console: a scratch project answers with its own version and the files built with
    another, so a GameCube project needs no JavaScript change. A public decomp answers nothing: its build decides."""
    fp = _project(tmp_path)
    (tmp_path / "objdiff.json").write_text(json.dumps({"custom_make": "ninja", "units": [
        {"name": "auto/text_80004000", "metadata": {"source_path": "src/auto/text_80004000.c"}},
        {"name": "auto/text_80005000", "metadata": {"source_path": "src/auto/text_80005000.c"}},
        {"name": "auto/module/text_80006000", "metadata": {"source_path": "src/auto/module/text_80006000.c"}},
    ]}), encoding="utf-8")
    GAMECUBE.set_file_compiler(fp, "src/auto/text_80005000.c", "GC/1.2.5")
    eng = ForkEngine.__new__(ForkEngine)  # (these two are pure: they ask the project and its console only)
    facts = eng._compiler_facts(fp, {"platform": "gc"})
    assert facts["compiler"]["toolchain"] == "GC/1.3.2"
    assert facts["compilers"] == [c.id for c in GAMECUBE.compilers]
    assert facts["compiler_ranges"] == [{
        "file": "src/auto/text_80005000.c", "start": 0x80005000, "end": 0x80005100, "toolchain": "GC/1.2.5",
        "name": "CodeWarrior GC/1.2.5", "flags": []}]
    (tmp_path / ".decomp.wtf.json").unlink()  # a public decomp: its own build sets its compilers
    assert eng._compiler_facts(fp, {"platform": "gc"}) == {}
