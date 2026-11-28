"""Parity and fallback tests for the Rust `elf-extract` gateway subcommand."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from decompnet import elfobj
from decompnet.app import mcp_gateway


def _gateway_exe() -> Path | None:
    root = Path(__file__).resolve().parents[1]
    for rel in (
        "gateway/target/debug/decomp-mcp-gateway",
        "gateway/target/release/decomp-mcp-gateway",
        "gateway/target/checkout/decomp-mcp-gateway",
    ):
        cand = root / rel
        if cand.is_file():
            return cand
    return mcp_gateway.program()


def _build_mips_elf32(big_endian: bool) -> bytes:
    endian = ">" if big_endian else "<"
    text = struct.pack(f"{endian}4I", 0x0C000000, 0x00000000, 0x03E00008, 0x00000000)
    shstrtab = b"\0.text\0.rel.text\0.symtab\0.strtab\0.shstrtab\0"
    strtab = b"\0foo\0bar\0"

    symtab = b"\0" * 16
    # 1: foo (STT_FUNC, STB_GLOBAL, .text, 0..16)
    symtab += struct.pack(f"{endian}IIIBBH", 1, 0, 16, (1 << 4) | 2, 0, 1)
    # 2: bar (UNDEF, STB_GLOBAL)
    symtab += struct.pack(f"{endian}IIIBBH", 5, 0, 0, 1 << 4, 0, 0)

    # .rel.text: R_MIPS_26 at offset 0 -> sym 2 (bar)
    rel_text = struct.pack(f"{endian}II", 0, (2 << 8) | 4)

    text_off = 52
    rel_off = text_off + len(text)
    sym_off = rel_off + len(rel_text)
    str_off = sym_off + len(symtab)
    shstr_off = str_off + len(strtab)
    sh_off = shstr_off + len(shstrtab)

    ehdr = (
        b"\x7fELF"
        + bytes([1, 2 if big_endian else 1, 1])
        + b"\0" * 9
        + struct.pack(f"{endian}HHIIIIIHHHHHH", 1, 8, 1, 0, 0, sh_off, 0, 52, 0, 0, 40, 6, 5)
    )

    def shdr(name: int, kind: int, flags: int, off: int, size: int, link: int, info: int, entsize: int) -> bytes:
        return struct.pack(f"{endian}10I", name, kind, flags, 0, off, size, link, info, 4, entsize)

    shdrs = (
        b"\0" * 40
        + shdr(1, 1, 6, text_off, len(text), 0, 0, 0)
        + shdr(7, 9, 0, rel_off, len(rel_text), 3, 1, 8)
        + shdr(17, 2, 0, sym_off, len(symtab), 4, 1, 16)
        + shdr(25, 3, 0, str_off, len(strtab), 0, 0, 0)
        + shdr(33, 3, 0, shstr_off, len(shstrtab), 0, 0, 0)
    )
    return ehdr + text + rel_text + symtab + strtab + shstrtab + shdrs


@pytest.mark.parametrize("big_endian", [True, False])
def test_rust_elf_extract_parity(monkeypatch, big_endian):
    exe = _gateway_exe()
    if exe is None:
        pytest.skip("decomp-mcp-gateway binary not built")

    data = _build_mips_elf32(big_endian)

    monkeypatch.setattr(mcp_gateway, "program", lambda: None)
    py_list = elfobj.list_functions(data)
    py_fn = elfobj.extract_function(data, "foo")
    py_missing = elfobj.extract_function(data, "nonexistent")

    monkeypatch.setattr(mcp_gateway, "program", lambda: exe)
    rs_list = elfobj.list_functions(data)
    rs_fn = elfobj.extract_function(data, "foo")
    rs_missing = elfobj.extract_function(data, "nonexistent")

    assert rs_list == py_list == ["foo"]
    assert rs_fn == py_fn
    assert rs_missing is None and py_missing is None


def test_rust_elf_extract_fallback_when_gateway_missing(monkeypatch):
    monkeypatch.setattr(mcp_gateway, "program", lambda: None)
    data = _build_mips_elf32(True)
    assert elfobj.list_functions(data) == ["foo"]
    fn = elfobj.extract_function(data, "foo")
    assert fn is not None
    assert fn.arch == "mips"
    assert fn.big_endian is True
    assert len(fn.relocs) == 1
    assert fn.relocs[0].symbol == "bar"
