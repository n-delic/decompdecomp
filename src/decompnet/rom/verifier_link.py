"""Pack-specific ROM reads and diagnostic decoding for the Rust MIPS verifier."""

from dataclasses import asdict

from ..elfobj import FunctionObj, Reloc, symbol_at
from .link import DataMismatch, LinkResult
from .match import _Linker, compiler_library_name


def _encode_reads(reads):
    return [(address, size, data.hex() if data is not None else None) for address, size, data in reads]


def link_rom(obj, target, symbols, read, exchange):
    """Keep overlay-aware memory reads and ECOFF/dispatch evidence with the project's reader."""
    evidence = _Linker(obj, target.name, target, symbols, read)

    def lookup(request):
        if "read" in request:
            data = read(*request["read"])
            return data.hex() if data is not None else None
        if "switch_extent" in request:
            address, offsets = request["switch_extent"]
            start = len(evidence.required_data_reads)
            extent = evidence.switch_extent(address, set(offsets))
            return {"extent": extent, "required": _encode_reads(evidence.required_data_reads[start:])}
        if "elf_name" in request:
            return symbol_at(evidence.symtab, *request["elf_name"])
        if "resolve" in request:
            return symbols.resolve(request["resolve"])
        if "name_at" in request:
            return symbols.name_at(request["name_at"])
        if "label" in request:
            return symbols.label(request["label"])
        if "next_start" in request:
            return symbols.next_after(request["next_start"] - 1)
        if "library_name" in request:
            return compiler_library_name(request["library_name"])
        raise ValueError("Unknown MIPS verifier lookup")

    plan = asdict(target)
    plan["code"] = target.code.hex()
    reply = exchange("rom_mips", {"object": obj.hex(), "target": plan}, lookup)
    if reply is None:
        return None
    if "code" not in reply:
        return LinkResult(None, False, reply["problems"])
    candidate = FunctionObj(target.name, "mips", target.big_endian, bytes.fromhex(reply["code"]),
                            [Reloc.from_json(row) for row in reply["relocs"]])
    placements = {section: {int(offset): address for offset, address in offsets.items()}
                  for section, offsets in reply["placements"].items()}
    required = [(addr, size, bytes.fromhex(value) if value is not None else None)
                for addr, size, value in reply["required"]]
    missing = [(addr, size) for addr, size, value in required if value is None]
    mismatches = [DataMismatch(addr, bytes.fromhex(rom), bytes.fromhex(mine), frozenset(pointers))
                  for addr, rom, mine, pointers in reply["data_mismatches"]]
    return LinkResult(candidate, reply["exact"], reply["problems"], placements, reply["instructions_exact"],
                      bytes.fromhex(reply["linked"]), mismatches, missing, required)
