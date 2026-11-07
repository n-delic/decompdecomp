"""DOS LE uses the same judge as PE and exports the original bytes, including unmapped overlays."""

import os
import struct
from pathlib import Path

import pytest

from decompnet.consoles.pc import scratch
from decompnet.forks.project import ForkError


def le_image():
    """Homemade three-page LE: out-of-order physical pages and a fixup crossing the code page boundary."""
    data = bytearray(0x4000)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 0x80)
    data[0x80:0x84] = b"LE\0\0"
    struct.pack_into("<HH", data, 0x88, 2, 1)
    fields = {20: 3, 24: 1, 28: 0, 32: 2, 36: 0x100, 40: 4096, 44: 0x100,
              64: 196, 68: 2, 72: 244, 104: 256, 108: 272, 112: 290, 128: 0x1000}
    for off, value in fields.items():
        struct.pack_into("<I", data, 0x80 + off, value)
    struct.pack_into("<6I", data, 0x80 + 196, 8192, 0x10000, 0x2005, 1, 2, 0)
    struct.pack_into("<6I", data, 0x80 + 220, 4096, 0x20000, 0x2003, 3, 1, 0)
    data[0x80 + 244:0x80 + 256] = b"\0\0\2\0\0\0\1\0\0\0\3\0"
    fix = struct.pack("<BBhBI", 7, 16, 4094, 2, 0x20)
    fix += struct.pack("<BBhBI", 7, 16, -2, 2, 0x20)
    struct.pack_into("<4I", data, 0x80 + 256, 0, 9, 18, 18)
    data[0x80 + 272:0x80 + 290] = fix
    data[0x2000:0x2003] = b"\x01\xd0\xc3"
    data[0x3000:0x3100] = b"a" * 256
    data += b"unmapped-overlay"
    return bytes(data)


def test_le_pages_and_cross_page_fixups_roundtrip_without_touching_the_overlay():
    linear = scratch._template_tool("linear")
    original = le_image()
    image = linear.Linear(original)
    pe = scratch.read_pe(original)
    assert pe.entry == 0x10000 and pe.read(0x10000, 3) == b"\x01\xd0\xc3"
    assert pe.read(0x10FFE, 4) == struct.pack("<I", 0x20020)
    assert pe.read(0x20100, 8) == bytes(8)
    assert image.restore(image.view()) == original
    changed = bytearray(image.view())
    changed[pe.offset(0x10000)] = 0x29
    restored = image.restore(changed)
    assert restored[0x2000] == 0x29 and restored.endswith(b"unmapped-overlay")
    assert restored[0x2FFE:0x3000] == original[0x2FFE:0x3000]


@pytest.mark.parametrize("change", ["truncated", "cpu", "page", "iterated", "target", "import", "overlap"])
def test_unsupported_or_broken_le_is_rejected_before_analysis(change):
    data = bytearray(le_image())
    if change == "truncated":
        data = data[:100]
    elif change == "cpu":
        struct.pack_into("<H", data, 0x88, 1)
    elif change == "page":
        data[0x80 + 244:0x80 + 247] = b"\xff\xff\xff"
    elif change == "iterated":
        data[0x80 + 247] = 1
    elif change == "target":
        data[0x80 + 276] = 3
    elif change == "import":
        data[0x80 + 273] = 17
    else:
        struct.pack_into("<I", data, 0x80 + 224, 0x10000)
    with pytest.raises(ForkError):
        scratch.read_pe(bytes(data))


def test_rebuild_rejects_changes_to_loader_fixups_and_unstored_bytes():
    image = scratch._template_tool("linear").Linear(le_image())
    pe = scratch.read_pe(le_image())
    for address in (0x10FFE, 0x20100):
        changed = bytearray(image.view())
        changed[pe.offset(address)] ^= 1
        with pytest.raises(ValueError):
            image.restore(changed)


