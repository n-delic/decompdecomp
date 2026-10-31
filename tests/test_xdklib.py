"""The XDK's library code found in a game (consoles/xbox/xdklib.py), on homemade COFF archives: no XDK involved.
Also Setup's local toolchain (the user's own XDK folder)."""

import json
import struct

import pytest

from decompnet import toolchains
from decompnet.consoles.pc import scratch as pc
from decompnet.consoles.xbox import xdklib


def _coff(symbol: str, code: bytes, relocs=(), machine=0x14C) -> bytes:
    """An i386 COFF object with one .text section holding `code` (one public symbol at its start); `relocs` are
    (offset, type) pairs against that symbol."""
    raw_at = 20 + 40
    relocs_at = raw_at + len(code)
    symbols_at = relocs_at + 10 * len(relocs)
    section = struct.pack("<8sIIIIIIHHI", b".text", 0, 0, len(code), raw_at, relocs_at, 0, len(relocs), 0, 0x60000020)
    table = b"".join(struct.pack("<IIH", off, 0, typ) for off, typ in relocs)
    name = symbol.encode()
    sym = (struct.pack("<8sIhHBB", name, 0, 1, 0x20, 2, 0) if len(name) <= 8 else
           struct.pack("<IIIhHBB", 0, 4, 0, 1, 0x20, 2, 0))
    strings = b"" if len(name) <= 8 else struct.pack("<I", 4 + len(name) + 1) + name + b"\0"
    header = struct.pack("<HHIIIHH", machine, 1, 0, symbols_at, 1, 0, 0)
    return header + section + code + table + sym + (strings or struct.pack("<I", 4))


def _archive(**objs: bytes) -> bytes:
    out = b"!<arch>\n"
    for name, body in objs.items():
        out += f"{name + '/':<16}{0:<12}{0:<6}{0:<6}{100644:<8}{len(body):<10}`\n".encode() + body
        out += b"\n" if len(body) & 1 else b""
    return out


# push ebp; mov ebp,esp; call <rel32>; mov eax,[abs32]; pop ebp; ret 4, plus padding
FUNC = bytes.fromhex("55 8B EC E8 00 00 00 00 A1 00 00 00 00 5D C2 04 00 33 C0 40".replace(" ", ""))
RELOCS = [(4, 0x14), (9, 0x06)]  # REL32, DIR32


def _program_code(rel=0x1234, abs_=0x5678) -> bytes:
    return FUNC[:4] + struct.pack("<i", rel) + FUNC[8:9] + struct.pack("<I", abs_) + FUNC[13:]


def test_a_member_becomes_a_pattern_with_its_relocations_wildcarded(tmp_path):
    lib = tmp_path / "XapiLib.LIB"
    lib.write_bytes(_archive(**{"xapi.obj": _coff("_XapiInit@0", FUNC, RELOCS)}))
    (tmp_path / "d3d8ltcg.lib").write_bytes(_archive(**{"x.obj": _coff("_Ltcg", FUNC, machine=0)}))
    libs = xdklib.libraries(tmp_path, ["xapilib", "d3d8ltcg"])
    assert [p.name for p in libs] == ["XapiLib.LIB"]  # case ignored, LTCG left out
    (name, body, mask), = list(xdklib.code_sections(next(xdklib.members(lib.read_bytes()))[1]))
    assert (name, body) == ("_XapiInit@0", FUNC)
    assert [i for i, m in enumerate(mask) if m] == [4, 5, 6, 7, 9, 10, 11, 12]

    pad = b"\xcc" * 8
    region = (0x11000, pad + _program_code(7, 9) + pad + _program_code(0x99, 0x77))
    found = xdklib.match([region], libs)
    assert sorted(found) == [0x11008, 0x11008 + len(FUNC) + 8]
    assert found[0x11008].reason == "XDK library code: _XapiInit@0 from XapiLib.LIB (xapi.obj)"
    # a different instruction is not the function
    assert xdklib.match([(0x11000, b"\x55" + _program_code()[1:5] + b"\x00" * 30)], libs) == {}


