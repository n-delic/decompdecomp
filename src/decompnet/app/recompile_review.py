"""Review static recompiler discoveries and register the selected original byte ranges."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..network import IN_BUILD, NetworkError
from ..rom import split
from ..rom.symbols import Symbol
from . import recompile

PROVENANCE = "reviewed_recompile"


def findings(net, proj, summary):
    found = summary.get("found", [])
    if not found:
        return []
    cfg = net.cfg(proj)
    segments = [split.Segment(**segment) for segment in cfg.get("segments", [])]
    root = Path(proj["root"])
    config = root / "splat.yaml"
    if not config.is_file():
        configs = sorted(root.glob("*.yaml"))
        config = configs[0] if len(configs) == 1 else config
    ranges = split.code_ranges(config) if config.is_file() else []
    rows = net.project_store(proj["id"]).all(
        f"SELECT name, vram, rom_offset, size FROM functions WHERE project_id=? AND {IN_BUILD}", proj["id"])
    room = proj["slug"] in net.rooms or proj["slug"] in net.links
    expected = cfg["rom"].get("analysis_sha1") or cfg["rom"].get("sha1")
    result = []
    for entry in found:
        vram, size = entry["vram"], entry["size"]
        # Older summaries omitted the image offset. A single unchanged mapping can recover it.
        owners = [(s, s.rom_start + vram - s.vram) for s in segments
                  if s.vram <= vram and vram + size <= s.vram + s.rom_end - s.rom_start]
        offset = entry.get("rom")
        if offset is None and len(owners) == 1:
            offset = owners[0][1]
        owner = next((s for s, start in owners if start == offset), None)
        exact = next((row for row in rows if row["vram"] == vram and row["rom_offset"] == offset
                      and row["size"] == size), None)
        reason = ""
        if exact:
            reason = "Already in the split as " + exact["name"] + "."
        elif room:
            reason = "Leave the shared room to change this split."
        elif summary.get("image_sha1") and expected and summary["image_sha1"] != expected:
            reason = "The ROM image changed. Run N64Recomp again."
        elif owner is None or len({start for _, start in owners}) != 1 or size <= 0 or (vram | offset | size) & 3:
            reason = "This range has no unambiguous aligned ROM mapping. Run N64Recomp again."
        elif any((row["rom_offset"] is not None and row["rom_offset"] < offset + size
                  and offset < row["rom_offset"] + row["size"])
                 or (row["vram"] is not None and row["vram"] < vram + size
                     and vram < row["vram"] + row["size"]) for row in rows):
            reason = "Overlaps a function in the current split. Review its boundary separately."
        elif not any(lo <= offset and offset + size <= hi and base + offset - lo == vram
                     for lo, hi, base in ranges):
            reason = "Outside a declared code range. Review the code and data boundaries separately."
        result.append({**entry, "rom": offset, "unit": owner.name if owner else None,
                       "blocked": reason, "added": exact["name"] if exact else None})
    return result


def preview(net, proj, revision, address):
    """Show a bounded disassembly of the reported bytes without registering a function."""
    summary = recompile.summary(net, proj)
    if not summary or summary["at"] != revision:
        raise NetworkError("The N64Recomp report changed. Refresh Porting to review the latest findings.")
    selected = [entry for entry in summary["found"] if entry["vram"] == address]
    if not selected:
        raise NetworkError("This function is absent from the latest report.")
    row = findings(net, proj, {**summary, "found": selected})[0]
    cfg = net.cfg(proj)["rom"]
    if summary.get("image_sha1") and summary["image_sha1"] != (cfg.get("analysis_sha1") or cfg.get("sha1")):
        raise NetworkError("The analysis image changed. Run N64Recomp again.")
    if row["unit"] is None or row["rom"] is None or (address | row["rom"] | row["size"]) & 3:
        raise NetworkError("This range has no aligned mapping in the analysis image.")
    image, _ = net._rom(proj)
    lo, size = row["rom"], row["size"]
    if not 0 <= lo < lo + size <= len(image):
        raise NetworkError("This range falls outside the analysis image. Run N64Recomp again.")
    # For long guesses, show the entry and return boundary without disassembling a whole segment.
    first = min(size, 4 * 96)
    tail = min(size - first, 4 * 16)
    def disassemble(start, length):
        fn = split.function_from_bytes(row["name"], image[lo + start:lo + start + length], lo + start,
                                       address + start, row["unit"], bool(proj["big_endian"]))
        return [f"{line.vram:08X}  {line.mnemonic} {line.operands}".rstrip() for line in fn.lines]

    lines = disassemble(0, first)
    if tail:
        if first + tail < size:
            lines.append("…")
        lines.extend(disassemble(size - tail, tail))
    return {"assembly": "\n".join(lines)}


def add(net, slug, revision, addresses, log):
    """Validate the report and all requested ranges again before publishing any new targets."""
    if type(revision) not in (float, int) or not isinstance(addresses, list) or not addresses or any(
        type(address) is not int for address in addresses
    ):
        raise NetworkError("Select at least one function from the latest N64Recomp report.")
    proj = net.project_row(slug, sync=False)
    console = net.console(proj)
    if not net.is_rom(proj) or console.recompiler is None:
        raise NetworkError("Adding recompiled functions needs a supported ROM project.")
    phase = "Waiting for the project’s split"
    log.progress({"phase": phase})
    log(phase)
    with recompile.build_turn(console.recompiler.folder(Path(proj["root"])), log, slug=slug):
        lock = net.progress._project_lock(slug)
        while not lock.acquire(timeout=.25):
            log.check_cancel()
        try:
            log.check_cancel()
            proj = net.project_row(slug, sync=False)
            summary = recompile.summary(net, proj)
            if not summary or summary["at"] != revision:
                raise NetworkError("The N64Recomp report changed. Refresh Porting and review the latest findings.")
            cfg = net.cfg(proj)
            image = (Path(proj["root"]) / cfg["rom"]["file"]).read_bytes()
            sha1 = hashlib.sha1(image).hexdigest()
            expected = summary.get("image_sha1") or cfg["rom"].get("analysis_sha1") or cfg["rom"].get("sha1")
            if sha1 != expected:
                raise NetworkError("The analysis image changed. Run N64Recomp again before adding functions.")
            log.progress({"phase": "Validating selected ROM ranges"})
            added = _publish(net, proj, summary, addresses, image, sha1, log)
        finally:
            lock.release()
    log(f"Added {len(added)} {'function' if len(added) == 1 else 'functions'} to the split.")
    return {"added": added}


def _publish(net, proj, summary, addresses, image, sha1, log):
    pid = proj["id"]
    console = net.console(proj)
    with net.project_store(pid).tx() as db:
        rows = {row["vram"]: row for row in findings(net, proj, summary)}
        plans = []
        symbols = net.symbols(pid).copy()
        names = {row["name"] for row in db.execute("SELECT name FROM functions WHERE project_id=?", (pid,))}
        for address in dict.fromkeys(addresses):
            log.check_cancel()
            row = rows.get(address)
            if row is None:
                raise NetworkError("A selected function is absent from this report. Refresh and select it again.")
            if row["added"]:
                continue
            if row["blocked"]:
                raise NetworkError(f"{row['name']}: {row['blocked']}")
            lo, size = row["rom"], row["size"]
            hi = lo + size
            if not 0 <= lo < hi <= len(image) or any(fn.rom < hi and lo < fn.rom + fn.size for fn, _ in plans):
                raise NetworkError("Selected functions cross each other or the end of the ROM. Review their ranges.")
            name = row["name"]
            if name in names or name in symbols.by_name:
                name = f"func_{address:08X}_{lo:X}"
            if name in names or name in symbols.by_name:
                raise NetworkError(f"{name} is already used. Resolve the symbol conflict before adding this function.")
            names.add(name)
            fn = split.function_from_bytes(name, image[lo:hi], lo, address, row["unit"], bool(proj["big_endian"]))
            symbols.add(Symbol(name, address, size, "func"), reindex=False, alias=True)
            plans.append((fn, row["why"]))
        symbols.reindex_symbols()
        for index, (fn, evidence) in enumerate(plans, 1):
            log.check_cancel()
            log.progress({"phase": "Adding functions to the split", "done": index - 1, "total": len(plans)})
            target = net._make_target(proj, fn, symbols)
            if target.code != image[fn.rom:fn.rom + fn.size]:
                raise NetworkError(f"{fn.name} did not retain its original ROM bytes.")
            meta = {**console.function_meta(fn), PROVENANCE: {"image_sha1": sha1, "evidence": evidence}}
            db.execute("INSERT INTO functions(project_id,name,size,n_insns,target,relocs,asm,unit,vram,rom_offset,"
                       "status,meta) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                       (pid, fn.name, fn.size, console.function_insns(fn), target.code, json.dumps(target.to_json()),
                        console.function_asm(fn), fn.segment, fn.vram, fn.rom, console.function_status(fn),
                        json.dumps(meta)))
            db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)",
                       (pid, fn.name, fn.vram, fn.size, "func"))
        log.check_cancel()
        if plans:
            net._event(db, pid, None, "boundary", f"Added {len(plans)} reviewed N64Recomp discoveries to the split.")
    if plans:
        for cache in (net._syms, net._sym_override, net._xrefs, net._asm_names, net._ops, net._warmed, net._cmaps):
            cache.pop(pid, None)
        net._headers = {key: value for key, value in net._headers.items() if key[0] != pid}
        net.bump_generation(pid)
        net.twins.forget(proj)
    return [{"name": fn.name, "vram": fn.vram} for fn, _ in plans]


def keep_reviewed_on_reimport(net, proj, result, image, sha1, log):
    """Carry reviewed entries through a fresh split of the same image when the range stays free."""
    store = net.project_store(proj["id"])
    rows = store.all(f"SELECT name, vram, rom_offset, size, target, meta, unit FROM functions "
                     f"WHERE project_id=? AND {IN_BUILD} AND json_extract(meta, ?) IS NOT NULL",
                     proj["id"], f"$.{PROVENANCE}")
    if not rows:
        return
    names = {fn.name for fn in result.functions}
    functions = list(result.functions)
    added = []
    for row in rows:
        evidence = json.loads(row["meta"])[PROVENANCE]
        if evidence["image_sha1"] != sha1 or row["rom_offset"] is None or row["vram"] is None:
            continue
        lo, address, size = row["rom_offset"], row["vram"], row["size"]
        hi = lo + size
        owner = next((s for s in result.segments if s.rom_start <= lo < hi <= s.rom_end
                      and s.vram + lo - s.rom_start == address), None)
        if owner is None or row["name"] in names or image[lo:hi] != row["target"]:
            continue
        if any((fn.rom < hi and lo < fn.rom + fn.size)
               or (fn.vram < address + size and address < fn.vram + fn.size) for fn in functions):
            continue
        fn = split.function_from_bytes(row["name"], image[lo:hi], lo, address, owner.name, bool(proj["big_endian"]))
        names.add(fn.name)
        functions.append(fn)
        added.append(fn)
    if added:
        result.functions.extend(added)
        log(f"Kept {len(added)} previously reviewed N64Recomp functions in the new split.")
