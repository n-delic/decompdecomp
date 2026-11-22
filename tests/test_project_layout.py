"""A ROM project laid out as a project folder: guessed source files beside the rebuildable export."""

import json
import shutil
from pathlib import Path

import pytest

from decompnet import toolchains
from decompnet.app.engine import RomNetwork
from decompnet.network import NetworkError
from decompnet.rom import project_layout

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "src/decompnet/app/static/demo/demo-quest-64.z64"


def fn(name, vram, size, segment="main", status="open"):
    return {"name": name, "vram": vram, "size": size, "segment": segment, "status": status}


def test_padding_to_a_file_boundary_starts_a_new_file():
    units = project_layout.guess_units([fn("a", 0x80000400, 0x18), fn("b", 0x80000418, 0x20),
                                        fn("c", 0x80000440, 0x10)])  # 0x438..0x440: padding to 16 bytes
    assert [u["functions"] for u in units] == [["a", "b"], ["c"]]
    assert [u["why"] for u in units] == ["start", "padding"]


def test_neighbours_without_padding_stay_together_even_on_an_aligned_address():
    units = project_layout.guess_units([fn("a", 0x80000400, 0x40), fn("b", 0x80000440, 0x10)])
    assert len(units) == 1


def test_a_gap_that_does_not_end_aligned_is_not_a_file_boundary():
    units = project_layout.guess_units([fn("a", 0x80000400, 0x18), fn("b", 0x80000420 - 4, 0x10)])
    assert len(units) == 1


def test_segments_and_compiler_ranges_start_files():
    ranges = [{"start": 0x80000500, "end": 0x80000600, "toolchain": "gcc-2.7.2-kmc", "flags": ["-O2"]}]
    units = project_layout.guess_units([fn("a", 0x80000400, 0x100), fn("b", 0x80000500, 0x100),
                                        fn("c", 0x80000600, 0x10), fn("d", 0x80100000, 0x10, segment="ovl1")], ranges)
    assert [u["why"] for u in units] == ["start", "compiler", "compiler", "segment"]
    assert units[-1]["name"] == "ovl1_80100000"


def test_a_file_whose_functions_all_match_is_confirmed():
    units = project_layout.guess_units([fn("a", 0x80000400, 0x10, status="matched"),
                                        fn("b", 0x80000410, 0x10, status="matched"),
                                        fn("c", 0x80000430, 0x10)])
    assert [(u["matched"], u["provisional"]) for u in units] == [(2, False), (0, True)]


def test_units_file_lists_addresses_in_hex(tmp_path):
    units = project_layout.guess_units([fn("a", 0x80000400, 0x10)])
    data = json.loads(project_layout.write_units(tmp_path, units).read_text())
    assert data["units"][0] | {} == {"name": "main_80000400", "segment": "main", "start": "0x80000400",
                                     "end": "0x80000410", "why": "start", "functions": ["a"], "matched": 0,
                                     "provisional": True}
    assert project_layout.summary(units, tmp_path)["units"] == 1


def _net(tmp_path):
    (tmp_path / "home").mkdir()
    return RomNetwork(tmp_path / "app.db", tmp_path / "home")


def test_a_failed_project_folder_never_fails_the_import(tmp_path, monkeypatch):
    net = _net(tmp_path)

    def broken(slug, log=print):
        raise NetworkError("no room on the disk")
    monkeypatch.setattr(net, "setup_project_layout", broken)
    said = []
    slug = net.import_rom(DEMO, confirmed=True, log=said.append, project_layout=True)
    assert net.project_row(slug) is not None
    assert any("couldn't be set up" in line for line in said)


def test_without_the_checkbox_no_folder_is_set_up(tmp_path, monkeypatch):
    net = _net(tmp_path)
    monkeypatch.setattr(net, "setup_project_layout", lambda *a, **k: pytest.fail("set up unasked"))
    net.import_rom(DEMO, confirmed=True, log=lambda *_: None)


DEMOS = {  # console: (game, its compiler, the script that builds it)
    "n64": (DEMO, "gcc-2.7.2-kmc", None),
    "ps1": (ROOT / "projects/demo-ps1/build/DEMOQ_PS1.EXE", "gcc-2.7.2-psx", ROOT / "projects/demo-ps1/build.py"),
    "ps2": (ROOT / "projects/demo-ps2/build/SLUS_999.99", "ee-gcc-2.96", ROOT / "projects/demo-ps2/build.py"),
}


