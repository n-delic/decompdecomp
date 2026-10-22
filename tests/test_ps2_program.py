"""A PS2 program's layout and compiler, read before anything is built: these need no compiler, so CI runs them."""

import hashlib
import subprocess

import pytest

from decompnet import toolchains

MW_NOTE = b"MW MIPS C Compiler (2.4.1.01)\0PlayStation2\0"


def test_object_files_say_which_compiler_built_them(monkeypatch):
    """A 2002 game linked by CodeWarrior: 170 of its 183 object files carry GCC's gcc2_compiled. mark (its code), 13
    don't (CodeWarrior's C library, kernel stubs). GCC is the game's compiler and the rest are its ranges."""
    from decompnet.consoles.ps2 import elf as ps2

    starts = {0x200000: False, 0x2002D0: True, 0x200470: True, 0x2257D0: False, 0x225948: False, 0x225BC0: True}
    monkeypatch.setattr(ps2, "_object_starts", lambda data: (starts, dict.fromkeys(starts, 0x2D0080)))
    monkeypatch.setattr(ps2, "_sections", lambda data: {".mwcats": b"", ".comment": MW_NOTE})
    asked = []
    monkeypatch.setattr(ps2, "code_style", lambda data, within=None: asked.append(within) or (100, 0.14, 3.7, 0.0))
    assert ps2.object_compilers(b"") == [(0x200000, 0x2002D0, "MWCPS2-2"), (0x2002D0, 0x2257D0, "EEGCC"),
                                         (0x2257D0, 0x225BC0, "MWCPS2-2"), (0x225BC0, 0x2D0080, "EEGCC")]
    assert asked[0] == [(0x2002D0, 0x200470), (0x200470, 0x2257D0), (0x225BC0, 0x2D0080)]  # the GCC objects' code
    assert ps2.guess_compiler(b"") == "EEGCC"
    # SN's GCC saves $ra with sq (93% of a 2001 game's GCC objects): its 2.95.2, not Sony's 2.96
    monkeypatch.setattr(ps2, "code_style", lambda data, within=None: (100, 0.16, 0.3, 0.93))
    assert {g for *_, g in ps2.object_compilers(b"")} == {"EEGCC-2.95.2", "MWCPS2-2"}
    monkeypatch.setattr(ps2, "_sections", lambda data: {})
    assert ps2.object_compilers(b"") == []  # no sign of CodeWarrior: unmarked objects are hand-written
    monkeypatch.setattr(ps2, "_sections", lambda data: {".mwcats": b""})
    monkeypatch.setattr(ps2, "_object_starts", lambda data: ({0x200000: True}, {0x200000: 0x2D0080}))
    assert ps2.object_compilers(b"") == []  # one compiler: nothing to split


def test_the_import_gives_other_compilers_their_object_files():
    """Unless the user chose a compiler, each other compiler's object files become its ranges, and every installed
    compiler is tried on the rest (CodeWarrior too: its getters match)."""
    from types import SimpleNamespace

    from decompnet import consoles

    ps2 = consoles.get("ps2")
    calls = []
    rom = {"compiler_guess": "EEGCC", "compiler_parts": [[0x200000, 0x2002D0, "MWCPS2-2"], [0x2002D0, 0x2257D0, "EEGCC"],
                                                         [0x2257D0, 0x225BC0, "MWCPS2-2"]]}
    net = SimpleNamespace(cfg=lambda proj: {"rom": rom}, project_row=lambda slug: {},
                          set_compiler_ranges=lambda slug, spans, tc: calls.append(("ranges", spans, tc)),
                          test_compilers=lambda slug, tcs, log: calls.append(("test", tcs)),
                          _update_cfg=lambda slug, change: None)
    ps2.after_import(net, "game", chosen=False, log=lambda *_: None)
    assert calls == [("ranges", [[0x200000, 0x2002D0], [0x2257D0, 0x225BC0]], "mwcps2-2.3.3"),
                     ("test", [c.id for c in ps2.compilers])]
    calls.clear()
    ps2.after_import(net, "game", chosen=True, log=lambda *_: None)
    assert calls == []


