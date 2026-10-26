"""PlayStation Portable ROM projects (consoles/psp/): the disc and its PARAM.SFO, the program a PRX laid out where
the loader puts it, the imported functions' names, the compilers, and what's set aside or exported as words.

Every input is a small one made here: a UMD image of a few sectors, a PRX of a few words with each relocation the
loader applies. The whole path on a real program is the demo's (projects/demo-psp, a PRX built with the SDK's GCC),
in tests/test_consoles.py.
"""

import json
import sqlite3
import struct
from contextlib import contextmanager
from pathlib import Path

import pytest

from decompnet import consoles, toolchains
from decompnet.consoles.psp import PSP, prx, set_stubs_aside, sfo
from decompnet.rom import RomError

ET_EXEC, ET_PSP_PRX = 2, 0xFFA0
ALLEGREX_FLAGS, R5900_FLAGS = 0x10A23001, 0x20924001
SHT_PROGBITS, SHT_SYMTAB, SHT_STRTAB, SHT_NOBITS, SHT_REL = 1, 2, 3, 8, 9
SHT_MIPS_DEBUG, SHT_PSP_REL = 0x70000005, 0x700000A0
SHF_WRITE, SHF_ALLOC, SHF_EXECINSTR = 1, 2, 4
STT_OBJECT, STT_FUNC, STT_SECTION = 1, 2, 3
R_MIPS_32, R_MIPS_26, R_MIPS_HI16, R_MIPS_LO16, R_MIPS_GPREL16 = 2, 4, 5, 6, 7
BASE = prx.USER_BASE
HEADERS = 0x34 + 0x20  # the ELF header and one program header


def _elf(sections: list[tuple], *, e_type: int = ET_PSP_PRX, flags: int = ALLEGREX_FLAGS, memsz: int = 0,
         entry: int = 0) -> bytes:
    """A little-endian MIPS ELF: (name, type, flags, address, contents, link, info) per section, the loaded ones first
    and by address, laid out in the file as their addresses are, under one program header from the lowest."""
    names = b"\0" + b"".join(s[0].encode() + b"\0" for s in sections) + b".shstrtab\0"
    loaded = [s for s in sections if s[2] & SHF_ALLOC]
    low = min((s[3] for s in loaded), default=0)
    body = bytearray(HEADERS)
    heads = [bytes(40)]
    name_at = 1
    for name, kind, sflags, addr, data, link, info in sections:
        if sflags & SHF_ALLOC:
            offset = HEADERS + addr - low
            if kind != SHT_NOBITS:
                body += bytes(offset - len(body))
                body += data
        else:
            body += bytes(-len(body) % 4)
            offset = len(body)
            body += data
        heads.append(struct.pack("<10I", name_at, kind, sflags, addr, offset, len(data), link, info, 4,
                                 8 if kind in (SHT_REL, SHT_PSP_REL, SHT_MIPS_DEBUG) else 16 if kind == SHT_SYMTAB else 0))
        name_at += len(name) + 1
    filesz = max((s[3] + len(s[4]) - low for s in loaded if s[1] != SHT_NOBITS), default=0)
    body += bytes(-len(body) % 4)
    heads.append(struct.pack("<10I", name_at, SHT_STRTAB, 0, 0, len(body), len(names), 0, 0, 1, 0))
    body += names
    body += bytes(-len(body) % 4)
    shoff = len(body)
    body += b"".join(heads)
    struct.pack_into("<4s5B7x", body, 0, b"\x7fELF", 1, 1, 1, 0, 0)
    struct.pack_into("<HHIIIIIHHHHHH", body, 16, e_type, 8, 1, entry, 0x34, shoff, flags, 0x34, 0x20, 1, 40,
                     len(heads), len(heads) - 1)
    struct.pack_into("<8I", body, 0x34, 1, HEADERS, low, low, filesz, max(memsz, filesz), 7, 16)
    return bytes(body)


def _symbols(entries: list[tuple]) -> tuple[bytes, bytes]:
    """.symtab and .strtab for (name, value, type, section index)."""
    strings, table = bytearray(b"\0"), bytearray(16)
    for name, value, kind, index in entries:
        table += struct.pack("<IIIBBH", len(strings), value, 0, kind, 0, index)
        strings += name.encode() + b"\0"
    return bytes(table), bytes(strings)


def _rels(entries: list[tuple[int, int]]) -> bytes:
    return b"".join(struct.pack("<II", offset, info) for offset, info in entries)


# The PRX: a function that loads an address in the .bss (its low half carries once the module is placed), calls
# another, and reads a $gp-relative global; a pointer in .data; the module info's $gp; symbols counted from their
# section's start, as Metrowerks' linker writes them; DWARF 1 that points at the code and at itself
TEXT = struct.pack("<12I", 0x3C040000, 0x24844010, 0x0C000008, 0x8F820010, *[0] * 4, 0x03E00008, 0, 0x03E00008, 0)
DATA = struct.pack("<I", 0x24)
MODULE_INFO = bytes(32) + struct.pack("<5I", 0x4000, 0, 0, 0, 0)
TEXT_AT, DATA_AT, INFO_AT, BSS_AT, MEMSZ = 0, 0x30, 0x34, 0x68, 0x5000
DEBUG = struct.pack("<II", 0x20, 0x4)


def _prx() -> bytes:
    symtab, strtab = _symbols([("callee", 0x20, STT_FUNC, 1), ("pointer", 0, STT_OBJECT, 2), (".debug", 0, STT_SECTION, 10)])
    return _elf([
        (".text", SHT_PROGBITS, SHF_ALLOC | SHF_EXECINSTR, TEXT_AT, TEXT, 0, 0),
        (".data", SHT_PROGBITS, SHF_ALLOC | SHF_WRITE, DATA_AT, DATA, 0, 0),
        (".rodata.sceModuleInfo", SHT_PROGBITS, SHF_ALLOC, INFO_AT, MODULE_INFO, 0, 0),
        (".bss", SHT_NOBITS, SHF_ALLOC | SHF_WRITE, BSS_AT, b"", 0, 0),  # its size left 0, as Metrowerks' linker does
        (".rel.text", SHT_PSP_REL, 0, 0, _rels([(0, R_MIPS_HI16), (4, R_MIPS_LO16), (8, R_MIPS_26),
                                               (0xC, R_MIPS_GPREL16)]), 7, 1),
        (".rel.data", SHT_PSP_REL, 0, 0, _rels([(DATA_AT, R_MIPS_32)]), 7, 2),
        (".symtab", SHT_SYMTAB, 0, 0, symtab, 8, 0),
        (".strtab", SHT_STRTAB, 0, 0, strtab, 0, 0),
        (".rel.rodata.sceModuleInfo", SHT_PSP_REL, 0, 0, _rels([(INFO_AT + 32, R_MIPS_32)]), 7, 3),
        (".debug", SHT_MIPS_DEBUG, 0, 0, DEBUG, 0, 0),
        (".rel.debug", SHT_MIPS_DEBUG, 0, 0, _rels([(0, 1 << 8 | R_MIPS_32), (4, 3 << 8 | R_MIPS_32)]), 7, 10),
        (".comment", SHT_PROGBITS, 0, 0, b"MW MIPS C Compiler (2.4.1.01)\0PSP\0", 0, 0),
    ], memsz=MEMSZ)


