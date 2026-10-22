"""A PS2 program's layout and compiler, read before anything is built: these need no compiler, so CI runs them."""

import hashlib
import subprocess

import pytest

from decompnet import toolchains


def test_codewarriors_link_names_the_compiler_whatever_the_style(monkeypatch):
    """A 2002 game linked by CodeWarrior (.mwcats) left a nop after only 19% of its returns, under the style rule's
    60%, and was guessed EE-GCC: every compile came out wrong. The linker's section says CodeWarrior on its own."""
    from decompnet.consoles.ps2 import elf as ps2

    monkeypatch.setattr(ps2, "code_style", lambda data: (2245, 0.19, 3.71, 0.0))
    monkeypatch.setattr(ps2, "_sections", lambda data: {".mwcats": b"", ".comment": b"MW MIPS C Compiler (2.4.1.01)\0"})
    assert ps2.guess_compiler(b"") == "MWCPS2-3"
    monkeypatch.setattr(ps2, "_sections", lambda data: {".comment": b"MW MIPS C Compiler (2.4.1.01)\0"})
    assert ps2.guess_compiler(b"") == "EEGCC"  # a library's note alone still doesn't


def test_an_empty_section_below_the_code_does_not_shift_the_split(tmp_path):
    """A CodeWarrior-linked PS2 game has an empty section at 0x100000 and its code at 0x200000: objcopy's flat image
    started at the empty one, splat counted from the code, and every function was read from the zeros between."""
    from decompnet.rom import split

    if toolchains.installed("binutils-mips") is None:
        pytest.skip("needs binutils-mips")
    bin_dir = toolchains.binary("binutils-mips").parent
    tools = {name: bin_dir / f"mips-ps2-decompals-{name}" for name in ("as", "ld", "objcopy")}
    (tmp_path / "a.s").write_text(".section main,\"awx\",@progbits\n  jr $ra\n  nop\n.section indata,\"aw\",@progbits\n")
    subprocess.run([tools["as"], "-EL", "-march=r5900", "-o", tmp_path / "a.o", tmp_path / "a.s"], check=True)
    (tmp_path / "a.ld").write_text("SECTIONS { indata 0x100000 : { *(indata) . = .; } main 0x200000 : { *(main) } }\n")
    subprocess.run([tools["ld"], "-EL", "-T", tmp_path / "a.ld", "-o", tmp_path / "game.elf", tmp_path / "a.o"],
                   check=True)
    flat = tmp_path / "with-empty.rom"
    subprocess.run([tools["objcopy"], "-O", "binary", "--gap-fill=0x00", tmp_path / "game.elf", flat], check=True)
    assert flat.stat().st_size > 0x100000  # what splat's own flattening makes of it
    config = tmp_path / "game.yaml"
    config.write_text("sha1: 0000\noptions:\n")
    split._flatten_without_empty_sections(tmp_path / "game.elf", tmp_path / "game.rom",
                                          tools["objcopy"], config)
    image = (tmp_path / "game.rom").read_bytes()
    assert image.startswith(bytes.fromhex("0800e003" "00000000")) and len(image) < 0x1000  # jr $ra; nop at 0
    assert config.read_text().startswith(f"sha1: {hashlib.sha1(image).hexdigest()}\n")


def test_a_tail_call_to_a_function_named_l_is_not_a_label():
    """A PS2 game with symbols tail-calls `LoadBTF`: read as a local label (.L + hex), the import stopped on it."""
    from pathlib import Path

    from decompnet.rom import match
    from decompnet.rom.split import AsmLine, RomFunction

    lines = [AsmLine(0, 0x200000, 0x08080010, "j", "LoadBTF"), AsmLine(4, 0x200004, 0, "nop", ""),
             AsmLine(8, 0x200008, 0x08080000, "j", ".L00200000"), AsmLine(12, 0x20000C, 0, "nop", "")]
    fn = RomFunction("f", 0x200000, 0, "cod", lines, Path("f.s"))
    target = match.target_from_split(fn, {"LoadBTF": 0x200040}.get, big_endian=False)
    assert [(off, addr) for off, _, addr in target.relocs] == [(0, 0x200040), (8, 0x200000)]
