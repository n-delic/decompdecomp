"""64-bit (PE32+/AMD64) Windows programs: reading their headers and imports, finding their functions in .pdata and
calls, and the AMD64 relocations their code needs. Hand-made programs, so no compiler is needed."""

import struct

import pytest

from decompnet.consoles.pc import scratch as pc
from decompnet.forks.project import ForkError


def _program(code: bytes = b"", pdata: list[tuple[int, int]] = (), imports: list[tuple[str, int]] | None = None,
             data: bytes = b"", base: int = 0x140000000) -> bytes:
    """A PE32+ image with a .text at RVA 0x1000, a .pdata at 0x2000, a .data at 0x3000 and an import table at 0x4000."""
    text_va, pdata_va, data_va, import_va = 0x1000, 0x2000, 0x3000, 0x4000
    sections: list[tuple[str, int, bytes, int]] = []
    if code:
        sections.append((".text", text_va, code, 0x60000020))
    if pdata:
        sections.append((".pdata", pdata_va, b"".join(struct.pack("<III", a, b, 0) for a, b in pdata), 0x40000040))
    if data:
        sections.append((".data", data_va, data, 0xC0000040))
    import_bytes = b""
    if imports:
        lookup_at, iat_at, names_at = 40, 40 + 8 * (len(imports) + 1), 40 + 16 * (len(imports) + 1)
        names, entries = b"", b""
        for name, ordinal in imports:
            if ordinal:
                entries += struct.pack("<Q", (1 << 63) | ordinal)
            else:
                entries += struct.pack("<Q", import_va + names_at + len(names))  # a hint, then the name
                names += struct.pack("<H", 0) + name.encode() + b"\0"
        dll = b"KERNEL32.dll\0"
        dll_at = import_va + names_at + len(names)
        import_bytes = (struct.pack("<IIIII", import_va + lookup_at, 0, 0, dll_at, import_va + iat_at) + bytes(20)
                        + entries + bytes(8) + entries + bytes(8) + names + dll)
        sections.append((".rdata", import_va, import_bytes, 0x40000040))
    raw, headers = {}, bytearray(0x400)
    headers[:2] = b"MZ"
    struct.pack_into("<I", headers, 0x3C, 0x80)
    headers[0x80:0x84] = b"PE\0\0"
    opt_size = 0xF0
    struct.pack_into("<HHIIIHH", headers, 0x84, 0x8664, len(sections), 0, 0, 0, opt_size, 0x22)
    opt = 0x98
    struct.pack_into("<HBB", headers, opt, 0x20B, 14, 16)
    struct.pack_into("<III", headers, opt + 16, text_va, text_va, 0)  # entry and code
    struct.pack_into("<Q", headers, opt + 24, base)
    struct.pack_into("<II", headers, opt + 32, 0x1000, 0x200)
    struct.pack_into("<I", headers, opt + 56, 0x5000)  # SizeOfImage
    struct.pack_into("<I", headers, opt + 60, 0x400)
    struct.pack_into("<H", headers, opt + 68, 3)  # console subsystem
    struct.pack_into("<I", headers, opt + 108, 16)
    if pdata:
        struct.pack_into("<II", headers, opt + 112 + 8 * pc.EXCEPTION_DIR, pdata_va, 12 * len(pdata))
    if imports:
        struct.pack_into("<II", headers, opt + 112 + 8 * 1, import_va, len(import_bytes))
    at = 0x400
    for i, (name, va, body, flags) in enumerate(sections):
        raw[va] = at
        struct.pack_into("<8sIIIIIIHHI", headers, opt + opt_size + 40 * i, name.encode(), len(body), va,
                         (len(body) + 0x1FF) & ~0x1FF, at, 0, 0, 0, 0, flags)
        at += (len(body) + 0x1FF) & ~0x1FF
    image = bytearray(headers) + bytearray(at - len(headers))
    for _, va, body, _ in sections:
        image[raw[va]:raw[va] + len(body)] = body
    return bytes(image)


def _call(target: int, at: int) -> bytes:
    """`call rel32` from the address `at` to `target`."""
    return b"\xe8" + struct.pack("<i", target - (at + 5))


def test_a_64_bit_program_and_its_imports_are_read():
    image = _program(code=b"\xc3", imports=[("Sleep", 0), ("GetTickCount", 0), ("ExitProcess", 123)])
    assert pc.is_pe(image[:4096])
    pe = pc.read_pe(image)
    assert pe.x64 and pe.image_base == 0x140000000 and pe.entry == 0x140001000
    assert pe.dlls == ["KERNEL32.dll"]
    # 8-byte IAT slots, and an ordinal in bit 63
    assert set(pe.imports.values()) == {"GetTickCount", "Sleep", "KERNEL32_123"}
    assert sorted(pe.imports) == [0x140004048, 0x140004050, 0x140004058]


