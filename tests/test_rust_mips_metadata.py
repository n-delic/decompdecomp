"""Use period compiler output to verify Rust-only dispatch and ECOFF evidence on the hosted queue."""

import hashlib
import json

import pytest

from decompnet import toolchains
from decompnet.app import mcp_gateway
from decompnet.compiler import compile_source
from decompnet.project import compiler_config
from decompnet.rom import match
from tests.test_hosted_pack import ROM, game
from tests.test_literal_pool_placement import SWITCH
from tests.test_rust_mips_link import CALLEE, DATA, VRAM, queue_parity

pytestmark = pytest.mark.skipif(mcp_gateway.built() is None, reason="the Rust gateway isn't built")
HEADER = "typedef int s32;"
PRIVATE = (
    "static int f1(int x) { return x * 3 + 1; }\n"
    "int (*chosen)(int) = f1;\n"
    "int entry(int x) { return f1(x) + chosen(x+1); }"
)


def compile_fixture(tmp_path, toolchain, flags, source):
    if not toolchains.installed(toolchain):
        pytest.skip(f"{toolchain} is not installed")
    compiler = {"toolchain": toolchain, "flags": flags}
    result = compile_source(compiler_config(tmp_path, {"compiler": compiler}), HEADER, source)
    assert result.ok, result.log
    return result.obj, compiler


def prepare_project(tmp_path, net, compiler, rom, target):
    offset = target.vram - 0x80000000
    rom[offset:offset + len(target.code)] = target.code
    (tmp_path / "rom.z64").write_bytes(rom)
    project = net.project_row("game", sync=False)
    cfg = json.loads(project["config"])
    cfg["rom"]["sha1"] = hashlib.sha1(rom).hexdigest()
    cfg["compiler"] = compiler
    with net.store.tx() as db:
        db.execute("UPDATE projects SET config=? WHERE id=?", (json.dumps(cfg), project["id"]))
        db.execute("UPDATE functions SET target=?,relocs=?,size=?,n_insns=?,rom_offset=? WHERE project_id=? AND name='f1'",
                   (target.code, json.dumps(target.relocs), len(target.code), len(target.code) // 4, offset, project["id"]))
    return {"slug": "game", "function": {"name": "f1", "vram": target.vram, "target": target.code.hex()},
            "compiler": compiler, "header_sha256": hashlib.sha256(HEADER.encode()).hexdigest()}


@pytest.mark.parametrize("via_pointer", [False, True])
def test_real_ido_private_procedures_without_elf_symbols(tmp_path, via_pointer):
    source = PRIVATE.replace("f1", "helper").replace("entry", "f1") if via_pointer else PRIVATE
    obj, compiler = compile_fixture(tmp_path, "ido-5.3", ["-O3", "-mips2", "-G", "0", "-non_shared"], source)
    net = game(tmp_path)
    project = net.project_row("game", sync=False)
    if via_pointer:
        with net.store.tx() as db:
            db.executemany("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)",
                           [(project["id"], "chosen", DATA, 4, "data"), (project["id"], "helper", CALLEE, 12, "func")])
    symbols = net.symbols(project["id"])
    empty = match.RomTarget("f1", VRAM, b"", [])
    linker = match._Linker(obj, "f1", empty, symbols, lambda *_: None)
    assert linker.elf.get_section_by_name(".mdebug") is not None
    hidden = "helper" if via_pointer else "f1"
    assert hidden not in {entry.name for entry in linker.symtab.iter_symbols()}
    result = linker.run()
    assert result.linked_code and not result.problems
    relocs = [(r.offset, r.type, symbols.resolve(r.symbol)) for r in result.candidate.relocs]
    assert all(address is not None for _, _, address in relocs)
    target = match.RomTarget("f1", VRAM, result.linked_code, relocs)
    rom = bytearray(ROM)
    if via_pointer:
        # The data pointer is section-relative in ELF and names the hidden ECOFF procedure.
        pointer = CALLEE.to_bytes(4, "big")
        rom[DATA - 0x80000000:DATA - 0x80000000 + 4] = pointer
        assert (DATA, 4) in result.missing_data_reads
    request = prepare_project(tmp_path, net, compiler, rom, target)
    scores = queue_parity(tmp_path, net, request, [source, source.replace("* 3", "* 5")])
    assert scores[0] == 0 and scores[1] > 0


@pytest.mark.parametrize("corrupt", [False, True])
def test_real_gcc_dispatch_verifies_a_late_jump_table_entry(tmp_path, corrupt):
    source = SWITCH.replace("void f(", "void f1(")
    flags = ["-G0", "-mips3", "-mgp32", "-O2"]
    obj, compiler = compile_fixture(tmp_path, "gcc-2.7.2-kmc", flags, source)
    net = game(tmp_path)
    project = net.project_row("game", sync=False)
    with net.store.tx() as db:
        db.executemany("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)",
                       [(project["id"], "call", CALLEE, 4, "func"), (project["id"], "g", 0x80002700, 24, "data")])
    symbols = net.symbols(project["id"])
    empty = match.RomTarget("f1", VRAM, b"", [])
    linker = match._Linker(obj, "f1", empty, symbols, lambda *_: None)
    section, start, size = linker.function()
    linker.fn_shndx = section
    code = linker.elf.get_section(section).data()[start:start + size]
    raw = match.raw_relocs(linker.elf, section, start, size, code)

    table_refs = [row for row in raw if row["type"] in (5, 6) and
                  isinstance(row["symtab"].get_symbol(row["symidx"])["st_shndx"], int) and
                  row["symtab"].get_symbol(row["symidx"])["st_shndx"] != section]
    assert len(table_refs) == 2
    table_section = table_refs[0]["symtab"].get_symbol(table_refs[0]["symidx"])["st_shndx"]
    entries = linker.elf.get_section(table_section).data()
    words = []
    for at, kind, symbol in linker._relocs_in(table_section):
        if kind == 2:
            address, own = linker._pointee(symbol, int.from_bytes(entries[at:at + 4], "big"))
            assert address is not None and own is None
            words.append(address)
    assert len(words) == 6 and len(set(words)) == 6
    table = DATA
    refs = [(row["off"], row["type"], table) for row in table_refs]
    baseline = match.link(obj, "f1", match.RomTarget("f1", VRAM, bytes(size), refs), symbols, lambda *_: None)
    assert baseline.linked_code and not baseline.problems
    # Recreate all references from the real linked instruction stream.
    refs = [(row.offset, row.type,
             table if row.symbol == f"D_{table:08X}" else (VRAM if row.symbol == "f1" else symbols.resolve(row.symbol)) + (
                 row.addend << 2 if row.type == 4 else row.addend if row.type == 6 else 0))
            for row in baseline.candidate.relocs]
    target = match.RomTarget("f1", VRAM, baseline.linked_code, refs)
    if corrupt:
        words[-1] = words[1]
    data = b"".join(word.to_bytes(4, "big") for word in words)

    def read(address, size):
        offset = address - table
        return data[offset:offset + size] if 0 <= offset <= offset + size <= len(data) else None

    python = match.link(obj, "f1", target, symbols, read)
    assert python.instructions_exact and python.exact is not corrupt, python.problems
    assert python.required_data_reads.count((table, len(data), data)) >= 2, python.required_data_reads
    rom = bytearray(ROM)
    rom[table - 0x80000000:table - 0x80000000 + len(data)] = data
    request = prepare_project(tmp_path, net, compiler, rom, target)
    assert queue_parity(tmp_path, net, request, [source]) == [int(corrupt)]
