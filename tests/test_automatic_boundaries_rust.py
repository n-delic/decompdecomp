"""Parity and fallback tests for the Rust `automatic-boundaries` gateway subcommand."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from decompnet.app import mcp_gateway
from decompnet.rom import automatic_boundaries as ab
from decompnet.rom.split import AsmLine, RomFunction, Segment


def _make_sample(big_endian=True, pad_large=False):
    fmt = '>I' if big_endian else '<I'
    import struct

    # Functions:
    # 1. func_80001000: jal func_80002000; nop; beq $zero,$zero,+2; bgezal +1; jr $ra; lui $t0, %hi(func_80002000)
    # 2. func_80002000: jr $ra; nop
    # 3. func_80003000: single zero word (zero_entry)
    # 4. func_80004000: in a shared overlay slot
    f1 = RomFunction(
        name='func_80001000',
        vram=0x80001000,
        rom=0x1000,
        segment='main',
        asm_path=Path('main.s'),
        lines=[
            AsmLine(0x1000, 0x80001000, 0x0C000800, 'jal', 'func_80002000'),
            AsmLine(0x1004, 0x80001004, 0x00000000, 'nop', ''),
            AsmLine(0x1008, 0x80001008, 0x10000002, 'beq', '$zero, $zero, .L80001014'),
            AsmLine(0x100C, 0x8000100C, 0x04110001, 'bgezal', '$zero, .L80001014'),
            AsmLine(0x1010, 0x80001010, 0x03E00008, 'jr', '$ra'),
            AsmLine(0x1014, 0x80001014, 0x3C088000, 'lui', '$t0, %hi(func_80002000)'),
        ],
    )
    f2 = RomFunction(
        name='func_80002000',
        vram=0x80002000,
        rom=0x2000,
        segment='main',
        asm_path=Path('main.s'),
        lines=[
            AsmLine(0x2000, 0x80002000, 0x03E00008, 'jr', '$ra'),
            AsmLine(0x2004, 0x80002004, 0x00000000, 'nop', ''),
        ],
    )
    f3 = RomFunction(
        name='func_80003000',
        vram=0x80003000,
        rom=0x3000,
        segment='main',
        asm_path=Path('main.s'),
        lines=[AsmLine(0x3000, 0x80003000, 0x00000000, 'nop', '')],
    )
    f4 = RomFunction(
        name='func_80004000',
        vram=0x80004000,
        rom=0x4000,
        segment='ovl',
        asm_path=Path('ovl.s'),
        lines=[
            AsmLine(0x4000, 0x80004000, 0x03E00008, 'jr', '$ra'),
            AsmLine(0x4004, 0x80004004, 0x00000000, 'nop', ''),
        ],
    )
    functions = [f1, f2, f3, f4]

    words = [0x80001000, 0x80002000, 0x80003000, 0x80004000, 0x12345678, 0x80003000]
    image = b''.join(struct.pack(fmt, w) for w in words)
    if pad_large:
        image = image + (b'\x00' * 70000)

    # Mark offset 8 as data, but offset 20 as non-data so 0x80003000 still qualifies as a pointer.
    console = SimpleNamespace(
        big_endian=big_endian,
        import_pointer_is_data=lambda _img, off: off == 8,
    )
    starts = {f.vram for f in functions}
    slots = [Segment('ovl', 0x4000, 0x4100, 0x80004000)]
    store_tails = set()
    shared = []
    names = {f.name: f.vram for f in functions}
    return functions, image, console, starts, slots, store_tails, shared, names


@pytest.mark.parametrize('big_endian', [True, False])
@pytest.mark.parametrize('pad_large', [False, True])
def test_rust_matches_python(monkeypatch, big_endian, pad_large):
    exe = mcp_gateway.program() or mcp_gateway.built()
    if exe is None:
        pytest.skip('decomp-mcp-gateway binary not built')
    monkeypatch.setattr(mcp_gateway, 'program', lambda: exe)
    args = _make_sample(big_endian=big_endian, pad_large=pad_large)
    py_res = ab._scan_image_and_references_python(*args)
    rs_res = ab._scan_image_and_references_rust(*args)
    assert rs_res is not None
    assert rs_res == py_res


def test_fallback_when_gateway_missing(monkeypatch):
    args = _make_sample()
    monkeypatch.setattr(mcp_gateway, 'program', lambda: None)
    assert ab._scan_image_and_references_rust(*args) is None
    assert ab._scan_image_and_references(*args) == ab._scan_image_and_references_python(*args)


def test_fallback_when_gateway_fails(monkeypatch):
    args = _make_sample()
    monkeypatch.setattr(mcp_gateway, 'program', lambda: '/nonexistent/decomp-mcp-gateway')
    assert ab._scan_image_and_references_rust(*args) is None
    assert ab._scan_image_and_references(*args) == ab._scan_image_and_references_python(*args)
