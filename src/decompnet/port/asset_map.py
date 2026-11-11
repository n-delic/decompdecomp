"""The ROM's data as the port's resources (docs/PORTING.md, phase 2), written as the kit's extractor config.

A port builds the game's code from its C, so what it needs from the user's ROM is every byte outside the code
segments: whatever the game reads at run time (DMA from the cartridge) must be in the resource archive the kit
extracts. This module divides those bytes into resources from ROM Explorer's saved scan, and checks the division:
the resources, the code segments, the console's boot bytes and the trailing fill cover the image exactly once.

A resource holds one kind of thing, as far as the scan knows: an archive the game unpacks itself (a ZIP's members
are inflated by the game's own code, which the kit's extractor can't do, so the archive stays whole), audio, or
other data. Bytes the scan can't name are kept in the data around them and counted, so the map is complete before
it is typed: textures, vertices and display lists in the kit's own types come next (ROADMAP.md, Ports).

The console's kit writes the files (`PortKit.asset_map`; Torch YAML for the N64). They name offsets and sizes only,
no game bytes, so they can be shared like the rest of a decomp.
"""

from __future__ import annotations

from pathlib import Path

from ..network import NetworkError

FOLDER = "port/assets"  # in the project's folder
_FILL = "repeated bytes"
_AUDIO = ("audio", "music sequence")
_CRUMB = 16  # unnamed bytes fewer than this are alignment between files


def _group(region: dict) -> str:
    """Which resource a region's bytes go to: "archive", "audio", "fill" (joins its neighbours) or "data"."""
    kind = region.get("kind") or "unknown"
    if kind == "container" or region.get("archive_path") or any(
            e.get("archive") or e.get("archive_path") for e in region.get("evidence", [])):
        return "archive"  # a container is an archive's own records (a ZIP's directory)
    if kind == _FILL or (kind == "unknown" and int(region["end"]) - int(region["start"]) < _CRUMB):
        return "fill"  # padding, or a few unnamed bytes between files: they join their neighbours
    return "audio" if kind in _AUDIO else "data"


def _outside(start: int, end: int, taken: list[tuple[int, int]]):
    """The parts of [start, end) outside the sorted, merged ranges `taken`."""
    for lo, hi in taken:
        if hi <= start or lo >= end:
            continue
        if lo > start:
            yield start, lo
        start = max(start, hi)
        if start >= end:
            return
    if start < end:
        yield start, end


def _merged(ranges) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for lo, hi in sorted(r for r in ranges if r[1] > r[0]):
        if out and lo <= out[-1][1]:
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append([lo, hi])
    return [(lo, hi) for lo, hi in out]


def plan(regions: list[dict], code: list[tuple[int, int]], size: int, *, boot: int = 0) -> dict:
    """{"entries": [{"name", "group", "offset", "size", "holds", "loaders"}], "code", "boot", "fill", "unknown"}.

    `regions` are ROM Explorer's (start, end, kind, evidence, direct_function_refs); `code` the image's code segments
    as (rom_start, rom_end); `boot` the bytes at the start the console's boot code reads, never the game."""
    taken = _merged([*code, (0, boot)])
    pieces = []
    covered = 0
    for r in sorted(regions, key=lambda r: (int(r["start"]), int(r["end"]))):
        start, end = max(int(r["start"]), covered), min(int(r["end"]), size)
        if start >= end:
            continue
        if start > covered:  # a hole in the scan: nothing known of it
            pieces += [(lo, hi, "data", None) for lo, hi in _outside(covered, start, taken)]
        pieces += [(lo, hi, _group(r), r) for lo, hi in _outside(start, end, taken)]
        covered = end
    pieces += [(lo, hi, "data", None) for lo, hi in _outside(covered, size, taken)]

    # Trailing fill is the cartridge's padding, never read; other fill joins the resource before it (alignment
    # between files), or the one after when it comes first.
    last = len(pieces) - 1  # the unbroken run of fill that reaches the image's end
    while (last >= 0 and pieces[last][2] == "fill"
           and pieces[last][1] == (pieces[last + 1][0] if last + 1 < len(pieces) else size)):
        last -= 1
    fill = sum(hi - lo for lo, hi, _, _ in pieces[last + 1:])
    entries: list[dict] = []
    pending: list[tuple] = []  # fill not yet joined to a resource

    def start(group, parts):
        entry = {"name": "", "group": group, "offset": parts[0][0], "size": 0, "holds": {}, "loaders": set()}
        for part in parts:
            _add(entry, *part)
        entries.append(entry)

    for lo, hi, group, region in pieces[:last + 1]:
        part = (lo, hi, (region or {}).get("kind") or "unknown", region)
        top = entries[-1] if entries else None
        joins = top is not None and top["offset"] + top["size"] == lo
        if pending and pending[-1][1] != lo:  # fill with nothing after it to join: data of its own
            start("data", pending)
            pending, top, joins = [], entries[-1], entries[-1]["offset"] + entries[-1]["size"] == lo
        if group == "fill":
            if joins:
                _add(top, *part)
            else:
                pending.append(part)
        elif joins and top["group"] == group and not pending:
            _add(top, *part)
        else:
            start(group, [*pending, part])
            pending = []
    if pending:  # fill before code, with no file after it
        start("data", pending)
    for entry in entries:
        entry["name"] = f"{entry['group']}_{entry['offset']:X}"
        entry["loaders"] = sorted(entry["loaders"])[:8]
        entry["holds"] = dict(sorted(entry["holds"].items(), key=lambda kv: -kv[1]))
    out = {"entries": entries, "code": sum(hi - lo for lo, hi in _merged(code)), "boot": boot, "fill": fill,
           "unknown": sum(e["holds"].get("unknown", 0) for e in entries), "size": size}
    if problems := check(out, code, size):
        raise NetworkError("The asset map doesn't cover the ROM exactly: " + "; ".join(problems[:5]))
    return out


