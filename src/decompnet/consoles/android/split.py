"""ARMv7 functions and editing: preserve ELF file offsets and disassemble at their real addresses."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from functools import cached_property

import capstone


@dataclass
class Function:
    name: str
    vram: int
    rom: int
    code: bytes
    thumb: bool
    refs: list = field(default_factory=list)
    segment: str = "native"
    asm_only: bool = False

    @property
    def size(self):
        return len(self.code)

    @cached_property
    def asm(self):
        mode = capstone.CS_MODE_THUMB if self.thumb else capstone.CS_MODE_ARM
        reader = capstone.Cs(capstone.CS_ARCH_ARM, mode)
        reader.skipdata = True
        lines = [f"{'thumb' if self.thumb else 'arm'}_func_start {self.name}"]
        for ins in reader.disasm(self.code, self.vram):
            lines.append(f"/* {ins.address:08X} */ {ins.mnemonic} {ins.op_str}".rstrip())
        return "\n".join(lines) + "\n"

    @property
    def n_insns(self):
        return max(1, len(self.asm.splitlines()) - 1)


def from_row(row):
    row = dict(row)
    return Function(row["name"], row["vram"], row.get("rom_offset") or 0, row["target"],
                    json.loads(row.get("meta") or "{}").get("thumb", True),
                    json.loads(row.get("relocs") or "[]"), row.get("unit") or "native")


def cut(row, at, new_name):
    fn = row if isinstance(row, Function) else from_row(row)
    reader = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB if fn.thumb else capstone.CS_MODE_ARM)
    boundaries = {ins.address for ins in reader.disasm(fn.code, fn.vram)}
    if at not in boundaries or at == fn.vram:
        raise ValueError(f"0x{at:08X} isn't an instruction boundary inside {fn.name}")
    offset = at - fn.vram
    return (replace(fn, code=fn.code[:offset], refs=[r for r in fn.refs if r[0] < offset]),
            replace(fn, name=new_name, vram=at, rom=fn.rom + offset, code=fn.code[offset:],
                    refs=[(o - offset, k, a) for o, k, a in fn.refs if o >= offset]))


def join(rows, start, end, read):
    functions = [from_row(row) for row in rows]
    first = functions[0]
    if any(fn.thumb != first.thumb for fn in functions):
        raise ValueError("ARM and Thumb functions can't be joined into one function")
    data = read(start, end - start)
    if data is None or len(data) != end - start:
        raise ValueError("The joined range isn't entirely in the native library")
    refs = [(o + fn.vram - start, k, a) for fn in functions for o, k, a in fn.refs]
    return replace(first, vram=start, code=data, refs=refs)
