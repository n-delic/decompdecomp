"""The changing parts of a saved project's README, without replacing the owner's notes.

HTML comments mark the text we own. The local SVG travels with the save and needs no image service or game data.
"""

import json
import math
import re
from collections import Counter
from html import escape

from ..network import IN_BUILD, SET_ASIDE, NetworkError

CARD = "decompdecomp-progress.svg"
MAP_COLUMNS = 80
MAX_TILES = 4800
COLORS = {"matched": "#238636", "attempt": "#bf8700", "open": "#64748b", "aside": "#b54747"}


def block(name, content, newline="\n"):
    return f"<!-- decompdecomp:{name}:start -->\n{content}\n<!-- decompdecomp:{name}:end -->".replace("\n", newline)


def _replace(text, name, content):
    newline = "\r\n" if "\r\n" in text else "\n"
    start, end = f"<!-- decompdecomp:{name}:start -->", f"<!-- decompdecomp:{name}:end -->"
    # Incomplete or duplicated markers may surround someone's notes. Leave those alone.
    if text.count(start) == text.count(end) == 1 and text.index(start) < text.index(end):
        before, rest = text.split(start, 1)
        _, after = rest.split(end, 1)
        return before + block(name, content, newline) + after
    if start in text or end in text:
        return text
    return text + (newline if text.endswith("\n") else newline * 2) + block(name, content, newline) + newline


def update(net, proj, folder, initial, best, details=None):
    """Refresh counts and graphics in both save modes; create the guide only on the first save."""
    store = net.project_store(proj['id'])
    # One snapshot, bounded packets: sqlite3 hands the GIL back for every
    # returned row. Thousands of metadata rows can stall a save behind CPU-heavy
    # background workers even though no source payloads are read here.
    packets = store.all(f"""WITH numbered AS (
        SELECT name, CASE WHEN {SET_ASIDE} THEN 'aside'
            WHEN status='matched' THEN 'matched'
            WHEN best_score IS NOT NULL THEN 'attempt' ELSE 'open' END AS state,
            row_number() OVER (ORDER BY vram, unit, name) AS ordinal
        FROM functions WHERE project_id=? AND {IN_BUILD})
        SELECT json_group_array(json_array(ordinal,name,state)) AS packet
        FROM numbered GROUP BY (ordinal-1)/128 ORDER BY min(ordinal)""", proj['id'])
    ordered = []
    while packets:
        ordered.extend(json.loads(packets.pop()['packet']))
    # Aggregate input ordering is not guaranteed; ordinals retain the original
    # address/unit/name order, including SQLite's NULL-first ordering.
    ordered.sort(key=lambda row: row[0])
    states = [(name, state) for _, name, state in ordered]
    progress, svg = progress_content(states, best, net.cfg(proj).get("platform") or "")
    path = folder / "README.md"
    image = folder / CARD
    for target in (path, image):
        # A save, a sync or an autosave writes these; a link here would write outside the progress folder.
        if target.is_symlink():
            raise NetworkError("Progress paths cannot use symbolic links. Replace the link and try again.")
        if target.exists() and target.is_dir():
            raise NetworkError("The save folder contains a path with the wrong type. Move it and try again.")
    # Preserve CRLF, Unicode, and every byte outside our blocks when adopting an existing README.
    text = path.read_bytes().decode("utf-8") if path.exists() else initial
    newline = "\r\n" if "\r\n" in text else "\n"
    if "<!-- decompdecomp:progress:" not in text:
        # Older saves and hand-written guides can contain this one known counter line. Adopt only a full
        # numeric line, never a paragraph or a line with someone's extra notes on it.
        legacy = r"(?m)^- \*\*Progress:\*\* [\d,]+ matching function(?:s|\(s\))?[,;] [\d,]+ best attempt(?:s|\(s\))?(?=\r?$)"
        text = re.sub(legacy, lambda _: block("progress", progress, newline), text, count=1)
    if details is not None and "<!-- decompdecomp:details:start -->" in text:
        text = _replace(text, "details", details)
    text = _replace(text, "progress", progress)
    visuals = (f"## Progress map\n\n![Decompilation completion and function map]({CARD})\n\n"
               "Green: matched. Gold: attempted. Grey: open. Red: set aside. "
               "Functions follow address, then source unit and name order.\n"
               "The image and tagged counts update on save. Keep project notes outside the hidden "
               "`decompdecomp` comment pairs to preserve them.")
    text = _replace(text, "visuals", visuals)
    encoded = text.encode("utf-8")
    if not path.exists() or path.read_bytes() != encoded:
        path.write_bytes(encoded)
    if not image.exists() or image.read_text(encoding="utf-8") != svg:
        image.write_text(svg, encoding="utf-8")
    if details is None:
        cfg = net.cfg(proj)
        compiler = cfg.get("compiler") or {}
        compiler = compiler.get("toolchain") if isinstance(compiler, dict) else str(compiler)
        details = (f"- **Game:** {proj['name']}\n- **Platform:** {cfg.get('platform') or 'not recorded'}\n"
                   f"- **Compiler:** {compiler or 'not recorded'}")
    _update_root(folder, details, progress, _port_progress(net, proj, folder))