@pytest.mark.parametrize("console", sorted(DEMOS))
def test_an_import_with_the_checkbox_gets_its_project_folder(tmp_path, console):
    game, compiler, build = DEMOS[console]
    if toolchains.installed(compiler) is None or toolchains.installed("binutils-mips") is None \
            or shutil.which("ld.lld") is None or shutil.which("llvm-objcopy") is None:
        pytest.skip(f"the {console} demo's compiler and linker aren't installed")
    if not game.exists():
        import subprocess
        import sys
        subprocess.run([sys.executable, str(build)], check=True)
    net = _net(tmp_path)
    slug = net.import_rom(game, confirmed=True, log=lambda *_: None, project_layout=True)
    layout = net.overview(slug)["project_layout"]
    assert layout and layout["units"] >= 1 and layout["identical"] is True
    units = json.loads((Path(layout["path"]) / "units.json").read_text())["units"]
    assert sum(len(u["functions"]) for u in units) == net.overview(slug)["functions"]
    # objdiff shows each guessed file's progress: every function's unit sits in one
    config = json.loads((Path(layout["path"]) / "objdiff.json").read_text())
    assert {c["id"] for c in config["progress_categories"]} == {u["name"] for u in units}
    assert layout["objdiff_units"] == len(config["units"])
    assert all(u["metadata"]["progress_categories"][0] in {c["id"] for c in config["progress_categories"]}
               for u in config["units"])
    # a later plain export keeps the folder's files and groups (it would otherwise drop them)
    (Path(layout["path"]) / "units.json").unlink()
    again = net.export_rom(slug, log=lambda *_: None)
    assert again["project_layout"]["units"] == layout["units"]
    assert (Path(layout["path"]) / "units.json").is_file()
    assert "progress_categories" in json.loads((Path(layout["path"]) / "objdiff.json").read_text())
    # objdiff itself reads it: a report per guessed file
    if toolchains.installed("objdiff-cli") is None:  # (objdiff.report would download it)
        return
    assert layout["objdiff_ready"] is True
    from decompnet.forks import objdiff
    rep =objdiff.report(Path(layout["path"]), tmp_path / "report.json")
    assert {c["id"] for c in rep.get("categories", [])} == {u["name"] for u in units}


def test_export_panel_project_folder_controls():
    node = shutil.which("node")
    if not node:
        pytest.skip("needs node")
    import subprocess
    subprocess.run([node, str(Path(__file__).with_name("gui_project_layout.js"))], check=True, timeout=30, cwd=ROOT)


def test_import_checkbox_shows_only_where_a_rom_game_exports():
    """The checkbox sends layout=project, and only ROM-mode consoles with a rebuildable export show it."""
    home = (ROOT / "src/decompnet/app/static/js/home.js").read_text(encoding="utf-8")
    assert 'con.mode === "rom" && (con.features || []).includes("export")' in home
    assert '"&layout=project"' in home
    assert 'id="layoutPick"' in (ROOT / "src/decompnet/app/static/index.html").read_text(encoding="utf-8")


def test_gaps_the_compilers_own_function_alignment_explains_are_not_files():
    """EE-GCC starts every PS2 function on 8 bytes: a 4-byte gap before a 16-aligned function is that, not a file."""
    functions = [fn(f"f{i}", 0x00100000 + 0x10 * i, 0xC) for i in range(20)]  # each padded 0xC -> 0x10
    assert project_layout.function_alignment([f["vram"] for f in functions]) == 16
    assert len(project_layout.guess_units(functions)) == 1
    eight = [fn(f"g{i}", 0x00100008 + 0x18 * i, 0x14) for i in range(20)]  # 0x14 padded to 8 is 0x18
    assert project_layout.function_alignment([f["vram"] for f in eight]) == 8
    assert len(project_layout.guess_units(eight)) == 1
    assert project_layout.function_alignment([0x400, 0x410, 0x430]) == 4  # too few to say


DEMO_BUILDS = {"n64": ROOT / "projects/demo-rom/build", "ps1": ROOT / "projects/demo-ps1/build",
               "ps2": ROOT / "projects/demo-ps2/build"}


def _elf_functions(path):
    from elftools.elf.elffile import ELFFile
    with open(path, "rb") as f:
        table = ELFFile(f).get_section_by_name(".symtab")
        return {s.name: s["st_value"] for s in table.iter_symbols() if s["st_info"]["type"] == "STT_FUNC"
                and s["st_shndx"] != "SHN_UNDEF"} if table else {}