def _elffile(data: bytes):
    import io

    from elftools.elf.elffile import ELFFile

    return ELFFile(io.BytesIO(data))


def test_a_prx_is_laid_out_where_the_loader_puts_a_main_module():
    fixed = prx.relocate(_prx())
    elf = _elffile(fixed)
    assert elf["e_type"] == "ET_EXEC" and elf["e_entry"] == BASE
    text = elf.get_section_by_name(".text")
    assert text["sh_addr"] == BASE and elf.get_section_by_name(".data")["sh_addr"] == BASE + DATA_AT
    words = struct.unpack("<4I", text.data()[:16])
    # 0x4010 placed is 0x08808010: its low half reads as negative, so the upper half carries one more
    assert words == (0x3C040881, 0x24848010, 0x0C000000 | (BASE + 0x20) >> 2, 0x8F820010)
    assert struct.unpack("<I", elf.get_section_by_name(".data").data())[0] == BASE + 0x24
    assert prx.gp_value(fixed) == BASE + 0x4000
    bss = elf.get_section_by_name(".bss")
    assert bss["sh_addr"] == BASE + BSS_AT and bss["sh_addr"] + bss["sh_size"] == BASE + MEMSZ  # the rest of the segment
    assert elf.get_segment(0)["p_vaddr"] == BASE


def test_its_symbols_and_debug_addresses_move_with_it_and_debug_links_dont():
    elf = _elffile(prx.relocate(_prx()))
    symbols = {s.name: s["st_value"] for s in elf.get_section_by_name(".symtab").iter_symbols() if s.name}
    assert symbols["callee"] == BASE + 0x20 and symbols["pointer"] == BASE + DATA_AT  # counted from .data's start
    assert struct.unpack("<II", elf.get_section_by_name(".debug").data()) == (BASE + 0x20, 0x4)
    assert all(s["sh_type"] != "SHT_REL" and s["sh_type"] != SHT_PSP_REL for s in elf.iter_sections())


def test_a_fixed_program_passes_through_and_a_ps2_one_isnt_a_psps():
    fixed = _elf([(".text", SHT_PROGBITS, SHF_ALLOC | SHF_EXECINSTR, BASE, TEXT, 0, 0)], e_type=ET_EXEC)
    assert prx.relocate(fixed) is fixed and prx.is_psp_elf(fixed)
    ps2 = _elf([(".text", SHT_PROGBITS, SHF_ALLOC | SHF_EXECINSTR, 0x100000, TEXT, 0, 0)], e_type=ET_EXEC,
               flags=R5900_FLAGS)
    assert not prx.is_psp_elf(ps2) and consoles.identify(ps2).id == "ps2"
    assert consoles.identify(_prx()) is PSP and consoles.identify(fixed) is PSP


def test_a_relocation_the_loader_has_no_rule_for_is_refused_with_a_sentence():
    data = bytearray(_prx())
    elf = _elffile(bytes(data))
    rel = elf.get_section_by_name(".rel.data")
    struct.pack_into("<I", data, rel["sh_offset"] + 4, 3)  # R_MIPS_REL32
    with pytest.raises(RomError, match="kind this app doesn't read yet"):
        prx.relocate(bytes(data))


# --------------------------------------------------------------------------------------- imported functions

UNKNOWN_NID = 0x12345678  # a NID the SDK names nothing


def _with_stubs(symbols: list[tuple] = ()) -> bytes:
    """A fixed program importing two functions of one library, the SDK's sceIoOpen and a NID it doesn't know: their
    stubs, the stub table, its name and NIDs; and a symbol table of (name, value, type) in .sceStub.text if given."""
    stubs = struct.pack("<4I", 0x03E00008, 0, 0x03E00008, 0)
    table = struct.pack("<IIBBHII", BASE + 0x300, 0x40010011, 5, 0, 2, BASE + 0x320, BASE + 0x100)
    rodata = b"IoFileMgrForUser".ljust(32, b"\0") + struct.pack("<II", 0x109F50BC, UNKNOWN_NID)
    sections = [(".text", SHT_PROGBITS, SHF_ALLOC | SHF_EXECINSTR, BASE, TEXT, 0, 0),
                (".sceStub.text", SHT_PROGBITS, SHF_ALLOC | SHF_EXECINSTR, BASE + 0x100, stubs, 0, 0),
                (".lib.stub", SHT_PROGBITS, SHF_ALLOC, BASE + 0x200, table, 0, 0),
                (".rodata.sceResident", SHT_PROGBITS, SHF_ALLOC, BASE + 0x300, rodata, 0, 0)]
    if symbols:
        symtab, strtab = _symbols([(name, value, kind, 2) for name, value, kind in symbols])
        sections += [(".symtab", SHT_SYMTAB, 0, 0, symtab, 6, 0), (".strtab", SHT_STRTAB, 0, 0, strtab, 0, 0)]
    return _elf(sections, e_type=ET_EXEC)


def test_imported_functions_are_named_by_the_sdks_nids_and_unknown_ones_by_library_and_nid():
    program = _with_stubs()
    assert prx.stub_names(program) == {BASE + 0x100: "sceIoOpen", BASE + 0x108: "IoFileMgrForUser_12345678"}
    assert prx.stub_range(program) == (BASE + 0x100, BASE + 0x110)
    named = {n.name: (n.address, n.function, n.size) for n in PSP.debug_info(program, set()).named}
    assert named["sceIoOpen"] == (BASE + 0x100, True, 8)
    assert named["IoFileMgrForUser_12345678"] == (BASE + 0x108, True, 8)


def test_the_games_own_name_for_a_stub_wins_and_a_name_taken_elsewhere_gets_a_number():
    """Metrowerks' linker names each stub (an object of no size) beside its library's section symbol; a game
    function the SDK's name already names elsewhere keeps it, and the stub becomes _2."""
    program = _with_stubs([(".sceStub.text.IoFileMgrForUser", BASE + 0x100, STT_SECTION),
                           ("myIoOpen", BASE + 0x100, STT_OBJECT)])
    assert prx.stub_names(program)[BASE + 0x100] == "myIoOpen"
    program = _with_stubs([("sceIoOpen", BASE + 0x10, STT_FUNC)])  # (in .sceStub.text's index, but not a stub)
    named = {n.address: n.name for n in PSP.debug_info(program, set()).named}
    assert prx.stub_names(program)[BASE + 0x100] == "sceIoOpen" and named[BASE + 0x100] == "sceIoOpen_2"