def _update_root(folder, details, progress, port=None):
    """In a rebuildable repository the progress folder is a subfolder, and visitors land on the root README: carry
    the game, platform, compiler, counts and progress map there too (the picture is the progress folder's own)."""
    root = folder.parent
    if folder.name != "progress" or not (root / "build.json").is_file():
        return
    path = root / "README.md"
    if path.is_symlink() or path.is_dir() or not path.is_file():
        return
    text = path.read_bytes().decode("utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    visuals = (f"![Decompilation completion and function map](progress/{CARD})\n\n"
               "Green: matched. Gold: attempted. Grey: open. Red: set aside.")
    blocks = [(name, content) for name, content in (("details", details), ("progress", progress), ("visuals", visuals),
                                                    ("port", port)) if content is not None]
    if "<!-- decompdecomp:visuals:start -->" in text:
        for name, content in blocks:
            if name == "port" and "<!-- decompdecomp:port:start -->" not in text:
                # Repositories saved before this section existed: add it right after the progress map.
                text = text.replace("<!-- decompdecomp:visuals:end -->",
                                    "<!-- decompdecomp:visuals:end -->" + newline * 2 + block(name, content, newline), 1)
            else:
                text = _replace(text, name, content)
    else:
        # First time: put the summary under the title, before the build instructions, instead of at the very end.
        summary = newline.join(block(name, content, newline) for name, content in blocks)
        head, sep, rest = text.partition(newline)
        if head.startswith("# "):
            text = head + sep + newline + summary + newline + (rest if rest.startswith(newline) else newline + rest)
        else:
            text = summary + newline + newline + text
    encoded = text.encode("utf-8")
    if path.read_bytes() != encoded:
        path.write_bytes(encoded)


def progress_content(states, best, platform):
    """Render the generated README counters and SVG from an ordered function map."""
    counts = Counter(state for _, state in states)
    total = len(states) - counts["aside"]
    pct = 100 * counts["matched"] / total if total else 0
    progress = (f"- **Progress:** {counts['matched']} matching function(s), {best} best attempt(s)\n"
                f"- **Completion:** {counts['matched']:,} / {total:,} functions ({pct:.1f}%). "
                f"{counts['aside']:,} set aside, excluded from completion.")
    return progress, _card(states, counts, total, pct, platform)


