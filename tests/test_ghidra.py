"""Ghidra itself, on this computer: what PC games' imports take from its analysis. CI installs Ghidra on every
system it runs on (check.yml), so the test that needs it runs on Windows, Linux and both kinds of Mac, where
Ghidra's decompiler was built at install (toolchains._build_ghidra_decompiler)."""

import hashlib
import os
import struct
from types import SimpleNamespace

import pytest

from decompnet.forks import ghidra
from decompnet.forks.project import ForkError


def _program() -> bytes:
    """A 32-bit Windows program of 1 KB, made here byte by byte, so the test needs no compiler: its entry calls one
    function of each calling convention, the way Visual C++ calls them, at 0x401040, 0x401050, 0x401060, 0x401070."""
    base, text = 0x400000, 0x1000
    functions = [
        bytes.fromhex("8b442404 01c0 c3"),            # __cdecl:    mov eax,[esp+4]; add eax,eax; ret
        bytes.fromhex("8b442404 03442408 c20800"),    # __stdcall:  mov eax,[esp+4]; add eax,[esp+8]; ret 8
        bytes.fromhex("8d0411 c3"),                   # __fastcall: lea eax,[ecx+edx]; ret
        bytes.fromhex("8b01 03442404 c20400"),        # __thiscall: mov eax,[ecx]; add eax,[esp+4]; ret 4
    ]
    calls = [bytes.fromhex("6a05"), bytes.fromhex("6a02 6a01"), bytes.fromhex("b901000000 ba02000000"),
             bytes.fromhex("6a03 b900204000")]  # the arguments: on the stack, in ecx and edx, `this` in ecx
    code = b""
    for i, (args, after) in enumerate(zip(calls, (b"\x83\xc4\x04", b"", b"", b""))):  # (__cdecl: the caller pops)
        code += args
        code += b"\xe8" + struct.pack("<i", text + 0x40 + 0x10 * i - (text + len(code) + 5)) + after
    code += bytes.fromhex("33c0 c3")  # xor eax,eax; ret
    body = code.ljust(0x40, b"\xcc") + b"".join(f.ljust(0x10, b"\xcc") for f in functions)
    dos = b"MZ".ljust(0x3C, b"\0") + struct.pack("<I", 0x40)
    coff = struct.pack("<HHIIIHH", 0x14C, 1, 0, 0, 0, 0xE0, 0x0102)  # i386, one section, an executable
    optional = struct.pack("<HBBIIIIIIIIIHHHHHHIIIIHHIIIIII", 0x10B, 6, 0, 0x200, 0, 0, text, text, 0x2000, base,
                           0x1000, 0x200, 4, 0, 0, 0, 4, 0, 0, 0x2000, 0x200, 0, 3, 0, 0x100000, 0x1000, 0x100000,
                           0x1000, 0, 16) + bytes(16 * 8)
    section = b".text".ljust(8, b"\0") + struct.pack("<IIIIIIHHI", 0x200, text, 0x200, 0x200, 0, 0, 0, 0, 0x60000020)
    return (dos + b"PE\0\0" + coff + optional + section).ljust(0x200, b"\0") + body.ljust(0x200, b"\xcc")


@pytest.mark.skipif(not ghidra.available(), reason="needs Ghidra")
def test_ghidra_tells_every_calling_convention(tmp_path):
    """The PC import names each function as Visual C++ decorated it, from the calling convention Ghidra's analysis
    gives it: on a Mac, whose Ghidra release has no decompiler, every one was "unknown", and a __fastcall function
    (@f@8) was imported as __cdecl (_f). Each convention, and what each function takes from the stack."""
    program = tmp_path / "conventions.exe"
    program.write_bytes(_program())
    found = {r["entry"]: (r["convention"], r["purge"], r["params"]) for r in ghidra.functions(program)}
    assert found[0x401040] == ("__cdecl", 0, 1)
    assert found[0x401050] == ("__stdcall", 8, 2)
    assert found[0x401060] == ("__fastcall", 0, 2)
    assert found[0x401070] == ("__thiscall", 4, 2)