@pytest.mark.parametrize("empty_at", [0x100000, 0x300000])
def test_an_empty_section_below_the_code_does_not_shift_the_split(tmp_path, empty_at):
    """A CodeWarrior-linked PS2 game has an empty section at 0x100000 and its code at 0x200000: objcopy's flat image
    started at the empty one, splat counted from the code, and every function was read from the zeros between. Empty
    sections above the code (a 5.5 MB game has eight, all its sections without names) leave the image as it is."""
    from decompnet.rom import split

    if toolchains.installed("binutils-mips") is None:
        pytest.skip("needs binutils-mips")
    bin_dir = toolchains.binary("binutils-mips").parent
    tools = {name: bin_dir / f"mips-ps2-decompals-{name}" for name in ("as", "ld", "objcopy")}
    (tmp_path / "a.s").write_text(".section main,\"awx\",@progbits\n  jr $ra\n  nop\n.section indata,\"aw\",@progbits\n")
    subprocess.run([tools["as"], "-EL", "-march=r5900", "-o", tmp_path / "a.o", tmp_path / "a.s"], check=True)
    (tmp_path / "a.ld").write_text(f"SECTIONS {{ main 0x200000 : {{ *(main) }} indata 0x{empty_at:X} : {{ *(indata) . = .; }} }}\n")
    subprocess.run([tools["ld"], "-EL", "-T", tmp_path / "a.ld", "-o", tmp_path / "game.elf", tmp_path / "a.o"],
                   check=True)
    flat = tmp_path / "game.rom"
    subprocess.run([tools["objcopy"], "-O", "binary", "--gap-fill=0x00", tmp_path / "game.elf", flat], check=True)
    made = flat.read_bytes()  # what splat's own flattening makes of it
    config = tmp_path / "game.yaml"
    config.write_text("sha1: 0000\noptions:\n")
    split._drop_leading_gap(tmp_path / "game.elf", flat, config)
    image = flat.read_bytes()
    assert image.startswith(bytes.fromhex("0800e003" "00000000"))  # jr $ra; nop at 0
    if empty_at < 0x200000:
        assert len(made) > 0x100000 and len(image) < 0x1000
        assert config.read_text().startswith(f"sha1: {hashlib.sha1(image).hexdigest()}\n")
    else:
        assert image == made and config.read_text().startswith("sha1: 0000")


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


def test_check_again_suggests_what_the_symbols_say_and_overwrites_nothing(tmp_path, monkeypatch):
    """A project imported before object files were read: its ranges are its agents' work, so Check again doesn't set
    ranges. Stretches not built with their compiler yet go through the range check as the autopilot's suggestion,
    once; stretches already built with it are left alone."""
    import contextlib
    from types import SimpleNamespace

    from decompnet import consoles
    from decompnet.consoles.ps2 import elf as ps2elf

    ps2 = consoles.get("ps2")
    (tmp_path / "game.elf").write_bytes(b"\x7fELF")
    monkeypatch.setattr(ps2elf, "object_compilers", lambda data: [
        (0x200000, 0x200100, "MWCPS2-2"), (0x200100, 0x210000, "EEGCC"), (0x210000, 0x210100, "MWCPS2-2")])
    cfg = {"kind": "rom", "rom": {"program": "game.elf"}, "compiler": {"toolchain": "ee-gcc-2.95.3", "flags": []},
           "compiler_ranges": [{"start": 0x200000, "end": 0x200100, "toolchain": "mwcps2-3.0.1", "flags": []}]}
    suggested, events = [], []

    def update(slug, change):
        change(cfg)
    net = SimpleNamespace(
        project_row=lambda slug: {"id": 1, "root": str(tmp_path)}, cfg=lambda proj: cfg, _link=lambda slug: None,
        local_agent=lambda name: (name, None), _update_cfg=update,
        suggest=lambda agent, slug, text, compiler="", ranges="": suggested.append((compiler, ranges)),
        store=SimpleNamespace(all=lambda sql, pid, a, b: [{"vram": a}], tx=contextlib.nullcontext),
        _event=lambda db, pid, agent, kind, detail: events.append(kind))
    ps2.suggest_object_compilers(net, "game")
    assert suggested == [("mwcps2-2.3.3", "0x210000-0x210100")]  # the first stretch is CodeWarrior's already
    assert cfg["compiler_ranges"][0]["toolchain"] == "mwcps2-3.0.1" and events == ["analysis"]
    ps2.suggest_object_compilers(net, "game")
    assert len(suggested) == 1  # read once per reading


