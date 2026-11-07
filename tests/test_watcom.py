"""#81: evidence from homemade files, without needing or executing a proprietary Watcom copy."""

import json
import struct

import pytest

from decompnet.consoles.pc import watcom


def record(kind, payload=b"", checksum=True):
    head = struct.pack("<BH", kind, len(payload) + 1) + payload
    return head + bytes([(-sum(head)) % 256 if checksum else 0])


def mz(signature=b"PE\0\0", machine=0x14C):
    data = bytearray(128)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 64)
    data[64:64 + len(signature)] = signature
    struct.pack_into("<H", data, 68, machine)
    return bytes(data)


@pytest.mark.parametrize(("data", "expected"), [
    (mz(), "PE x86"), (mz(machine=0x8664), "PE x64"), (mz(b"LE"), "LE (DOS extender or OS/2)"),
    (mz(b"LX"), "LX (OS/2)"), (mz(b"NE"), "NE (16-bit Windows or OS/2)"),
    (b"MZ" + bytes(62), "DOS MZ"), (b"not an executable", "unknown"),
])
def test_executable_formats_do_not_imply_a_compiler(data, expected):
    assert watcom.executable_format(data) == expected


def test_truncated_executables_have_actionable_errors():
    for data in (b"MZ", mz()[:65], mz()[:75], b"MZ" + bytes(58) + struct.pack("<I", 1000)):
        with pytest.raises(watcom.ProbeError, match="complete executable"):
            watcom.executable_format(data)


def test_omf_inventory_keeps_fixups_and_unknown_records_visible():
    data = (record(0x80, b"\x07probe.c") + record(0x99, b"segment", checksum=False)
            + record(0x9D, b"fixups") + record(0xCE, b"vendor") + record(0x8B, b"\0"))
    assert watcom.omf_records(data) == {"THEADR": 1, "SEGDEF32": 1, "FIXUPP32": 1,
                                       "UNKNOWN_0xCE": 1, "MODEND32": 1}


@pytest.mark.parametrize("data", [
    b"", b"MZ", b"\x80\x00\x00", b"\x80\xff\xff", record(0x80) + b"\x99",
    record(0x80), record(0x80) + record(0x80), record(0x80) + record(0x8A) + b"padding",
    record(0x80) + record(0x8B) + record(0x80), b"\x80\x02\x00\x01\x01",
])
def test_invalid_omf_never_produces_a_successful_inventory(data):
    with pytest.raises(watcom.ProbeError):
        watcom.omf_records(data)


def test_compiler_report_is_case_insensitive_and_never_guesses_105(tmp_path):
    binary = tmp_path / "BINNT" / "WCC386.EXE"
    binary.parent.mkdir()
    binary.write_bytes(mz())
    (tmp_path / "H").mkdir()
    report = watcom.compiler_report(tmp_path)
    assert report["compilers"]["binnt/wcc386.exe"]["format"] == "PE x86"
    assert report["compilers"]["binnt/wcc386.exe"]["file_version"] is None
    assert report["headers_present"]
    assert not report["supported"] and not report["runner_tested"]
    assert str(tmp_path) not in json.dumps(report)


def test_dos_hosted_compiler_is_not_offered_as_a_windows_compiler(tmp_path):
    (tmp_path / "binw").mkdir()
    (tmp_path / "binw/wcc386.exe").write_bytes(mz())
    with pytest.raises(watcom.ProbeError, match="Windows tools"):
        watcom.compiler_report(tmp_path)


def test_probe_output_has_no_paths_or_object_symbols(tmp_path, capsys):
    obj = tmp_path / "private-name.obj"
    obj.write_bytes(record(0x80, b"private-source-name.c") + record(0x8B))
    assert watcom.main(["--object", str(obj)]) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["object_records"] == {"MODEND32": 1, "THEADR": 1}
    assert "private" not in output


def test_probe_read_errors_do_not_expose_local_paths(tmp_path, capsys):
    with pytest.raises(SystemExit) as error:
        watcom.main(["--object", str(tmp_path / "private-name.obj")])
    assert error.value.code == 1
    assert "private" not in capsys.readouterr().err


def test_probe_bounds_its_reads(tmp_path, monkeypatch):
    monkeypatch.setattr(watcom, "MAX_FILE_BYTES", 4)
    file = tmp_path / "large.obj"
    file.write_bytes(bytes(5))
    with pytest.raises(watcom.ProbeError, match="smaller"):
        watcom.read_file(file)


def test_compiler_body_is_fingerprinted_separately_from_identical_loaders(tmp_path):
    for sub in ("BINNT", "BINW"):
        (tmp_path / sub).mkdir()
        for name in ("WCC386.EXE", "WPP386.EXE"):
            (tmp_path / sub / name).write_bytes(mz() if sub == "BINNT" else mz(b"LX") + name.encode())
    compilers = watcom.compiler_report(tmp_path)["compilers"]
    assert compilers["binnt/wcc386.exe"]["sha256"] == compilers["binnt/wpp386.exe"]["sha256"]
    assert compilers["binw/wcc386.exe"]["sha256"] != compilers["binw/wpp386.exe"]["sha256"]


def test_older_compiler_body_in_binb_is_not_missed(tmp_path):
    for sub in ("BINNT", "BINB"):
        (tmp_path / sub).mkdir()
        (tmp_path / sub / "WCC386.EXE").write_bytes(mz() if sub == "BINNT" else mz(b"LX"))
    compilers = watcom.compiler_report(tmp_path)["compilers"]
    assert compilers["binb/wcc386.exe"]["format"] == "LX (OS/2)"


def test_conversion_never_overwrites_the_original_or_existing_output(tmp_path, capsys):
    from tests.test_x86_omf import module

    obj = tmp_path / "original.obj"
    original = module()
    obj.write_bytes(original)
    with pytest.raises(SystemExit) as error:
        watcom.main(["--object", str(obj), "--coff", str(obj)])
    assert error.value.code == 1
    assert obj.read_bytes() == original
    assert "original.obj" not in capsys.readouterr().err


def test_probe_can_convert_a_homemade_object(tmp_path, capsys):
    from tests.test_x86_omf import module

    obj, coff = tmp_path / "probe.obj", tmp_path / "probe.coff"
    obj.write_bytes(module())
    assert watcom.main(["--object", str(obj), "--coff", str(coff)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["coff"] == {"bytes": coff.stat().st_size, "experimental": True}
    assert coff.read_bytes()[:2] == b"\x4c\x01"