@pytest.mark.skipif(os.name == "nt", reason="a shell-script stand-in for Ghidra")
@pytest.mark.parametrize("said, convention", [
    ("ERROR os/mac_arm_64/decompile does not exist (see GettingStarted.md, 'Building Native Components') "
     "(DecompileProcessFactory)", "__cdecl"),
    ("ERROR Decompiler executable may not be compatible with your system and may need to be rebuilt.", "__cdecl"),
    ("", "unknown"),  # (a decompiler that can't be run at all: Ghidra says nothing)
])
def test_a_ghidra_whose_decompiler_didnt_run_stops_the_import(tmp_path, monkeypatch, said, convention):
    """Without its decompiler Ghidra's analysis goes on and gives no function a calling convention, so the import
    named them wrong and nobody heard why: it stops, and says so. A decompiler that ran is no news."""
    def rows(convention):  # (the output file is the post-script's argument: the 9th of analyzeHeadless's)
        return "".join(f'{{"entry": {e}, "end": {e + 8}, "name": "FUN", "convention": "{convention}", "purge": 0, '
                       f'"params": 0, "thunk": false}}\\n' for e in (0x401000, 0x401010))
    fake = tmp_path / "analyzeHeadless"
    fake.write_text(f"#!/bin/sh\necho \"{said}\"\nprintf '{rows(convention)}' > \"$9\"\n")
    fake.chmod(0o755)
    monkeypatch.setattr(ghidra, "_headless", lambda: fake)
    monkeypatch.setattr(ghidra, "capped", lambda argv, **_: argv)
    monkeypatch.setattr(ghidra, "available", lambda: True)  # (installed, as far as decompdecomp can tell)
    program = tmp_path / "game.exe"
    program.write_bytes(b"MZ" + bytes(64))
    with pytest.raises(ForkError, match="Ghidra's decompiler didn't run .* can't tell the functions' calling conventions"):
        ghidra.functions(program)
    fake.write_text(f"#!/bin/sh\nprintf '{rows('__cdecl')}' > \"$9\"\n")
    assert [r["convention"] for r in ghidra.functions(program)] == ["__cdecl", "__cdecl"]


def test_repairing_ghidra_retries_legacy_empty_disk_drafts_and_database_seeds(tmp_path, monkeypatch):
    from decompnet.app.forkengine import ForkEngine

    obj, native, cache = tmp_path / "unit.obj", tmp_path / "decompile", tmp_path / "drafts"
    obj.write_bytes(b"homemade object")
    cache.mkdir()
    legacy = hashlib.sha1(obj.read_bytes()).hexdigest()[:16]
    (cache / f"unit-{legacy}.json").write_text("{}")
    monkeypatch.setattr(ghidra.toolchains, "ghidra_decompiler", lambda *_: native)
    missing = ghidra.draft_key(obj)
    native.write_bytes(b"working native decompiler")
    monkeypatch.setattr(ghidra, "available", lambda: True)
    calls = []

    def run(*args, **kwargs):
        calls.append(args)
        return [{"name": "function", "c": "int function(void) { return 1; }"}]

    monkeypatch.setattr(ghidra, "_run_script", run)
    key = ghidra.draft_key(obj)
    assert key not in (legacy, missing)
    assert ForkEngine._draft_key({"platform": "pc"}, SimpleNamespace(target=obj)) == key
    assert ghidra.decompile_object(obj, cache)["function"]
    assert ghidra.decompile_object(obj, cache)["function"] and len(calls) == 1
    native.write_bytes(b"rebuilt native decompiler with another identity")
    assert ghidra.draft_key(obj) != key
    assert ghidra.decompile_object(obj, cache)["function"] and len(calls) == 2


def test_native_decompiler_errors_are_not_cached_as_successful_empty_drafts(tmp_path, monkeypatch):
    obj, cache = tmp_path / "unit.obj", tmp_path / "drafts"
    obj.write_bytes(b"homemade object")
    monkeypatch.setattr(ghidra, "available", lambda: True)

    def run(*args, said, **kwargs):
        said.append("ERROR os/mac_arm_64/decompile does not exist")
        return []

    monkeypatch.setattr(ghidra, "_run_script", run)
    assert ghidra.decompile_object(obj, cache) == {}
    assert not cache.exists()


@pytest.mark.skipif(not ghidra.available(), reason="needs complete Ghidra with its native decompiler")
def test_real_ghidra_produces_c_from_a_pc_target_object(tmp_path):
    from decompnet.consoles.pc import scratch

    obj = tmp_path / "unit.obj"
    function = {"name": "_func_00401000", "addr": 0x401000, "offset": 0, "size": 3, "relocs": []}
    obj.write_bytes(scratch._template_tool("delink").coff([function], bytes.fromhex("01d0c3")))
    drafts = ghidra.decompile_object(obj, tmp_path / "drafts")
    assert any("func_00401000" in name and "+" in code for name, code in drafts.items()), drafts