def test_the_nid_database_loads_and_its_names_hash_to_their_nids():
    from decompnet.consoles.psp import nids

    libraries = nids.database()
    assert len(libraries) > 50 and sum(map(len, libraries.values())) > 2000
    for library, nid, name in (("sceDisplay", 0x289D82FE, "sceDisplaySetFrameBuf"),
                               ("ThreadManForUser", 0x446D8DE6, "sceKernelCreateThread"),
                               ("IoFileMgrForUser", 0x109F50BC, "sceIoOpen")):
        assert nids.name(library, nid) == name and nids.nid_of(name) == nid
    hashed = [nid == nids.nid_of(name) for names in libraries.values() for nid, name in names.items()]
    assert sum(hashed) > 0.95 * len(hashed)  # the rest are names the SDK guessed for NIDs no name is known to hash to
    assert nids.name("IoFileMgrForUser", UNKNOWN_NID) is None and nids.name("NoSuchLibrary", 0x109F50BC) is None
    assert "Redistributions of source code must retain" in (nids.HERE / "LICENSE.pspsdk").read_text()


def test_the_nid_database_is_read_from_the_sdks_import_files():
    from decompnet.consoles.psp import nids

    found = nids.from_sources({
        "src/display/sceDisplay.S": '\tIMPORT_START\t"sceDisplay",0x40010000\n'
                                    '\tIMPORT_FUNC\t"sceDisplay",0x289D82FE,sceDisplaySetFrameBuf\n'
                                    '\tIMPORT_FUNC\t"sceDisplay",0x0E20F177,sceDisplay_0E20F177\n',
        "src/sas/sceSasCore.S": '\tIMPORT_FUNC_WITH_ALIAS\t"sceSasCore",0xa232cbe6,__sceSasSetTrianglarWave,'
                                '__sceSasSetTriangularWave\n',
        "src/samples/prx/MyLib.S": '\tIMPORT_FUNC\t"MyLib",0x563FF2B2,getModuleInfo\n',
    })
    assert found == {"sceDisplay": {0x289D82FE: "sceDisplaySetFrameBuf"},
                     "sceSasCore": {0xA232CBE6: "__sceSasSetTrianglarWave"}}


class _Store:
    """The few queries set_stubs_aside makes, on SQLite in memory."""

    def __init__(self, functions: list[tuple[int, str]]):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE functions(id INTEGER PRIMARY KEY, project_id INT, vram INT, status TEXT, meta TEXT)")
        self.db.executemany("INSERT INTO functions(project_id, vram, status, meta) VALUES(1, ?, ?, NULL)", functions)

    def all(self, sql, *args):
        return self.db.execute(sql, args).fetchall()

    @contextmanager
    def tx(self):
        yield self.db
        self.db.commit()


class _Net:
    def __init__(self, store, stubs):
        self.store, self.stubs = store, stubs

    def project_row(self, slug):
        return {"id": 1}

    def cfg(self, proj):
        return {"rom": {"stubs": self.stubs}}


def test_the_stubs_are_set_aside_and_the_games_code_isnt():
    store = _Store([(BASE, "open"), (BASE + 0x100, "open"), (BASE + 0x108, "matched")])
    assert set_stubs_aside(_Net(store, [BASE + 0x100, BASE + 0x110]), "game", log=lambda *_: None) == 1
    rows = {r["vram"]: (r["status"], json.loads(r["meta"] or "{}")) for r in store.all("SELECT * FROM functions")}
    assert rows[BASE][0] == "open" and rows[BASE + 0x108][0] == "matched"
    assert rows[BASE + 0x100][0] == "asm" and "NID" in rows[BASE + 0x100][1]["asm_code"]
    assert set_stubs_aside(_Net(store, []), "game") == 0


# --------------------------------------------------------------------------------------------- the disc

def _sfo(entries: dict[str, str | int]) -> bytes:
    keys, values, table = bytearray(), bytearray(), bytearray()
    for key, value in entries.items():
        raw = struct.pack("<I", value) if isinstance(value, int) else value.encode() + b"\0"
        room = len(raw) + -len(raw) % 4
        table += struct.pack("<HHIII", len(keys), 0x0404 if isinstance(value, int) else 0x0204, len(raw), room,
                             len(values))
        keys += key.encode() + b"\0"
        values += raw.ljust(room, b"\0")
    keys += bytes(-len(keys) % 4)
    keys_at = 20 + len(table)
    return struct.pack("<4sIIII", b"\0PSF", 0x101, keys_at, keys_at + len(keys), len(entries)) + table + keys + values


