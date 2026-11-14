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


@pytest.mark.skipif(toolchains.installed("gcc-2.7.2-kmc") is None or toolchains.installed("binutils-mips") is None
                    or shutil.which("ld.lld") is None, reason="the N64 demo's compiler and linker aren't installed")
def test_an_n64_import_with_the_checkbox_gets_its_project_folder(tmp_path):
    net = _net(tmp_path)
    slug = net.import_rom(DEMO, confirmed=True, log=lambda *_: None, project_layout=True)
    layout = net.overview(slug)["project_layout"]
    assert layout and layout["units"] >= 1 and layout["identical"] is True
    units = json.loads((Path(layout["path"]) / "units.json").read_text())["units"]
    assert sum(len(u["functions"]) for u in units) == net.overview(slug)["functions"]