def _card(states, counts, total, pct, platform=""):
    """Bound a large game's map by grouping neighbours; mixed tiles show each state's actual fraction."""
    group_size = max(1, math.ceil(len(states) / MAX_TILES))
    tiles = math.ceil(len(states) / group_size)
    height = 180 + math.ceil(tiles / MAP_COLUMNS) * 10
    label = f"{counts['matched']:,} / {total:,} functions matched ({pct:.1f}%)"
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="840" height="{height}" '
           f'viewBox="0 0 840 {height}" role="img" aria-labelledby="title desc">',
           f'<title id="title">{label}</title>',
           '<desc id="desc">Completion excludes set-aside functions. Green: matched; gold: attempted; '
           'grey: open; red: set aside.</desc>',
           f'<rect width="840" height="{height}" rx="12" fill="#0f172a"/>',
           '<g font-family="sans-serif" fill="#f1f5f9">',
           '<text x="20" y="32" font-size="20">Decompilation progress</text>',
           f'<text x="820" y="32" text-anchor="end" font-size="16">{escape(platform)}</text>',
           f'<text x="20" y="59" font-size="16">{label}</text>',
           '<rect x="20" y="74" width="800" height="18" rx="4" fill="#334155"/>',
           f'<rect x="20" y="74" width="{8 * pct:.3f}" height="18" rx="4" fill="{COLORS["matched"]}"/>']
    for index, (state, color) in enumerate(COLORS.items()):
        x = 20 + index * 200
        svg.extend([f'<rect x="{x}" y="108" width="10" height="10" fill="{color}"/>',
                    f'<text x="{x + 16}" y="118" font-size="13">{state.title()}: {counts[state]:,}</text>'])
    svg.append(f'<text x="20" y="144" font-size="12">Function map: up to {group_size:,} function(s) per tile</text>')
    for index, offset in enumerate(range(0, len(states), group_size)):
        group = states[offset:offset + group_size]
        parts = Counter(state for _, state in group)
        title = group[0][0] if len(group) == 1 else f"Functions {offset + 1:,}–{offset + len(group):,}"
        svg.append(f'<g><title>{escape(title)}: {escape(str(dict(parts)))}</title>')
        x, y = 20 + (index % MAP_COLUMNS) * 10, 160 + (index // MAP_COLUMNS) * 10
        for state, color in COLORS.items():
            width = 8 * parts[state] / len(group)
            if width:
                svg.append(f'<rect x="{x:.3f}" y="{y}" width="{width:.3f}" height="8" fill="{color}"/>')
                x += width
        svg.append('</g>')
    return "\n".join(svg + ['</g></svg>', ''])


def _port_progress(net, proj, folder):
    """The Porting tab's checklist as README text, so visitors see how far a native build of the game is. None when the
    report can't be read (a save must never fail on it) or the project isn't in a rebuildable repository."""
    if folder.name != "progress" or not (folder.parent / "build.json").is_file():
        return None
    try:
        from ..port.readiness import STEPS, readiness, steps_done
        report = readiness(net, proj["slug"])
        done = steps_done(report)
    except Exception:  # noqa: BLE001 - a README refresh is never a reason to lose a save
        return None
    pc, link, assets = report.get("pc"), report.get("link"), report.get("assets")
    detail = {"code": f"{report['code']['c']:,} of {report['code']['total']:,} functions in C",
              "compiles": f"{pc['ok'] + pc['warned']:,} of {pc['total']:,} functions compile" if pc else "not checked yet",
              "links": f"{link['undefined']:,} symbols still undefined" if link else "not checked yet",
              "assets": (f"{assets['resources']:,} resources mapped" if assets else "not checked yet")}
    lines = ["## Porting progress", "",
             "A native build of the game (Windows, Linux, macOS) needs these steps. The game's own data is never in this "
             "repository: each person's port is built from their own copy.", "", "| Step | Status | Detail |", "| :-- | :-- | :-- |"]
    lines += [f"| {title} | {'✅ done' if step in done else '⬜ in progress'} | {detail[step]} |"
              for step, title in STEPS.items()]
    runtimes = report.get("runtimes") or []
    if runtimes:
        lines += ["", "**Runtimes:** " + "; ".join(
            f"{r['name']}: " + ("ready to play" if r["playable"] else "needs " + ", ".join(r["missing"][:3]).lower() if r["missing"]
                                else "not built yet") for r in runtimes)]
    return "\n".join(lines)
