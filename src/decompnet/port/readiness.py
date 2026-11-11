"""The port readiness report: what still stands between a matched project and a native build of it.

It reads the project's functions, its symbols, its split and its image, and changes nothing: no compiler runs, so it
is quick enough to ask again after every match. The console's port kit (consoles/: `Console.port_kit`) says which
library functions a port gets from the kit and which graphics microcode it renders; a console with no kit still
gets the counts that don't depend on one.

Library code is only what the app has proven is library code (app/sdk_identity.py: full-size signatures, verified
assembly), never what a function's name looks like. A function only named like one is listed as unproven, and
counts as the game's own until it is proven.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..network import IN_BUILD, NetworkError
from ..rom.symbols import AUTO_NAME

_COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)
_STRING = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')
_IDENT = re.compile(r"\b[A-Za-z_]\w*\b")
_HEX = re.compile(r"\b0[xX]([0-9A-Fa-f]{1,8})[uUlL]*\b")
_LIMIT = 40  # names listed per line of the report; the counts are always whole


def _code(source: str) -> str:
    """A function's C without its comments and string literals, which name nothing the port has to move."""
    return _STRING.sub('""', _COMMENT.sub(" ", source or ""))


_UNSHAPED = ("unknown", "repeated bytes", "")  # ROM Explorer's kinds that say nothing of what the bytes are


def _rom_data(net, slug: str, cfg: dict, size: int) -> dict:
    """The image's bytes outside its code segments, which a port takes as assets, and what ROM Explorer's saved
    scan (when there is one) recognised in them. The scan is read, never started: it's ROM Explorer's job."""
    code = sum(max(0, int(s["rom_end"]) - int(s["rom_start"])) for s in cfg.get("segments", [])
               if s.get("rom_end") is not None and s.get("rom_start") is not None)
    out = {"bytes": max(0, size - code), "scanned": False, "stale": False, "kinds": {}, "unshaped": 0}
    from ..app import rom_explorer
    try:
        listing = rom_explorer.listing(net, slug)
    except (NetworkError, KeyError, TypeError, ValueError):
        return out
    if not listing.get("available"):
        return out
    kinds = {}
    for region in listing["regions"]:
        if region.get("kind") != "code":
            kinds[region.get("kind") or "unknown"] = kinds.get(region.get("kind") or "unknown", 0) + int(region["size"])
    out.update(scanned=True, stale=bool(listing.get("stale")), kinds=dict(sorted(kinds.items(), key=lambda kv: -kv[1])),
               unshaped=sum(v for k, v in kinds.items() if k in _UNSHAPED))
    return out


def readiness(net, slug: str) -> dict:
    """{"kit", "code", "graphics", "audio", "data", "addresses", "rom_data", "blockers"} for a ROM project."""
    from ..app.sdk_identity import display_name, identify

    proj = net.project_row(slug)
    if not net.is_rom(proj):
        raise NetworkError("The port readiness report reads projects decompdecomp split from a ROM; projects built "
                           "from a public decomp or started in project mode aren't read yet (ROADMAP.md, Ports).")
    console = net.console(proj)
    kit = console.port_kit
    cfg = net.cfg(proj)
    root = Path(proj["root"])
    rows = net.project_store(proj["id"]).all(
        f"SELECT id, name, vram, size, status, matched_source, meta FROM functions WHERE project_id=? AND {IN_BUILD} "
        "ORDER BY vram, id", proj["id"])
    sdk = identify(net, proj, net.sdk_functions(proj), include_asm=True)

    code = {"total": len(rows), "c": 0, "replaced": [], "native": [], "library_other": [], "game_asm": [],
            "open": [], "unproven": []}
    game_c = []
    for r in rows:
        hint = sdk.get(r["id"])
        if hint:
            name = display_name(hint, r["name"])
            groups = {kit.sdk_group(n) for n in hint.get("names") or [name]} if kit else {None}
            group = "replaced" if "replaced" in groups else "native" if "native" in groups else "library_other"
            code[group].append(name)
            if r["status"] == "matched":
                code["c"] += 1
            continue
        if kit and kit.sdk_group(r["name"]):
            code["unproven"].append(r["name"])
        if r["status"] == "matched":
            code["c"] += 1
            game_c.append(r)
        elif r["status"] == "asm":
            code["game_asm"].append(r["name"])
        else:
            code["open"].append(r["name"])

    # Data the C reaches by a symbol defined only by its address: a port needs each as C or as an extracted asset.
    symbols = net.symbols(proj["id"])
    functions = {r["name"] for r in rows}
    data, automatic = set(), set()
    low, high = console.ram
    literals = {}
    for r in game_c:
        text = _code(r["matched_source"])
        for name in set(_IDENT.findall(text)):
            symbol = symbols.by_name.get(name)
            if name in functions or (symbol is None and not AUTO_NAME.match(name)):
                continue
            if symbol is not None and symbol.kind == "func":
                continue
            if symbol is None and not name.startswith(("D_", "B_")):
                continue  # an automatic name for code (func_, jtbl_) or a local the split doesn't know
            data.add(name)
            if AUTO_NAME.match(name):
                automatic.add(name)
        found = [v for v in (int(m.group(1), 16) for m in _HEX.finditer(text)) if high > low and low <= v < high]
        if found:
            literals[r["name"]] = len(found)
    # Data already in C (app/data_units.py): a symbol inside a matched unit's span is reached by name there
    from ..app.data_units import matched
    units = sorted((a, a + (u["span"] or u["size"] or 0)) for a, u in matched(net, proj).items())
    in_c = {n for n in data if (a := symbols.resolve(n)) is not None and any(lo <= a < hi for lo, hi in units)}
    data -= in_c
    automatic -= in_c

    graphics = []
    audio = {"library": "", "need": ""}
    image_path = root / cfg.get("rom", {}).get("file", "")
    image = image_path.read_bytes() if image_path.is_file() else b""
    if kit:
        graphics = kit.microcode(image) if kit.microcode and image else []
        names = [r["name"] for r in rows] + [n for hint in sdk.values() for n in hint.get("names", [])]
        audio = kit.audio(names) if kit.audio else audio

    report = {
        "console": console.name,
        "kit": {"name": kit.name, "summary": kit.summary} if kit else None,
        "code": {k: (v if isinstance(v, int) else len(v)) for k, v in code.items()},
        "names": {k: v[:_LIMIT] for k, v in code.items() if isinstance(v, list)},
        "graphics": graphics,
        "audio": audio,
        "data": {"symbols": len(data), "automatic": len(automatic), "in_c": len(in_c), "units": len(units),
                 "examples": sorted(automatic or data)[:_LIMIT]},
        "addresses": {"functions": len(literals), "literals": sum(literals.values()),
                      "examples": sorted(literals, key=lambda n: -literals[n])[:_LIMIT]},
        "rom_data": _rom_data(net, slug, cfg, len(image)),
    }
    report["blockers"] = blockers(report)
    report["ready"] = not report["blockers"]
    return report


