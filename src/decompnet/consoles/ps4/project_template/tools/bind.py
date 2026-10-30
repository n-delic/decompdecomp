"""Apply only explicit link-time bindings to compiled PS4 objects.

GOT loads for internal symbols can be relaxed to LEA. An imported stack guard is
left unresolved unless the project's config explicitly supplies its GOT address.
No address is assumed from another game. The unbound object remains judgeable.
"""

import argparse
import struct
import sys
from pathlib import Path

GUARD_SYMBOL = "__stack_chk_guard"
GOTPCREL, GOTPCRELX, REX_GOTPCRELX, PC32 = 9, 41, 42, 2

SHDR = struct.Struct("<IIQQQQIIQQ")
SYM = struct.Struct("<IBBHQQ")
RELA = struct.Struct("<QQq")


def section_table(data: bytes) -> dict:
    shoff, = struct.unpack_from("<Q", data, 0x28)
    shentsize, shnum, shstrndx = struct.unpack_from("<HHH", data, 0x3A)
    secs = []
    for i in range(shnum):
        name, kind, _flags, addr, off, size, link, info, _align, entsize = SHDR.unpack_from(
            data, shoff + i * shentsize)
        secs.append({"i": i, "name": name, "type": kind, "addr": addr, "off": off, "size": size,
                     "link": link, "info": info, "entsize": entsize})
    return {"data": data, "secs": secs, "shoff": shoff, "shentsize": shentsize, "shstrndx": shstrndx}


