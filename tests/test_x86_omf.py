"""#81: relocation arithmetic is checked independently of the OMF writer, before objdiff sees converted bytes."""

import json
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from decompnet import limits, toolchains
from decompnet.arch.x86_omf import OMFError, convert
from tests.test_watcom import record


def name(value):
    raw = value.encode()
    return bytes([len(raw)]) + raw


def module(fixup=b"\xe4\x00\x16\x01\x01", extra=b""):
    # Flat segment, one external symbol, four initialized bytes plus a return, one public function.
    return (record(0x80, name("homemade.c"))
            + record(0x96, b"\0" + name("_TEXT") + name("CODE") + name("FLAT"))
            + record(0x99, bytes.fromhex("a9 05 00 00 00 02 03 01"))
            + record(0x9A, b"\x04")
            + record(0x8C, name("outside_") + b"\0")
            + record(0xA1, b"\x01" + bytes(4) + struct.pack("<I", 7) + b"\xc3")
            + record(0x9D, fixup) + record(0x91, b"\x01\x01" + name("function_") + bytes(5))
            + extra + record(0x8A, b"\0"))


def coff_parts(data):
    _, count, _, sym_at, num_symbols, _, _ = struct.unpack_from("<HHIIIHH", data)
    sections = [struct.unpack_from("<8sIIIIIIHHI", data, 20 + 40 * i) for i in range(count)]
    return sections, sym_at, num_symbols


def test_absolute_external_fixup_keeps_inline_addend_and_symbol_index():
    obj = convert(module())
    sections, _, count = coff_parts(obj)
    section = sections[0]
    assert count == 3  # segment, function, external
    assert obj[section[4]:section[4] + section[3]] == struct.pack("<I", 7) + b"\xc3"
    assert struct.unpack_from("<IIH", obj, section[5]) == (0, 2, 6)


def test_relative_fixup_keeps_coff_pc_relative_semantics_and_explicit_displacement():
    # Self-relative 32-bit target, explicit displacement 9: 7 + 9 inline; COFF applies S - (P + 4).
    obj = convert(module(bytes.fromhex("a4 00 12 01 01 09 00 00 00")))
    sections, _, _ = coff_parts(obj)
    section = sections[0]
    assert struct.unpack_from("<I", obj, section[4])[0] == 16
    assert struct.unpack_from("<IIH", obj, section[5]) == (0, 2, 0x14)


def test_internal_fixup_targets_segment_symbol():
    obj = convert(module(bytes.fromhex("e4 00 14 01 01")))
    sections, _, _ = coff_parts(obj)
    assert struct.unpack_from("<IIH", obj, sections[0][5]) == (0, 0, 6)


@pytest.mark.parametrize("fixup", [
    bytes.fromhex("00"),  # thread definition
    bytes.fromhex("e4 00 1e"),  # threaded target
    bytes.fromhex("c4 00"),  # 16-bit fixup location
    bytes.fromhex("e4 02 16 01 01"),  # past end of LEDATA
    bytes.fromhex("e4 00 16 02 01"),  # missing frame group
    bytes.fromhex("e4 00 16 01 02"),  # missing external
    bytes.fromhex("e4 00 15 01 01"),  # group target
])
def test_unsupported_fixups_never_reach_the_judge(fixup):
    with pytest.raises(OMFError):
        convert(module(fixup))


@pytest.mark.parametrize("kind", [0xC2, 0xC3, 0xA2, 0xA3, 0xB0, 0x98, 0x90, 0x80])
def test_unsupported_data_and_symbol_records_are_not_silently_dropped(kind):
    with pytest.raises(OMFError):
        convert(module(extra=record(kind)))


def test_corrupt_and_concatenated_objects_are_rejected():
    data = module()
    corrupt = bytearray(data)
    corrupt[5] ^= 1
    for bad in (b"", data[:-1], data + data, bytes(corrupt), b"\x80\0\0"):
        with pytest.raises(OMFError):
            convert(bad)


