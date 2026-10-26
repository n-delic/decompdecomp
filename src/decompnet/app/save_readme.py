"""The changing parts of a saved project's README, without replacing the owner's notes.

HTML comments mark the text we own. The local SVG travels with the save and needs no image service or game data.
"""

import math
import re
from collections import Counter
from html import escape

from ..network import IN_BUILD, SET_ASIDE

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
    rows = net.store.all(f"SELECT name, status, best_score, {SET_ASIDE} AS aside FROM functions "
                         f"WHERE project_id=? AND {IN_BUILD} ORDER BY vram, unit, name", proj["id"])
    states = [(r["name"], "aside" if r["aside"] else "matched" if r["status"] == "matched" else
               "attempt" if r["best_score"] is not None else "open") for r in rows]
    counts = Counter(state for _, state in states)
    total = len(rows) - counts["aside"]
    pct = 100 * counts["matched"] / total if total else 0
    progress = (f"- **Progress:** {counts['matched']} matching function(s), {best} best attempt(s)\n"
                f"- **Completion:** {counts['matched']:,} / {total:,} functions ({pct:.1f}%). "
                f"{counts['aside']:,} set aside, excluded from completion.")
    path = folder / "README.md"
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
    svg = _card(states, counts, total, pct, net.cfg(proj).get("platform") or "")
    image = folder / CARD
    if not image.exists() or image.read_text(encoding="utf-8") != svg:
        image.write_text(svg, encoding="utf-8")


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