def test_an_x64_code_section_is_pdata_functions_plus_leaf_calls():
    """Adjacent .pdata records are two functions (their ends merely touch); a leaf nothing describes but something
    calls is found by the call."""
    code = bytearray(0x40)
    code[:5] = _call(0x1000 + 0x30, 0x1000)  # first function calls the leaf at 0x1030
    code[0x10:0x11] = b"\xc3"
    code[0x20:0x21] = b"\xc3"
    code[0x30:0x31] = b"\xc3"
    pe = pc.read_pe(_program(code=bytes(code), pdata=[(0x1000, 0x1010), (0x1010, 0x1020)]))
    rows = pc.x64_functions(pe)
    assert [(r["entry"] - pe.image_base, r["end"] - pe.image_base) for r in rows] == \
        [(0x1000, 0x1010), (0x1010, 0x1020), (0x1030, 0x1040)]


def test_x64_calls_and_rip_relative_data_are_rel32_relocations():
    code = bytearray(0x40)
    code[:5] = _call(0x140001030, 0x140001000)
    code[5:11] = b"\x8b\x05\x00\x00\x00\x00"  # mov eax, [rip + disp32], filled below
    code[11] = 0xC3  # ret
    struct.pack_into("<i", code, 7, 0x140003000 - (0x140001005 + 6))
    pe = pc.read_pe(_program(code=bytes(code), pdata=[(0x1000, 0x1020)], data=b"\x2a\x00\x00\x00"))
    functions = pc.analyse(pe, pc.x64_functions(pe))
    first = next(f for f in functions if f.addr == 0x140001000)
    assert first.relocs == [(0x1, pc.X64_REL32, "func_140001030", 0), (0x7, pc.X64_REL32, "D_140003000", 0)]
    assert first.name == "func_140001000"


def test_an_x64_switch_table_is_relative_entries_in_the_function():
    """MSVC's x64 dense switch: `lea rcx, [rip + __ImageBase]`, `mov eax, [rcx + rax*4 + table]`, `add rax, rcx`,
    `jmp rax`, then the table's 4-byte RVAs, which the object holds as ADDR32NB relocations to case labels."""
    code = bytearray()
    base, lea_at = 0x140000000, 0x140001000
    code += b"\x48\x8d\x0d\x00\x00\x00\x00"  # lea rcx, [rip + __ImageBase]
    struct.pack_into("<i", code, 3, base - (lea_at + 7))
    code += b"\x8b\x84\x81\x00\x00\x00\x00"  # mov eax, [rcx + rax*4 + table_rva]
    struct.pack_into("<i", code, len(code) - 4, 0x1030)
    code += b"\x48\x03\xc1\xff\xe0"  # add rax, rcx; jmp rax
    table_at = 0x1030  # an RVA: the load's disp32 holds it
    code += b"\xcc" * (table_at - 0x1000 - len(code))  # the padding between a function's code and its table
    code += struct.pack("<II", 0x1008, 0x100c)  # two case labels inside the function
    image = _program(code=bytes(code), pdata=[(0x1000, 0x1030)])
    pe = pc.read_pe(image)
    functions = pc.analyse(pe, pc.x64_functions(pe))
    function = next(f for f in functions if f.addr == 0x140001000)
    assert function.size == 0x38  # through the table
    assert function.relocs == [
        (0x3, pc.X64_REL32, "__ImageBase", 0),
        (0x0A, pc.X64_ADDR32NB, None, 0x30),  # the load's disp32: the table's offset in the function
        (0x30, pc.X64_ADDR32NB, None, 0x8),
        (0x34, pc.X64_ADDR32NB, None, 0xC)]


def test_an_x64_program_that_names_its_compiler_builds_with_its_x64_compiler():
    """A 64-bit game's Rich header names Visual C++ 2017; the project builds it with the pack's 64-bit compiler."""
    from .test_pc import _windows_program

    built = pc.describe(_windows_program(machine=0x8664, rich=[(0x104, 27023, 100)], linker=(14, 16)))
    assert built.x64 and built.version == "14.1" and built.pack_version == "14.16-x64"
    assert built.blockers() == []


def test_a_32_bit_program_still_gets_its_32_bit_compiler():
    from .test_pc import _windows_program

    built = pc.describe(_windows_program(rich=[(0x104, 27023, 100)], linker=(14, 16)))
    assert not built.x64 and built.pack_version == "14.16"


def test_an_x64_program_that_is_not_a_windows_program_is_refused():
    with pytest.raises(ForkError, match="isn't a Windows program"):
        pc.read_pe(b"MZ" + bytes(0x3E))