def _add(entry: dict, lo: int, hi: int, kind: str, region) -> None:
    entry["size"] = hi - entry["offset"]
    entry["holds"][kind] = entry["holds"].get(kind, 0) + hi - lo
    entry["loaders"].update((region or {}).get("direct_function_refs") or ())


def check(result: dict, code: list[tuple[int, int]], size: int) -> list[str]:
    """What's wrong with a map: a resource overlapping another or the code, or a byte nothing covers."""
    problems = []
    spans = sorted([(e["offset"], e["offset"] + e["size"], e["name"]) for e in result["entries"]]
                   + [(lo, hi, "code") for lo, hi in _merged(code)]
                   + ([(0, result["boot"], "boot")] if result["boot"] else [])
                   + ([(size - result["fill"], size, "fill")] if result["fill"] else []))
    at = 0
    for lo, hi, name in spans:
        if hi > size:
            problems.append(f"{name} ends at 0x{hi:X}, past the ROM's end")
        if lo < at:
            if name != "code" and name != "boot":  # code ranges may overlap boot; they're not resources
                problems.append(f"{name} at 0x{lo:X} overlaps what's before it")
        elif lo > at:
            problems.append(f"0x{at:X}-0x{lo:X} is in no resource")
        at = max(at, hi)
    if at < size:
        problems.append(f"0x{at:X}-0x{size:X} is in no resource")
    return problems


def build(net, slug: str) -> dict:
    """The map of a project's ROM, from ROM Explorer's saved scan (read, never started), and the kit's files."""
    from ..app import rom_explorer
    proj = net.project_row(slug)
    if not net.is_rom(proj):
        raise NetworkError("The asset map reads projects decompdecomp split from a ROM (ROADMAP.md, Ports).")
    kit = net.console(proj).port_kit
    if kit is None or kit.asset_map is None:
        raise NetworkError(f"{net.console(proj).name} has no port kit with an asset extractor yet (ROADMAP.md, Ports).")
    listing = rom_explorer.listing(net, slug)
    if not listing.get("available"):
        raise NetworkError("ROM Explorer hasn't scanned this ROM yet: scan it first (ROM Explorer → Scan), then "
                           "write the asset map from what it found.")
    cfg = net.cfg(proj)
    code = [(int(s["rom_start"]), int(s["rom_end"])) for s in cfg.get("segments", [])
            if s.get("rom_start") is not None and s.get("rom_end") is not None]
    result = plan(listing["regions"], code, int(listing["size"]), boot=kit.boot)
    image = Path(proj["root"]) / cfg.get("rom", {}).get("file", "")
    graphics = kit.microcode(image.read_bytes()) if kit.microcode and image.is_file() else []
    game = {"title": cfg.get("rom", {}).get("title") or proj["slug"], "slug": proj["slug"],
            "sha1": cfg.get("rom", {}).get("sha1", ""), "graphics": graphics}
    result["files"] = kit.asset_map(game, result["entries"])
    result["stale"] = bool(listing.get("stale"))
    return result


def summary(result: dict, written: bool | None = None) -> dict:
    groups: dict[str, int] = {}
    for e in result["entries"]:
        groups[e["group"]] = groups.get(e["group"], 0) + e["size"]
    out = {"resources": len(result["entries"]), "bytes": sum(groups.values()), "groups": groups,
           "unknown": result["unknown"], "fill": result["fill"], "stale": result.get("stale", False),
           "folder": FOLDER, "files": sorted(result.get("files", {}))}
    if written is not None:
        out["written"] = written
    return out


def current(net, slug: str) -> dict | None:
    """The map as it would be written now, and whether the project's folder has it: None without a scan or a kit."""
    try:
        result = build(net, slug)
    except (NetworkError, KeyError, TypeError, ValueError):  # no scan, no kit, or a scan this can't read
        return None
    root = Path(net.project_row(slug)["root"]) / FOLDER
    written = all((root / name).is_file() and (root / name).read_text(encoding="utf-8") == text
                  for name, text in result["files"].items())
    return summary(result, written) | {"was_written": any((root / name).is_file() for name in result["files"])}


def write(net, slug: str) -> dict:
    """Write the kit's files into the project's folder (FOLDER), replacing an older map."""
    result = build(net, slug)
    root = Path(net.project_row(slug)["root"]) / FOLDER
    for name, text in result["files"].items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return summary(result, True) | {"path": str(root)}