def test_a_tiny_function_is_library_code_only_next_to_library_code(tmp_path):
    lib = tmp_path / "xapilib.lib"
    tiny = bytes.fromhex("33C0C3")  # xor eax,eax; ret
    lib.write_bytes(_archive(**{"a.obj": _coff("_Big", FUNC, RELOCS), "b.obj": _coff("_Zero", tiny)}))
    libs = xdklib.libraries(tmp_path, ["xapilib"])
    known = xdklib.match([(0x11000, _program_code())], libs)
    assert list(known) == [0x11000]
    funcs = [(0x10FF0, tiny), (0x11000, _program_code()), (0x11020, tiny), (0x11040, tiny), (0x12000, tiny)]
    small = xdklib.match_small(funcs, libs, known)
    # next to library code, one run of helpers at a time; 0x11040 is next to 0x11020 which was just found
    assert sorted(small) == [0x10FF0, 0x11020, 0x11040, 0x12000]  # (0x12000 follows 0x11040 in the run)
    assert xdklib.match_small([(0x20000, tiny)], libs, {}) == {}


def _project(tmp_path, monkeypatch, code: bytes, entries: list[dict]):
    root = tmp_path / "proj"
    (root / "config" / "v").mkdir(parents=True)
    info = {"units": {"a.c": entries}}
    (root / "config" / "v" / "functions.json").write_text(json.dumps(info))
    sec = pc.Section(".text", 0x11000, len(code), 0, len(code), True)
    pe = pc.PE(code, 0x10000, 0x20000, 0x11000, 0, [sec])
    monkeypatch.setattr(pc, "programs", lambda root, info: {"": pe})
    return root


def test_functions_of_exactly_the_library_function_are_set_aside(tmp_path, monkeypatch):
    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()
    (lib_dir / "xapilib.lib").write_bytes(_archive(**{"xapi.obj": _coff("_XapiInit@0", FUNC, RELOCS)}))
    game = bytes.fromhex("B801000000C3") + b"\xcc" * 10  # the game's own function, then padding
    a = 0x11000 + len(game)
    code = game + _program_code(1, 2) + b"\xcc" * 12 + _program_code(3, 4) + b"\x90" * 4 + b"\x55\x8B\xEC"
    b = a + len(FUNC) + 12
    entries = [
        {"name": "_game", "addr": 0x11000, "offset": 0, "size": 6, "relocs": []},
        {"name": "_lib1", "addr": a, "offset": a - 0x11000, "size": len(FUNC) + 12, "relocs": []},  # trailing padding
        {"name": "_merged", "addr": b, "offset": b - 0x11000, "size": len(FUNC) + 7, "relocs": []},  # took a neighbour
    ]
    root = _project(tmp_path, monkeypatch, code, entries)
    assert xdklib.set_aside_xdk_functions(root, "v", lib_dir, ["xapilib"]) == 1
    fns = json.loads((root / "config" / "v" / "functions.json").read_text())["units"]["a.c"]
    assert [f.get("generated") for f in fns] == [
        None, "XDK library code: _XapiInit@0 from xapilib.lib (xapi.obj)", None]
    assert xdklib.set_aside_xdk_functions(root, "v", lib_dir, ["xapilib"]) == 0  # already marked
    assert xdklib.set_aside_xdk_functions(root, "v", tmp_path / "nope", ["xapilib"]) == 0


def test_the_users_xdk_folder_is_checked_and_recorded_never_copied(tmp_path, monkeypatch):
    monkeypatch.setenv("DECOMPNET_TOOLCHAINS", str(tmp_path / "tc"))
    xdk = tmp_path / "XDK" / "xbox"
    (xdk / "Include").mkdir(parents=True)
    (xdk / "Lib").mkdir()
    (xdk / "Include" / "XTL.H").write_text("")
    (xdk / "Lib" / "XAPILIB.LIB").write_bytes(b"x")
    tc = toolchains.get("xdk")
    assert tc.local and tc.proprietary and tc.supported
    with pytest.raises(toolchains.ToolchainError, match="proprietary"):
        toolchains.install("xdk", folder=tmp_path / "XDK")
    with pytest.raises(toolchains.ToolchainError, match="doesn't look like"):
        toolchains.install("xdk", accept_license=True, folder=tmp_path / "tc")
    toolchains.install("xdk", accept_license=True, folder=tmp_path / "XDK", log=lambda *_: None)  # the folder above xbox/
    assert toolchains.local_folder("xdk") == xdk.resolve()
    assert not any(p.suffix.lower() in (".h", ".lib") for p in (tmp_path / "tc").rglob("*"))  # nothing copied
    row = next(r for r in toolchains.status() if r["id"] == "xdk")
    assert row["installed"] and row["local"] and row["folder"] == str(xdk.resolve())
