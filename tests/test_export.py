"""Exported projects rebuild the game byte for byte: the base for ports and mods.

For each homemade demo (N64, PlayStation 2, PSP), match the game's functions the way an agent would,
export the project, build it, then build it again from a clean tree (as from a fresh clone: only the sources and
your own copy of the game), and check that nothing made from the game would be committed.
"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from elftools.elf.elffile import ELFFile

from decompnet import toolchains
from decompnet.app.engine import RomNetwork
from decompnet.forks import csource

ROOT = Path(__file__).resolve().parent.parent
GAME = ROOT / "projects" / "demo-n64" / "original"
FUNCTIONS = ["clamp_s32", "abs_s32", "player_take_damage", "rng_next", "count_alive_entities", "entity_update_physics",
             "entity_default_hp", "entity_spawn", "add_score", "lerp", "checksum_save", "entity_collides", "game_tick"]
DEMOS = {
    "n64": (ROOT / "src/decompnet/app/static/demo/demo-quest-64.z64", ROOT / "projects/demo-rom/build/demo-quest-64.elf",
            "gcc-2.7.2-kmc", ROOT / "projects/demo-rom/build.py"),
    "ps2": (ROOT / "projects/demo-ps2/build/SLUS_999.99", ROOT / "projects/demo-ps2/build/demoq_ps2.elf",
            "ee-gcc-2.96", ROOT / "projects/demo-ps2/build.py"),
    "psp": (ROOT / "projects/demo-psp/build/BOOT.BIN", ROOT / "projects/demo-psp/build/demoq_psp.elf",
            "psp-gcc-1.3.1", ROOT / "projects/demo-psp/build.py"),
}


def _ready(platform: str) -> bool:
    _, _, tc, _ = DEMOS[platform]
    return (toolchains.installed(tc) is not None and toolchains.installed("binutils-mips") is not None
            and shutil.which("ld.lld") is not None)


# the demo's C that doesn't match on a platform as it is: PSP GCC schedules entity_spawn's call of entity_default_hp
# as the same file let it, which a function compiled alone can't see
NEAR_MISSES = {"psp": {"entity_spawn"}}


def _decompile(tmp: Path, platform: str, skip: tuple[str, ...] = ()):
    game, elf, _, build = DEMOS[platform]
    if not elf.exists() or not game.exists():
        subprocess.run([sys.executable, str(build)], check=True)
    net = RomNetwork(tmp / "app.db", tmp)
    slug = net.import_rom(game, confirmed=True, log=lambda *_: None)
    agent, _ = net.local_agent("agent")
    syms = {s.name: s["st_value"] for s in ELFFile(open(elf, "rb")).get_section_by_name(".symtab").iter_symbols()}
    pid = net.project_row(slug)["id"]
    for name in FUNCTIONS:  # functions the splitter merged into the one before (after one that never returns)
        row = net.store.one("SELECT name, vram FROM functions WHERE project_id=? AND vram<=? AND vram+size>?",
                            pid, syms[name], syms[name])
        if row and row["vram"] != syms[name]:
            net.split_function(agent, slug, row["name"], f"0x{syms[name]:08X}", name)
    header = "\n".join(l for l in (GAME / "game.h").read_text().splitlines()
                       if not l.startswith("#include") and not re.match(r"typedef \w+( \w+)? [su]\d+;|typedef \w+ f\d+;", l))
    if platform != "n64":  # small globals are $gp-relative: tentative definitions, not extern
        header = re.sub(r"^extern (s32|u32) (g_\w+);", r"\1 \2;", header, flags=re.M)
    if platform == "psp":  # (consoles/psp/guides/psp.md: GCC found abs_s32 const in the game's file)
        header += "\ns32 abs_s32(s32 v) __attribute__((const));\n"
    assert net.edit_context(agent, slug, "", header, "the game's types").startswith("✅")
    table = net.symbols(pid)
    for name in FUNCTIONS + ["g_player", "g_entities", "g_score", "g_frame_count", "g_rng_state",
                             "__subsf3", "__mulsf3", "__addsf3", "__muldi3"]:
        if name in syms:
            current, offset = table.name_at(syms[name])
            if offset:
                current = f"D_{syms[name]:08X}"
            if current != name:
                net.rename_symbol(agent, slug, current, name)
                table = net.symbols(pid)
    source = (GAME / "game.c").read_text()
    for name in matched(platform, skip):
        assert net.attempt(agent, name, csource.get(source, name), slug)["matched"], name
    return net, slug


def matched(platform: str, skip: tuple[str, ...] = ()) -> list[str]:
    return [f for f in FUNCTIONS if f not in NEAR_MISSES.get(platform, ()) and f not in skip]


@pytest.mark.parametrize("platform", ["n64", "ps2", "psp"])
def test_export_rebuilds_the_game(tmp_path, platform):
    if not _ready(platform):
        pytest.skip(f"needs the {platform} toolchain and binutils-mips")
    net, slug = _decompile(tmp_path, platform)
    out = net.export_rom(slug, log=lambda *_: None)
    assert out["identical"], out["log"]
    assert out["matched"] == len(matched(platform)) and not out["fallback"]
    tree = Path(out["path"])
    assert len(list((tree / "src").glob("*/*.c"))) == len(matched(platform))
    # the functions still in assembly are assembled a run at a time (a PSP game with its modules has 48,000), and
    # `build.py targets` gives each its own object, where objdiff.json looks for it
    runs = list((tree / "build" / "asm" / "runs").glob("*/*.o"))
    assert 0 < len(runs) < out["functions"] - out["matched"]
    assert subprocess.run([sys.executable, "build.py", "targets"], cwd=tree, capture_output=True).returncode == 0
    assert all((tree / u["target_path"]).exists() for u in json.loads((tree / "objdiff.json").read_text())["units"])

    # a fresh clone: no assembly, assets or build output, only the sources and your copy of the game
    for generated in ("asm", "assets", "build"):
        shutil.rmtree(tree / generated, ignore_errors=True)
    res = subprocess.run([sys.executable, "build.py"], cwd=tree, capture_output=True, text=True)
    assert "identical" in res.stdout, res.stdout + res.stderr

    # nothing made from the game would be committed
    if shutil.which("git"):
        subprocess.run(["git", "init", "-q"], cwd=tree, check=True)
        subprocess.run(["git", "add", "-A"], cwd=tree, check=True)
        files = subprocess.run(["git", "ls-files", "--cached"], cwd=tree, capture_output=True, text=True).stdout.split()
        assert not [f for f in files if f.startswith(("asm/", "assets/", "build/", "baserom"))], files
        assert "build.py" in files and "build.json" in files


def test_branches_into_another_function_and_data_named_by_its_address_link(tmp_path):
    """What a PSP game's export met (a CodeWarrior game with debug symbols, 270 functions matched): hand-written
    assembly that branches into another function (its SDK's deflate routine, cut in pieces), and a twin's C naming
    data by its address (D_088EA738) where the symbol has another name. Neither linked. On the PSP demo: a function
    split between a branch and its target, and a match that names g_entities by its address."""
    if not _ready("psp"):
        pytest.skip("needs the psp toolchain and binutils-mips")
    net, slug = _decompile(tmp_path, "psp", skip=("count_alive_entities",))
    agent, _ = net.local_agent("agent")
    pid = net.project_row(slug)["id"]
    entities = net.symbols(pid).resolve("g_entities")
    source = csource.get((GAME / "game.c").read_text(), "count_alive_entities").replace("g_entities", f"D_{entities:08X}")
    source = f"extern Entity D_{entities:08X}[MAX_ENTITIES];\n{source}"
    assert net.attempt(agent, "count_alive_entities", source, slug)["matched"]
    branch = re.compile(r"/\* \w+ ([0-9A-F]{8}) [0-9A-F]{8} \*/\s+b\w*\s.*\.L([0-9A-F]{8})")
    for fn in net.store.all("SELECT name, vram, size, asm FROM functions WHERE project_id=? AND status='open' "
                            "ORDER BY vram", pid):
        at = next((int(b, 16) + 8 for b, t in branch.findall(fn["asm"]) if int(t, 16) > int(b, 16) + 8), None)
        if at is not None and at < fn["vram"] + fn["size"]:
            assert net.split_function(agent, slug, fn["name"], f"0x{at:08X}").startswith("✅")
            break
    else:
        pytest.fail("no function of the demo branches forward")
    out = net.export_rom(slug, log=lambda *_: None)
    assert out["identical"], out["log"]


def test_check_records_a_function_written_outside(tmp_path):
    if not _ready("n64"):
        pytest.skip("needs the n64 toolchain and binutils-mips")
    net, slug = _decompile(tmp_path, "n64")
    tree = Path(net.export_rom(slug, log=lambda *_: None)["path"])
    py = lambda *a: subprocess.run([sys.executable, "build.py", *a], cwd=tree, capture_output=True, text=True)
    (tree / "src" / "main" / "lerp.c").unlink()  # back to assembly
    assert "identical" in py().stdout
    (tree / "src" / "main" / "lerp.c").write_text('#include "common.h"\n\nf32 lerp(f32 a, f32 b, f32 t) {\n    return a;\n}\n')
    assert "differs" in py().stdout
    assert py("check", "lerp").returncode == 1
    (tree / "src" / "main" / "lerp.c").write_text('#include "common.h"\n\n' + csource.get((GAME / "game.c").read_text(), "lerp"))
    assert py("check", "lerp").returncode == 0
    assert "identical" in py().stdout


def test_eight_byte_data_is_exported_as_the_words_it_is():
    """GNU as aligns a .double to 8 bytes from the start of its file, and a data file can start 4 bytes past such an
    address (a PSP game's module: every double in its .data was assembled 4 bytes late, and the export differed)."""
    from decompnet.arch.mips import raw_doubles
    from decompnet.consoles import get

    line = "    /* E6F30 08E27B00 AE0AA9BECFDE5A3F */ .double 0.00164003646445169292\n"
    assert raw_doubles(line, False) == "    /* E6F30 08E27B00 AE0AA9BECFDE5A3F */ .word 0xBEA90AAE, 0x3F5ADECF\n"
    assert get("psp").export_asm(line) == raw_doubles(line, False)
    big = "    /* 1F30 80027B00 3F5ADECFBEA90AAE */ .dword 0x3F5ADECFBEA90AAE\n"
    assert get("n64").export_asm(big) == "    /* 1F30 80027B00 3F5ADECFBEA90AAE */ .word 0x3F5ADECF, 0xBEA90AAE\n"
    assert raw_doubles("    /* 1F30 80027B00 3F800000 */ .float 1.0\n", True).endswith(".float 1.0\n")


def test_an_agents_own_include_of_context_h_names_the_exports_header():
    """#4: the judge offers the shared header as context.h; the export calls it common.h, so a matched function that
    included context.h itself didn't build in the export."""
    from decompnet.rom.export import exported_source

    text = exported_source("", "common_ovl.h", '#include "context.h"\n\nint f(void) { return 1; }')
    assert text == '#include "common_ovl.h"\n\n#include "common_ovl.h"\n\nint f(void) { return 1; }\n'
    assert "context.h" not in exported_source("", "common.h", '  # include "context.h"\nint g;')