def symbol_table(tab: dict) -> list[tuple[str, int, int, int, int]]:
    """(name, type, section index, value, size)."""
    data, secs = tab["data"], tab["secs"]
    symtab = next((s for s in secs if s["type"] == 2), None)
    if symtab is None:
        return []
    strtab = secs[symtab["link"]]
    out = []
    for i in range(symtab["size"] // SYM.size):
        name_off, info, _other, shndx, value, size = SYM.unpack_from(data, symtab["off"] + i * SYM.size)
        start = strtab["off"] + name_off
        end = data.index(b"\0", start)
        out.append((data[start:end].decode("utf-8", "replace"), info & 0xF, shndx, value, size))
    return out


def unit_vaddr(obj: Path) -> int | None:
    """A unit object is named after the game address it starts at: text_005C08A0 -> 0x5c08a0."""
    try:
        return int(obj.stem.rsplit("_", 1)[1], 16)
    except (IndexError, ValueError):
        return None


def game_addresses(target: Path) -> dict[str, int]:
    """name -> the address the game has that symbol at (from the delinked object, laid out at real offsets)."""
    base = unit_vaddr(target)
    if base is None:
        return {}
    tab = section_table(target.read_bytes())
    return {name: base + value for name, _t, shndx, value, _s in symbol_table(tab) if shndx != 0 and name}


def bind(obj: Path, target: Path | None, guard: int | None) -> int:
    """Bind an explicitly configured guard slot; absent means preserve its relocation."""
    if guard is None:
        return 0
    data = bytearray(obj.read_bytes())
    if data[:4] != b"\x7fELF":
        return 0
    tab = section_table(bytes(data))
    secs, shoff, shentsize = tab["secs"], tab["shoff"], tab["shentsize"]
    syms = symbol_table(tab)
    addr_of = game_addresses(target) if target is not None else {}
    if not addr_of:
        return 0
    slot_name = f"D_{guard:08X}"
    target_symbols = symbol_table(section_table(target.read_bytes())) if target is not None else []
    named_slot = any(name == slot_name for name, *_ in target_symbols)
    if named_slot:
        # The target delinker names mapped slots. Keep the operand relocated to
        # that exact slot instead of turning it into an unlabelled literal.
        symtab = next(s for s in secs if s["type"] == 2)
        strtab = secs[symtab["link"]]
        strings = bytes(data[strtab["off"]:strtab["off"] + strtab["size"]])
        name_offset = len(strings)
        strings += slot_name.encode("utf-8") + b"\0"
        for index, (name, *_rest) in enumerate(syms):
            if name == GUARD_SYMBOL:
                struct.pack_into("<I", data, symtab["off"] + index * SYM.size, name_offset)
        new_offset = len(data)
        data.extend(strings)
        struct.pack_into("<Q", data, shoff + strtab["i"] * shentsize + 24, new_offset)
        struct.pack_into("<Q", data, shoff + strtab["i"] * shentsize + 32, len(strings))
    patched = 0
    for sec in secs:
        if sec["type"] != 4:  # SHT_RELA
            continue
        owner = next((s for s in syms if s[2] == sec["info"] and s[1] == 2), None)  # the section's STT_FUNC symbol
        if owner is None or owner[0] not in addr_of:
            continue
        func_addr = addr_of[owner[0]]
        count = sec["size"] // RELA.size
        kept = []
        for i in range(count):
            r_offset, r_info, _addend = RELA.unpack_from(data, sec["off"] + i * RELA.size)
            sym_index, r_type = r_info >> 32, r_info & 0xFFFFFFFF
            raw = bytes(data[sec["off"] + i * RELA.size: sec["off"] + (i + 1) * RELA.size])
            name = syms[sym_index][0] if sym_index < len(syms) else ""
            if r_type in (GOTPCREL, GOTPCRELX, REX_GOTPCRELX) and name == GUARD_SYMBOL:
                if named_slot:
                    kept.append(RELA.pack(r_offset, (sym_index << 32) | PC32, _addend))
                    patched += 1
                    continue
                field = secs[sec["info"]]["off"] + r_offset
                insn_end = func_addr + (r_offset - owner[3]) + 4
                struct.pack_into("<I", data, field, (guard - insn_end) & 0xFFFFFFFF)
                patched += 1
                continue
            kept.append(raw)
        if len(kept) != count or named_slot:
            body = b"".join(kept)
            data[sec["off"]:sec["off"] + len(body)] = body
            struct.pack_into("<Q", data, shoff + sec["i"] * shentsize + 32, len(body))  # sh_size
    if patched:
        obj.write_bytes(bytes(data))
    return patched


def relax_got(obj: Path, target: Path | None) -> int:
    """The linker's GOTPCREL relaxation, done here because nothing links these objects.

    `mov reg,[rip+GOT]` against a symbol the binary defines is relaxed by the linker into `lea reg,[rip+sym]`
    (GOTPCRELX), which is why the game's code reads `lea rax,[D]; load [rax+off]` while our object holds the
    pre-link `mov rax,[rip+GOT]`. Those are the same 7 bytes with 8B where the lea has 8D, so this rewrites the
    opcode and turns the relocation into R_X86_64_PC32 with the addend kept (-4) - exactly what `ld` leaves behind.

    Only symbols this binary defines: `D_`/`func_` names the delinked target names, i.e. the game's own, which are
    non-preemptible and therefore relaxable. An imported symbol (`__stack_chk_guard`, libc data) keeps its GOT load.
    """
    data = bytearray(obj.read_bytes())
    if data[:4] != b"\x7fELF" or target is None or not target.exists():
        return 0
    tab = section_table(bytes(data))
    secs, syms = tab["secs"], symbol_table(tab)
    known = {name for name, _t, shndx, _v, _s in symbol_table(section_table(target.read_bytes()))
             if name and (shndx != 0 or name.startswith(("D_", "func_")))}
    if not known:
        return 0
    relaxed = 0
    for sec in secs:
        if sec["type"] != 4:  # SHT_RELA
            continue
        base = secs[sec["info"]]["off"]  # sh_info: the section these relocations apply to
        for i in range(sec["size"] // RELA.size):
            at = sec["off"] + i * RELA.size
            r_offset, r_info, r_addend = RELA.unpack_from(data, at)
            sym_index, r_type = r_info >> 32, r_info & 0xFFFFFFFF
            if r_type not in (GOTPCREL, GOTPCRELX, REX_GOTPCRELX):
                continue
            name = syms[sym_index][0] if sym_index < len(syms) else ""
            if name not in known:
                continue
            field = base + r_offset  # the disp32
            start = field - 3  # REX 8B modrm disp32, or 8B modrm disp32
            if start >= base and data[start] in (0x48, 0x49, 0x4C, 0x4D) and data[start + 1] == 0x8B \
                    and (data[start + 2] & 0xC7) == 0x05:
                data[start + 1] = 0x8D
            elif field - 2 >= base and data[field - 2] == 0x8B and (data[field - 1] & 0xC7) == 0x05:
                data[field - 2] = 0x8D
            else:
                continue  # not the load-of-address shape: leave the GOT access alone
            struct.pack_into("<Q", data, at + 8, (sym_index << 32) | PC32)
            relaxed += 1
    if relaxed:
        obj.write_bytes(bytes(data))
    return relaxed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("object")
    parser.add_argument("--target", help="the delinked object the addresses come from (default: beside build/)")
    parser.add_argument("--guard", type=lambda s: int(s, 0), default=None)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    obj = Path(args.object)
    target = Path(args.target) if args.target else obj.parent.parent.parent / "target" / obj.name
    exists = target.exists()
    n = bind(obj, target if exists else None, args.guard)
    relaxed = relax_got(obj, target if exists else None)
    if not args.quiet:
        print(f"{obj}: bound {n} guard load(s), relaxed {relaxed} GOT load(s)"
              + ("" if exists else f" (no target object at {target})"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