@pytest.mark.parametrize("console", sorted(DEMOS))
def test_every_guessed_file_starts_where_a_real_one_does(tmp_path, console):
    """Measured on each demo's own objects: a guess may miss a file, never invent one (provisional units would
    otherwise split real files apart). Today: N64 finds 14 of 16 files, PS1 and PS2 1 of 18 (no padding to read)."""
    game, _, _ = DEMOS[console]
    build = DEMO_BUILDS[console]
    elfs = list(build.glob("*.elf"))
    if not game.exists() or not elfs:
        pytest.skip(f"the {console} demo isn't built")
    addr = _elf_functions(elfs[0])
    real = set()
    for obj in build.glob("*.o"):
        names = [n for n in _elf_functions(obj) if n in addr]
        if names:
            real.add(min(addr[n] for n in names))
    net = _net(tmp_path)
    slug = net.import_rom(game, confirmed=True, log=lambda *_: None)
    pid = net.project_row(slug)["id"]
    rows = net.project_store(pid).all("SELECT name, vram, size, unit, status FROM functions WHERE project_id=?", pid)
    units = project_layout.guess_units([{"name": r["name"], "vram": r["vram"], "size": r["size"] or 0,
                                         "segment": r["unit"] or "main", "status": r["status"]} for r in rows])
    assert {u["start"] for u in units} <= real


def test_project_layout_reads_back_an_untouched_file_as_the_c_that_matched(tmp_path):
    """An exported file without edits is exactly the C the judge took (the reverse of export.exported_source)."""
    from decompnet.rom.export import exported_source
    source = '#include "context.h"\n\nvoid f(void) {\n}\n'
    text = exported_source("/* prelude */\n", "common_ovl.h", source)
    assert project_layout.as_submitted(text, project_layout._prefix(text)) == source
    (tmp_path / "src" / "main").mkdir(parents=True)
    (tmp_path / "src" / "main" / "f.c").write_text(text)
    project_layout.record_sources(tmp_path)
    assert project_layout.edits(tmp_path) == []
    (tmp_path / "src" / "main" / "f.c").write_text(text.replace("{\n}", "{ return; }"))
    (tmp_path / "src" / "main" / "g.c").write_text('#include "common.h"\nint g(void) { return 1; }\n')
    found = {e["function"]: e for e in project_layout.edits(tmp_path)}
    assert found["f"]["sources"]["f"] == '#include "context.h"\n\nvoid f(void) { return; }\n'
    assert found["g"]["sources"]["g"] == "int g(void) { return 1; }\n"  # the judge puts the header first itself
    assert found["g"]["segment"] == "main" and found["g"]["path"] == "src/main/g.c"


def test_an_edit_the_project_did_not_take_is_written_back_never_lost(tmp_path):
    edit = {"path": "src/main/f.c", "text": "void f(void) { for (;;); }\n"}
    assert project_layout.keep(tmp_path, edit, False) == "src/main/f.c"  # the function is still assembly
    (tmp_path / "src/main/f.c").write_text("void f(void) {}\n")  # the export wrote its matched C there
    assert project_layout.keep(tmp_path, edit, False) == "src/main/f.c.kept"
    assert (tmp_path / "src/main/f.c.kept").read_text() == edit["text"]
    assert project_layout.keep(tmp_path, {"path": "src/main/f.c", "text": "void f(void) {}\n"}, True) == "src/main/f.c"