def test_install_all_brings_every_compiler_then_tests_them(monkeypatch):
    """Every compiler of the console the app installs, a proprietary one only with consent, then the compiler test
    again with all of them, unless the user picked the compiler."""
    from types import SimpleNamespace

    from decompnet import consoles
    from decompnet.app.engine import RomNetwork

    ps2 = consoles.get("ps2")
    have = {"ee-gcc-2.96"}
    monkeypatch.setattr(toolchains, "installed", lambda tid, linux=None: {} if tid in have else None)
    monkeypatch.setattr(toolchains, "install", lambda tid, accept_license=False, log=None: have.add(tid))
    tested = []
    net = SimpleNamespace(_project=lambda slug: {}, console=lambda proj: ps2, is_rom=lambda proj: True,
                          test_compilers=lambda slug, tcs, log: tested.append(tcs) or {"ee-gcc-2.96": 4})
    res = RomNetwork.install_compilers(net, "game", accept_license=False, log=lambda *_: None)
    assert res["left_out"] == ["mwcps2-2.3.3", "mwcps2-3.0.1"] and "mwcps2-3.0.1" not in have
    assert set(res["installed"]) == set(ps2.installable_compilers()) - {"ee-gcc-2.96", "mwcps2-2.3.3", "mwcps2-3.0.1"}
    assert tested == [ps2.installable_compilers()] and res["test"] == {"ee-gcc-2.96": 4}
    res = RomNetwork.install_compilers(net, "game", accept_license=True, retest=False, log=lambda *_: None)
    assert res["installed"] == ["mwcps2-2.3.3", "mwcps2-3.0.1"] and len(tested) == 1


def test_a_dnas_block_marked_reginfo_does_not_stop_the_split(tmp_path):
    """An online PS2 game keeps its encrypted DNAS block in a 1 MB section marked MIPS_REGINFO (a type that holds 24
    bytes): objcopy refused the whole program, and the import stopped at "Failed to run objcopy". The section is
    hidden while the program is laid out, and the program put back as it was."""
    import struct

    from elftools.elf.elffile import ELFFile

    from decompnet.rom import split

    if toolchains.installed("binutils-mips") is None:
        pytest.skip("needs binutils-mips")
    bin_dir = toolchains.binary("binutils-mips").parent
    tools = {name: bin_dir / f"mips-ps2-decompals-{name}" for name in ("as", "ld", "objcopy")}
    (tmp_path / "a.s").write_text(".section main,\"awx\",@progbits\n  jr $ra\n  nop\n"
                                  ".section dnas,\"awx\",@progbits\n  .fill 64, 1, 0x5a\n")
    subprocess.run([tools["as"], "-EL", "-march=r5900", "-o", tmp_path / "a.o", tmp_path / "a.s"], check=True)
    (tmp_path / "a.ld").write_text("SECTIONS { main 0x110000 : { *(main) } dnas 0x1CCEA10 : { *(dnas) } }\n")
    program = tmp_path / "game.elf"
    subprocess.run([tools["ld"], "-EL", "-T", tmp_path / "a.ld", "-o", program, tmp_path / "a.o"], check=True)
    data = bytearray(program.read_bytes())
    with open(program, "rb") as f:
        elf = ELFFile(f)
        index = next(i for i, s in enumerate(elf.iter_sections()) if s.name == "dnas")
        struct.pack_into("<I", data, elf["e_shoff"] + elf["e_shentsize"] * index + 4, 0x70000006)  # SHT_MIPS_REGINFO
    program.write_bytes(data)
    flatten = [tools["objcopy"], "-O", "binary", "--gap-fill=0x00", program, tmp_path / "game.rom"]
    assert subprocess.run(flatten, capture_output=True).returncode != 0  # what the import ran into
    original = split._readable_for_splat(program)
    assert original == bytes(data)
    subprocess.run(flatten, check=True)
    assert (tmp_path / "game.rom").read_bytes().startswith(bytes.fromhex("0800e003" "00000000"))


