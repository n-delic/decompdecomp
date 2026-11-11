"""The game's data as C (docs/PORTING.md, phase 1): a data unit is one of the game's data symbols, and its C is
kept only when the data judge (rom/data_match.py) finds the game's bytes in it.

Where a datum ends comes from the game, never from the C: the next symbol the project names, and every address the
game's own code refers to (each function's stored relocations, which were read from the ROM, not from agents' C).
A unit is keyed by its address, so renaming its symbol keeps its work. The table lives in the project's database.
"""

from __future__ import annotations

import json
import time

from ..network import NetworkError
from ..rom.data_match import DataResult, link_data

SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS data_units(project_id INTEGER, address INTEGER, size INTEGER, span INTEGER,
       status TEXT DEFAULT 'open', source TEXT, problems TEXT DEFAULT '[]', attempts INTEGER DEFAULT 0,
       matched_at REAL, agent_id INTEGER, lease REAL, note TEXT, PRIMARY KEY(project_id, address))''',
)
_CALL = 4  # R_MIPS_26: a call's target is code, not where a datum starts
# columns added after the first release of the table (projects that used compile_data then have it without them)
_ADDED = (("agent_id", "INTEGER"), ("lease", "REAL"), ("note", "TEXT"))


def _store(net, proj):
    store = net.project_store(proj["id"])
    ready = net.__dict__.setdefault("_data_unit_stores", set())
    if proj["id"] not in ready:
        with store.tx() as db:
            for sql in SCHEMA:
                db.execute(sql)
            have = {r[1] for r in db.execute("PRAGMA table_info(data_units)").fetchall()}
            for column, kind in _ADDED:
                if column not in have:
                    db.execute(f"ALTER TABLE data_units ADD COLUMN {column} {kind}")
        ready.add(proj["id"])
    return store


def _project(net, slug):
    proj = net.project_row(slug)
    if not net.is_rom(proj):
        raise NetworkError("Data units are for projects decompdecomp split from a ROM; in a project-mode game the "
                           "data is in the project's own source files.")
    return proj


def references(net, proj) -> dict[int, list[str]]:
    """Every address the game's code refers to for data, and which functions refer to it (read from the ROM)."""
    out: dict[int, list[str]] = {}
    for row in net.project_store(proj["id"]).all(
            "SELECT name, relocs FROM functions WHERE project_id=? AND relocs IS NOT NULL AND gone_mark IS NULL",
            proj["id"]):
        try:
            relocs = json.loads(row["relocs"])
        except (TypeError, ValueError):
            continue
        for reloc in relocs:
            if len(reloc) >= 3 and reloc[1] != _CALL:
                out.setdefault(int(reloc[2]), []).append(row["name"])
    return out


def initialised_data(net, proj) -> list[dict]:
    """Where the program's initialised data is: [{"rom_start", "vram", "size"}]. Splits record code segments' text;
    the data the compiler put after it (.data, .rodata) follows in the ROM and in RAM, up to where .bss starts. The
    console reads that from the program's own code (`Console.bss_start`): the code segment whose text ends nearest
    below it owns the data, if those ROM bytes are no other code segment's."""
    cfg = net.cfg(proj)
    image = net._rom(proj)[0]
    start = net.console(proj).bss_start(bytes(image[:0x2000]))
    segments = [s for s in cfg.get("segments", []) if s.get("rom_end") is not None]
    if start is None or not segments:
        return []
    ends = [(s["vram"] + s["rom_end"] - s["rom_start"], s) for s in segments]
    below = [(end, s) for end, s in ends if end <= start]
    if not below:
        return []
    end, owner = max(below, key=lambda x: x[0])
    size = start - end
    rom_start = owner["rom_end"]
    clash = any(s is not owner and s["rom_start"] < rom_start + size and rom_start < s["rom_end"] for s in segments)
    if size <= 0 or size > 0x400000 or clash or rom_start + size > len(image):
        return []
    return [{"rom_start": rom_start, "vram": end, "size": size}]


def reader(net, proj):
    """The project's reader (code segments), and the initialised data after them."""
    base = net.reader(proj)
    image = net._rom(proj)[0]
    regions = initialised_data(net, proj)

    def read(addr: int, n: int) -> bytes | None:
        if (data := base(addr, n)) is not None:
            return data
        for r in regions:
            if r["vram"] <= addr and addr + n <= r["vram"] + r["size"]:
                at = r["rom_start"] + addr - r["vram"]
                return bytes(image[at:at + n])
        return None
    return read


def _bounds(net, proj, address: int):
    """(where the next named symbol starts, the other places the game's code refers to) after `address`."""
    symbols = net.symbols(proj["id"])
    return symbols.next_after(address), frozenset(a for a in references(net, proj) if a > address)


def unit(net, slug: str, name: str) -> dict:
    """What an agent needs to write a datum's C: its address, how far it runs, its bytes and who uses it."""
    proj = _project(net, slug)
    symbols = net.symbols(proj["id"])
    address = symbols.resolve(name)
    if address is None:
        raise NetworkError(f"`{name}` is not a known symbol: use the name the game's code refers to it by "
                           "(like D_80012340).")
    end, ends = _bounds(net, proj, address)
    nxt = min([e for e in (end, *ends) if e is not None], default=None)
    span = None if nxt is None else nxt - address
    read = reader(net, proj)
    data = read(address, min(span or 64, 4096))
    refs = references(net, proj)
    row = _store(net, proj).one("SELECT * FROM data_units WHERE project_id=? AND address=?", proj["id"], address)
    return {"name": symbols.name_at(address)[0] if symbols.by_addr.get(address) else name, "address": address,
            "span": span, "next": symbols.label(nxt) if nxt is not None else None,
            "bytes": data.hex() if data is not None else None, "bss": data is None,
            "used_by": sorted(set(refs.get(address, []))), "status": row["status"] if row else "open",
            "source": row["source"] if row else None,
            "problems": json.loads(row["problems"]) if row else []}


def compile_data(net, slug: str, name: str, source: str) -> DataResult:
    """Compile a datum's C with the project's compiler and shared header, judge it, and keep it if it matches. A
    matched unit is replaced only by C that matches too."""
    proj = _project(net, slug)
    symbols = net.symbols(proj["id"])
    address = symbols.resolve(name)
    if address is None:
        raise NetworkError(f"`{name}` is not a known symbol: use the name the game's code refers to it by "
                           "(like D_80012340), or give that address this name first with rename_symbol.")
    context = net._context(proj["id"])
    if context is None:  # a project nobody has written a header for yet: the one every project starts with
        from .engine.common import BASE_HEADER
    header = context["header"] if context is not None else BASE_HEADER.format(title=proj["name"])
    identity = {"id": None, "name": name, "vram": address, "meta": "{}", "target": None, "relocs": None}
    compiled = net._compile(proj, header, source, identity)
    if not compiled.ok:
        result = DataResult(False, [f"it doesn't compile: {compiled.log.strip()[-600:]}"], address)
    else:
        end, ends = _bounds(net, proj, address)
        result = link_data(compiled.obj, name, symbols, reader(net, proj),
                           big_endian=bool(net.cfg(proj).get("big_endian", True)), end=end, ends=ends)
    store = _store(net, proj)
    with store.tx() as db:
        row = db.execute("SELECT status FROM data_units WHERE project_id=? AND address=?",
                         (proj["id"], address)).fetchone()
        if row is None:
            db.execute("INSERT INTO data_units(project_id, address) VALUES(?, ?)", (proj["id"], address))
        db.execute("UPDATE data_units SET attempts=attempts+1 WHERE project_id=? AND address=?", (proj["id"], address))
        if result.exact:
            db.execute("UPDATE data_units SET status='matched', source=?, size=?, span=?, problems='[]', matched_at=? "
                       "WHERE project_id=? AND address=?",
                       (source, result.size, result.span, time.time(), proj["id"], address))
        elif row is None or row["status"] != "matched":
            db.execute("UPDATE data_units SET source=?, problems=? WHERE project_id=? AND address=?",
                       (source, json.dumps(result.problems), proj["id"], address))
    return result


def matched(net, proj) -> dict[int, dict]:
    """The project's data units in C, by address: {"size", "span"}."""
    return {r["address"]: {"size": r["size"], "span": r["span"]} for r in _store(net, proj).all(
        "SELECT address, size, span FROM data_units WHERE project_id=? AND status='matched'", proj["id"])}


def unit_text(info: dict) -> str:
    """A data unit for agents (the get_data_unit tool): where it is, how far it runs, its bytes and its users."""
    lines = [f"{info['name']} at 0x{info['address']:08X}: "
             + (f"{info['span']} bytes, to {info['next']}" if info["span"] else "its end isn't known (nothing after it)")
             + f". Status: {info['status']}."]
    if info["used_by"]:
        lines.append(f"Used by: {', '.join(info['used_by'][:20])}")
    if info["bss"]:
        lines.append("No bytes in the ROM: .bss. Define it without an initialiser.")
    elif info["bytes"]:
        raw = bytes.fromhex(info["bytes"])[:256]
        lines += [f"  +0x{i:03X}: {raw[i:i + 16].hex(' ')}" for i in range(0, len(raw), 16)]
        if info["span"] and info["span"] > 256:
            lines.append(f"  … {info['span'] - 256} more bytes: read_data for the rest")
    if info["source"]:
        lines.append(("Its matched C:\n" if info["status"] == "matched" else "Last C tried:\n") + info["source"])
    if info["problems"]:
        lines += [f"- {p}" for p in info["problems"]]
    lines.append("compile_data(name, source) judges one definition: the whole datum, pointers by name.")
    return "\n".join(lines)


def describe(result: DataResult, name: str) -> str:
    """The judge's verdict as text, for agents."""
    if result.exact:
        what = f"{result.size} bytes" + (f" of {result.span}" if result.span and result.span != result.size else "")
        return (f"MATCH: `{name}` is the game's data ({what}" + ("" if result.initialised else ", .bss")
                + "). Saved as its C.")
    return f"Not yet: `{name}`\n" + "\n".join(f"- {p}" for p in result.problems)