@pytest.mark.parametrize("console", sorted(DEMOS))
def test_edits_in_the_project_folder_are_judged_by_the_rom_judge(tmp_path, console):
    """You edit the folder's C (a new file for an unmatched function, a wrong one, a misnamed one) and refresh:
    each is judged like the function page's Try, credited to you, and the folder is regenerated without losing any."""
    game, compiler, _ = DEMOS[console]
    if toolchains.installed(compiler) is None or toolchains.installed("binutils-mips") is None \
            or shutil.which("ld.lld") is None or shutil.which("llvm-objcopy") is None or not game.exists():
        pytest.skip(f"the {console} demo, its compiler or linker isn't here")
    net = _net(tmp_path)
    slug = net.import_rom(game, confirmed=True, log=lambda *_: None, project_layout=True)
    folder = Path(net.overview(slug)["project_layout"]["path"])
    pid = net.project_row(slug)["id"]
    rows = net.project_store(pid).all("SELECT name, size FROM functions WHERE project_id=? AND status='open' "
                                      "ORDER BY vram", pid)
    empty = next((r["name"] for r in rows if r["size"] == 8), None)  # jr ra; nop
    if empty is None:
        pytest.skip(f"the {console} demo has no empty function to match")
    wrong = next(r["name"] for r in rows if r["size"] > 16)

    def where(name):
        return next((folder / "asm" / "nonmatchings").glob(f"*/{name}.s")).parent.name
    (folder / "src" / where(empty)).mkdir(parents=True, exist_ok=True)
    (folder / "src" / where(empty) / f"{empty}.c").write_text(f'#include "common.h"\n\nvoid {empty}(void) {{\n}}\n')
    (folder / "src" / where(wrong)).mkdir(parents=True, exist_ok=True)
    wrong_text = f"int {wrong}(void) {{ return 12345; }}\n"
    (folder / "src" / where(wrong) / f"{wrong}.c").write_text(wrong_text)
    (folder / "src" / where(wrong) / "no_such_function.c").write_text("void no_such_function(void) {}\n")

    layout = net.setup_project_layout(slug, log=lambda *_: None)
    assert layout["edits"]["judged"] == 3 and layout["edits"]["matched"] == 1, layout["edits"]
    status = {r["name"]: r for r in net.project_store(pid).all(
        "SELECT f.name, f.status, a.name AS who FROM functions f LEFT JOIN agents a ON a.id=f.matched_agent_id "
        "WHERE f.project_id=?", pid)}
    assert status[empty]["status"] == "matched" and status[empty]["who"] == "you"
    assert status[wrong]["status"] == "open"
    assert not net.project_store(pid).one("SELECT 1 FROM claims WHERE agent_id=?",
                                          net.attribution_agent("project-folder").id)
    # the match is now built from the export's own copy; the others are back as written
    assert f"void {empty}(void)" in (folder / "src" / where(empty) / f"{empty}.c").read_text()
    assert (folder / "src" / where(wrong) / f"{wrong}.c").read_text() == wrong_text
    assert (folder / "src" / where(wrong) / "no_such_function.c").is_file()
    assert {k["function"] for k in layout["edits"]["kept"]} == {wrong, "no_such_function"}
    assert layout["identical"] is True
    # nothing changed since: the next refresh judges nothing
    assert net.setup_project_layout(slug, log=lambda *_: None)["edits"]["judged"] == 0
    # an edit of a matched function that no longer matches stays beside it; the project keeps its C
    path = folder / "src" / where(empty) / f"{empty}.c"
    path.write_text(f"void {empty}(void) {{ for (;;); }}\n")
    layout = net.setup_project_layout(slug, log=lambda *_: None)
    assert "keeps the C it matched with" in layout["edits"]["kept"][0]["said"]
    assert path.with_name(path.name + ".kept").read_text() == f"void {empty}(void) {{ for (;;); }}\n"
    assert net.project_store(pid).one("SELECT status FROM functions WHERE project_id=? AND name=?", pid,
                                      empty)["status"] == "matched"


def test_a_file_with_several_functions_judges_each_alone():
    source = ("int shared;\n\n/* adds */\nint a(void) { return shared + 1; }\n\n"
              "int b(void) {\n    return a() * 2;\n}\n\nvoid c(void) {}\n")
    assert project_layout.one_definition(source, "b", ["a", "c"]) == \
        "int shared;\n\nint b(void) {\n    return a() * 2;\n}\n\n"
    assert "adds" in project_layout.one_definition(source, "a", ["b", "c"])


def test_a_guessed_units_file_holds_the_functions_it_defines(tmp_path):
    project_layout.write_units(tmp_path, project_layout.guess_units([fn("a", 0x80000400, 0x10),
                                                                     fn("b", 0x80000410, 0x10)]))
    project_layout.record_sources(tmp_path)
    (tmp_path / "src" / "main").mkdir(parents=True)
    (tmp_path / "src" / "main" / "main_80000400.c").write_text("void a(void) {}\nvoid b(void) { a(); }\n")
    (tmp_path / "src" / "main" / "anything.c").write_text("void b(void) {}\n")
    found = {e["function"]: e for e in project_layout.edits(tmp_path)}
    assert found["main_80000400"]["functions"] == ["a", "b"]
    assert found["main_80000400"]["sources"]["b"] == "void b(void) { a(); }\n"
    assert found["anything"]["functions"] == ["b"]  # any other name: the game's functions it defines


