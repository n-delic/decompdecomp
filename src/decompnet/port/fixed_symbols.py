"""Symbols a port defines at a fixed place or with a fixed value, not as C (docs/PORTING.md, phase 3).

Two kinds the game's C reaches that are neither code nor data in the split:
- ROM offsets: names the original linker gave positions in the cartridge (`D_15DB10`, where an archive starts),
  which the game only uses as numbers for its DMA reads. The port defines each at that address, in an assembly file
  (`.set`: a symbol with a fixed address, which links on macOS and Linux alike), so `(u32)D_15DB10` is 0x15DB10 and
  the port's DMA glue reads that offset from the user's ROM (the asset map covers it).
- What the console's boot leaves in low memory for its SDK (the N64's 0x80000300: TV type, ROM base, memory size,
  the buffer kept over a reset), under whichever name the game uses, automatic or the SDK's, with the value the
  boot would have left (`PortKit.boot_globals`).

The files hold names and numbers, no game bytes, and go in the project's `port/src` with the zeroed memory's.
"""

from __future__ import annotations

ROM_OFFSETS, BOOT_GLOBALS = "rom_offsets.S", "boot_globals.c"


def plan(net, proj, names) -> tuple[list[dict], list[dict]]:
    """Of the undefined data `names`: (ROM offsets [{"name", "offset"}], boot globals [{"name", "address", "libultra",
    "ctype", "size", "value"}])."""
    from pathlib import Path
    cfg = net.cfg(proj)
    image = Path(proj["root"]) / cfg.get("rom", {}).get("file", "")
    size = int(cfg.get("rom", {}).get("size") or 0) or (image.stat().st_size if image.is_file() else 0)
    low, _ = net.console(proj).ram
    kit = net.console(proj).port_kit
    table = kit.boot_globals(cfg) if kit and kit.boot_globals else {}
    symbols = net.symbols(proj["id"])
    offsets, boot = [], []
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
    return offsets, boot


def files(offsets: list[dict], boot: list[dict]) -> dict[str, str]:
    """The two files: an assembly file of ROM offsets (preprocessed, for macOS's leading underscore) and a C file
    of boot values with libultra's own types (no game header, so it can't disagree with one)."""
    asm = ["/* ROM offsets the game uses as numbers, each a symbol at that offset (port/fixed_symbols.py). */",
           "#ifdef __APPLE__", "#define SYM(x) _##x", "#else", "#define SYM(x) x", "#endif"]
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
    return {ROM_OFFSETS: "\n".join(asm) + "\n", BOOT_GLOBALS: "\n".join(c) + "\n"}
