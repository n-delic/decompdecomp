"""Offline evidence for #81, before Watcom can join the PC compiler connector.

The current PC build uses MSVC commands and COFF. A compiler folder alone cannot make Watcom work: its OMF
relocations, register conventions and possibly DOS executable format need readers first. This probe never runs
the compiler or imports a game. It reports file fingerprints and OMF record kinds, not proprietary file contents.
Run `python -m decompnet.consoles.pc.watcom --help` for the evidence a contributor can collect locally.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from collections import Counter
from pathlib import Path

from ...kits import file_version, find

MAX_FILE_BYTES = 64 * 1024 * 1024
DOS_HEADER_BYTES, NEW_HEADER_AT = 64, 0x3C
OMF_HEADER = {0x80, 0x82}
OMF_END = {0x8A, 0x8B}
# Record numbers from Open Watcom's bld/watcom/h/pcobj.h. Unknown records stay visible in a report: this is an
# inventory, not a converter that could silently drop a relocation and give the judge different bytes.
RECORD_NAMES = {
    0x80: "THEADR", 0x82: "LHEADR", 0x88: "COMENT", 0x8A: "MODEND", 0x8B: "MODEND32",
    0x8C: "EXTDEF", 0x90: "PUBDEF", 0x91: "PUBDEF32", 0x94: "LINNUM", 0x95: "LINNUM32",
    0x96: "LNAMES", 0x98: "SEGDEF", 0x99: "SEGDEF32", 0x9A: "GRPDEF", 0x9C: "FIXUPP",
    0x9D: "FIXUPP32", 0xA0: "LEDATA", 0xA1: "LEDATA32", 0xA2: "LIDATA", 0xA3: "LIDATA32",
    0xB0: "COMDEF", 0xB4: "LEXTDEF", 0xB6: "LPUBDEF", 0xB7: "LPUBDEF32", 0xB8: "LCOMDEF",
    0xBC: "CEXTDEF", 0xC2: "COMDAT", 0xC3: "COMDAT32", 0xC6: "ALIAS", 0xCA: "LLNAMES",
}


class ProbeError(ValueError):
    """A malformed input or missing compiler file, with an action the contributor can take."""


def read_file(path: Path) -> bytes:
    """Bound reads even if the selected file grows after stat; never follow a folder recursively."""
    with path.open("rb") as stream:
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ProbeError("Choose a file smaller than 64 MiB for the compatibility probe.")
    return data


def executable_format(data: bytes) -> str:
    """Distinguish Windows PE from DOS/OS2 formats; an MZ stub alone proves neither Windows nor Watcom."""
    if data[:2] != b"MZ":
        return "unknown"
    if len(data) < DOS_HEADER_BYTES:
        raise ProbeError("The DOS header is truncated. Choose the complete executable.")
    offset = struct.unpack_from("<I", data, NEW_HEADER_AT)[0]
    if offset < DOS_HEADER_BYTES:
        return "DOS MZ"
    if offset + 2 > len(data):
        raise ProbeError("The executable's new header is outside the file. Choose the complete executable.")
    signature = data[offset:offset + 2]
    if signature == b"PE":
        if data[offset:offset + 4] != b"PE\0\0" or offset + 24 > len(data):
            raise ProbeError("The PE header is truncated or invalid. Choose the complete executable.")
        machine = struct.unpack_from("<H", data, offset + 4)[0]
        return {0x14C: "PE x86", 0x8664: "PE x64"}.get(machine, f"PE machine 0x{machine:04X}")
    return {b"LE": "LE (DOS extender or OS/2)", b"LX": "LX (OS/2)", b"NE": "NE (16-bit Windows or OS/2)"}.get(
        signature, "DOS MZ")


def omf_records(data: bytes) -> dict[str, int]:
    """Inventory one complete OMF module, checking lengths and nonzero checksums before trusting the counts.

    OMF allows a zero checksum byte when the producer doesn't supply a checksum. Archives and concatenated
    modules aren't accepted: their padding and dictionary need a library reader, not a scan for plausible bytes.
    """
    if not data or data[0] not in OMF_HEADER:
        raise ProbeError("Choose a single OMF .obj beginning with THEADR or LHEADR, not a library or executable.")
    counts: Counter[str] = Counter()
    offset, ended = 0, False
    while offset < len(data):
        if ended:
            raise ProbeError("Data follows OMF MODEND. Choose one .obj, not a library or joined modules.")
        if offset + 3 > len(data):
            raise ProbeError(f"Truncated OMF record header at byte {offset}. Choose the complete .obj.")
        kind, length = struct.unpack_from("<BH", data, offset)
        end = offset + 3 + length
        if length == 0 or end > len(data):
            raise ProbeError(f"Invalid OMF record length at byte {offset}. Choose the complete .obj.")
        if offset and kind in OMF_HEADER:
            raise ProbeError("A second OMF module header was found. Choose one .obj.")
        record = data[offset:end]
        if record[-1] and sum(record) % 256:
            raise ProbeError(f"Bad OMF checksum at byte {offset}. Check the original .obj.")
        counts[RECORD_NAMES.get(kind, f"UNKNOWN_0x{kind:02X}")] += 1
        ended = kind in OMF_END
        offset = end
    if not ended:
        raise ProbeError("OMF MODEND is missing. Choose the complete .obj.")
    return dict(sorted(counts.items()))


def compiler_report(folder: Path) -> dict:
    """Fingerprint the Windows-hosted 386 compilers without executing proprietary programs or guessing versions.

    Watcom's binnt layout is the candidate for the existing Windows runners. DOS-hosted binaries under binw are
    deliberately not treated as runnable through wibo. A real 10.5 copy must still establish the runner and layout.
    """
    paths = {f"{sub}/{name}": find(folder, f"{sub}/{name}")
             for sub in ("binnt", "binw", "binb") for name in ("wcc386.exe", "wpp386.exe")}
    if paths["binnt/wcc386.exe"] is None:
        raise ProbeError("No binnt/wcc386.exe was found. Choose the Watcom installation root with its Windows tools.")
    files = {}
    for name, path in paths.items():
        if path is not None:
            data = read_file(path)
            files[name] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
                           "format": executable_format(data), "file_version": file_version(path) or None}
    return {"compilers": files, "headers_present": find(folder, "h", dirs=True) is not None,
            "note": "BINNT programs are loaders; identify the BINW (10.5) or BINB (10.0a) compiler bodies too.",
            "supported": False, "runner_tested": False,
            "next": "Compile the homemade probe locally; Watcom is not yet connected to the PC build or judge."}


def main(argv: list[str] | None = None) -> int:
    """Print a shareable inventory with no local paths, code, symbols or game contents."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler", type=Path, help="your Watcom root (reads binnt/wcc386.exe and wpp386.exe)")
    parser.add_argument("--object", type=Path, help="one homemade probe's OMF object; never upload the object")
    parser.add_argument("--coff", type=Path, help="convert --object to a new COFF file (experimental flat OMF subset)")
    parser.add_argument("--executable", type=Path, help="a local executable; only its format is reported")
    args = parser.parse_args(argv)
    if not any((args.compiler, args.object, args.executable)):
        parser.error("give --compiler, --object or --executable")
    if args.coff and not args.object:
        parser.error("--coff needs --object")
    report = {}
    try:
        if args.compiler:
            report["watcom"] = compiler_report(args.compiler)
        if args.object:
            data = read_file(args.object)
            report["object_records"] = omf_records(data)
            if args.coff:
                from ...arch.x86_omf import OMFError, convert

                try:
                    converted = convert(data)
                except OMFError as error:
                    raise ProbeError(str(error)) from error
                # Never replace an existing object, especially the input itself.
                with args.coff.open("xb") as output:
                    output.write(converted)
                report["coff"] = {"bytes": len(converted), "experimental": True}
        if args.executable:
            report["executable_format"] = executable_format(read_file(args.executable))
    except (OSError, ProbeError) as error:
        # OSError's filename may contain a private local path. Keep reports suitable for an issue comment.
        parser.exit(1, f"Watcom probe: {error if isinstance(error, ProbeError) else 'Could not read an input. Check its path and permissions.'}\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
