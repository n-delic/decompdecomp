"""Symbols a port defines at a fixed place or with a fixed value, not as C (docs/PORTING.md, phase 3).

Two kinds the game's C reaches that are neither code nor data in the split:
- ROM offsets: names the original linker gave positions in the cartridge (`D_15DB10`, where an archive starts),
  which the game only uses as numbers for its DMA reads. The port defines each at that address, in an assembly file
  (`.set`: a symbol with a fixed address, which links on macOS and Linux alike), so `(u32)D_15DB10` is 0x15DB10 and
  the port's DMA glue reads that offset from the user's ROM (the asset map covers it).
- The coprocessor's microcode (the N64's RSP programs: rspboot and the graphics and audio microcode after it), which
  the game only points at when it builds a task; a port's renderer and mixer do that work, so each gets zeroed room
  of its size, in the same order, so `D_80001DE0 - D_80001D10` (the boot's size, which a task carries) still holds.
  A symbol counts only when its ROM bytes begin with the console's boot microcode (`PortKit.coprocessor_boot`), or it
  starts where that ends.
- What the console's boot leaves in low memory for its SDK (the N64's 0x80000300: TV type, ROM base, memory size,
  the buffer kept over a reset), under whichever name the game uses, automatic or the SDK's, with the value the
  boot would have left (`PortKit.boot_globals`).

The files hold names and numbers, no game bytes, and go in the project's `port/src` with the zeroed memory's.
"""

from __future__ import annotations

ROM_OFFSETS, BOOT_GLOBALS, MICROCODE = "rom_offsets.S", "boot_globals.c", "rsp_placeholders.S"


def plan(net, proj, names) -> tuple[list[dict], list[dict], list[dict]]:
    """Of the undefined data `names`: (ROM offsets [{"name", "offset"}], boot globals [{"name", "address", "libultra",
    "ctype", "size", "value"}], microcode [{"name", "address", "size"}])."""
    from pathlib import Path
    cfg = net.cfg(proj)
    image = Path(proj["root"]) / cfg.get("rom", {}).get("file", "")
    size = int(cfg.get("rom", {}).get("size") or 0) or (image.stat().st_size if image.is_file() else 0)
    low, _ = net.console(proj).ram
    kit = net.console(proj).port_kit
    table = kit.boot_globals(cfg) if kit and kit.boot_globals else {}
    symbols = net.symbols(proj["id"])
    offsets, boot, rest = [], [], []
    for name in sorted(names):
        address = symbols.resolve(name)
        if address is None:
            continue
        if address < low and address < size:
            offsets.append({"name": name, "offset": address})
        elif address in table:
            libultra, ctype, length, value = table[address]
            boot.append({"name": name, "address": address, "libultra": libultra, "ctype": ctype, "size": length,
                         "value": value})
        else:
            rest.append((name, address))
    return offsets, boot, _microcode(net, proj, cfg, image, rest, kit)


def _microcode(net, proj, cfg, image, rest, kit) -> list[dict]:
    """The names in `rest` that are the console's microcode: one whose ROM bytes begin like the boot microcode,
    and one that starts where such a symbol ends."""
    if not (kit and kit.coprocessor_boot and image.is_file() and rest):
        return []
    data = image.read_bytes()
    segments = [(int(s["vram"]), int(s["rom_start"]), int(s["rom_end"])) for s in cfg.get("segments", [])
                if s.get("vram") is not None and s.get("rom_start") is not None and s.get("rom_end") is not None]
    symbols = net.symbols(proj["id"])
    known = sorted({s.address for s in symbols.by_name.values() if s.address is not None} | {a for _, a in rest})

    def rom(address):
        return next((start + address - vram for vram, start, end in segments if 0 <= address - vram < end - start),
                    None)

    def room(name, address):
        symbol = symbols.by_name.get(name)
        if symbol is not None and symbol.size:
            return symbol.size
        after = next((a for a in known if a > address), None)
        return after - address if after else 0

    found, ends = [], set()
    for name, address in sorted(rest, key=lambda r: r[1]):
        offset = rom(address)
        if offset is None:
            continue
        boot = data[offset:offset + len(kit.coprocessor_boot)] == kit.coprocessor_boot
        if boot or address in ends:
            length = room(name, address)
            if length:
                found.append({"name": name, "address": address, "size": length})
                if boot:  # one step: the text right after the boot code, not whatever follows that
                    ends.add(address + length)
    return found


def files(offsets: list[dict], boot: list[dict], microcode: list[dict] = ()) -> dict[str, str]:
    """The files: an assembly file of ROM offsets (preprocessed, for macOS's leading underscore), a C file of boot
    values with libultra's own types (no game header, so it can't disagree with one), and an assembly file of
    zeroed room for the microcode, in its order."""
    head = ["#ifdef __APPLE__", "#define SYM(x) _##x", "#else", "#define SYM(x) x", "#endif"]
    asm = ["/* ROM offsets the game uses as numbers, each a symbol at that offset (port/fixed_symbols.py). */", *head]
    for o in offsets:
        asm += [f".globl SYM({o['name']})", f".set SYM({o['name']}), 0x{o['offset']:X}"]
    c = ["/* What the console's boot leaves in low memory, with the values it would leave (port/fixed_symbols.py). */",
         "#include <stdint.h>"]
    for b in boot:
        what = f"/* 0x{b['address']:08X}: {b['libultra']} */"
        if b["size"] > 4:
            c.append(f"{b['ctype']} {b['name']}[{b['size']}]; {what}")
        else:
            c.append(f"{b['ctype']} {b['name']} = 0x{b['value']:X}; {what}")
    ucode = ["/* Room for the coprocessor's microcode, which the port's renderer and mixer replace: zeroed, each the size",
             "   it had and in its order, so the distance between two still holds (port/fixed_symbols.py). */",
             *head, ".data", ".p2align 4"]
    for m in sorted(microcode, key=lambda m: m["address"]):
        ucode += [f".globl SYM({m['name']})", f"SYM({m['name']}): /* 0x{m['address']:08X} */",
                  f".space {m['size']}"]
    return {ROM_OFFSETS: "\n".join(asm) + "\n", BOOT_GLOBALS: "\n".join(c) + "\n", MICROCODE: "\n".join(ucode) + "\n"}