def _iso(files: dict[str, bytes]) -> bytes:
    """The smallest ISO 9660 image with folders the disc reader takes: files by their paths ("A/B/C.BIN")."""
    def both16(v):
        return struct.pack("<H", v) + struct.pack(">H", v)

    def both32(v):
        return struct.pack("<I", v) + struct.pack(">I", v)

    def record(name: bytes, lba: int, size: int, is_dir: bool) -> bytes:
        rec = bytes([0, 0]) + both32(lba) + both32(size) + bytes(7) + bytes([2 if is_dir else 0, 0, 0]) + both16(1) \
            + bytes([len(name)]) + name
        rec += bytes(len(rec) % 2)
        return bytes([len(rec)]) + rec[1:]

    def parent(path: str) -> str:
        return path.rsplit("/", 1)[0] if "/" in path else ""

    folders = sorted({"/".join(p.split("/")[:k]) for p in files for k in range(len(p.split("/")))})  # "" is the root
    lba = 18 + len(folders)
    where = {folder: 18 + k for k, folder in enumerate(folders)}
    places = {}
    for path, data in files.items():
        places[path] = (lba, len(data))
        lba += max(1, -(-len(data) // 2048))
    sectors = []
    for folder in folders:
        body = record(b"\0", where[folder], 2048, True) + record(b"\1", where[folder], 2048, True)
        for sub in folders:
            if sub and parent(sub) == folder:
                body += record(sub.rsplit("/", 1)[-1].encode(), where[sub], 2048, True)
        for path, (at, size) in places.items():
            if parent(path) == folder:
                body += record(f"{path.rsplit('/', 1)[-1]};1".encode(), at, size, False)
        sectors.append(body.ljust(2048, b"\0"))
    pvd = bytearray(2048)
    pvd[0:7] = b"\x01CD001\x01"
    pvd[156:156 + 34] = record(b"\0", 18, 2048, True)
    image = bytearray(16 * 2048) + pvd + bytearray(b"\xffCD001\x01".ljust(2048, b"\0")) + b"".join(sectors)
    for data in files.values():
        image += data.ljust(max(1, -(-len(data) // 2048)) * 2048, b"\0")
    return bytes(image)


PARAMS = {"CATEGORY": "UG", "DISC_ID": "ULJM05999", "DISC_VERSION": "1.00", "PARENTAL_LEVEL": 5,
          "TITLE": "デモクエスト"}


def test_param_sfo_says_the_title_and_the_product_code():
    facts = sfo.read(_sfo(PARAMS))
    assert facts["TITLE"] == "デモクエスト" and facts["PARENTAL_LEVEL"] == 5
    assert sfo.product_code(facts["DISC_ID"]) == "ULJM-05999" and sfo.region_of("ULJM-05999") == "Japan"
    with pytest.raises(RomError, match="PARAM.SFO"):
        sfo.read(b"not a PARAM.SFO")


def test_a_umd_image_is_the_psps_and_its_program_comes_with_the_discs_facts(tmp_path):
    disc = _iso({"UMD_DATA.BIN": b"ULJM-05999|0001G", "PSP_GAME/PARAM.SFO": _sfo(PARAMS),
                 "PSP_GAME/SYSDIR/BOOT.BIN": _prx(), "PSP_GAME/SYSDIR/EBOOT.BIN": b"~PSP" + bytes(60)})
    (tmp_path / "game.iso").write_bytes(disc)
    assert consoles.identify(tmp_path / "game.iso") is PSP and consoles.identify(disc) is PSP
    picked = PSP.read_game(tmp_path / "game.iso")
    info = picked.info
    assert (info["title"], info["game_code"], info["region"]) == ("デモクエスト (ULJM-05999)", "ULJM-05999", "Japan")
    assert info["boot"] == "BOOT.BIN" and info["prx"] and info["compiler_guess"] == "MWCCPSP"
    assert info["gp"] == BASE + 0x4000 and _elffile(picked.data)["e_type"] == "ET_EXEC"


def test_other_playstation_discs_are_not_the_psps():
    ps2 = _iso({"SYSTEM.CNF": b"BOOT2 = cdrom0:\\SLUS_999.99;1\r\n", "SLUS_999.99": bytes(64)})
    assert not PSP.sniff(ps2[:consoles.HEAD_BYTES], ps2)


def test_an_encrypted_program_says_to_decrypt_it_first():
    disc = _iso({"PSP_GAME/PARAM.SFO": _sfo(PARAMS), "PSP_GAME/SYSDIR/BOOT.BIN": bytes(2048),
                 "PSP_GAME/SYSDIR/EBOOT.BIN": b"~PSP" + bytes(2044)})
    with pytest.raises(RomError, match="EBOOT.BIN.*encrypted.*decrypted ELF or PRX"):
        PSP.read_game(disc)
    assert consoles.identify(b"~PSP" + bytes(300)) is PSP  # the encrypted program alone: the same sentence
    with pytest.raises(RomError, match="encrypted"):
        PSP.read_game(b"~PSP" + bytes(300), "EBOOT.BIN")


# ------------------------------------------------------------------------------------ compilers and code

@pytest.mark.parametrize("note, guess", [
    (b"MW MIPS C Compiler (2.4.1.01)\0PSP\0", "MWCCPSP"),
    (b"\0GCC: (GNU) 3.3.3+allegrex-2.2.1-psp-1.3.1\0", "PSPGCC-1.3"),
    (b"\0GCC: (GNU) 3.3.3+allegrex-2.2.2-psp-1.7.1\0", "PSPGCC-1.7"),
    (b"", "PSPGCC-1.7"),
])
def test_the_programs_note_names_its_compiler(note, guess):
    program = _elf([(".text", SHT_PROGBITS, SHF_ALLOC | SHF_EXECINSTR, BASE, TEXT, 0, 0),
                    (".comment", SHT_PROGBITS, 0, 0, note, 0, 0)], e_type=ET_EXEC)
    assert prx.guess_compiler(program) == guess
    chosen = PSP.default_compiler({"compiler_guess": guess}, "")
    assert chosen == {"MWCCPSP": "mwccpsp-3.0.1-b219", "PSPGCC-1.3": "psp-gcc-1.3.1", "PSPGCC-1.7": "psp-gcc-1.7.1"}[guess]


def test_small_data_is_said_each_compilers_way():
    assert PSP.default_flags("mwccpsp-3.0.1-b219", 0x08900000) == ["-O3", "-sdatathreshold", "8"]
    assert PSP.default_flags("psp-gcc-1.3.1", 0x08900000) == ["-O2", "-G8"]
    assert PSP.default_flags("psp-gcc-1.7.1", 0) == ["-O2", "-G0"]
    assert PSP.default_flags("pspsnc-1.2.7503", 0x08900000) == ["-O2", "-G8"]
    assert PSP.default_flags("pspsnc-1.2.7503", 0) == ["-O2", "-G0"]


LW_V0_FROM_GP, LW_V0_FROM_A0, SETS_GP = 0x8F820010, 0x8C820010, 0x279C7FF0  # lw $v0, 0x10($gp|$a0); addiu $gp, $gp
VFPU_WITH_28_AS_RS = 0x34 << 26 | 28 << 21  # a VFPU instruction (not a load) whose vector register reads as $gp


def _gp_program(text: list[int], rels: list[tuple[int, int]] = (), e_type: int = ET_PSP_PRX) -> bytes:
    """A program whose module info gives a gp value, as every PSP module's does: its code `text`, its code's
    relocations `rels`."""
    code = struct.pack(f"<{len(text)}I", *text)
    return _elf([(".text", SHT_PROGBITS, SHF_ALLOC | SHF_EXECINSTR, 0, code, 0, 0),
                 (".rodata.sceModuleInfo", SHT_PROGBITS, SHF_ALLOC, len(code), MODULE_INFO, 0, 0),
                 (".rel.text", SHT_PSP_REL, 0, 0, _rels(rels), 0, 1)], e_type=e_type)


def test_small_data_is_the_programs_own_evidence_not_its_gp_value():
    """Every PSP module's info has a gp value; a PSP game with debug symbols has one in all 29 of its modules, none of
    which reads through $gp, and its code matches only with small data off. A $gp-relative relocation says small data,
    and so does code that reads through $gp (the demo's linker, like the SDK's, leaves those relocations out)."""
    assert prx.gp_value(_gp_program([LW_V0_FROM_A0])) and not prx.uses_small_data(_gp_program([LW_V0_FROM_A0]))
    assert prx.uses_small_data(_gp_program([LW_V0_FROM_A0], [(0, R_MIPS_GPREL16)]))  # the relocation alone
    assert prx.uses_small_data(_gp_program([LW_V0_FROM_GP]))  # the code alone
    assert prx.uses_small_data(_gp_program([LW_V0_FROM_GP], e_type=ET_EXEC))  # a fixed program, without relocations
    assert not prx.uses_small_data(_gp_program([SETS_GP, LW_V0_FROM_A0], e_type=ET_EXEC))  # setting $gp isn't using it
    assert not prx.uses_small_data(_gp_program([VFPU_WITH_28_AS_RS]))  # (a real game's VFPU code read as $gp once)
    assert not prx.uses_small_data(_gp_program([LW_V0_FROM_A0], [(0, R_MIPS_HI16), (4, R_MIPS_LO16)]))


def test_a_project_without_small_data_builds_with_it_off_whatever_its_gp():
    gp = 0x088E9410
    off = {"platform": "psp", "kind": "rom", "gp": gp, "rom": {"small_data": False}}
    on = {**off, "rom": {"small_data": True}}
    assert consoles.default_flags(off, "mwccpsp-3.0.1-b219") == ["-O3", "-sdatathreshold", "0"]
    assert consoles.default_flags(on, "mwccpsp-3.0.1-b219") == ["-O3", "-sdatathreshold", "8"]
    assert consoles.default_flags(off, "psp-gcc-1.7.1") == ["-O2", "-G0"]
    assert consoles.small_gp(off) == 0 and consoles.small_gp(on) == gp
    old = {"platform": "psp", "kind": "rom", "gp": gp, "rom": {}}  # imported before small data was read: its gp says
    assert consoles.default_flags(old, "psp-gcc-1.7.1") == ["-O2", "-G8"]
    assert PSP.read_game(_gp_program([LW_V0_FROM_A0])).info["small_data"] is False
    assert PSP.read_game(_prx()).info["small_data"] is True


def test_a_program_without_a_note_that_moves_registers_with_or_is_sncs():
    """SNC leaves no .comment note; its code copies registers with `or rd, rs, $zero` where GCC and CodeWarrior use
    `addu`. The stubs don't count."""
    def program(move: int) -> bytes:
        code = struct.pack("<4I", move, move, 0x03E00008, 0)
        return _elf([(".text", SHT_PROGBITS, SHF_ALLOC | SHF_EXECINSTR, BASE, code, 0, 0),
                     (".sceStub.text", SHT_PROGBITS, SHF_ALLOC | SHF_EXECINSTR, BASE + 0x10,
                      struct.pack("<3I", OR_MOVE, OR_MOVE, OR_MOVE), 0, 0)], e_type=ET_EXEC)
    OR_MOVE, ADDU_MOVE = 0x00801025, 0x00801021  # or v0, a0, $zero; addu v0, a0, $zero
    assert prx.guess_compiler(program(OR_MOVE)) == "SNC"
    assert PSP.default_compiler({"compiler_guess": "SNC"}, "") == "pspsnc-1.2.7503"
    assert prx.guess_compiler(program(ADDU_MOVE)) == "PSPGCC-1.7"


def test_vfpu_code_is_set_aside_and_plain_code_isnt():
    vfpu = struct.pack("<4I", 0xD8A00000, 0xF8800000, 0x03E00008, 0)  # lv.q C000, 0(a1); sv.q C000, 0(a0); jr ra
    assert "VFPU" in PSP.asm_reason(vfpu)
    assert PSP.asm_reason(struct.pack("<4I", 0x00851021, 0x03E00008, 0, 0)) is None  # addu v0, a0, a1
    assert PSP.asm_reason(struct.pack("<3I", 0x01001021, 0x03E00008, 0)) is None  # t0 is an argument (EABI)


def test_the_allegrexs_own_instructions_are_exported_as_words():
    text = ("    /* 0 08804000 2C108500 */  max        $2, $4, $5\n"
            "    /* 4 08804004 2110A400 */  addu       $2, $5, $4\n"
            "    /* 8 08804008 00008F7C */  seb        $3, $4\n"
            "    /* C 0880400C 0000A4D8 */  lv.q       C000, 0x0($5)\n")
    out = PSP.export_asm(text).splitlines()
    assert out[0].endswith(".word 0x0085102C") and out[1].endswith("addu       $2, $5, $4")
    assert out[2].endswith(".word 0x7C8F0000") and out[3].endswith(".word 0xD8A40000")


# --------------------------------------------------------------------------------- splat's config (rom/split.py)

def test_splats_ps2_reader_takes_a_psp_program_and_its_config_names_the_psp(tmp_path):
    from decompnet.rom import split

    program = tmp_path / "game.elf"
    program.write_bytes(_with_stubs())
    original = split._readable_for_splat(program, as_ps2=True)
    assert struct.unpack_from("<I", program.read_bytes(), 36)[0] & 0x00FF0000 == 0x00920000
    assert original == _with_stubs()
    config = tmp_path / "game.yaml"
    config.write_text("options:\n  platform: ps2\n  compiler: EEGCC\n")
    split._set_platform(config, "psp")
    assert "platform: psp" in config.read_text()


def test_a_segments_second_code_section_is_a_file_of_its_own(tmp_path):
    from decompnet.rom import split

    config = tmp_path / "game.yaml"
    config.write_text("options:\n  platform: psp\nsegments:\n  - name: cod\n    type: code\n    subsegments:\n"
                      "      - [0x000000, asm, cod/000000] # .text\n      - [0x06A09C, asm, cod/06A09C] # .sceStub.text\n"
                      "      - [0x06A92C, rodata, cod/06A92C]\n")
    split._patch_config(config)
    text = config.read_text()
    assert "[0x000000, c, cod]" in text and "[0x06A09C, c, cod_06A09C]" in text


def test_the_psps_module_tables_are_read_only_data_for_splat(tmp_path):
    from decompnet.rom import Picked

    config = tmp_path / "game.yaml"
    config.write_text("options:\n  platform: psp\n  compiler: EEGCC\n\n  section_order:\n    - .text\n    - .data\n")
    PSP.add_code(config, tmp_path, "game.rom", Picked("psp", b"", "game.elf", {"compiler_guess": "MWCCPSP"}, False))
    text = config.read_text()
    assert "    - .text\n    - .rodata\n    - .data\n" in text and "compiler: MWCCPS2" in text


# ---------------------------------------------------------------------------------- the compilers themselves

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "projects" / "demo-psp"


@pytest.mark.skipif(toolchains.installed("psp-gcc-1.3.1") is None, reason="needs psp-gcc-1.3.1")
def test_the_demos_module_placed_by_the_loader_is_its_link_at_that_address():
    """The demo is linked at 0 with its relocations, which become the PSP's (build.py's prx_from_link), and at
    0x08804000: placed there, the module is that program, section by section."""
    import subprocess
    import sys

    if not (DEMO / "build" / "demo.json").exists():
        subprocess.run([sys.executable, str(DEMO / "build.py")], check=True)
    placed = _elffile(prx.relocate((DEMO / "build" / "BOOT.BIN").read_bytes()))
    linked = _elffile((DEMO / "build" / "demoq_psp.elf").read_bytes())
    for section in (s for s in linked.iter_sections() if s["sh_flags"] & SHF_ALLOC):
        mine = placed.get_section_by_name(section.name)
        assert (mine["sh_addr"], mine["sh_size"]) == (section["sh_addr"], section["sh_size"]), section.name
        assert section["sh_type"] == "SHT_NOBITS" or mine.data() == section.data(), section.name


@pytest.mark.parametrize("toolchain", [c.id for c in PSP.compilers])
def test_each_psp_compiler_builds_c(toolchain):
    from decompnet.compiler import compile_source
    from decompnet.project import compiler_config

    tc = toolchains.get(toolchain)
    if toolchains.installed(toolchain) is None or any(toolchains.installed(d) is None for d in tc.requires):
        pytest.skip(f"needs {toolchain}")
    flags = PSP.default_flags(toolchain, 0x08900000)
    result = compile_source(compiler_config(ROOT, {"compiler": {"toolchain": toolchain, "flags": flags}}),
                            "typedef int s32;\n", "s32 abs_s32(s32 v) { return v < 0 ? -v : v; }")
    assert result.ok, result.log
    elf = _elffile(result.obj)
    assert elf["e_machine"] == "EM_MIPS" and elf.little_endian


SNC = "pspsnc-1.2.7503"


@pytest.mark.skipif(any(toolchains.installed(t) is None for t in (SNC, "wibo", "psp-gcc-1.3.1", "binutils-mips")),
                    reason=f"needs {SNC} (proprietary: installed from Setup), wibo, psp-gcc-1.3.1 and binutils-mips")
def test_the_demo_built_with_snc_is_guessed_matches_and_rebuilds(tmp_path):
    """No SN-built PSP game is at hand (the readable one is CodeWarrior's): the demo built with SNC stands in. Its
    import guesses SNC from the code alone, its C matches with SNC's -O2 -G8, and the export rebuilds it."""
    import subprocess
    import sys

    from decompnet.app.engine import RomNetwork

    from .test_consoles import FUNCTION, ZLIB, _function

    if not ZLIB.exists():
        pytest.skip("needs zlib (projects/zlib-n64/prepare.py)")
    build = DEMO / "build" / SNC
    if not (build / "demo.json").exists():
        subprocess.run([sys.executable, str(DEMO / "build.py"), SNC], check=True)
    assert prx.guess_compiler(prx.relocate((build / "BOOT.BIN").read_bytes())) == "SNC"
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    slug = net.import_rom(build / "BOOT.BIN", confirmed=True, log=lambda *_: None)
    compiler = net.overview(slug)["compiler"]
    assert (compiler["toolchain"], compiler["flags"]) == (SNC, ["-O2", "-G8"])
    name, source = _function(net, slug, json.loads((build / "demo.json").read_text())["symbols"], FUNCTION)
    agent, _ = net.local_agent("snc-agent")
    net.claim(agent, slug, name)
    right = net.attempt(agent, name, source, slug)
    assert right["matched"] and right["score"] == 0, right["text"]
    # a function written as a class's method: SNC's name for it (__0fESaveN...) is given back to the game's
    name, source = _function(net, slug, json.loads((build / "demo.json").read_text())["symbols"], "checksum_save")
    method = source.replace(f"u32 {name}(", f"struct Save {{ static u32 {name}(const u8 *, s32); }};\nu32 Save::{name}(", 1)
    net.claim(agent, slug, name)
    assert net.attempt(agent, name, method, slug)["matched"]
    res = net.export_rom(slug, log=lambda *_: None)  # (the method built as .cpp, to the same bytes)
    assert res["identical"], res.get("log") or res.get("detail")


def test_sncs_cpp_names_are_given_back_to_the_games():
    """SN's SNC writes an identifier's length as a letter (A-Z 0-25, a-z 26-51): a method an agent wrote, a function
    C++ compiled, one in a namespace; a name that isn't the game's stays."""
    from io import BytesIO

    from elftools.elf.elffile import ELFFile

    from decompnet.elfobj import unmangle

    from .test_ps2_debug import STB_GLOBAL, _elf, _symbols

    names = ["__0fEMathNfunc_08804388iii", "__0FTplain_func_088046ACi", "__0f5CnsCInNfunc_08804594iT",
             "__0fnA_very_long_class_name_of_forty_chars_xma_function_name_longer_than_twenty_sixv", "__0fEMathBmv"]
    table, strings = _symbols([(n, 0, 8, STB_GLOBAL, STT_FUNC, 1) for n in names])
    obj = _elf([(".text", SHT_PROGBITS, SHF_ALLOC | SHF_EXECINSTR, 0, bytes(8), 0),
                (".symtab", SHT_SYMTAB, 0, 0, table, 3), (".strtab", SHT_STRTAB, 0, 0, strings, 0)])
    game = {"func_08804388", "plain_func_088046AC", "func_08804594", "a_function_name_longer_than_twenty_six"}
    renamed = ELFFile(BytesIO(unmangle(obj, game.__contains__))).get_section_by_name(".symtab").iter_symbols()
    assert [s.name for s in renamed][1:] == ["func_08804388", "plain_func_088046AC", "func_08804594",
                                             "a_function_name_longer_than_twenty_six", "__0fEMathBmv"]


@pytest.mark.skipif(toolchains.installed("psp-gcc-1.3.1") is None, reason="needs psp-gcc-1.3.1")
def test_a_saved_psp_project_comes_back_with_what_was_set_aside(tmp_path):
    """The demo's work saved to plain files and opened on another computer: a match judged again, and a function
    set aside (the user's word here; agents' verdicts and a boundary report's leftover are kept the same way)
    set aside again, where the restore used to give it back to the agents."""
    import subprocess
    import sys

    from decompnet.app import saves
    from decompnet.app.engine import RomNetwork

    from .test_consoles import _function

    if not (DEMO / "build" / "demo.json").exists():
        subprocess.run([sys.executable, str(DEMO / "build.py")], check=True)
    game, symbols = DEMO / "build" / "BOOT.BIN", json.loads((DEMO / "build" / "demo.json").read_text())["symbols"]
    net = RomNetwork(tmp_path / "one.db", tmp_path / "one")
    slug = net.import_rom(game, confirmed=True, log=lambda *_: None)
    agent, _ = net.local_agent("claude-opus-5.5-1")
    name, source = _function(net, slug, symbols, "abs_s32")
    net.claim(agent, slug, name)
    assert net.attempt(agent, name, source, slug)["matched"]
    aside, _ = _function(net, slug, symbols, "lerp")
    net.set_aside_by_you(aside, slug, "hand-written")
    saves.save(net, slug, tmp_path / "save")

    fresh = RomNetwork(tmp_path / "two.db", tmp_path / "two")
    out = saves.restore(fresh, str(tmp_path / "save"), game, log=lambda *_: None)
    assert out["matched"] == 1 and out["lost"] == []
    row = fresh._function(aside, out["slug"])
    assert row["status"] == "asm" and "hand-written" in json.loads(row["meta"])["asm_code"]


@pytest.mark.skipif(toolchains.installed("psp-gcc-1.3.1") is None, reason="needs psp-gcc-1.3.1")
def test_a_function_end_report_and_a_split_take_the_address_as_hex_writes_it(tmp_path):
    """An agent's report_boundary and split_function on the demo: PSP addresses have seven digits (0x8804200), which
    were refused as "not an address like 0x80001234"."""
    import subprocess
    import sys

    from decompnet.app import tools
    from decompnet.app.engine import RomNetwork

    if not (DEMO / "build" / "demo.json").exists():
        subprocess.run([sys.executable, str(DEMO / "build.py")], check=True)
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    slug = net.import_rom(DEMO / "build" / "BOOT.BIN", confirmed=True, log=lambda *_: None)
    agent, _ = net.local_agent("claude-opus-5.5-1")
    pid = net.project_row(slug)["id"]
    one, two = net.store.all("SELECT name, vram, size FROM functions WHERE project_id=? AND status='open' AND size>40 "
                             "ORDER BY vram LIMIT 2", pid)
    said = tools.call(net, agent, slug, "report_boundary",
                      {"function": one["name"], "end": hex(one["vram"] + one["size"] - 8), "reason": "two words of padding"})
    assert said.startswith("Reported"), said
    net.resolve_boundary(slug, net.cfg(net.project_row(slug))["boundaries"][-1]["id"], True)
    assert net._function(f"pad_{one['vram'] + one['size'] - 8:08X}", slug)["status"] == "asm"
    said = tools.call(net, agent, slug, "split_function", {"function": two["name"], "at": hex(two["vram"] + 16)})
    assert said.startswith("✅"), said


def _module(name: bytes, *, memsz: int = 0x2000, symbol: str = "") -> bytes:
    """A PRX module named `name`: a function that calls itself (a jump the loader relocates), one import stub, its
    module info and a pointer in .data; its .bss to `memsz`."""
    text = struct.pack("<4I", 0x0C000000, 0, 0x03E00008, 0)
    info = struct.pack("<HH28sI", 0, 1, name, 0)
    sections = [
        (".text", SHT_PROGBITS, SHF_ALLOC | SHF_EXECINSTR, 0, text, 0, 0),
        (".sceStub.text", SHT_PROGBITS, SHF_ALLOC | SHF_EXECINSTR, 0x10, struct.pack("<2I", 0x03E00008, 0), 0, 0),
        (".rodata.sceModuleInfo", SHT_PROGBITS, SHF_ALLOC, 0x18, info, 0, 0),
        (".data", SHT_PROGBITS, SHF_ALLOC | SHF_WRITE, 0x3C, struct.pack("<I", 0x8), 0, 0),
        (".bss", SHT_NOBITS, SHF_ALLOC | SHF_WRITE, 0x40, b"", 0, 0),
        (".rel.text", SHT_PSP_REL, 0, 0, _rels([(0, R_MIPS_26)]), 0, 1),
        (".rel.data", SHT_PSP_REL, 0, 0, _rels([(0x3C, R_MIPS_32)]), 0, 4),
    ]
    if symbol:
        symtab, strtab = _symbols([(symbol, 0, STT_FUNC, 1)])
        sections += [(".symtab", SHT_SYMTAB, 0, 0, symtab, len(sections) + 2, 0), (".strtab", SHT_STRTAB, 0, 0, strtab, 0, 0)]
    return _elf(sections, memsz=memsz)



def _named_main() -> bytes:
    data = bytearray(_prx())
    info = _elffile(bytes(data)).get_section_by_name(".rodata.sceModuleInfo")
    data[info["sh_offset"] + 4:info["sh_offset"] + 8] = b"main"
    return bytes(data)



def _game_with_modules(**module_b) -> bytes:
    return _iso({"PSP_GAME/PARAM.SFO": _sfo(PARAMS), "PSP_GAME/SYSDIR/BOOT.BIN": _named_main(),
                 "PSP_GAME/SYSDIR/UPDATE/EBOOT.BIN": b"~PSP" + bytes(300),  # the system update: not the game's
                 "PSP_GAME/USRDIR/ENGINE.BIN": _module(b"engine", memsz=0x12345),  # a module by what it is, not its name
                 "PSP_GAME/USRDIR/GMODULE/MENU.PRX": _module(b"menu", **module_b),
                 "PSP_GAME/USRDIR/MAIN_DBG.PRX": _named_main(),  # a debug build of the main program
                 "PSP_GAME/USRDIR/MODULE/LIBFONT.PRX": b"~SCE" + bytes(300),
                 "PSP_GAME/USRDIR/SOUND.BIN": bytes(4096)})



def test_a_discs_other_modules_are_laid_out_one_after_another_after_the_main_program():
    picked = PSP.read_game(_game_with_modules())
    first = BASE + 0x5000  # the main program's end, its .bss included, is a page's start
    second = first + 0x13000  # the next page after the first module's .bss
    assert [(o[0], o[2]) for o in picked.overlays] == [("engine", first), ("menu", second)]
    engine = picked.overlays[0]
    assert struct.unpack_from("<I", engine[1])[0] == 0x0C000000 | first >> 2  # its jump relocated to its own address
    assert struct.unpack_from("<I", engine[1], 0x3C)[0] == first + 8
    assert engine[4] == [(0, "c"), (0x10, "c"), (0x18, "rodata"), (0x3C, "data")]
    assert picked.info["stubs"] == [[first + 0x10, first + 0x18], [second + 0x10, second + 0x18]]
    assert picked.info["modules_left_out"] == [("PSP_GAME/USRDIR/MODULE/LIBFONT.PRX", "encrypted")]
    assert [prx.module_name(p) for p in picked.programs] == [b"engine", b"menu"]
    assert PSP.read_game(_game_with_modules()).overlays == picked.overlays  # the same disc splits the same way



def test_a_module_whose_code_wouldnt_fit_below_the_main_programs_jumps_is_left_out():
    picked = PSP.read_game(_iso({"PSP_GAME/PARAM.SFO": _sfo(PARAMS), "PSP_GAME/SYSDIR/BOOT.BIN": _named_main(),
                                 "PSP_GAME/USRDIR/A.PRX": _module(b"a", memsz=0x10000000 - BASE - 0x5000),
                                 "PSP_GAME/USRDIR/B.PRX": _module(b"b")}))
    assert [o[0] for o in picked.overlays] == ["a"]
    assert picked.info["modules_left_out"] == [("PSP_GAME/USRDIR/B.PRX", "no room below 0x10000000")]



def test_each_module_is_a_segment_of_its_own_in_splats_config_and_the_log_says_whats_left_out(tmp_path):
    import yaml

    picked = PSP.read_game(_game_with_modules())
    (tmp_path / "game.rom").write_bytes(bytes(0x100))
    config = tmp_path / "game.yaml"
    config.write_text("options:\n  platform: psp\n  compiler: EEGCC\n  target_path: game.rom\n\n  section_order:\n"
                      "    - .text\nsegments:\n  - name: cod\n    type: code\n    start: 0\n    vram: 0x08804000\n"
                      "    subsegments:\n      - [0x0, c, cod]\n  - [0x100]\n")
    said = []
    image = PSP.add_code(config, tmp_path, "game.rom", picked, log=said.append)
    segments = yaml.safe_load(config.read_text())["segments"]
    engine = next(s for s in segments if isinstance(s, dict) and s["name"] == "ovl_engine")
    start = engine["start"]
    assert engine["vram"] == BASE + 0x5000
    assert engine["subsegments"] == [[start, "c", "ovl_engine"], [start + 0x10, "c", "ovl_engine_000010"],
                                     [start + 0x18, "rodata", "ovl_engine/000018"],
                                     [start + 0x3C, "data", "ovl_engine/00003C"]]
    assert (tmp_path / image).read_bytes()[start:start + 0x40] == picked.overlays[0][1]
    assert any("1 module on the disc left out, encrypted" in line and "LIBFONT.PRX" in line for line in said)



def test_a_modules_own_names_join_the_main_programs_and_a_name_it_has_gets_its_address():
    main = prx.relocate(_prx())
    module = prx.relocate(_module(b"menu", symbol="callee"), 0x08900000)
    debug = PSP.debug_info(main, set()).merge(PSP.debug_info(module, set()))
    named = {n.name: n.address for n in debug.named}
    assert named["callee"] == BASE + 0x20 and named["callee_08900000"] == 0x08900000



def test_modules_whose_small_data_isnt_the_main_programs_build_with_their_own_flags():
    """The main program reads through $gp (_prx), its modules don't: each module's segment is a range of the
    project's compiler with small data off, set after the compiler test has chosen that compiler."""
    from decompnet.consoles.psp import set_module_small_data

    picked = PSP.read_game(_game_with_modules())
    first, second = BASE + 0x5000, BASE + 0x5000 + 0x13000
    assert picked.info["small_data"] is True
    assert picked.info["small_data_differs"] == [[first, first + 0x40], [second, second + 0x40]]

    class Net:
        def __init__(self):
            self.ranges = []

        def project_row(self, slug):
            return {}

        def cfg(self, proj):
            return {"gp": BASE + 0x4000, "compiler": {"toolchain": "mwccpsp-3.0.1-b219", "flags": ["-O3"]},
                    "rom": picked.info}

        def set_compiler_ranges(self, slug, spans, toolchain, flags):
            self.ranges.append((spans, toolchain, flags))

    net = Net()
    set_module_small_data(net, "game", log=lambda *_: None)
    assert net.ranges == [(picked.info["small_data_differs"], "mwccpsp-3.0.1-b219", ["-O3", "-sdatathreshold", "0"])]
    alike = Net()
    picked.info["small_data_differs"] = []
    set_module_small_data(alike, "game", log=lambda *_: None)
    assert alike.ranges == []


def test_every_modules_stubs_are_set_aside():
    store = _Store([(BASE + 0x100, "open"), (0x08900010, "open"), (0x08900000, "open")])
    set_stubs_aside(_Net(store, [[BASE + 0x100, BASE + 0x108], [0x08900010, 0x08900018]]), "game", log=lambda *_: None)
    rows = {r["vram"]: r["status"] for r in store.all("SELECT * FROM functions")}
    assert rows == {BASE + 0x100: "asm", 0x08900010: "asm", 0x08900000: "open"}



def _cso(image: bytes, *, align: int = 2, magic: bytes = b"CISO") -> bytes:
    """A CSO of `image`: every other block deflated, the rest stored (as tools do when deflating doesn't pay), each
    block starting at a multiple of 1 << align."""
    import zlib

    block = 2048
    count = -(-len(image) // block)
    at = 0x18 + 4 * (count + 1)
    index, body = [], bytearray()
    for n in range(count):
        at += -at % (1 << align)
        body += bytes(at - 0x18 - 4 * (count + 1) - len(body))
        raw = image[n * block:(n + 1) * block]
        if n % 2:
            packer = zlib.compressobj(9, zlib.DEFLATED, -15)
            raw, stored = packer.compress(raw) + packer.flush(), 0
        else:
            stored = 0x80000000
        index.append(at >> align | stored)
        body += raw
        at += len(raw)
    index.append(at >> align)
    return struct.pack("<4sIQIBB2x", magic, 0x18, len(image), block, 1, align) + struct.pack(f"<{count + 1}I", *index) \
        + body



def test_a_cso_image_is_read_in_place_and_imports_as_its_disc_does(tmp_path):
    from decompnet.consoles.ps2 import cso

    disc = _game_with_modules()
    packed = _cso(disc)
    with cso.Cso(packed) as image:
        assert image.read() == disc
        image.seek(2048 * 3 + 100)  # across a stored block and a deflated one
        assert image.read(4000) == disc[2048 * 3 + 100:2048 * 3 + 4100]
    (tmp_path / "game.cso").write_bytes(packed)
    assert consoles.identify(tmp_path / "game.cso") is PSP and consoles.identify(packed) is PSP
    from_iso = PSP.read_game(disc)
    for picked in (PSP.read_game(tmp_path / "game.cso"), PSP.read_game(packed)):
        assert picked.data == from_iso.data and picked.overlays == from_iso.overlays
        assert picked.info["title"] == from_iso.info["title"]



def test_a_zso_or_damaged_cso_says_what_to_do():
    from decompnet.consoles.ps2 import cso

    zso = _cso(_game_with_modules(), magic=b"ZISO")
    assert consoles.identify(zso) is PSP
    with pytest.raises(RomError, match="LZ4.*decompress it to .iso"):
        PSP.read_game(zso)
    with pytest.raises(RomError, match="damaged"):
        cso.Cso(_cso(_game_with_modules())[:0x40])



def test_a_ps2_disc_in_a_cso_is_the_ps2s_and_a_gamecube_ciso_isnt_a_cso():
    from decompnet.consoles.gc_wii import scratch
    from decompnet.consoles.ps2 import cso

    ps2 = _cso(_iso({"SYSTEM.CNF": b"BOOT2 = cdrom0:\\SLUS_999.99;1\r\n", "SLUS_999.99": bytes(64)}))
    assert consoles.identify(ps2).id == "ps2"
    with pytest.raises(RomError, match="program SLUS_999.99 isn't a PlayStation 2 program"):  # (read inside the CSO)
        consoles.get("ps2").read_game(ps2)
    gc_ciso = b"CISO" + struct.pack("<I", 0x200000) + bytes(0x100)
    assert not cso.is_cso(gc_ciso) and scratch.disc_kind(gc_ciso) == "packed" and scratch.disc_kind(ps2) is None



@pytest.mark.skipif(any(toolchains.installed(t) is None for t in PSP.demo_tools), reason="needs the PSP demo's tools")
def test_a_disc_with_a_second_module_imports_and_its_export_rebuilds_both_byte_for_byte(tmp_path):
    """The demo's module twice on one disc (it has no module info, so no name to tell a copy of the main program by):
    the copy is a segment of its own at the next free page, its functions are the main program's again there, and the
    export rebuilds the whole image."""
    import subprocess
    import sys

    from decompnet.app.engine import RomNetwork

    if not (DEMO / "build" / "demo.json").exists():
        subprocess.run([sys.executable, str(DEMO / "build.py")], check=True)
    main = (DEMO / "build" / "BOOT.BIN").read_bytes()
    (tmp_path / "game.iso").write_bytes(_iso({"PSP_GAME/PARAM.SFO": _sfo(PARAMS), "PSP_GAME/SYSDIR/BOOT.BIN": main,
                                              "PSP_GAME/USRDIR/COPY.PRX": main}))
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    slug = net.import_rom(tmp_path / "game.iso", confirmed=True, log=lambda *_: None)
    pid = net.project_row(slug)["id"]
    units = [r["unit"] for r in net.store.all("SELECT unit FROM functions WHERE project_id=?", pid)]
    assert units.count("ovl_copy") > 15 and len([u for u in units if not u.startswith("ovl_")]) == units.count("ovl_copy")
    res = net.export_rom(slug, log=lambda *_: None)
    assert res["identical"], res.get("log")