def test_duplicate_fixup_is_rejected():
    fixup = bytes.fromhex("e4 00 16 01 01")
    with pytest.raises(OMFError, match="Overlapping"):
        convert(module(fixup + fixup))


def test_segment_allocation_is_bounded(monkeypatch):
    from decompnet.arch import x86_omf

    monkeypatch.setattr(x86_omf, "MAX_TOTAL_BYTES", 4)
    with pytest.raises(OMFError, match="limit"):
        convert(module())


def test_weak_external_comment_is_not_silently_dropped():
    with pytest.raises(OMFError, match="symbol resolution"):
        convert(module(extra=record(0x88, b"\x80\xa8\x01\x02")))


def test_real_watcom_cpp_objects_convert_for_both_conventions(tmp_path):
    """Opt in with a local compiler root and a pinned wibo installation; nothing is copied from the compiler."""
    from decompnet.kits import find

    own = os.environ.get("DECOMP_TEST_WATCOM")
    if not own:
        pytest.skip("needs DECOMP_TEST_WATCOM pointing at a local Watcom 10.5 copy")
    compiler = find(Path(own), "binnt/wpp386.exe")
    wibo = toolchains.installed("wibo")
    objdiff = toolchains.installed("objdiff-cli")
    if compiler is None or not wibo or not objdiff:
        pytest.skip("needs Watcom C++ 10.5, pinned wibo and objdiff-cli")
    source = Path(__file__).resolve().parents[1] / "projects/demo-watcom/probe.c"
    (tmp_path / "probe.cpp").write_bytes(source.read_bytes())
    folders = [find(Path(own), n, dirs=True) for n in ("binb", "binw", "bin")]
    env = {**os.environ, "HOME": str(tmp_path), "WATCOM": own,
           "PATH": os.pathsep.join([*(str(p) for p in folders if p), os.environ["PATH"]])}
    for convention in ("3r", "3s"):
        output = tmp_path / f"probe-{convention}.obj"
        command = [str(toolchains.binary("wibo")), str(compiler), "-bt=nt", "-mf", f"-{convention}", "-ox",
                   f"-fo={output.name}", "probe.cpp"]
        result = subprocess.run(limits.capped(command), cwd=tmp_path, env=env, capture_output=True, timeout=30)
        assert result.returncode == 0, result.stdout.decode(errors="replace") + result.stderr.decode(errors="replace")
        coff = output.with_suffix(".coff")
        coff.write_bytes(convert(output.read_bytes()))
        result = subprocess.run(limits.capped([str(toolchains.binary("objdiff-cli")), "diff", "-1", str(coff), "-2", str(coff),
                                              "-o", str(output.with_suffix(".json"))]),
                                cwd=tmp_path, env=env, capture_output=True, timeout=30)
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        report = json.loads(output.with_suffix(".json").read_text())
        functions = [s for s in report["left"]["symbols"] if s.get("kind") == "SYMBOL_FUNCTION"]
        assert len(functions) == 3
        assert all(s.get("match_percent") == 100 for s in functions)
        instructions = [row["instruction"] for symbol in functions for row in symbol.get("instructions", [])]
        relocation_types = {i["relocation"]["type"] for i in instructions if "relocation" in i}
        assert relocation_types == {6, 20}  # both global references and external calls survive objdiff's reader


