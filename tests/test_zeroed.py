"""Definitions for the game's zeroed memory (port/zeroed.py), written from the declarations in its C and compiled
by the pinned Zig, on test_port_readiness' homemade project (skips without Zig)."""
import pytest

from decompnet import toolchains
from decompnet.port import pc_compile, pc_link, zeroed
from tests.test_port_readiness import project  # noqa: F401  (the fixture)

# update reaches four pieces of zeroed memory (past the code, which ends at 0x800004C0): a pointer, an array
# declared without a size, an automatic name, and one whose type only its own C knows
UPDATE = """
typedef int s32;
typedef unsigned char u8;
typedef struct Local { s32 a, b, c; } Local;
extern s32 *gPlayer;
extern u8 gBuf[];
extern s32 D_80100040;
extern Local gLocal;
extern u8 D_100[];
extern s32 D_80000300;
extern unsigned int osMemSize;
void update(void) { *gPlayer = gBuf[3] + D_80100040 + gLocal.b + (s32)D_100 + D_80000300 + osMemSize; }
"""


def sizes(obj):
    from elftools.elf.elffile import ELFFile
    with open(obj, "rb") as f:
        return {s.name: s["st_size"] for s in ELFFile(f).get_section_by_name(".symtab").iter_symbols() if s.name}


def test_zeroed_memory_is_defined_with_the_size_the_pc_needs(project, tmp_path):  # noqa: F811
    if not toolchains.installed(pc_compile.TOOL):
        pytest.skip("The pinned Zig is not installed; never download it here.")
    net, root = project
    with net.store.tx() as db:
        db.execute("UPDATE functions SET matched_source=? WHERE name='update'", (UPDATE,))
        pid = db.execute("SELECT id FROM projects WHERE slug='game'").fetchone()[0]
        db.execute("INSERT INTO context_versions(project_id, version, header, message, created_at, unit) "
                   "VALUES(?, 1, 'typedef int s32;\ntypedef unsigned char u8;\n', 'base types', 0, '')", (pid,))
        for name, address in (("gPlayer", 0x80100000), ("gBuf", 0x80100010), ("gLocal", 0x80100050),
                              ("g_after", 0x80100060)):
            db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)",
                       (pid, name, address, 4, "data"))
    pc_compile.check(net, "game", print)
    with net.progress.view():
        proj = net.project_row("game")
        link = pc_link.report(net, proj)
        made = zeroed.summary(net, proj)
    # zeroed memory, and a ROM offset (0x100, where the game reads from its cartridge) and the boot's TV type
    assert "data" not in {g["kind"] for g in link["groups"]} and link["zeroed"] == 6, link
    assert (made["symbols"], made["typed"], made["raw"]) == (6, 3, 1)  # and a ROM offset and a boot value
    typed = (root / "port/src/zeroed_data.c").read_text()
    raw = (root / "port/src/zeroed_data_raw.c").read_text()
    assert "__typeof__(gPlayer) gPlayer;" in typed and "extern s32 *gPlayer;" in typed  # its declaration, copied
    assert "__typeof__(gBuf[0]) gBuf[(2 * 48 + sizeof(gBuf[0]) - 1) / sizeof(gBuf[0])];" in typed  # 48 bytes of room
    assert "unsigned char gLocal[32];" in raw  # its type is only update's: zeroed bytes, twice its 16 bytes
    # compiled for the PC, the pointer is 8 bytes (4 on the console) and the array has its room twice over
    linked = {}
    pc_compile.compile_one("typedef int s32;\ntypedef unsigned char u8;\n", typed.split("\n", 1)[1], linked=linked)
    assert set(linked["symbols"]["defines"]) == {"gPlayer", "gBuf", "D_80100040"}
    found = {}
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        for folder in ("include", "src", "obj"):
            (work / folder).mkdir()
        (work / "include/common.h").write_text("typedef int s32;\ntypedef unsigned char u8;\n")
        pc_compile._compile(work, pc_compile.compiler(), pc_compile._environment(), 0, "common.h",
                            typed.split("\n", 1)[1])
        found = sizes(work / "obj/f0.o")
    assert (found["gPlayer"], found["gBuf"], found["D_80100040"]) == (8, 96, 4)


def test_rom_offsets_and_boot_values_are_written_for_the_pc(project):  # noqa: F811
    if not toolchains.installed(pc_compile.TOOL):
        pytest.skip("The pinned Zig is not installed; never download it here.")
    net, root = project
    with net.store.tx() as db:
        db.execute("UPDATE functions SET matched_source=? WHERE name='update'", (UPDATE,))
        pid = db.execute("SELECT id FROM projects WHERE slug='game'").fetchone()[0]
        db.execute("INSERT INTO context_versions(project_id, version, header, message, created_at, unit) "
                   "VALUES(?, 1, 'typedef int s32;\ntypedef unsigned char u8;\n', 'base types', 0, '')", (pid,))
        db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)",
                   (pid, "osMemSize", 0x80000318, 4, "data"))
    pc_compile.check(net, "game", print)
    asm = (root / "port/src/rom_offsets.S").read_text()
    boot = (root / "port/src/boot_globals.c").read_text()
    assert ".set SYM(D_100), 0x100" in asm and "#define SYM(x) _##x" in asm  # macOS's leading underscore
    assert "int32_t D_80000300 = 0x1; /* 0x80000300: osTvType */" in boot  # NTSC: no game code says otherwise
    # libultra's own name sorts as glue in the link check, and still gets the boot's value
    assert "uint32_t osMemSize = 0x400000; /* 0x80000318: osMemSize */" in boot
    # linked into a program on this computer, the ROM offset's address is the offset and the boot value is set
    import shutil
    import subprocess
    cc = shutil.which("cc") or shutil.which("clang")
    if cc is None:
        return
    main = root / "main.c"
    main.write_text('#include <stdio.h>\n#include <stdint.h>\nextern unsigned char D_100[]; extern int32_t D_80000300;\n'
                    'int main(void) { printf("%x %d", (unsigned)(uintptr_t)D_100, D_80000300); return 0; }\n')
    program = root / "fixed"
    subprocess.run([cc, "-o", str(program), str(main), str(root / "port/src/rom_offsets.S"),
                    str(root / "port/src/boot_globals.c")], check=True, capture_output=True)
    assert subprocess.run([str(program)], capture_output=True, text=True).stdout == "100 1"