@pytest.mark.parametrize("console", sorted(DEMOS))
def test_a_guessed_file_edited_whole_matches_function_by_function(tmp_path, console):
    """You write a guessed unit's file with two of its functions, one right and one not yet: the right one matches
    and the export builds it from a file of its own; the unit's file comes back for the other."""
    game, compiler, _ = DEMOS[console]
    if toolchains.installed(compiler) is None or toolchains.installed("binutils-mips") is None \
            or shutil.which("ld.lld") is None or shutil.which("llvm-objcopy") is None or not game.exists():
        pytest.skip(f"the {console} demo, its compiler or linker isn't here")
    net = _net(tmp_path)
    slug = net.import_rom(game, confirmed=True, log=lambda *_: None, project_layout=True)
    folder = Path(net.overview(slug)["project_layout"]["path"])
    pid = net.project_row(slug)["id"]
    size = {r["name"]: r["size"] for r in net.project_store(pid).all(
        "SELECT name, size FROM functions WHERE project_id=?", pid)}
    units = json.loads((folder / "units.json").read_text())["units"]
    unit = next((u for u in units if any(size[n] == 8 for n in u["functions"])
                 and any(size[n] > 16 for n in u["functions"])), None)
    if unit is None:
        pytest.skip(f"no {console} demo unit has an empty function and a longer one")
    empty = next(n for n in unit["functions"] if size[n] == 8)
    wrong = next(n for n in unit["functions"] if size[n] > 16)
    text = f'#include "common.h"\n\nvoid {empty}(void) {{\n}}\n\nint {wrong}(void) {{ return 12345; }}\n'
    path = folder / "src" / unit["segment"] / f"{unit['name']}.c"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)

    layout = net.setup_project_layout(slug, log=lambda *_: None)
    assert layout["edits"] | {"kept": None} == {"judged": 1, "matched": 1, "kept": None, "waiting": 0}
    assert f"{empty}: matched" in layout["edits"]["kept"][0]["said"]
    assert path.read_text() == text  # back for the function still to do
    own = next((folder / "src").glob(f"*/{empty}.c"))
    assert f"void {empty}(void)" in own.read_text() and wrong not in own.read_text()
    assert layout["identical"] is True


def _saved_project_folder(tmp_path, monkeypatch, setup):
    """Open a saved project whose save says it was a project folder (tests/test_saves.py's restore, stubbed)."""
    from types import SimpleNamespace

    from decompnet.app import saves
    net = _net(tmp_path)
    root = tmp_path / "home" / "game"
    root.mkdir(parents=True)
    save = tmp_path / "save"
    (save / "include").mkdir(parents=True)
    (save / saves.MANIFEST).write_text(json.dumps({
        "format": saves.FORMAT, "title": "Game", "platform": "n64", "game": {"sha1": "demo"},
        "headers": {"": "common.h"}, "layout": [{"name": "f", "vram": 4096, "size": 4}],
        "functions": {}, "knowledge": [], "notes": [], "project_folder": True}), encoding="utf-8")
    (save / "include/common.h").write_text("/* saved header */\n", encoding="utf-8")
    monkeypatch.setattr(saves.consoles, "identify", lambda _: SimpleNamespace(
        read_game=lambda *_: SimpleNamespace(info={"sha1": "demo"})))
    monkeypatch.setattr(net, "apply_remote_layout", lambda *args: None)

    def import_game(*_args, **_kwargs):
        with net.store.tx() as db:
            pid = db.execute("INSERT INTO projects(slug,name,root,config) VALUES('game','Game',?,?)",
                             (str(root), json.dumps({"kind": "rom", "platform": "n64", "rom": {"sha1": "demo"}}))).lastrowid
            db.execute("INSERT INTO context_versions(project_id,version,header,created_at,unit) VALUES(?,1,'',0,'')",
                       (pid,))
            db.execute("INSERT INTO functions(project_id,name,vram,size,n_insns,status,unit) "
                       "VALUES(?,'f',4096,4,1,'open','')", (pid,))
        return "game"
    monkeypatch.setattr(net, "import_rom", import_game)
    monkeypatch.setattr(net, "setup_project_layout", setup)
    said = []
    return saves.restore(net, str(save), tmp_path / "own-game.bin", log=said.append), said


def test_opening_a_saved_project_folder_sets_the_folder_up_again(tmp_path, monkeypatch):
    asked = []
    result, _ = _saved_project_folder(tmp_path, monkeypatch, lambda slug, log=print: asked.append(slug) or {"units": 2})
    assert asked == ["game"] and result["project_layout"] == {"units": 2}
    assert "project_folder" not in result


def test_a_project_folder_that_cannot_come_back_never_fails_the_restore(tmp_path, monkeypatch):
    def broken(slug, log=print):
        raise NetworkError("no room on the disk")
    result, said = _saved_project_folder(tmp_path, monkeypatch, broken)
    assert result["slug"] == "game" and "project_layout" not in result
    assert any("couldn't be set up again" in line for line in said)