@pytest.mark.parametrize(("compiler_name", "suffix"), [("wpp386.exe", ".cpp"), ("wcc386.exe", ".c")])
def test_generated_watcom_compile_is_judged_against_independent_machine_code(tmp_path, compiler_name, suffix):
    """#81: the real compiler -> converter -> names.py path must match bytes, and reject changed C and failures."""
    from decompnet.arch import x86_omf
    from decompnet.consoles.pc import scratch
    from decompnet.kits import find

    own = os.environ.get("DECOMP_TEST_WATCOM_C" if suffix == ".c" else "DECOMP_TEST_WATCOM")
    if not own or not toolchains.installed("wibo") or not toolchains.installed("objdiff-cli"):
        pytest.skip("needs DECOMP_TEST_WATCOM, pinned wibo and objdiff-cli")
    compiler = find(Path(own), f"binnt/{compiler_name}")
    assert compiler is not None
    root = tmp_path / "Project with spaces"
    shutil.copytree(scratch.TEMPLATE, root)
    shutil.copyfile(x86_omf.__file__, root / "tools/omf.py")
    (root / "include").mkdir()
    config = root / "config/PROBE/functions.json"
    config.parent.mkdir(parents=True)
    fn = {"name": "_func_00401000", "addr": 0x401000, "offset": 0, "size": 3, "relocs": []}
    config.write_text(json.dumps({"units": {"unit": [fn]}}))
    # A hand-assembled independent target: add eax, edx; ret (Watcom's 386 register convention).
    import importlib.util

    spec = importlib.util.spec_from_file_location("watcom_probe_delink", root / "tools/delink.py")
    delink = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(delink)
    target = root / "target.obj"
    target.write_bytes(delink.coff([fn], b"\x01\xd0\xc3"))
    source, output = root / f"unit{suffix}", root / "unit.obj"
    (root / "types.h").write_text("typedef int probe_int;\n")
    env = {**os.environ, "HOME": str(tmp_path), "WPP386": "-invalid_global_option"}
    command = [sys.executable, "tools/compile.py", "--family", "watcom", "--watcom-root", own,
               "--compiler", os.path.relpath(compiler, root), "--wrapper", str(toolchains.binary("wibo")), "--include", str(Path(own) / "H"),
               "--config", str(config), "--output", str(output), "--source", str(source),
               "--", "-bt=nt", "-mf", "-3r", "-ox"]
    for expression, expected in (("a + b", 100), ("a - b", None)):
        source.write_text(f'#include "types.h"\nint func_00401000(probe_int a, probe_int b) {{ return {expression}; }}\n')
        result = subprocess.run(limits.capped(command), cwd=root, env=env, capture_output=True, timeout=30)
        assert result.returncode == 0, result.stdout.decode(errors="replace") + result.stderr.decode(errors="replace")
        report = root / "verdict.json"
        result = subprocess.run(limits.capped([str(toolchains.binary("objdiff-cli")), "diff", "-1", str(target),
                                              "-2", str(output), "-o", str(report)]),
                                cwd=root, env=env, capture_output=True, timeout=30)
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        symbols = json.loads(report.read_text())["left"]["symbols"]
        percent = next(s["match_percent"] for s in symbols if s["name"] == fn["name"])
        assert percent == expected if expected is not None else percent < 100
    last_good = output.read_bytes()
    source.write_text("this source does not compile\n")
    result = subprocess.run(limits.capped(command), cwd=root, env=env, capture_output=True, timeout=30)
    assert result.returncode != 0
    assert output.read_bytes() == last_good
    if suffix == ".cpp":
        source.write_text("struct Item { virtual int get() {return 1;} }; Item item;\n")
        result = subprocess.run(limits.capped(command), cwd=root, env=env, capture_output=True, timeout=30)
        assert result.returncode != 0
        assert b"could not be converted" in result.stderr
        assert output.read_bytes() == last_good


def test_project_tools_include_the_single_omf_implementation(tmp_path):
    from decompnet.arch import x86_omf
    from decompnet.consoles.pc import scratch

    (tmp_path / "tools").mkdir()
    (tmp_path / "config/PROBE").mkdir(parents=True)
    changed = scratch.write_tools(tmp_path, "PROBE")
    assert "tools/omf.py" in changed
    assert (tmp_path / "tools/omf.py").read_bytes() == Path(x86_omf.__file__).read_bytes()
    assert scratch.write_tools(tmp_path, "PROBE") == []


def test_virtual_function_linker_directive_is_not_silently_ignored():
    with pytest.raises(OMFError, match="linker directive"):
        convert(module(extra=record(0x88, b"\x80\xfeV\x01\x02")))
