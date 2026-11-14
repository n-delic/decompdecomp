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
            or shutil.which("ld.lld") is None:
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