def test_zero_page_with_a_loader_fixup_roundtrips_without_storing_any_bytes():
    data = bytearray(le_image())
    # Last virtual page is zero-filled; physical page 2 is now the short final stored page.
    data[0x80 + 252:0x80 + 256] = b"\0\0\0\3"
    struct.pack_into("<4I", data, 0x80 + 256, 0, 0, 0, 9)
    data[0x80 + 272:0x80 + 281] = struct.pack("<BBhBI", 7, 16, 0, 1, 0)
    image = scratch._template_tool("linear").Linear(bytes(data))
    pe = scratch.read_pe(bytes(data))
    assert pe.read(0x20000, 4) == struct.pack("<I", 0x10000)
    assert image.restore(image.view()) == data


def test_source_lists_apply_each_fixup_and_overlapping_records_are_rejected():
    data = bytearray(le_image())
    fix = struct.pack("<BBBBIhh", 0x27, 16, 2, 2, 0x20, 8, 16)
    struct.pack_into("<4I", data, 0x80 + 256, 0, len(fix), len(fix), len(fix))
    data[0x80 + 272:0x80 + 272 + len(fix)] = fix
    pe = scratch.read_pe(bytes(data))
    assert pe.read(0x10008, 4) == pe.read(0x10010, 4) == struct.pack("<I", 0x20020)
    struct.pack_into("<h", data, 0x80 + 282, 10)
    with pytest.raises(ForkError, match="overlap"):
        scratch.read_pe(bytes(data))


def test_selector_fixups_keep_their_symbolic_targets_for_judging():
    data = bytearray(le_image())
    # Replace the second page's duplicate pointer fixup with one selector fixup.
    struct.pack_into("<4I", data, 0x80 + 256, 0, 9, 14, 14)
    data[0x80 + 281:0x80 + 286] = struct.pack("<BBhB", 2, 0, 10, 2)
    pe = scratch.read_pe(bytes(data))
    assert pe.segment_fixups == [(0x1100A, 2)]
    fn = scratch.Function(0x11000, 16, "_func_00011000")
    entry = scratch._entry(pe, fn)
    assert "generated" not in entry
    assert entry["segments"] == [[10, 2, 0x20000, 0, 0]]


def test_integer_that_looks_like_an_address_does_not_become_a_dos_relocation():
    data = bytearray(le_image())
    data[0x2000:0x2006] = bytes.fromhex("b800000100c3")  # return 65536, not a pointer to the first function
    pe = scratch.read_pe(bytes(data))
    functions = scratch.analyse(pe, [{"entry": 0x10000, "end": 0x10006}])
    assert functions[0].relocs == []


def test_watcom_function_names_do_not_inherit_ghidras_microsoft_abi_guess():
    pe = scratch.read_pe(demo_le())
    rows = [{"entry": 0x10000, "end": 0x10003, "convention": "__fastcall", "params": 2}]
    assert scratch.analyse(pe, rows)[0].name == "@func_00010000@8"
    assert scratch.analyse(pe, rows, msvc=False)[0].name == "_func_00010000"


def test_dos_sniff_folder_and_default_compiler_do_not_need_an_msvc_pack(tmp_path, monkeypatch):
    from decompnet import consoles, kits

    monkeypatch.setattr(kits, "compilers", lambda *_: [])
    game = tmp_path / "GAME.EXE"
    game.write_bytes(le_image())
    assert consoles.get("pc").sniff(game.read_bytes(), game)
    assert scratch.program_in(tmp_path) == game
    info = scratch.game_info(game)
    assert info["format"] == "LE" and info["compiler"] == "watcom-unconfigured"
    with pytest.raises(ForkError, match="Watcom"):
        scratch.create(tmp_path / "project", game)


def demo_le():
    """Two homemade functions with register arguments, a call and one absolute data fixup."""
    data = bytearray(le_image()[:0x3004])
    fields = {20: 2, 24: 1, 28: 3, 44: 4, 112: 281}
    for off, value in fields.items():
        struct.pack_into("<I", data, 0x80 + off, value)
    struct.pack_into("<6I", data, 0x80 + 196, 26, 0x10000, 0x2005, 1, 1, 0)
    struct.pack_into("<6I", data, 0x80 + 220, 4, 0x20000, 0x2003, 2, 1, 0)
    data[0x80 + 244:0x80 + 252] = b"\0\0\1\0\0\0\2\0"
    struct.pack_into("<3I", data, 0x80 + 256, 0, 9, 9)
    data[0x80 + 272:0x80 + 281] = struct.pack("<BBhBI", 7, 16, 20, 2, 0)
    data[0x1000:0x101A] = bytes.fromhex("01d0c352ba03000000b802000000e8edffffffa3000000005ac3")
    data[0x2000:0x2004] = bytes(4)
    return bytes(data[:0x2004])