def blockers(report: dict) -> list[str]:
    """What stands in a port's way, in the order docs/PORTING.md works through it, each a sentence to act on."""
    out = []
    code = report["code"]
    if report["kit"] is None:
        out.append(f"{report['console']} has no port kit yet: no library to build a port on (ROADMAP.md, Ports).")
    if code["open"]:
        out.append(f"{code['open']} functions have no C yet: match them, or write C for the port that needn't match.")
    if code["game_asm"]:
        out.append(f"{code['game_asm']} of the game's own functions are assembly: each needs C for the port, behind "
                   "#ifdef PORT if it can't match.")
    if code["unproven"]:
        out.append(f"{code['unproven']} functions are named like library code the kit replaces, but the app hasn't "
                   "proven they are: they count as the game's own until their SDK signature or assembly is verified.")
    for g in report["graphics"]:
        if g["support"] == "missing":
            out.append(f"The game loads {g['name']} {g['version']} microcode, which the kit doesn't render: its "
                       "commands need adding to the kit's renderer.")
        elif g["support"] == "check":
            out.append(f"The game loads {g['name']} {g['version']} microcode, which the kit renders only in part: try "
                       "the game's own display lists with it first.")
    if report["kit"] and not report["audio"]["library"]:
        out.append(report["audio"]["need"])
    if report["data"]["symbols"]:
        out.append(f"The C reaches {report['data']['symbols']} data symbols that exist only as addresses in the "
                   "split's assembly: each needs to be C (compile_data), or an asset the kit extracts.")
    if report["addresses"]["literals"]:
        out.append(f"{report['addresses']['functions']} functions write {report['addresses']['literals']} addresses "
                   "as numbers: each needs to be a symbol so the port (and mods) can move it.")
    rom = report["rom_data"]
    if rom["bytes"] and not rom["scanned"]:
        out.append(f"{rom['bytes']:,} bytes of the ROM are outside its code, and ROM Explorer hasn't scanned it: scan "
                   "it to see which are textures, sounds and models, for the kit's extractor.")
    elif rom["unshaped"]:
        out.append(f"ROM Explorer can't yet say what {rom['unshaped']:,} bytes of the ROM are: each needs a shape the "
                   "kit's extractor knows, or a reason the port doesn't need it."
                   + (" Its scan is older than the project's layout: scan again." if rom["stale"] else ""))
    return out


def describe(report: dict) -> str:
    """The report as text, for agents (the `port_readiness` tool)."""
    code = report["code"]
    lines = [f"Port readiness ({report['console']}"
             + (f", kit: {report['kit']['name']})" if report["kit"] else ", no port kit yet)"),
             f"Functions: {code['total']}, {code['c']} in C. Library code the kit replaces: {code['replaced']}; "
             f"library code compiled as it is: {code['native']}; other library code: {code['library_other']}. "
             f"The game's own in assembly: {code['game_asm']}; without C: {code['open']}."]
    if report["graphics"]:
        lines.append("Graphics microcode: " + ", ".join(
            f"{g['name']}{'.' + g['variant'] if g['variant'] else ''} {g['version']} ({g['support']})"
            for g in report["graphics"]))
    if report["audio"]["library"]:
        lines.append(f"Audio: {report['audio']['need']}")
    for key, title in (("game_asm", "Game functions in assembly"), ("open", "Without C"),
                       ("unproven", "Named like library code, unproven")):
        if report["names"][key]:
            lines.append(f"{title}: {', '.join(report['names'][key])}")
    if report["data"]["examples"]:
        lines.append(f"Data symbols by address ({report['data']['symbols']}): {', '.join(report['data']['examples'])}")
    rom = report["rom_data"]
    if rom["scanned"]:
        lines.append("ROM outside code, as ROM Explorer read it: "
                     + ", ".join(f"{kind} {size:,} bytes" for kind, size in rom["kinds"].items()))
    if report["addresses"]["examples"]:
        lines.append(f"Functions with addresses as numbers: {', '.join(report['addresses']['examples'])}")
    lines.append("To do:" if report["blockers"] else "Nothing found in the way of a port.")
    lines += [f"- {b}" for b in report["blockers"]]
    return "\n".join(lines)
