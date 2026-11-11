"""The export builds the game's data from its C (rom/export.py _data_in_c, export_build trim_data), and gives splat
one name per address. Objects come from the pinned KMC GCC; tests skip without it."""
import json
import runpy
import shutil
from pathlib import Path

import pytest

from decompnet import toolchains
from decompnet.rom import export
from tests.test_data_units import build

HEADER = "typedef int s32;\n"


@pytest.fixture(scope="module")
def compile_c(tmp_path_factory):
    if not toolchains.installed("gcc-2.7.2-kmc"):
        pytest.skip("Pinned KMC compiler is not installed; never download it here.")
    from decompnet.compiler import compile_source
    from decompnet.project import compiler_config
    cfg = compiler_config(tmp_path_factory.mktemp("kmc"),
                          {"compiler": {"toolchain": "gcc-2.7.2-kmc", "flags": ["-G0", "-mips3", "-mgp32", "-O2"]}})

    def run(source):
        result = compile_source(cfg, HEADER, source, trusted=True)
        assert result.ok, result.log
        return result.obj
    return run


def test_only_a_datum_with_nothing_of_its_own_goes_in_the_export(compile_c):
    assert export._plain_datum(compile_c("s32 D_80000500[3] = {1, 2, 3};"), "D_80000500", 12)
    # its string would land right after it, not where the game keeps it
    assert not export._plain_datum(compile_c('char *D_80000500 = "LAP";'), "D_80000500", 4)
    assert not export._plain_datum(compile_c("static s32 x = 5; s32 *D_80000500 = &x;"), "D_80000500", 4)


def test_the_build_trims_a_datum_to_its_size(compile_c, tmp_path):
    shutil.copyfile(Path(export.__file__).with_name("export_build.py"), tmp_path / "build.py")
    (tmp_path / "build.json").write_text(json.dumps({"image": "baserom.z64", "symbols": {}, "functions": [],
                                                     "segments": []}))
    build_py = runpy.run_path(str(tmp_path / "build.py"), run_name="not_main")
    obj = tmp_path / "datum.o"
    obj.write_bytes(compile_c("s32 D_80000500[3] = {1, 2, 3};"))  # .data padded to 16
    from elftools.elf.elffile import ELFFile
    with open(obj, "rb") as f:
        assert ELFFile(f).get_section_by_name(".data")["sh_size"] == 16
    build_py["trim_data"](obj, "D_80000500", 12)
    with open(obj, "rb") as f:
        assert ELFFile(f).get_section_by_name(".data")["sh_size"] == 12


def test_splat_gets_one_name_per_address(tmp_path):
    net = build(tmp_path)
    with net.store.tx() as db:
        for name in ("gRecords", "gSlotState", "D_80000600"):
            db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(1,?,?,4,'data')",
                       (name, 0x80000600))
    with net.progress.view():
        lines = export._symbol_lines(net, net.project_row("game"))
    named = [line for line in lines if "0x80000600" in line]
    assert len(named) == 1
    if hasattr(net, "autosave"):
        net.autosave.close()


def test_the_data_segment_places_each_datum_exactly(tmp_path):
    """Code segments align each piece to 4; a 2-byte datum at ...42 was pushed to ...44 and the link failed
    ("cannot move location counter backwards")."""
    shutil.copyfile(Path(export.__file__).with_name("export_build.py"), tmp_path / "build.py")
    seg = {"name": "seg_data", "kind": "code", "start": 0x100, "end": 0x110, "vram": 0x80000500, "bss_vram": None,
           "bss_size": 0, "pieces": [], "bss": [], "subalign": 1}
    code = dict(seg, name="seg_code", start=0, end=0x100, vram=0x80000400)
    del code["subalign"]
    (tmp_path / "build.json").write_text(json.dumps({"image": "baserom.z64", "symbols": {}, "functions": [],
                                                     "segments": [code, seg]}))
    (tmp_path / "baserom.z64").write_bytes(bytes(0x110))
    build_py = runpy.run_path(str(tmp_path / "build.py"), run_name="not_main")
    script = build_py["linker_script"]({}, [], [])
    assert ".seg_data 0x80000500 : AT(0x100) SUBALIGN(1)" in script
    assert ".seg_code 0x80000400 : AT(0x0) SUBALIGN(4)" in script
