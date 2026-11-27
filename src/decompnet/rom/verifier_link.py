"""Pack-specific ROM reads and diagnostic decoding for the Rust MIPS verifier."""

from contextvars import ContextVar
from dataclasses import asdict
from functools import cached_property
from pathlib import Path

from ..elfobj import FunctionObj, Reloc, symbol_at
from .link import DataMismatch, LinkResult
from .match import _Linker, compiler_library_name

project_inputs: ContextVar[bool] = ContextVar("mips_verifier_project_inputs", default=False)


class PackReader:
    """Describe a pinned scratch project and keep the Python reader lazy for fallback."""

    def __init__(self, net, project, function):
        self.net, self.project, self.function = net, project, function

    @cached_property
    def fallback(self):
        return self.net.reader(self.project, self.function)

    def __call__(self, address, size):
        return self.fallback(address, size)

    def inputs(self, symbols):
        root = Path(self.project["root"])
        cfg = self.net.cfg(self.project)
        images = []
        if any(root.glob("*.yaml")):
            from ..app.engine.core import _load_images

            images = _load_images(self.net, self.project)
        segments = [{"exclusive_ram_id": "", **segment} for segment in cfg["segments"]]
        return {"root": str(root), "image": cfg["rom"]["file"], "image_sha1": cfg["rom"].get("sha1"),
                "segments": segments, "images": images,
                "names": {name: symbol.address for name, symbol in symbols.by_name.items()},
                "addresses": {address: (symbol.name, symbol.size) for address, symbol in symbols.by_addr.items()}}


def _encode_reads(reads):
    return [(address, size, data.hex() if data is not None else None) for address, size, data in reads]


def link_rom(obj, target, symbols, read, exchange):
    """Keep overlay-aware memory reads and ECOFF/dispatch evidence with the project's reader."""
    native_inputs = isinstance(read, PackReader)

    def read_dispatch(address, size):
        value = exchange("rom_read", [address, size])["rom_read_value"]
        return bytes.fromhex(value) if value is not None else None

    evidence = _Linker(obj, target.name, target, symbols, read_dispatch if native_inputs else read)

    def lookup(request):
        if "project_inputs" in request and native_inputs:
            return read.inputs(symbols)
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
    request = {"object": obj.hex(), "target": plan}
    if native_inputs:
        request["rom_offset"] = read.function["rom_offset"]
    reply = exchange("rom_mips", request, lookup)
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


def compare_mips(target, candidate, align_branches, exchange, *, details=False):
    def function(fn):
        return {"name": fn.name, "big_endian": fn.big_endian, "code": fn.code.hex(),
                "relocs": [reloc.to_json() for reloc in fn.relocs]}

    request = {"target": function(target), "candidate": function(candidate), "align_branches": align_branches}
    if details:
        request["details"] = True
    return exchange("compare_mips", request)