@pytest.mark.parametrize("headers", ["none", "index past them"])
def test_a_program_without_section_headers_is_laid_out_from_its_segments(tmp_path, headers):
    """#44: a PS2 game's program without usable section headers stopped the import in splat's ELF reader
    ("IndexError: list index out of range"). Its program headers give splat and objcopy the sections they need, and
    the user's program is put back as it was."""
    import struct

    from splat.util.ps2 import ps2elfinfo

    from decompnet.rom import split

    if toolchains.installed("binutils-mips") is None:
        pytest.skip("needs binutils-mips")
    bin_dir = toolchains.binary("binutils-mips").parent
    tools = {name: bin_dir / f"mips-ps2-decompals-{name}" for name in ("as", "ld", "objcopy")}
    (tmp_path / "a.s").write_text(".section main,\"awx\",@progbits\n  jr $ra\n  nop\n")
    subprocess.run([tools["as"], "-EL", "-march=r5900", "-o", tmp_path / "a.o", tmp_path / "a.s"], check=True)
    (tmp_path / "a.ld").write_text("SECTIONS { main 0x100000 : { *(main) } }\n")
    program = tmp_path / "game.elf"
    subprocess.run([tools["ld"], "-EL", "-T", tmp_path / "a.ld", "-o", program, tmp_path / "a.o"], check=True)
    data = bytearray(program.read_bytes())
    if headers == "none":
        struct.pack_into("<I", data, 32, 0)
        struct.pack_into("<HHH", data, 46, 0, 0, 0)
    else:
        struct.pack_into("<H", data, 50, 99)  # e_shstrndx
    program.write_bytes(data)
    with pytest.raises(IndexError):
        ps2elfinfo.Ps2Elf.get_info(program, bytes(data))  # what the import ran into
    assert split._readable_for_splat(program) == bytes(data)
    assert ps2elfinfo.Ps2Elf.get_info(program, program.read_bytes()) is not None
    subprocess.run([tools["objcopy"], "-O", "binary", "--gap-fill=0x00", program, tmp_path / "game.rom"], check=True)
    assert (tmp_path / "game.rom").read_bytes().startswith(bytes.fromhex("0800e003" "00000000"))


def test_vu0_instructions_are_exported_as_the_words_they_are():
    """A real PS2 game's export stopped in the assembler: GNU as takes spimdisasm's spelling of VU0 macro instructions
    (`vrsqrt Q, $vf0w, $vf1x`) for invalid operands. Coprocessor 2's instructions go out as raw words, the bytes in
    the line's comment read in the game's byte order; every other line stays as it was."""
    from decompnet.arch import mips

    text = ("glabel f\n"
            "    /* 10C88 00110C88 3C0A004B */  vrsqrt      Q, $vf0w, $vf1x\n"
            "    /* 10C8C 00110C8C 0800E003 */  jr          $ra\n"
            "    /* 10C90 00110C90 00000000 */   nop\n")
    out = mips.raw_cop2(text, big_endian=False)
    assert "/* 10C88 00110C88 3C0A004B */  .word 0x4B000A3C\n" in out
    assert out.replace(".word 0x4B000A3C", "vrsqrt      Q, $vf0w, $vf1x") == text
    assert mips.raw_cop2(text.replace("3C0A004B", "4B000A3C"), big_endian=True).count(".word 0x4B000A3C") == 1