@pytest.mark.parametrize("language", ["c", "cpp"])
def test_real_dos_engine_import_judge_polish_rename_reimport_and_export(tmp_path, monkeypatch, language):
    from decompnet import kits, toolchains
    from decompnet.app.engine import RomNetwork

    folder = os.environ.get("DECOMP_TEST_WATCOM_C")
    if not folder or not all(toolchains.installed(t) for t in ("ghidra", "jdk", "wibo", "objdiff-cli")):
        pytest.skip("needs DECOMP_TEST_WATCOM_C and pinned Ghidra/JDK/wibo/objdiff")
    monkeypatch.setenv("HOME", str(tmp_path))
    original = toolchains.local_folders
    monkeypatch.setattr(toolchains, "local_folders", lambda kid: [Path(folder)] if kid == "watcom" else original(kid))
    compiler = next(c for c in kits.compilers(("watcom",)) if f"-{language}-" in c.version)
    game = tmp_path / "demo.exe"
    game.write_bytes(demo_le())
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    slug = net.import_rom(game, confirmed=True, toolchain=f"Watcom/{compiler.version}", log=lambda *_: None)
    overview = net.overview(slug)
    assert overview["ready"] and overview["rebuild"]["identical"]
    root = Path(net.project_row(slug)["root"])
    assert '"-bt=dos4g"' in (root / "tools/configure.py").read_text()
    agent, _ = net.local_agent("dos-test")
    first, second = "_func_00010000", "_func_00010003"
    assert len(net.forks.autopilot_prepare(slug)) == 2
    net.claim(agent, slug, first)
    assert "Watcom" in net.get_function(first, slug)
    assert not net.attempt(agent, first, "int func_00010000(int a,int b) {return a-b;}", slug)["matched"]
    good = "int func_00010000(int a,int b) {return a+b;}"
    other = next(c for c in kits.compilers(("watcom",)) if c.version != compiler.version)
    assert other.version in net.forks.try_compiler(agent, slug, first, f"Watcom/{other.version}",
                                                   source="int func_00010000(int a,int b) {return a-b;}")
    assert "match" in net.forks.search_flags(agent, slug, first, source=good).lower()
    assert net.attempt(agent, first, good, slug)["matched"]
    start = "extern int D_00020000; void func_00010003(void) {D_00020000=func_00010000(2,3);}"
    assert net.attempt(agent, second, start, slug)["matched"]
    assert "readable" in net.readability.submit(agent, slug, first, "/* Sum. */\n" + good)
    assert net.rename_symbol(agent, slug, first, "add_values").startswith("✅")
    assert scratch.check_code(root, "demo", log=lambda *_: None)["identical"]
    net.forks.check(slug, log=lambda *_: None)
    assert (root / "build/demo/demo.exe").read_bytes() == game.read_bytes()
    # Reimport must analyse the LE view, not hand Ghidra the 16-bit DOS stub.
    from decompnet.consoles.pc import WindowsPC
    WindowsPC().reimport(net, slug, game, log=lambda *_: None)
    assert scratch.check_code(root, "demo", log=lambda *_: None)["identical"]


def test_rebuild_rejects_changes_in_unmapped_object_tail():
    data = bytearray(le_image())
    struct.pack_into("<I", data, 0x80 + 196, 12288)
    image = scratch._template_tool("linear").Linear(bytes(data))
    assert image.restore(image.view()) == data
    pe = scratch.read_pe(bytes(data))
    changed = bytearray(image.view())
    changed[pe.offset(0x12000)] = 1
    with pytest.raises(ValueError, match="unmapped object"):
        image.restore(changed)
